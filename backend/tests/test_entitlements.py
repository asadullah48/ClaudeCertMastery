"""Commercial entitlement (Phase 2A/2B/2F/2G).

Real Clerk-mode tokens (as test_auth_isolation.py) and the REAL entitlement policy --
this module opts out of conftest's full-access override. Covers: Explorer defaults, the
free diagnostic allowance, server-side refusal of paid capabilities, Readiness Pass
access, expiry and revocation, cross-learner independence, the readiness preview being
a pure redaction of full readiness, evidence immutability under grants, the manual
beta-grant script and the payment-provider boundary.
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from tests.test_auth_isolation import ISSUER, _KEY, as_  # noqa: E402

REAL_ENTITLEMENTS = True  # conftest: do not grant full access in this module

SAMPLE_A, SAMPLE_B, LOCKED_C = "CCAO-F-PTE-SCN-001", "CCAO-F-PTE-SCN-002", "CCAO-F-OEV-SCN-001"
# A second, unrelated track standing in for any future certification.
FUTURE = "FUTURE-X"
FUTURE_LOCKED = "FUTURE-X-DOM-SCN-003"
EVIDENCE_TABLES = [
    "exam_attempts", "attempt_items", "attempt_domain_scores", "scenario_attempts",
    "scenario_step_attempts", "scenario_events", "learner_domain_states",
]


@pytest.fixture
def env(tmp_path, monkeypatch):
    from app import auth
    from app.config import settings

    monkeypatch.setattr(settings, "auth_mode", "clerk")
    monkeypatch.setattr(settings, "clerk_issuer", ISSUER)
    monkeypatch.setattr(auth, "_signing_key", lambda token: _KEY.public_key())

    engine = create_engine(f"sqlite:///{tmp_path / 'ent.db'}", connect_args={"check_same_thread": False})
    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    from app import models as _models  # noqa: F401
    from app.database import Base, get_db
    from app.main import app
    from app.models import (
        AnswerOption, Domain, Question, Scenario, ScenarioStep, ScenarioStepOption, Track,
    )

    Base.metadata.create_all(engine)
    with Session() as db:
        track = Track(code="CCAO-F", name="AI Operator, Foundation", item_count=4)
        db.add(track)
        db.flush()
        pte = Domain(track_id=track.id, code="PTE", name="Prompting", weight_bps=5000, position=1)
        oev = Domain(track_id=track.id, code="OEV", name="Evaluation", weight_bps=5000, position=2)
        db.add_all([pte, oev])
        db.flush()
        for d in (pte, oev):
            for i in range(6):
                q = Question(domain_id=d.id, external_id=f"{d.code}-Q{i}", stem="Stem?", question_type="mcq")
                db.add(q)
                db.flush()
                db.add_all([
                    AnswerOption(question_id=q.id, label="A", text="Right", is_correct=True, position=1),
                    AnswerOption(question_id=q.id, label="B", text="Wrong", is_correct=False, position=2),
                ])
        for ext, dom in ((SAMPLE_A, pte), (SAMPLE_B, pte), (LOCKED_C, oev)):
            sc = Scenario(domain_id=dom.id, external_id=ext, title=ext, setup_text="Setup.",
                          difficulty=2, is_active=True, content_version=1)
            db.add(sc)
            db.flush()
            step = ScenarioStep(scenario_id=sc.id, position=1, prompt_text="Q1", step_type="mcq")
            db.add(step)
            db.flush()
            db.add_all([
                ScenarioStepOption(step_id=step.id, label="A", text="Right", is_correct=True, position=1, rationale="r"),
                ScenarioStepOption(step_id=step.id, label="B", text="Wrong", is_correct=False, position=2, rationale="r"),
            ])
        future = Track(code=FUTURE, name="Future certification", item_count=4)
        db.add(future)
        db.flush()
        fdom = Domain(track_id=future.id, code="DOM", name="Domain", weight_bps=10000, position=1)
        db.add(fdom)
        db.flush()
        for i in range(6):
            q = Question(domain_id=fdom.id, external_id=f"FX-Q{i}", stem="Stem?", question_type="mcq")
            db.add(q)
            db.flush()
            db.add(AnswerOption(question_id=q.id, label="A", text="Right", is_correct=True, position=1))
        for ext in ("FUTURE-X-DOM-SCN-001", "FUTURE-X-DOM-SCN-002", FUTURE_LOCKED):
            sc = Scenario(domain_id=fdom.id, external_id=ext, title=ext, setup_text="Setup.",
                          difficulty=2, is_active=True, content_version=1)
            db.add(sc)
            db.flush()
            db.add(ScenarioStep(scenario_id=sc.id, position=1, prompt_text="Q1", step_type="mcq"))
        db.commit()

    def override_get_db():
        db = Session()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as client:
        yield client, Session
    app.dependency_overrides.clear()


def _user(Session, subject):
    from app.models import User

    with Session() as db:
        return db.scalar(select(User).where(User.auth_subject == subject))


def _grant(Session, subject, *, track="CCAO-F", now=None, days=90):
    from app import entitlements

    with Session() as db:
        user = db.merge(_user(Session, subject))
        g = entitlements.activate_readiness_pass(
            db, user, track, source="manual_grant", granted_by="test", now=now, days=days
        )
        db.commit()
        return g.id


def _provision(client, subject):
    assert client.get("/me/access", headers=as_(subject)).status_code == 200


def _submit_all_exams(Session, subject):
    from app.models import ExamAttempt
    from app.models.attempt import AttemptStatus

    with Session() as db:
        user = _user(Session, subject)
        for a in db.scalars(select(ExamAttempt).where(ExamAttempt.user_id == user.id)):
            a.status = AttemptStatus.SUBMITTED
        db.commit()


def _evidence_snapshot(Session):
    with Session() as db:
        return {t: db.execute(text(f"select * from {t} order by id")).all() for t in EVIDENCE_TABLES}


# --- 1. Explorer defaults ---------------------------------------------------------


def test_new_learner_is_explorer(env):
    client, _ = env
    r = client.get("/me/access?track_code=CCAO-F", headers=as_("user_free"))
    assert r.status_code == 200
    body = r.json()
    assert body["plan"] == "free" and body["track_code"] == "CCAO-F"
    assert body["expires_at"] is None
    assert set(body["capabilities"]) == {"diagnostic_exam", "sample_scenarios", "readiness_preview"}
    assert body["exam_allowance"] == {
        "track_code": "CCAO-F", "allowed": True, "submitted": 0, "started": 0, "free_submitted_limit": 1,
    }
    assert body["offer"]["price_usd"] == 29 and body["offer"]["duration_days"] == 90
    assert body["offer"]["recurring"] is False and body["offer"]["checkout_available"] is False


def test_access_requires_sign_in(env):
    client, _ = env
    assert client.get("/me/access").status_code == 401


def test_public_offer(env):
    client, _ = env
    r = client.get("/offer")
    assert r.status_code == 200
    assert r.json() == {
        "product_name": "ClaudeCertMastery Readiness Pass — CCAO-F", "track_code": "CCAO-F",
        "subtitle": "90-Day CCAO-F Preparation",
        "price_usd": 29, "duration_days": 90, "recurring": False, "checkout_available": False,
    }


# --- 2/3. Free diagnostic, then server-side refusal ---------------------------------


def test_free_learner_gets_one_diagnostic_then_402(env):
    client, Session = env
    h = as_("user_free")
    r = client.post("/exams/generate", json={"track_code": "CCAO-F"}, headers=h)
    assert r.status_code == 201, r.text
    _submit_all_exams(Session, "user_free")

    r = client.post("/exams/generate", json={"track_code": "CCAO-F"}, headers=h)
    assert r.status_code == 402
    detail = r.json()["detail"]
    assert detail["code"] == "entitlement_required"
    assert detail["capability"] == "additional_practice_exams"
    assert "Readiness Pass" in detail["message"]


def test_free_abandoned_diagnostics_are_capped(env):
    client, _ = env
    h = as_("user_free")
    for _ in range(3):
        assert client.post("/exams/generate", json={"track_code": "CCAO-F"}, headers=h).status_code == 201
    assert client.post("/exams/generate", json={"track_code": "CCAO-F"}, headers=h).status_code == 402


def test_free_learner_sees_catalog_with_locks_and_can_start_samples_only(env):
    client, _ = env
    h = as_("user_free")
    listing = {s["external_id"]: s["locked"] for s in client.get("/scenarios?track_code=CCAO-F", headers=h).json()}
    assert listing == {SAMPLE_A: False, SAMPLE_B: False, LOCKED_C: True}

    assert client.post(f"/scenarios/{SAMPLE_A}/start", headers=h).status_code == 201
    r = client.post(f"/scenarios/{LOCKED_C}/start", headers=h)
    assert r.status_code == 402
    assert r.json()["detail"]["capability"] == "full_scenario_lab"


def test_signed_out_catalog_shows_explorer_locks(env):
    client, _ = env
    listing = {s["external_id"]: s["locked"] for s in client.get("/scenarios?track_code=CCAO-F").json()}
    assert listing == {SAMPLE_A: False, SAMPLE_B: False, LOCKED_C: True}


# --- track scope: a CCAO-F pass is a CCAO-F pass ------------------------------------


def test_ccao_f_pass_unlocks_ccao_f_only(env):
    client, Session = env
    h = as_("user_paid")
    _provision(client, "user_paid")
    _grant(Session, "user_paid", track="CCAO-F")

    assert client.get("/me/access?track_code=CCAO-F", headers=h).json()["plan"] == "readiness_pass"
    assert client.post(f"/scenarios/{LOCKED_C}/start", headers=h).status_code == 201
    assert client.get("/me/tracks/CCAO-F/readiness", headers=h).json()["depth"] == "full"

    # The same learner, same moment, a different track: Explorer.
    other = client.get(f"/me/access?track_code={FUTURE}", headers=h).json()
    assert other["plan"] == "free" and other["expires_at"] is None
    r = client.post(f"/scenarios/{FUTURE_LOCKED}/start", headers=h)
    assert r.status_code == 402 and r.json()["detail"]["capability"] == "full_scenario_lab"
    locks = {s["external_id"]: s["locked"] for s in client.get(f"/scenarios?track_code={FUTURE}", headers=h).json()}
    assert locks[FUTURE_LOCKED] is True
    assert client.get(f"/me/tracks/{FUTURE}/readiness", headers=h).json()["depth"] == "preview"
    assert client.post("/exams/generate", json={"track_code": FUTURE}, headers=h).status_code == 201
    _submit_all_exams(Session, "user_paid")
    assert client.post("/exams/generate", json={"track_code": FUTURE}, headers=h).status_code == 402


def test_expiry_is_scoped_per_track(env):
    client, Session = env
    h = as_("user_two")
    _provision(client, "user_two")
    _grant(Session, "user_two", track="CCAO-F", now=datetime.now(timezone.utc) - timedelta(days=91))
    _grant(Session, "user_two", track=FUTURE)
    assert client.get("/me/access?track_code=CCAO-F", headers=h).json()["plan"] == "free"
    assert client.get(f"/me/access?track_code={FUTURE}", headers=h).json()["plan"] == "readiness_pass"
    assert client.post(f"/scenarios/{LOCKED_C}/start", headers=h).status_code == 402


def test_extension_only_counts_the_same_tracks_time(env):
    client, Session = env
    from app import entitlements

    _provision(client, "user_ext")
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    _grant(Session, "user_ext", track=FUTURE, now=now)
    with Session() as db:
        user = db.merge(_user(Session, "user_ext"))
        starts, _ = entitlements.plan_readiness_pass(db, user, "CCAO-F", now=now)
    assert starts == now  # a FUTURE-X pass does not push the CCAO-F start date


def test_grant_for_unknown_track_is_refused(env):
    client, Session = env
    from app import entitlements

    _provision(client, "user_x")
    with Session() as db:
        user = db.merge(_user(Session, "user_x"))
        with pytest.raises(ValueError):
            entitlements.activate_readiness_pass(db, user, "NOPE", source="manual_grant")


# --- 4. Readiness Pass ------------------------------------------------------------


def test_paid_learner_has_full_access(env):
    client, Session = env
    h = as_("user_paid")
    _provision(client, "user_paid")
    _grant(Session, "user_paid")

    body = client.get("/me/access", headers=h).json()
    assert body["plan"] == "readiness_pass"
    assert body["expires_at"] is not None
    assert len(body["capabilities"]) == 8

    for _ in range(3):
        assert client.post("/exams/generate", json={"track_code": "CCAO-F"}, headers=h).status_code == 201
        _submit_all_exams(Session, "user_paid")
    assert client.post(f"/scenarios/{LOCKED_C}/start", headers=h).status_code == 201
    listing = client.get("/scenarios?track_code=CCAO-F", headers=h).json()
    assert not any(s["locked"] for s in listing)


def test_grant_is_ninety_days_and_extends_rather_than_overlaps(env):
    client, Session = env
    from app import entitlements
    from app.models import LearnerEntitlement

    _provision(client, "user_paid")
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    _grant(Session, "user_paid", now=now)
    _grant(Session, "user_paid", now=now + timedelta(days=10))
    with Session() as db:
        rows = db.scalars(select(LearnerEntitlement).order_by(LearnerEntitlement.id)).all()
        ends = [entitlements._aware(r.expires_at) for r in rows]
        starts = [entitlements._aware(r.starts_at) for r in rows]
    assert ends[0] == now + timedelta(days=90)
    assert starts[1] == ends[0] and ends[1] == now + timedelta(days=180)


# --- 5. Expiry / revocation -------------------------------------------------------


def test_expired_pass_behaves_as_free(env):
    client, Session = env
    h = as_("user_lapsed")
    _provision(client, "user_lapsed")
    _grant(Session, "user_lapsed", now=datetime.now(timezone.utc) - timedelta(days=91))

    assert client.get("/me/access", headers=h).json()["plan"] == "free"
    assert client.post(f"/scenarios/{LOCKED_C}/start", headers=h).status_code == 402
    assert client.get("/me/tracks/CCAO-F/readiness", headers=h).json()["depth"] == "preview"


def test_revoked_pass_behaves_as_free(env):
    client, Session = env
    from app.models import LearnerEntitlement

    _provision(client, "user_rev")
    gid = _grant(Session, "user_rev")
    with Session() as db:
        db.get(LearnerEntitlement, gid).status = "revoked"
        db.commit()
    assert client.get("/me/access", headers=as_("user_rev")).json()["plan"] == "free"


def test_in_progress_scenario_can_be_finished_after_expiry(env):
    """Expiry gates starting, never finishing -- evidence is not stranded."""
    client, Session = env
    from app.models import LearnerEntitlement, Scenario, ScenarioStep, ScenarioStepOption

    h = as_("user_mid")
    _provision(client, "user_mid")
    gid = _grant(Session, "user_mid")
    started = client.post(f"/scenarios/{LOCKED_C}/start", headers=h).json()
    with Session() as db:
        db.get(LearnerEntitlement, gid).status = "revoked"
        right = db.scalar(
            select(ScenarioStepOption.id)
            .join(ScenarioStep, ScenarioStepOption.step_id == ScenarioStep.id)
            .join(Scenario, ScenarioStep.scenario_id == Scenario.id)
            .where(Scenario.external_id == LOCKED_C, ScenarioStepOption.is_correct.is_(True))
        )
        db.commit()
    assert client.get("/me/access", headers=h).json()["plan"] == "free"
    r = client.post(
        f"/scenario-attempts/{started['attempt_id']}/steps/1/answer",
        json={"selected_option_ids": [right]}, headers=h,
    )
    assert r.status_code == 200, r.text


# --- 6. Isolation -----------------------------------------------------------------


def test_one_learners_pass_does_not_affect_another(env):
    client, Session = env
    _provision(client, "user_a")
    _provision(client, "user_b")
    _grant(Session, "user_a")
    assert client.get("/me/access", headers=as_("user_a")).json()["plan"] == "readiness_pass"
    assert client.get("/me/access", headers=as_("user_b")).json()["plan"] == "free"
    assert client.post(f"/scenarios/{LOCKED_C}/start", headers=as_("user_b")).status_code == 402


# --- 7. Evidence immutability -----------------------------------------------------


def test_granting_and_revoking_never_touch_evidence(env):
    client, Session = env
    from app.models import LearnerEntitlement

    h = as_("user_ev")
    assert client.post("/exams/generate", json={"track_code": "CCAO-F"}, headers=h).status_code == 201
    assert client.post(f"/scenarios/{SAMPLE_A}/start", headers=h).status_code == 201
    before = _evidence_snapshot(Session)
    gid = _grant(Session, "user_ev")
    assert _evidence_snapshot(Session) == before
    with Session() as db:
        db.get(LearnerEntitlement, gid).status = "revoked"
        db.commit()
    assert _evidence_snapshot(Session) == before


# --- readiness preview is a pure redaction of full readiness ------------------------


def test_readiness_preview_only_withholds_depth(env):
    client, Session = env
    from app import entitlements
    from app.models import Domain, LearnerDomainState, User

    for sub in ("user_free", "user_paid"):
        _provision(client, sub)
    calculated = datetime(2026, 1, 2, tzinfo=timezone.utc)
    with Session() as db:
        pte = db.scalar(select(Domain).where(Domain.code == "PTE"))
        for sub in ("user_free", "user_paid"):
            u = db.scalar(select(User).where(User.auth_subject == sub))
            db.add(LearnerDomainState(
                user_id=u.id, track_id=pte.track_id, domain_id=pte.id,
                practice_evidence_count=24, scenario_evidence_count=1, distinct_scenario_content_versions=1,
                recent_practice_mastery_band="developing", recent_scenario_mastery_band="critical",
                unresolved_misconception_count=2, readiness_state="developing",
                reason_codes=["SCENARIO_EVIDENCE_WEAK"], calculated_at=calculated,
            ))
        db.commit()
    _grant(Session, "user_paid")

    full = client.get("/me/tracks/CCAO-F/readiness", headers=as_("user_paid")).json()
    preview = client.get("/me/tracks/CCAO-F/readiness", headers=as_("user_free")).json()
    assert full["depth"] == "full" and preview["depth"] == "preview"
    assert full["domains"][0]["unresolved_misconception_count"] == 2
    assert full["domains"][0]["recent_practice_mastery_band"] == "developing"

    withheld = {"recent_practice_mastery_band", "recent_scenario_mastery_band", "unresolved_misconception_count"}
    for f, p in zip(full["domains"], preview["domains"]):
        assert all(p[k] is None for k in withheld)
        assert {k: v for k, v in f.items() if k not in withheld} == {k: v for k, v in p.items() if k not in withheld}
    for key in ("overall_readiness_state", "overall_reason_codes", "track_code"):
        assert full[key] == preview[key]
    strip = lambda na: {k: v for k, v in na.items() if k != "scenario_locked"}  # noqa: E731
    assert strip(full["next_action"]) == strip(preview["next_action"])
    assert full["next_action"]["scenario_locked"] is False
    assert entitlements.PLAN_CAPABILITIES["free"] < entitlements.PLAN_CAPABILITIES["readiness_pass"]


# --- 2G. manual beta grant script -------------------------------------------------


@pytest.fixture
def grant_script(env, monkeypatch):
    import grant_readiness_pass

    _, Session = env
    monkeypatch.setattr(grant_readiness_pass, "SessionLocal", Session)
    return grant_readiness_pass


def _grant_rows(Session):
    from app.models import LearnerEntitlement

    with Session() as db:
        return db.scalars(select(LearnerEntitlement)).all()


def test_grant_script_dry_run_writes_nothing(env, grant_script, capsys):
    client, Session = env
    _provision(client, "user_beta")
    assert grant_script.main(["--subject", "user_beta", "--track", "CCAO-F", "--granted-by", "founder"]) == 0
    assert "DRY RUN" in capsys.readouterr().out
    assert _grant_rows(Session) == []


def test_grant_script_execute_grants_ninety_days_and_keeps_evidence(env, grant_script):
    client, Session = env
    h = as_("user_beta")
    assert client.post("/exams/generate", json={"track_code": "CCAO-F"}, headers=h).status_code == 201
    before = _evidence_snapshot(Session)
    assert grant_script.main(
        ["--subject", "user_beta", "--track", "CCAO-F", "--granted-by", "founder", "--execute"]
    ) == 0
    rows = _grant_rows(Session)
    assert len(rows) == 1
    g = rows[0]
    assert (g.source, g.granted_by, g.plan, g.status, g.track_code) == (
        "manual_grant", "founder", "readiness_pass", "active", "CCAO-F",
    )
    assert g.expires_at - g.starts_at == timedelta(days=90)
    assert _evidence_snapshot(Session) == before
    assert client.get("/me/access", headers=h).json()["plan"] == "readiness_pass"
    assert client.get(f"/me/access?track_code={FUTURE}", headers=h).json()["plan"] == "free"


def test_grant_script_requires_a_known_track(env, grant_script):
    client, Session = env
    _provision(client, "user_beta")
    with pytest.raises(SystemExit):  # --track is mandatory
        grant_script.main(["--subject", "user_beta", "--granted-by", "f", "--execute"])
    assert grant_script.main(["--subject", "user_beta", "--track", "NOPE", "--granted-by", "f", "--execute"]) == 2
    assert _grant_rows(Session) == []


def test_grant_script_refuses_unknown_or_unlinked_learner(env, grant_script):
    client, Session = env
    from app.models import User

    assert grant_script.main(["--subject", "user_nobody", "--track", "CCAO-F", "--granted-by", "f", "--execute"]) == 2
    with Session() as db:
        db.add(User(email="orphan@example.com", display_name="Orphan"))
        db.commit()
    assert grant_script.main(["--email", "orphan@example.com", "--track", "CCAO-F", "--granted-by", "f", "--execute"]) == 2
    assert _grant_rows(Session) == []


def test_grant_script_requires_exactly_one_selector(env, grant_script):
    with pytest.raises(SystemExit):
        grant_script.main(["--subject", "a", "--user-id", "1", "--track", "CCAO-F", "--granted-by", "f"])
    with pytest.raises(SystemExit):
        grant_script.main(["--track", "CCAO-F", "--granted-by", "f"])


# --- 2F. payment provider boundary ------------------------------------------------


def test_verified_purchase_activates_once(env):
    client, Session = env
    from app.services.billing import PurchaseRejected, VerifiedPurchase, apply_verified_purchase

    _provision(client, "user_buyer")
    p = VerifiedPurchase(provider="lemonsqueezy", external_reference="order_1",
                         learner_subject="user_buyer", product="readiness_pass_ccao_f",
                         occurred_at=datetime.now(timezone.utc))
    with Session() as db:
        first = apply_verified_purchase(db, p).id
        db.commit()
        assert apply_verified_purchase(db, p).id == first  # re-delivered event: no second grant
        db.commit()
        with pytest.raises(PurchaseRejected):
            apply_verified_purchase(db, VerifiedPurchase("paddle", "o2", "user_buyer", "other", p.occurred_at))
        with pytest.raises(PurchaseRejected):
            apply_verified_purchase(db, VerifiedPurchase("paddle", "o3", "user_ghost", "readiness_pass_ccao_f", p.occurred_at))
    rows = _grant_rows(Session)
    assert len(rows) == 1 and rows[0].track_code == "CCAO-F"
    assert client.get("/me/access", headers=as_("user_buyer")).json()["plan"] == "readiness_pass"
    assert client.get(f"/me/access?track_code={FUTURE}", headers=as_("user_buyer")).json()["plan"] == "free"


def test_no_webhook_endpoint_is_exposed(env):
    client, _ = env
    paths = {r.path for r in client.app.routes}
    assert not any("webhook" in p or "billing" in p or "checkout" in p for p in paths)
