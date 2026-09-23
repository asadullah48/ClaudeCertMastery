"""KSOR Slice 7: live projection recompute wiring integration tests.

Exercises the REAL exam-submission and Scenario Lab HTTP endpoints (not the
readiness service directly -- that is already covered by test_learner_readiness.py)
to prove: evidence commits before projection refresh, a projection failure never
turns a successful submission into an error response, evidence is never mutated by
a projection failure, multi-domain recompute is attempted independently per domain,
and recompute never runs for evidence that itself failed to commit.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def patch_recompute_to_raise():
    import app.services.readiness_integration as integration_module

    def raiser(db, *, user_id, track_id, domain_id):
        raise RuntimeError("synthetic projection failure")

    return patch.object(integration_module, "recompute_learner_domain_state", raiser)


# --- Exam fixture: hand-built 3-domain in-progress attempt, real submit() path ------


@pytest.fixture
def exam_env(tmp_path):
    db_path = tmp_path / "readiness_integration_exam.db"
    engine = create_engine(f"sqlite:///{db_path}", connect_args={"check_same_thread": False})
    TestingSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    from app import models as _models  # noqa: F401
    from app.database import Base, get_db
    from app.main import app
    from app.models import (
        AnswerOption, AttemptItem, AttemptMode, AttemptStatus, Domain,
        ExamAttempt, Question, QuestionType, Track, User,
    )

    Base.metadata.create_all(engine)
    with TestingSession() as db:
        track = Track(code="CCAO-F", name="AI Operator, Foundation", item_count=3)
        db.add(track)
        db.flush()
        domains = {
            "PTE": Domain(track_id=track.id, code="PTE", name="Prompting", weight_bps=3400, position=1),
            "OEV": Domain(track_id=track.id, code="OEV", name="Output Eval", weight_bps=3300, position=2),
            "WISD": Domain(track_id=track.id, code="WISD", name="Workflow", weight_bps=3300, position=3),
        }
        db.add_all(domains.values())
        db.flush()
        user = User(email="dev@certmastery.local", display_name="Dev")
        db.add(user)
        db.flush()

        attempt = ExamAttempt(
            user_id=user.id, track_id=track.id, mode=AttemptMode.EXAM,
            status=AttemptStatus.IN_PROGRESS, seed=1,
        )
        db.add(attempt)
        db.flush()

        answer_ids = {}
        for pos, code in enumerate(("PTE", "OEV", "WISD"), start=1):
            q = Question(
                domain_id=domains[code].id, external_id=f"Q-{code}", stem="s",
                question_type=QuestionType.MCQ, difficulty=2, static_explanation="e",
                is_active=True,
            )
            db.add(q)
            db.flush()
            correct = AnswerOption(question_id=q.id, label="A", text="right", is_correct=True, position=1)
            wrong = AnswerOption(question_id=q.id, label="B", text="wrong", is_correct=False, position=2)
            db.add_all([correct, wrong])
            db.flush()
            item = AttemptItem(
                attempt_id=attempt.id, question_id=q.id, domain_id=domains[code].id,
                position=pos, selected_option_ids=[],
            )
            db.add(item)
            db.flush()
            answer_ids[code] = {"correct": correct.id, "wrong": wrong.id, "question_id": q.id}

        db.commit()
        ids = {
            "attempt_id": attempt.id, "user_id": user.id, "track_id": track.id,
            "domain_ids": {c: d.id for c, d in domains.items()},
            "answer_ids": answer_ids,
        }

    def override_get_db():
        db = TestingSession()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as client:
        yield client, TestingSession, ids
    app.dependency_overrides.clear()


def _exam_submit_payload(ids, correct_codes=None, wrong_codes=None):
    """By default every domain's item is answered correctly. Pass `wrong_codes` to
    deliberately miss specific domains."""
    wrong_codes = wrong_codes or set()
    answers = []
    for code, a in ids["answer_ids"].items():
        chosen = a["wrong"] if code in wrong_codes else a["correct"]
        answers.append({"question_id": a["question_id"], "selected_option_ids": [chosen]})
    return answers


def snapshot_table(db, model) -> list[tuple]:
    cols = [c.name for c in model.__table__.columns]
    rows = db.scalars(select(model)).all()
    return sorted(tuple(getattr(row, c) for c in cols) for row in rows)


# --- Scenario fixture: minimal 1-step scenario, real answer_step() path -------------


@pytest.fixture
def scenario_env(tmp_path):
    db_path = tmp_path / "readiness_integration_scenario.db"
    engine = create_engine(f"sqlite:///{db_path}", connect_args={"check_same_thread": False})
    TestingSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    from app.database import Base, get_db
    from app.main import app
    from app.models import Domain, Scenario, ScenarioStep, ScenarioStepOption, Track, User

    Base.metadata.create_all(engine)
    with TestingSession() as db:
        track = Track(code="CCAO-F", name="AI Operator, Foundation")
        db.add(track)
        db.flush()
        domain = Domain(track_id=track.id, code="WISD", name="Workflow", weight_bps=1600, position=1)
        db.add(domain)
        db.flush()
        user = User(email="dev@certmastery.local", display_name="Dev")
        db.add(user)

        scenario = Scenario(
            domain_id=domain.id, external_id="CCAO-F-WISD-SCN-001", title="t",
            setup_text="s", difficulty=2, is_active=True, content_version=1,
        )
        db.add(scenario)
        db.flush()
        step = ScenarioStep(scenario_id=scenario.id, position=1, prompt_text="p", step_type="mcq")
        db.add(step)
        db.flush()
        opt_a = ScenarioStepOption(step_id=step.id, label="A", text="right", is_correct=True, position=1, rationale="r")
        opt_b = ScenarioStepOption(step_id=step.id, label="B", text="wrong", is_correct=False, position=2, rationale="r")
        db.add_all([opt_a, opt_b])
        db.commit()
        ids = {
            "user_id": user.id, "track_id": track.id, "domain_id": domain.id,
            "opt_a": opt_a.id, "opt_b": opt_b.id,
        }

    def override_get_db():
        db = TestingSession()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as client:
        yield client, TestingSession, ids
    app.dependency_overrides.clear()


# --- Exam success regression --------------------------------------------------------


class TestExamSuccessRegression:
    def test_submission_refreshes_every_affected_domain(self, exam_env):
        client, TestingSession, ids = exam_env
        r = client.post(
            f"/attempts/{ids['attempt_id']}/submit",
            json={"answers": _exam_submit_payload(ids)},
        )
        assert r.status_code == 200
        assert r.json()["passed"] is True

        from app.models import LearnerDomainState

        with TestingSession() as db:
            rows = {
                row.domain_id: row
                for row in db.scalars(
                    select(LearnerDomainState).where(LearnerDomainState.user_id == ids["user_id"])
                ).all()
            }
        assert set(rows) == set(ids["domain_ids"].values())
        for domain_id in ids["domain_ids"].values():
            assert rows[domain_id].practice_evidence_count == 1
            assert rows[domain_id].scenario_evidence_count == 0


# --- Scenario success regression -----------------------------------------------------


class TestScenarioSuccessRegression:
    def test_completion_refreshes_the_affected_domain(self, scenario_env):
        client, TestingSession, ids = scenario_env
        start = client.post("/scenarios/CCAO-F-WISD-SCN-001/start")
        attempt_id = start.json()["attempt_id"]
        r = client.post(
            f"/scenario-attempts/{attempt_id}/steps/1/answer",
            json={"selected_option_ids": [ids["opt_a"]]},
        )
        assert r.status_code == 200
        assert r.json()["attempt_status"] == "submitted"

        from app.models import LearnerDomainState

        with TestingSession() as db:
            row = db.scalar(
                select(LearnerDomainState).where(
                    LearnerDomainState.user_id == ids["user_id"],
                    LearnerDomainState.domain_id == ids["domain_id"],
                )
            )
        assert row is not None
        assert row.scenario_evidence_count == 1
        assert row.practice_evidence_count == 0


class TestScenarioRecomputeOnlyOnCompletion:
    def test_non_final_step_does_not_trigger_recompute(self, tmp_path, monkeypatch):
        """A two-step scenario: answering step 1 (not the last step) must not call
        the readiness integration boundary at all."""
        import app.routers.scenarios as scenarios_module

        spy = MagicMock(return_value=True)
        monkeypatch.setattr(scenarios_module, "best_effort_recompute_learner_domain_state", spy)

        db_path = tmp_path / "two_step.db"
        engine = create_engine(f"sqlite:///{db_path}", connect_args={"check_same_thread": False})
        TestingSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)

        from app.database import Base, get_db
        from app.main import app
        from app.models import Domain, Scenario, ScenarioStep, ScenarioStepOption, Track, User

        Base.metadata.create_all(engine)
        with TestingSession() as db:
            track = Track(code="CCAO-F", name="t")
            db.add(track)
            db.flush()
            domain = Domain(track_id=track.id, code="WISD", name="d", weight_bps=1600, position=1)
            db.add(domain)
            db.flush()
            user = User(email="dev@certmastery.local", display_name="Dev")
            db.add(user)
            scenario = Scenario(
                domain_id=domain.id, external_id="CCAO-F-WISD-SCN-002", title="t",
                setup_text="s", difficulty=2, is_active=True, content_version=1,
            )
            db.add(scenario)
            db.flush()
            step1 = ScenarioStep(scenario_id=scenario.id, position=1, prompt_text="p", step_type="mcq")
            step2 = ScenarioStep(scenario_id=scenario.id, position=2, prompt_text="p2", step_type="mcq")
            db.add_all([step1, step2])
            db.flush()
            s1a = ScenarioStepOption(step_id=step1.id, label="A", text="r", is_correct=True, position=1, rationale="r")
            s1b = ScenarioStepOption(step_id=step1.id, label="B", text="w", is_correct=False, position=2, rationale="r")
            s2a = ScenarioStepOption(step_id=step2.id, label="A", text="r", is_correct=True, position=1, rationale="r")
            s2b = ScenarioStepOption(step_id=step2.id, label="B", text="w", is_correct=False, position=2, rationale="r")
            db.add_all([s1a, s1b, s2a, s2b])
            db.commit()
            opt1a_id = s1a.id

        def override_get_db():
            db = TestingSession()
            try:
                yield db
            finally:
                db.close()

        app.dependency_overrides[get_db] = override_get_db
        try:
            with TestClient(app) as client:
                start = client.post("/scenarios/CCAO-F-WISD-SCN-002/start")
                attempt_id = start.json()["attempt_id"]
                r = client.post(
                    f"/scenario-attempts/{attempt_id}/steps/1/answer",
                    json={"selected_option_ids": [opt1a_id]},
                )
                assert r.status_code == 200
                assert r.json()["attempt_status"] == "in_progress"
                assert spy.call_count == 0
        finally:
            app.dependency_overrides.clear()


# --- Projection-failure containment ---------------------------------------------------


class TestExamProjectionFailureRegression:
    def test_submission_still_succeeds_and_evidence_is_intact(self, exam_env, caplog):
        client, TestingSession, ids = exam_env
        from app.models import AttemptDomainScore, AttemptItem, ExamAttempt

        with patch_recompute_to_raise():
            with caplog.at_level(logging.WARNING):
                r = client.post(
                    f"/attempts/{ids['attempt_id']}/submit",
                    json={"answers": _exam_submit_payload(ids)},
                )
        assert r.status_code == 200
        assert r.json()["passed"] is True
        assert any("Readiness projection refresh failed" in m for m in caplog.messages)

        with TestingSession() as db:
            db.expire_all()
            attempt = db.get(ExamAttempt, ids["attempt_id"])
            assert attempt.status == "submitted"
            items = db.scalars(
                select(AttemptItem).where(AttemptItem.attempt_id == ids["attempt_id"])
            ).all()
            assert len(items) == 3
            assert all(i.is_correct is not None for i in items)
            scores = db.scalars(
                select(AttemptDomainScore).where(AttemptDomainScore.attempt_id == ids["attempt_id"])
            ).all()
            assert len(scores) == 3

        from app.models import LearnerDomainState

        with TestingSession() as db:
            rows = db.scalars(select(LearnerDomainState)).all()
            assert rows == []  # recompute failed for every domain -- nothing persisted

    def test_session_remains_usable_after_projection_failure(self, exam_env):
        client, TestingSession, ids = exam_env
        with patch_recompute_to_raise():
            client.post(
                f"/attempts/{ids['attempt_id']}/submit",
                json={"answers": _exam_submit_payload(ids)},
            )
        # A completely unrelated subsequent request against the same session
        # machinery must succeed -- proves get_db()'s session pool isn't wedged.
        r = client.get(f"/attempts/{ids['attempt_id']}")
        assert r.status_code == 200


class TestScenarioProjectionFailureRegression:
    def test_completion_still_succeeds_and_evidence_is_intact(self, scenario_env, caplog):
        client, TestingSession, ids = scenario_env
        from app.models import ScenarioAttempt, ScenarioEvent, ScenarioStepAttempt

        with patch_recompute_to_raise():
            with caplog.at_level(logging.WARNING):
                start = client.post("/scenarios/CCAO-F-WISD-SCN-001/start")
                attempt_id = start.json()["attempt_id"]
                r = client.post(
                    f"/scenario-attempts/{attempt_id}/steps/1/answer",
                    json={"selected_option_ids": [ids["opt_a"]]},
                )
        assert r.status_code == 200
        assert r.json()["attempt_status"] == "submitted"
        assert any("Readiness projection refresh failed" in m for m in caplog.messages)

        with TestingSession() as db:
            db.expire_all()
            attempt = db.get(ScenarioAttempt, attempt_id)
            assert attempt.status == "submitted"
            assert attempt.score_pct is not None
            step_attempts = db.scalars(
                select(ScenarioStepAttempt).where(ScenarioStepAttempt.scenario_attempt_id == attempt_id)
            ).all()
            assert len(step_attempts) == 1
            events = db.scalars(
                select(ScenarioEvent).where(ScenarioEvent.scenario_attempt_id == attempt_id)
            ).all()
            assert len(events) == 3  # scenario_started + step_answered + scenario_submitted

        from app.models import LearnerDomainState

        with TestingSession() as db:
            assert db.scalars(select(LearnerDomainState)).all() == []


# --- Multi-domain partial-failure ----------------------------------------------------


class TestExamMultiDomainPartialFailure:
    def test_one_domain_failing_does_not_block_the_others(self, exam_env):
        client, TestingSession, ids = exam_env
        failing_domain_id = ids["domain_ids"]["OEV"]

        import app.services.readiness_integration as integration_module

        real = integration_module.recompute_learner_domain_state

        def flaky(db, *, user_id, track_id, domain_id):
            if domain_id == failing_domain_id:
                raise RuntimeError("synthetic projection failure for OEV")
            return real(db, user_id=user_id, track_id=track_id, domain_id=domain_id)

        with patch.object(integration_module, "recompute_learner_domain_state", flaky):
            r = client.post(
                f"/attempts/{ids['attempt_id']}/submit",
                json={"answers": _exam_submit_payload(ids)},
            )
        assert r.status_code == 200

        from app.models import LearnerDomainState

        with TestingSession() as db:
            rows = {row.domain_id: row for row in db.scalars(select(LearnerDomainState)).all()}
        # A (PTE) and C (WISD) attempted and succeeded; B (OEV) never persisted.
        assert ids["domain_ids"]["PTE"] in rows
        assert ids["domain_ids"]["WISD"] in rows
        assert failing_domain_id not in rows

    def test_evidence_for_all_three_domains_remains_authoritative(self, exam_env):
        client, TestingSession, ids = exam_env
        import app.services.readiness_integration as integration_module

        def always_fails(db, *, user_id, track_id, domain_id):
            raise RuntimeError("synthetic")

        from app.models import AttemptDomainScore, AttemptItem

        with patch.object(integration_module, "recompute_learner_domain_state", always_fails):
            r = client.post(
                f"/attempts/{ids['attempt_id']}/submit",
                json={"answers": _exam_submit_payload(ids, wrong_codes={"OEV"})},
            )
        assert r.status_code == 200

        with TestingSession() as db:
            db.expire_all()
            items = db.scalars(
                select(AttemptItem).where(AttemptItem.attempt_id == ids["attempt_id"])
            ).all()
            scores = db.scalars(
                select(AttemptDomainScore).where(AttemptDomainScore.attempt_id == ids["attempt_id"])
            ).all()
        # Evidence written by THIS submission must be present and graded correctly
        # (OEV answered wrong) regardless of every domain's projection failing.
        assert len(items) == 3
        assert len(scores) == 3
        oev_score = next(s for s in scores if s.domain_id == ids["domain_ids"]["OEV"])
        assert oev_score.correct == 0


# --- Evidence-submission failure must not recompute -----------------------------------


class TestEvidenceFailureNoRecompute:
    def test_already_submitted_attempt_does_not_trigger_recompute(self, exam_env, monkeypatch):
        client, TestingSession, ids = exam_env
        client.post(
            f"/attempts/{ids['attempt_id']}/submit",
            json={"answers": _exam_submit_payload(ids)},
        )
        import app.routers.attempts as attempts_module

        spy = MagicMock(return_value=True)
        monkeypatch.setattr(attempts_module, "best_effort_recompute_learner_domain_state", spy)

        r = client.post(
            f"/attempts/{ids['attempt_id']}/submit",
            json={"answers": _exam_submit_payload(ids)},
        )
        assert r.status_code == 409
        assert spy.call_count == 0

    def test_unknown_attempt_does_not_trigger_recompute(self, exam_env, monkeypatch):
        client, TestingSession, ids = exam_env
        import app.routers.attempts as attempts_module

        spy = MagicMock(return_value=True)
        monkeypatch.setattr(attempts_module, "best_effort_recompute_learner_domain_state", spy)

        r = client.post("/attempts/999999/submit", json={"answers": []})
        assert r.status_code == 404
        assert spy.call_count == 0


# --- Recompute call-count / ordering ---------------------------------------------------


class TestRecomputeCallCountAndOrdering:
    def test_exactly_once_per_distinct_affected_domain_in_position_order(self, exam_env, monkeypatch):
        client, TestingSession, ids = exam_env
        import app.routers.attempts as attempts_module

        spy = MagicMock(return_value=True)
        monkeypatch.setattr(attempts_module, "best_effort_recompute_learner_domain_state", spy)

        client.post(
            f"/attempts/{ids['attempt_id']}/submit",
            json={"answers": _exam_submit_payload(ids)},
        )
        assert spy.call_count == 3
        called_domain_ids = [c.kwargs["domain_id"] for c in spy.call_args_list]
        expected_order = [ids["domain_ids"]["PTE"], ids["domain_ids"]["OEV"], ids["domain_ids"]["WISD"]]
        assert called_domain_ids == expected_order  # Domain.position ascending

    def test_scenario_calls_recompute_exactly_once(self, scenario_env, monkeypatch):
        client, TestingSession, ids = scenario_env
        import app.routers.scenarios as scenarios_module

        spy = MagicMock(return_value=True)
        monkeypatch.setattr(scenarios_module, "best_effort_recompute_learner_domain_state", spy)

        start = client.post("/scenarios/CCAO-F-WISD-SCN-001/start")
        attempt_id = start.json()["attempt_id"]
        client.post(
            f"/scenario-attempts/{attempt_id}/steps/1/answer",
            json={"selected_option_ids": [ids["opt_a"]]},
        )
        assert spy.call_count == 1
        assert spy.call_args.kwargs["domain_id"] == ids["domain_id"]


# --- Production-shaped synthetic regression ---------------------------------------------


class TestProductionShapedSyntheticRegression:
    def test_new_submission_refreshes_only_from_complete_evidence_history(self, scenario_env):
        """Pre-seed a ScenarioAttempt shaped exactly like real production's
        ScenarioAttempt #1 (submitted, strong, score 100) directly in the DB --
        mirroring an already-existing attempt the router itself never created --
        then perform a NEW real submission through the actual endpoint and prove
        the projection reflects the FULL evidence history (both attempts), with no
        `attempt_id == 1` special-casing anywhere in the wiring."""
        client, TestingSession, ids = scenario_env
        from app.models import ScenarioAttempt

        with TestingSession() as db:
            preexisting = ScenarioAttempt(
                user_id=ids["user_id"], scenario_id=1, status="submitted",
                scenario_content_version=1, score_pct=100.0, mastery_band="strong",
            )
            # scenario_id=1 is the only scenario created by this fixture.
            db.add(preexisting)
            db.commit()
            assert preexisting.id == 1  # confirms this mirrors "attempt #1" shape

        r = client.post("/scenarios/CCAO-F-WISD-SCN-001/start")
        attempt_id = r.json()["attempt_id"]
        assert attempt_id != 1  # the NEW attempt, not the pre-seeded one
        r = client.post(
            f"/scenario-attempts/{attempt_id}/steps/1/answer",
            json={"selected_option_ids": [ids["opt_a"]]},
        )
        assert r.status_code == 200

        from app.models import LearnerDomainState

        with TestingSession() as db:
            row = db.scalar(
                select(LearnerDomainState).where(
                    LearnerDomainState.user_id == ids["user_id"],
                    LearnerDomainState.domain_id == ids["domain_id"],
                )
            )
        # Both the pre-seeded attempt and the new one are eligible submitted
        # evidence -- the projection must count both, not just the newest.
        assert row.scenario_evidence_count == 2
