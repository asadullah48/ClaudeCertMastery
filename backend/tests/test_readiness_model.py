"""KSOR Slice 1: storage-contract tests for the learner_domain_states projection.

Gate C3, Slice 1 -- proves the schema/model only: creation, safe defaults,
uniqueness, evidence independence, rebuildability, FK integrity, and bounded state
values. No classifier/recompute/recommender/API logic exists yet to test -- that is
Slice 2 onward, per docs/GATE-C3-KSOR-READINESS-IMPLEMENTATION-PLAN.md.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine, event, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.database import Base  # noqa: E402
from app.models import (  # noqa: E402
    AnswerOption,
    AttemptItem,
    AttemptMode,
    AttemptStatus,
    Domain,
    ExamAttempt,
    LearnerDomainState,
    Question,
    QuestionType,
    ReadinessState,
    Scenario,
    ScenarioAttempt,
    ScenarioEvent,
    ScenarioStep,
    ScenarioStepAttempt,
    ScenarioStepOption,
    Track,
    User,
)


@pytest.fixture
def db_session(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'readiness.db'}")

    # SQLite does not enforce FKs by default; this specific test file needs real FK
    # enforcement to prove TestForeignKeyIntegrity below (the plan's own instruction:
    # "where the test database supports FK enforcement").
    @event.listens_for(engine, "connect")
    def _enable_fk(dbapi_connection, _record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    TestingSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    Base.metadata.create_all(engine)
    session = TestingSession()
    yield session
    session.close()


def make_track_domain_user(db):
    track = Track(
        code="CCAO-F", name="t", item_count=60, duration_minutes=120,
        pass_scaled_score=720, pass_raw_threshold=0.70, price_usd=99.0,
        validity_months=12, is_seeded=True,
    )
    db.add(track)
    db.flush()
    domain = Domain(
        track_id=track.id, code="WISD", name="d", description="",
        weight_bps=1000, position=1,
    )
    db.add(domain)
    db.flush()
    user = User(email="learner@example.com", display_name="Learner")
    db.add(user)
    db.flush()
    db.commit()
    return track, domain, user


class TestSchemaAndDefaults:
    def test_row_can_be_created_for_user_track_domain(self, db_session):
        track, domain, user = make_track_domain_user(db_session)
        row = LearnerDomainState(user_id=user.id, track_id=track.id, domain_id=domain.id)
        db_session.add(row)
        db_session.commit()
        assert row.id is not None

    def test_default_readiness_state_is_insufficient_evidence(self, db_session):
        track, domain, user = make_track_domain_user(db_session)
        row = LearnerDomainState(user_id=user.id, track_id=track.id, domain_id=domain.id)
        db_session.add(row)
        db_session.commit()
        assert row.readiness_state == ReadinessState.INSUFFICIENT_EVIDENCE.value

    def test_default_reason_codes_is_empty_list(self, db_session):
        track, domain, user = make_track_domain_user(db_session)
        row = LearnerDomainState(user_id=user.id, track_id=track.id, domain_id=domain.id)
        db_session.add(row)
        db_session.commit()
        assert row.reason_codes == []

    def test_default_evidence_counts_are_zero(self, db_session):
        track, domain, user = make_track_domain_user(db_session)
        row = LearnerDomainState(user_id=user.id, track_id=track.id, domain_id=domain.id)
        db_session.add(row)
        db_session.commit()
        assert row.practice_evidence_count == 0
        assert row.scenario_evidence_count == 0
        assert row.distinct_scenario_content_versions == 0
        assert row.unresolved_misconception_count == 0

    def test_nullable_fields_default_to_none(self, db_session):
        track, domain, user = make_track_domain_user(db_session)
        row = LearnerDomainState(user_id=user.id, track_id=track.id, domain_id=domain.id)
        db_session.add(row)
        db_session.commit()
        assert row.recent_practice_mastery_band is None
        assert row.recent_scenario_mastery_band is None
        assert row.most_recent_evidence_at is None


class TestUniqueness:
    def test_duplicate_user_track_domain_rejected(self, db_session):
        track, domain, user = make_track_domain_user(db_session)
        db_session.add(
            LearnerDomainState(user_id=user.id, track_id=track.id, domain_id=domain.id)
        )
        db_session.commit()
        db_session.add(
            LearnerDomainState(user_id=user.id, track_id=track.id, domain_id=domain.id)
        )
        with pytest.raises(IntegrityError):
            db_session.commit()
        db_session.rollback()

    def test_different_domain_same_user_track_allowed(self, db_session):
        track, domain, user = make_track_domain_user(db_session)
        other_domain = Domain(
            track_id=track.id, code="CKM", name="d2", description="",
            weight_bps=1000, position=2,
        )
        db_session.add(other_domain)
        db_session.flush()
        db_session.add(
            LearnerDomainState(user_id=user.id, track_id=track.id, domain_id=domain.id)
        )
        db_session.add(
            LearnerDomainState(user_id=user.id, track_id=track.id, domain_id=other_domain.id)
        )
        db_session.commit()
        assert len(db_session.scalars(select(LearnerDomainState)).all()) == 2

    def test_different_user_same_track_domain_allowed(self, db_session):
        track, domain, user = make_track_domain_user(db_session)
        other_user = User(email="other@example.com", display_name="Other")
        db_session.add(other_user)
        db_session.flush()
        db_session.add(
            LearnerDomainState(user_id=user.id, track_id=track.id, domain_id=domain.id)
        )
        db_session.add(
            LearnerDomainState(user_id=other_user.id, track_id=track.id, domain_id=domain.id)
        )
        db_session.commit()
        assert len(db_session.scalars(select(LearnerDomainState)).all()) == 2


class TestEvidenceIndependence:
    """Proves the projection never mutates the evidence it is derived from, by
    re-reading evidence rows from the database after create+delete, not merely
    trusting the FK/cascade definitions."""

    def test_creating_and_deleting_projection_leaves_exam_evidence_untouched(
        self, db_session
    ):
        track, domain, user = make_track_domain_user(db_session)
        question = Question(
            domain_id=domain.id, external_id="Q1", stem="s",
            question_type=QuestionType.MCQ, difficulty=2, static_explanation="e",
            is_active=True,
        )
        db_session.add(question)
        db_session.flush()
        opt = AnswerOption(
            question_id=question.id, label="A", text="t", is_correct=True, position=1
        )
        db_session.add(opt)
        db_session.flush()
        attempt = ExamAttempt(
            user_id=user.id, track_id=track.id, mode=AttemptMode.EXAM,
            status=AttemptStatus.SUBMITTED, seed=1,
        )
        db_session.add(attempt)
        db_session.flush()
        item = AttemptItem(
            attempt_id=attempt.id, question_id=question.id, domain_id=domain.id,
            position=1, selected_option_ids=[opt.id], is_correct=True,
        )
        db_session.add(item)
        db_session.commit()

        attempt_id, item_id = attempt.id, item.id
        before_attempt = (attempt.status, attempt.seed)
        before_item = (item.is_correct, tuple(item.selected_option_ids))

        row = LearnerDomainState(user_id=user.id, track_id=track.id, domain_id=domain.id)
        db_session.add(row)
        db_session.commit()
        db_session.delete(row)
        db_session.commit()

        db_session.expire_all()
        reloaded_attempt = db_session.get(ExamAttempt, attempt_id)
        reloaded_item = db_session.get(AttemptItem, item_id)
        assert reloaded_attempt is not None
        assert (reloaded_attempt.status, reloaded_attempt.seed) == before_attempt
        assert reloaded_item is not None
        assert (
            reloaded_item.is_correct, tuple(reloaded_item.selected_option_ids)
        ) == before_item

    def test_creating_and_deleting_projection_leaves_scenario_evidence_untouched(
        self, db_session
    ):
        track, domain, user = make_track_domain_user(db_session)
        scenario = Scenario(
            domain_id=domain.id, external_id="S1", title="t", setup_text="s",
            difficulty=2, is_active=True, content_version=1,
        )
        db_session.add(scenario)
        db_session.flush()
        step = ScenarioStep(
            scenario_id=scenario.id, position=1, prompt_text="p", step_type="mcq"
        )
        db_session.add(step)
        db_session.flush()
        opt = ScenarioStepOption(
            step_id=step.id, label="A", text="t", is_correct=True, position=1,
            rationale="r",
        )
        db_session.add(opt)
        db_session.flush()
        s_attempt = ScenarioAttempt(
            user_id=user.id, scenario_id=scenario.id, status="submitted",
            scenario_content_version=1, score_pct=100.0, mastery_band="strong",
        )
        db_session.add(s_attempt)
        db_session.flush()
        step_attempt = ScenarioStepAttempt(
            scenario_attempt_id=s_attempt.id, step_id=step.id, position=1,
            selected_option_ids=[opt.id], is_correct=True, step_credit=1.0,
        )
        db_session.add(step_attempt)
        event_row = ScenarioEvent(
            scenario_attempt_id=s_attempt.id, user_id=user.id,
            event_type="scenario_submitted", payload={"score_pct": 100},
        )
        db_session.add(event_row)
        db_session.commit()

        s_attempt_id, step_attempt_id, event_id = s_attempt.id, step_attempt.id, event_row.id
        before_attempt = (s_attempt.status, s_attempt.score_pct)
        before_step_attempt = (
            step_attempt.is_correct, tuple(step_attempt.selected_option_ids)
        )
        before_event = (event_row.event_type, event_row.payload)

        row = LearnerDomainState(user_id=user.id, track_id=track.id, domain_id=domain.id)
        db_session.add(row)
        db_session.commit()
        db_session.delete(row)
        db_session.commit()

        db_session.expire_all()
        reloaded_attempt = db_session.get(ScenarioAttempt, s_attempt_id)
        reloaded_step_attempt = db_session.get(ScenarioStepAttempt, step_attempt_id)
        reloaded_event = db_session.get(ScenarioEvent, event_id)
        assert (reloaded_attempt.status, reloaded_attempt.score_pct) == before_attempt
        assert (
            reloaded_step_attempt.is_correct,
            tuple(reloaded_step_attempt.selected_option_ids),
        ) == before_step_attempt
        assert (reloaded_event.event_type, reloaded_event.payload) == before_event


class TestRebuildability:
    def test_deleting_projection_row_succeeds_and_row_is_gone(self, db_session):
        track, domain, user = make_track_domain_user(db_session)
        row = LearnerDomainState(user_id=user.id, track_id=track.id, domain_id=domain.id)
        db_session.add(row)
        db_session.commit()
        row_id = row.id
        db_session.delete(row)
        db_session.commit()
        assert db_session.get(LearnerDomainState, row_id) is None

    def test_deleted_projection_can_be_recreated_for_the_same_identity(self, db_session):
        track, domain, user = make_track_domain_user(db_session)
        row = LearnerDomainState(user_id=user.id, track_id=track.id, domain_id=domain.id)
        db_session.add(row)
        db_session.commit()
        db_session.delete(row)
        db_session.commit()

        rebuilt = LearnerDomainState(user_id=user.id, track_id=track.id, domain_id=domain.id)
        db_session.add(rebuilt)
        db_session.commit()
        assert rebuilt.id is not None
        assert rebuilt.readiness_state == ReadinessState.INSUFFICIENT_EVIDENCE.value


class TestForeignKeyIntegrity:
    def test_invalid_user_id_rejected(self, db_session):
        track, domain, _user = make_track_domain_user(db_session)
        db_session.add(
            LearnerDomainState(user_id=999999, track_id=track.id, domain_id=domain.id)
        )
        with pytest.raises(IntegrityError):
            db_session.commit()
        db_session.rollback()

    def test_invalid_track_id_rejected(self, db_session):
        _track, domain, user = make_track_domain_user(db_session)
        db_session.add(
            LearnerDomainState(user_id=user.id, track_id=999999, domain_id=domain.id)
        )
        with pytest.raises(IntegrityError):
            db_session.commit()
        db_session.rollback()

    def test_invalid_domain_id_rejected(self, db_session):
        track, _domain, user = make_track_domain_user(db_session)
        db_session.add(
            LearnerDomainState(user_id=user.id, track_id=track.id, domain_id=999999)
        )
        with pytest.raises(IntegrityError):
            db_session.commit()
        db_session.rollback()


class TestStateBounds:
    """The chosen storage representation is a plain String(24) column -- matching
    every other enum-backed string column already in this codebase
    (ScenarioAttempt.status, AttemptMode, AttemptStatus), none of which carry a
    database-level CHECK constraint either. Bounds are therefore enforced at the
    Python ReadinessState enum, not the database column -- proven here, not silently
    assumed."""

    def test_readiness_state_enum_rejects_unknown_value(self):
        with pytest.raises(ValueError):
            ReadinessState("not_a_real_state")

    def test_readiness_state_enum_has_exactly_the_four_approved_values(self):
        assert {s.value for s in ReadinessState} == {
            "insufficient_evidence", "developing", "approaching_ready", "ready",
        }


class TestNoProviderDependency:
    def test_readiness_model_module_has_no_ai_or_mcp_import(self):
        # Scoped to actual import statements, not the whole module source -- the
        # module's own docstring explains this boundary in prose, which would
        # false-positive a whole-file substring scan (the exact mistake caught and
        # fixed earlier this session for seed.py's own no-provider-dependency test).
        import ast
        import inspect

        from app.models import readiness as readiness_module

        tree = ast.parse(inspect.getsource(readiness_module))
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
                f"readiness.py must not import anything referencing {forbidden!r}"
            )
