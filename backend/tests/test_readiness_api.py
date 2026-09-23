"""KSOR Slice 6: the learner-facing readiness API (GET /me/tracks/{track_code}/readiness).

Same TestClient + dependency_override pattern as test_scenario_api.py: one shared
SQLite engine, get_db overridden to open sessions against it, hand-built
Track/Domain/User fixtures (no seed.py dependency).
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

NOW = datetime(2026, 1, 15, 12, 0, 0, tzinfo=timezone.utc)


@pytest.fixture
def readiness_env(tmp_path):
    db_path = tmp_path / "readiness_api.db"
    engine = create_engine(f"sqlite:///{db_path}", connect_args={"check_same_thread": False})
    TestingSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    from app.database import Base, get_db
    from app.main import app
    from app.models import Domain, Track, User

    Base.metadata.create_all(engine)
    with TestingSession() as db:
        track = Track(code="CCAO-F", name="AI Operator, Foundation")
        db.add(track)
        db.flush()
        domains = {
            "PTE": Domain(track_id=track.id, code="PTE", name="Prompting", weight_bps=1400, position=1),
            "OEV": Domain(track_id=track.id, code="OEV", name="Output Eval", weight_bps=2100, position=2),
            "WISD": Domain(track_id=track.id, code="WISD", name="Workflow", weight_bps=1600, position=3),
        }
        db.add_all(domains.values())
        db.flush()
        user = User(email="dev@certmastery.local", display_name="Dev")
        db.add(user)
        db.commit()
        track_id, domain_ids, user_id = (
            track.id, {c: d.id for c, d in domains.items()}, user.id,
        )

    def override_get_db():
        db = TestingSession()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as client:
        yield client, TestingSession, track_id, domain_ids, user_id
    app.dependency_overrides.clear()


def _add_state(
    TestingSession, *, user_id, track_id, domain_id, state, reasons=(),
    practice=0, scenario=0, distinct=0, misconceptions=0,
    practice_band=None, scenario_band=None, most_recent_at=None, calculated_at=NOW,
):
    from app.models import LearnerDomainState

    with TestingSession() as db:
        db.add(
            LearnerDomainState(
                user_id=user_id, track_id=track_id, domain_id=domain_id,
                readiness_state=state, reason_codes=list(reasons),
                practice_evidence_count=practice, scenario_evidence_count=scenario,
                distinct_scenario_content_versions=distinct,
                unresolved_misconception_count=misconceptions,
                recent_practice_mastery_band=practice_band,
                recent_scenario_mastery_band=scenario_band,
                most_recent_evidence_at=most_recent_at,
                calculated_at=calculated_at,
            )
        )
        db.commit()


def snapshot_table(db, model) -> list[tuple]:
    cols = [c.name for c in model.__table__.columns]
    rows = db.scalars(select(model)).all()
    return sorted(tuple(getattr(row, c) for c in cols) for row in rows)


# --- Zero-evidence learner -----------------------------------------------------------


class TestZeroEvidenceLearner:
    def test_every_domain_represented_as_insufficient_evidence(self, readiness_env):
        client, TestingSession, track_id, domain_ids, user_id = readiness_env
        r = client.get("/me/tracks/CCAO-F/readiness")
        assert r.status_code == 200
        body = r.json()
        assert body["track_code"] == "CCAO-F"
        assert {d["domain_code"] for d in body["domains"]} == {"PTE", "OEV", "WISD"}
        assert all(d["readiness_state"] == "insufficient_evidence" for d in body["domains"])
        assert all(d["is_materialized"] is False for d in body["domains"])
        assert body["overall_readiness_state"] == "insufficient_evidence"
        assert body["next_action"]["action"] == "COLLECT_PRACTICE_EVIDENCE"

    def test_get_creates_no_projection_rows(self, readiness_env):
        client, TestingSession, track_id, domain_ids, user_id = readiness_env
        client.get("/me/tracks/CCAO-F/readiness")
        with TestingSession() as db:
            from app.models import LearnerDomainState

            assert db.scalars(select(LearnerDomainState)).all() == []


# --- Partial projection learner -------------------------------------------------------


class TestPartialProjectionLearner:
    def test_materialized_and_missing_domains_both_represented(self, readiness_env):
        client, TestingSession, track_id, domain_ids, user_id = readiness_env
        _add_state(
            TestingSession, user_id=user_id, track_id=track_id, domain_id=domain_ids["PTE"],
            state="ready", practice=5, scenario=2, distinct=2,
            practice_band="strong", scenario_band="strong", most_recent_at=NOW,
        )
        r = client.get("/me/tracks/CCAO-F/readiness")
        body = r.json()
        by_code = {d["domain_code"]: d for d in body["domains"]}
        assert by_code["PTE"]["readiness_state"] == "ready"
        assert by_code["PTE"]["is_materialized"] is True
        assert by_code["OEV"]["is_materialized"] is False
        assert by_code["WISD"]["is_materialized"] is False
        # Missing coverage must block track-level ready (Slice 2 aggregation).
        assert body["overall_readiness_state"] == "insufficient_evidence"

    def test_get_still_creates_no_new_rows(self, readiness_env):
        client, TestingSession, track_id, domain_ids, user_id = readiness_env
        _add_state(
            TestingSession, user_id=user_id, track_id=track_id, domain_id=domain_ids["PTE"],
            state="ready", practice=5, scenario=2, distinct=2,
        )
        client.get("/me/tracks/CCAO-F/readiness")
        with TestingSession() as db:
            from app.models import LearnerDomainState

            rows = db.scalars(select(LearnerDomainState)).all()
            assert len(rows) == 1  # only the one this test itself inserted


# --- Production-shaped regression ----------------------------------------------------


class TestProductionShapedRegression:
    def test_ccao_f_pte_scn_001_shape(self, readiness_env):
        client, TestingSession, track_id, domain_ids, user_id = readiness_env
        _add_state(
            TestingSession, user_id=user_id, track_id=track_id, domain_id=domain_ids["PTE"],
            state="insufficient_evidence",
            reasons=["INSUFFICIENT_PRACTICE_EVIDENCE", "NO_APPLIED_SCENARIO_EVIDENCE"],
            practice=0, scenario=1, distinct=1, scenario_band="strong", most_recent_at=NOW,
        )
        r = client.get("/me/tracks/CCAO-F/readiness")
        assert r.status_code == 200
        body = r.json()
        pte = next(d for d in body["domains"] if d["domain_code"] == "PTE")
        assert pte["practice_evidence_count"] == 0
        assert pte["scenario_evidence_count"] == 1
        assert pte["readiness_state"] == "insufficient_evidence"
        assert "DOMAIN_BELOW_THRESHOLD" not in pte["reason_codes"]
        assert body["overall_readiness_state"] != "ready"
        assert body["next_action"]["action"] == "COLLECT_PRACTICE_EVIDENCE"


# --- Ready-track regression -----------------------------------------------------------


class TestReadyTrackRegression:
    def test_all_domains_ready(self, readiness_env):
        client, TestingSession, track_id, domain_ids, user_id = readiness_env
        for code in ("PTE", "OEV", "WISD"):
            _add_state(
                TestingSession, user_id=user_id, track_id=track_id, domain_id=domain_ids[code],
                state="ready", practice=5, scenario=2, distinct=2,
                practice_band="strong", scenario_band="strong", most_recent_at=NOW,
            )
        r = client.get("/me/tracks/CCAO-F/readiness")
        body = r.json()
        assert body["overall_readiness_state"] == "ready"
        assert body["next_action"]["action"] == "PROCEED_TO_READINESS_EVALUATION"
        assert body["next_action"]["domain_code"] is None
        assert [d["domain_code"] for d in body["domains"]] == ["PTE", "OEV", "WISD"]


# --- Mixed-state regression ------------------------------------------------------------


class TestMixedStateRegression:
    def test_faithfully_returns_each_domains_own_state(self, readiness_env):
        client, TestingSession, track_id, domain_ids, user_id = readiness_env
        _add_state(
            TestingSession, user_id=user_id, track_id=track_id, domain_id=domain_ids["PTE"],
            state="ready", practice=5, scenario=2, distinct=2,
            practice_band="strong", scenario_band="strong", most_recent_at=NOW,
        )
        _add_state(
            TestingSession, user_id=user_id, track_id=track_id, domain_id=domain_ids["OEV"],
            state="developing", reasons=["REPEATED_MISCONCEPTION"],
            practice=5, scenario=2, distinct=2, misconceptions=1,
            practice_band="strong", scenario_band="strong", most_recent_at=NOW,
        )
        _add_state(
            TestingSession, user_id=user_id, track_id=track_id, domain_id=domain_ids["WISD"],
            state="approaching_ready", reasons=["STALE_EVIDENCE"],
            practice=5, scenario=2, distinct=2,
            practice_band="strong", scenario_band="strong",
            most_recent_at=NOW - timedelta(days=200),
        )
        r = client.get("/me/tracks/CCAO-F/readiness")
        body = r.json()
        by_code = {d["domain_code"]: d for d in body["domains"]}
        assert by_code["PTE"]["readiness_state"] == "ready"
        assert by_code["OEV"]["readiness_state"] == "developing"
        assert by_code["WISD"]["readiness_state"] == "approaching_ready"
        # Router must not invent its own priority -- must match Slice 5 exactly:
        # misconception (OEV) outranks stale (WISD).
        assert body["next_action"]["action"] == "REMEDIATE_MISCONCEPTION"
        assert body["next_action"]["domain_code"] == "OEV"


# --- Cross-user / cross-track isolation ------------------------------------------------


class TestCrossUserSecurity:
    def test_another_users_state_never_appears(self, readiness_env):
        client, TestingSession, track_id, domain_ids, user_id = readiness_env
        from app.models import User

        with TestingSession() as db:
            other = User(email="other@example.com", display_name="Other")
            db.add(other)
            db.commit()
            other_id = other.id

        _add_state(
            TestingSession, user_id=other_id, track_id=track_id, domain_id=domain_ids["PTE"],
            state="ready", practice=50, scenario=20, distinct=20,
            practice_band="strong", scenario_band="strong", most_recent_at=NOW,
        )
        # The dev user (the only caller identity this endpoint supports) has none.
        r = client.get("/me/tracks/CCAO-F/readiness")
        body = r.json()
        pte = next(d for d in body["domains"] if d["domain_code"] == "PTE")
        assert pte["is_materialized"] is False
        assert pte["practice_evidence_count"] == 0
        assert body["overall_readiness_state"] != "ready"


class TestCrossTrackIsolation:
    def test_another_tracks_state_does_not_leak(self, readiness_env):
        client, TestingSession, track_id, domain_ids, user_id = readiness_env
        from app.models import Domain, Track

        with TestingSession() as db:
            other_track = Track(code="CCDV-F", name="Other Track")
            db.add(other_track)
            db.flush()
            other_domain = Domain(
                track_id=other_track.id, code="PTE", name="Prompting", weight_bps=1000, position=1,
            )
            db.add(other_domain)
            db.commit()
            other_track_id, other_domain_id = other_track.id, other_domain.id

        _add_state(
            TestingSession, user_id=user_id, track_id=other_track_id, domain_id=other_domain_id,
            state="ready", practice=50, scenario=20, distinct=20,
            practice_band="strong", scenario_band="strong", most_recent_at=NOW,
        )
        r = client.get("/me/tracks/CCAO-F/readiness")
        body = r.json()
        assert {d["domain_code"] for d in body["domains"]} == {"PTE", "OEV", "WISD"}
        assert body["overall_readiness_state"] != "ready"


# --- Unknown track / auth-boundary analog ----------------------------------------------


class TestUnknownTrack:
    def test_nonexistent_track_code_returns_404(self, readiness_env):
        client, TestingSession, track_id, domain_ids, user_id = readiness_env
        r = client.get("/me/tracks/NOPE/readiness")
        assert r.status_code == 404


class TestDevUserMissing:
    """This codebase has no real per-request authentication anywhere -- every
    router resolves the same shared dev user. The closest existing analog to an
    'unauthenticated' failure mode is the dev user row itself being absent, which
    every other router (exams.py, scenarios.py) already surfaces as a 500 with a
    stated remediation message; this router inherits that identical behavior."""

    def test_missing_dev_user_returns_500(self, readiness_env):
        client, TestingSession, track_id, domain_ids, user_id = readiness_env
        from app.models import User

        with TestingSession() as db:
            db.query(User).delete()
            db.commit()
        r = client.get("/me/tracks/CCAO-F/readiness")
        assert r.status_code == 500


# --- Read-only guarantee ----------------------------------------------------------------


class TestReadOnlyGuarantee:
    def test_evidence_and_projection_tables_unchanged_after_request(self, readiness_env):
        client, TestingSession, track_id, domain_ids, user_id = readiness_env
        _add_state(
            TestingSession, user_id=user_id, track_id=track_id, domain_id=domain_ids["PTE"],
            state="insufficient_evidence", reasons=["NO_EVIDENCE"],
            practice=0, scenario=0,
        )
        from app.models import LearnerDomainState, Track, Domain

        with TestingSession() as db:
            db.expire_all()
            before = {
                model.__name__: snapshot_table(db, model)
                for model in (Track, Domain, LearnerDomainState)
            }

        client.get("/me/tracks/CCAO-F/readiness")
        client.get("/me/tracks/CCAO-F/readiness")  # twice, to rule out a lazy-write on first hit

        with TestingSession() as db:
            db.expire_all()
            after = {
                model.__name__: snapshot_table(db, model)
                for model in (Track, Domain, LearnerDomainState)
            }
        assert after == before


# --- No readiness percentage / prediction ------------------------------------------------


class TestNoReadinessPercentageOrPrediction:
    def test_response_never_contains_a_score_or_probability_field(self, readiness_env):
        client, TestingSession, track_id, domain_ids, user_id = readiness_env
        _add_state(
            TestingSession, user_id=user_id, track_id=track_id, domain_id=domain_ids["PTE"],
            state="ready", practice=5, scenario=2, distinct=2,
            practice_band="strong", scenario_band="strong", most_recent_at=NOW,
        )
        r = client.get("/me/tracks/CCAO-F/readiness")
        raw = r.text.lower()
        for forbidden in ("percent", "probability", "confidence", "predicted", "chance"):
            assert forbidden not in raw

    def test_readiness_states_remain_the_four_bounded_values(self, readiness_env):
        client, TestingSession, track_id, domain_ids, user_id = readiness_env
        r = client.get("/me/tracks/CCAO-F/readiness")
        body = r.json()
        allowed = {"insufficient_evidence", "developing", "approaching_ready", "ready"}
        assert body["overall_readiness_state"] in allowed
        for d in body["domains"]:
            assert d["readiness_state"] in allowed


# --- No external providers -----------------------------------------------------------


class TestNoProviderDependency:
    def test_readiness_router_module_has_no_forbidden_import(self):
        import ast
        import inspect

        from app.routers import readiness as module

        tree = ast.parse(inspect.getsource(module))
        imported_names = {
            alias.name.lower()
            for node in ast.walk(tree)
            if isinstance(node, (ast.Import, ast.ImportFrom))
            for alias in node.names
        } | {
            node.module.lower()
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module
        }
        for forbidden in ("anthropic", "zia", "mcp"):
            assert not any(forbidden in name for name in imported_names), (
                f"readiness router must not import anything referencing {forbidden!r}"
            )
