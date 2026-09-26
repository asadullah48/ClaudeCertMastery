"""Learner identity and isolation (Commercial Completion Phase 1).

Runs in clerk auth mode with real RS256 session tokens signed by a throwaway key, so
signature, expiry, issuer and authorised-party checks are genuinely exercised -- only
the JWKS network fetch is replaced. Covers: unauthenticated rejection, token
validation, learner provisioning, object-level authorization on exam and scenario
attempts, and that two learners' evidence and readiness never mix.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

ISSUER = "https://clerk.test.example"
ORIGIN = "http://localhost:3000"  # settings.cors_origins default
SCENARIO = "CCAO-F-WISD-SCN-001"

_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
_OTHER_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)


def mint(sub: str, *, key=_KEY, iss: str = ISSUER, azp: str | None = ORIGIN, ttl: int = 300) -> str:
    now = int(time.time())
    claims = {"sub": sub, "iss": iss, "iat": now, "nbf": now, "exp": now + ttl}
    if azp is not None:
        claims["azp"] = azp
    return jwt.encode(claims, key, algorithm="RS256")


def as_(sub: str) -> dict:
    return {"Authorization": f"Bearer {mint(sub)}"}


@pytest.fixture
def env(tmp_path, monkeypatch):
    from app import auth
    from app.config import settings

    monkeypatch.setattr(settings, "auth_mode", "clerk")
    monkeypatch.setattr(settings, "clerk_issuer", ISSUER)
    monkeypatch.setattr(auth, "_signing_key", lambda token: _KEY.public_key())

    engine = create_engine(f"sqlite:///{tmp_path / 'auth.db'}", connect_args={"check_same_thread": False})
    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    from app import models as _models  # noqa: F401
    from app.database import Base, get_db
    from app.main import app
    from app.models import (
        Domain, ExamAttempt, Scenario, ScenarioStep, ScenarioStepOption, Track, User,
    )

    Base.metadata.create_all(engine)
    with Session() as db:
        track = Track(code="CCAO-F", name="AI Operator, Foundation")
        db.add(track)
        db.flush()
        domain = Domain(track_id=track.id, code="WISD", name="Workflow", weight_bps=10000, position=1)
        db.add(domain)
        # The pre-auth founder/dev learner, with existing evidence and no auth_subject.
        founder = User(email="dev@certmastery.local", display_name="Dev")
        db.add(founder)
        db.flush()
        scenario = Scenario(
            domain_id=domain.id, external_id=SCENARIO, title="The Silent Handoff",
            setup_text="Setup.", difficulty=2, is_active=True, content_version=1,
        )
        db.add(scenario)
        db.flush()
        s1 = ScenarioStep(scenario_id=scenario.id, position=1, prompt_text="Q1", step_type="mcq")
        s2 = ScenarioStep(scenario_id=scenario.id, position=2, prompt_text="Q2", step_type="mcq")
        db.add_all([s1, s2])
        db.flush()
        o1 = ScenarioStepOption(step_id=s1.id, label="A", text="Right", is_correct=True, position=1, rationale="r")
        o1b = ScenarioStepOption(step_id=s1.id, label="B", text="Wrong", is_correct=False, position=2, rationale="r")
        o2 = ScenarioStepOption(step_id=s2.id, label="A", text="Right", is_correct=True, position=1, rationale="r")
        o2b = ScenarioStepOption(step_id=s2.id, label="B", text="Wrong", is_correct=False, position=2, rationale="r")
        db.add_all([o1, o1b, o2, o2b])
        founder_exam = ExamAttempt(user_id=founder.id, track_id=track.id, seed=7)
        db.add(founder_exam)
        db.commit()
        ids = {"s1_right": o1.id, "s2_right": o2.id, "founder_id": founder.id,
               "founder_exam": founder_exam.id}

    def override_get_db():
        db = Session()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as client:
        yield client, ids, Session
    app.dependency_overrides.clear()


def _complete_scenario(client, headers, ids) -> int:
    r = client.post(f"/scenarios/{SCENARIO}/start", headers=headers)
    assert r.status_code == 201, r.text
    attempt_id = r.json()["attempt_id"]
    for pos, opt in ((1, ids["s1_right"]), (2, ids["s2_right"])):
        r = client.post(f"/scenario-attempts/{attempt_id}/steps/{pos}/answer",
                        json={"selected_option_ids": [opt]}, headers=headers)
        assert r.status_code == 200, r.text
    return attempt_id


# --- authentication -----------------------------------------------------------------

class TestAuthentication:
    @pytest.mark.parametrize("method,path", [
        ("get", "/me/tracks/CCAO-F/readiness"),
        ("post", f"/scenarios/{SCENARIO}/start"),
        ("get", "/scenario-attempts/1"),
        ("post", "/scenario-attempts/1/steps/1/answer"),
        ("post", "/scenario-attempts/1/steps/1/hint"),
        ("get", "/attempts/1"),
        ("post", "/attempts/1/submit"),
        ("post", "/attempts/1/explanations"),
        ("post", "/exams/generate"),
    ])
    def test_learner_private_routes_reject_anonymous(self, env, method, path):
        client, _, _ = env
        r = client.post(path, json={}) if method == "post" else client.get(path)
        assert r.status_code == 401

    def test_public_catalog_stays_public(self, env):
        client, _, _ = env
        assert client.get("/tracks").status_code == 200
        r = client.get("/scenarios", params={"track_code": "CCAO-F"})
        assert r.status_code == 200
        assert r.json()[0]["learner_status"] == "not_started"  # neutral, no learner

    @pytest.mark.parametrize("token", [
        mint("user_a", key=_OTHER_KEY),                 # forged signature
        mint("user_a", ttl=-60),                        # expired
        mint("user_a", iss="https://evil.example"),     # foreign issuer
        mint("user_a", azp="https://evil.example"),     # minted for another site
        "not-a-jwt",
    ])
    def test_untrustworthy_tokens_rejected(self, env, token):
        client, _, _ = env
        r = client.get("/me/tracks/CCAO-F/readiness", headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 401

    def test_misconfigured_server_fails_closed(self, env, monkeypatch):
        from app.config import settings

        client, _, _ = env
        monkeypatch.setattr(settings, "clerk_issuer", None)
        assert client.get("/me/tracks/CCAO-F/readiness", headers=as_("user_a")).status_code == 503

    def test_new_identity_gets_its_own_empty_learner(self, env):
        from app.models import User

        client, ids, Session = env
        for _ in range(2):
            assert client.get("/me/tracks/CCAO-F/readiness", headers=as_("user_new")).status_code == 200
        with Session() as db:
            users = db.scalars(select(User).where(User.auth_subject == "user_new")).all()
            assert len(users) == 1
            assert users[0].id != ids["founder_id"]
            # Never auto-attached to the founder's existing evidence.
            assert db.get(User, ids["founder_id"]).auth_subject is None


# --- object-level authorization -----------------------------------------------------

class TestObjectAuthorization:
    def test_learner_cannot_read_or_write_anothers_scenario_attempt(self, env):
        from app.models import ScenarioEvent

        client, ids, Session = env
        attempt_id = client.post(f"/scenarios/{SCENARIO}/start", headers=as_("user_a")).json()["attempt_id"]
        with Session() as db:
            events_before = db.scalar(select(func.count()).select_from(ScenarioEvent))

        assert client.get(f"/scenario-attempts/{attempt_id}", headers=as_("user_b")).status_code == 404
        r = client.post(f"/scenario-attempts/{attempt_id}/steps/1/answer",
                        json={"selected_option_ids": [ids["s1_right"]]}, headers=as_("user_b"))
        assert r.status_code == 404
        assert client.post(f"/scenario-attempts/{attempt_id}/steps/1/hint",
                           headers=as_("user_b")).status_code == 404

        with Session() as db:  # nothing B did reached A's attempt
            assert db.scalar(select(func.count()).select_from(ScenarioEvent)) == events_before
        assert client.get(f"/scenario-attempts/{attempt_id}", headers=as_("user_a")).status_code == 200

    def test_learner_cannot_read_submit_or_explain_anothers_exam_attempt(self, env):
        from app.models import AttemptStatus, ExamAttempt

        client, ids, Session = env
        exam = ids["founder_exam"]  # owned by the (unlinked) founder row
        assert client.get(f"/attempts/{exam}", headers=as_("user_b")).status_code == 404
        assert client.post(f"/attempts/{exam}/submit", json={"answers": []},
                           headers=as_("user_b")).status_code == 404
        assert client.post(f"/attempts/{exam}/explanations", json={},
                           headers=as_("user_b")).status_code == 404
        with Session() as db:
            assert db.get(ExamAttempt, exam).status == AttemptStatus.IN_PROGRESS

    def test_linked_owner_can_read_own_exam_attempt(self, env):
        from app.models import User

        client, ids, Session = env
        with Session() as db:  # what the founder backfill does, in miniature
            db.get(User, ids["founder_id"]).auth_subject = "user_founder"
            db.commit()
        r = client.get(f"/attempts/{ids['founder_exam']}", headers=as_("user_founder"))
        assert r.status_code == 200
        assert r.json()["id"] == ids["founder_exam"]


# --- evidence and readiness isolation ------------------------------------------------

class TestEvidenceIsolation:
    def test_two_learners_attempt_same_scenario_independently(self, env):
        from app.models import ScenarioAttempt, User

        client, ids, Session = env
        a = _complete_scenario(client, as_("user_a"), ids)
        b = _complete_scenario(client, as_("user_b"), ids)
        assert a != b
        with Session() as db:
            owner = {u.auth_subject: u.id for u in db.scalars(select(User)).all()}
            assert db.get(ScenarioAttempt, a).user_id == owner["user_a"]
            assert db.get(ScenarioAttempt, b).user_id == owner["user_b"]
            assert db.get(ScenarioAttempt, a).status == "submitted"
            assert db.get(ScenarioAttempt, b).status == "submitted"

        # A's exposure did not turn B's first attempt into a retake: both are evidence.
        for sub in ("user_a", "user_b"):
            listing = client.get("/scenarios", params={"track_code": "CCAO-F"}, headers=as_(sub)).json()
            assert listing[0]["evidence_band"] is not None

    def test_readiness_and_recommendation_are_per_learner(self, env):
        client, ids, _ = env
        rb_before = client.get("/me/tracks/CCAO-F/readiness", headers=as_("user_b")).json()
        ra_before = client.get("/me/tracks/CCAO-F/readiness", headers=as_("user_a")).json()
        _complete_scenario(client, as_("user_a"), ids)

        ra = client.get("/me/tracks/CCAO-F/readiness", headers=as_("user_a")).json()
        rb = client.get("/me/tracks/CCAO-F/readiness", headers=as_("user_b")).json()
        assert ra["domains"][0]["scenario_evidence_count"] == 1
        assert rb["domains"][0]["scenario_evidence_count"] == 0
        # A's evidence moved A's projection and recommendation...
        assert ra != ra_before
        # ...and left B's -- projection AND next action -- exactly as it was.
        assert rb == rb_before

        la = client.get("/scenarios", params={"track_code": "CCAO-F"}, headers=as_("user_a")).json()
        lb = client.get("/scenarios", params={"track_code": "CCAO-F"}, headers=as_("user_b")).json()
        assert (la[0]["learner_status"], la[0]["counts_as_evidence"]) == ("completed", False)
        assert (lb[0]["learner_status"], lb[0]["counts_as_evidence"]) == ("not_started", True)
