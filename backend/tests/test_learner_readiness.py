"""KSOR Slice 3: evidence aggregation + projection recompute tests.

Builds real ExamAttempt/AttemptItem/AttemptDomainScore and ScenarioAttempt rows
directly against a throwaway SQLite database (same fixture pattern as
test_readiness_model.py), then proves the Slice 3 service (build_domain_evidence_summary
/ recompute_learner_domain_state) aggregates them correctly, is idempotent and
replayable, isolates users/tracks/domains, and never mutates the evidence it reads.
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.database import Base  # noqa: E402
from app.models import (  # noqa: E402
    AnswerOption,
    AttemptDomainScore,
    AttemptItem,
    AttemptMode,
    AttemptStatus,
    Domain,
    ExamAttempt,
    LearnerDomainState,
    Question,
    QuestionType,
    Scenario,
    ScenarioAttempt,
    Track,
    User,
)
from app.services.learner_readiness import (
    INTERIM_UNRESOLVED_MISCONCEPTION_COUNT,
    PROJECTION_VERSION,
    ReadinessComputationError,
    build_domain_evidence_summary,
    recompute_learner_domain_state,
)
from app.services.readiness_policy import (
    MIN_PRACTICE_ITEMS_FOR_SUFFICIENCY,
    MIN_SCENARIO_ATTEMPTS_FOR_SUFFICIENCY,
    STALENESS_THRESHOLD_DAYS,
    STATE_APPROACHING_READY,
    STATE_INSUFFICIENT_EVIDENCE,
    STATE_READY,
)
from app.services.scoring import MasteryBand

NOW = datetime(2026, 1, 15, 12, 0, 0, tzinfo=timezone.utc)


@pytest.fixture
def db_session(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'learner_readiness.db'}")
    # expire_on_commit=False: SQLite does not round-trip tzinfo on DateTime(timezone=True)
    # columns, so a post-commit reload would silently strip it back off. The default
    # session behavior would otherwise expire every attribute on commit and reload it
    # naive on next access -- a SQLite-only artifact (production runs Postgres, which
    # preserves tzinfo), not something the service itself needs to guard against.
    TestingSession = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)
    Base.metadata.create_all(engine)
    session = TestingSession()
    yield session
    session.close()


# --- fixture builders ---------------------------------------------------------------


def make_track(db, code="CCAO-F"):
    track = Track(
        code=code, name="t", item_count=60, duration_minutes=120,
        pass_scaled_score=720, pass_raw_threshold=0.70, price_usd=99.0,
        validity_months=12, is_seeded=True,
    )
    db.add(track)
    db.flush()
    return track


def make_domain(db, track, code, position=1):
    domain = Domain(
        track_id=track.id, code=code, name=code, description="",
        weight_bps=1000, position=position,
    )
    db.add(domain)
    db.flush()
    return domain


def make_user(db, email="learner@example.com"):
    user = User(email=email, display_name="Learner")
    db.add(user)
    db.flush()
    return user


def make_scenario(db, *, domain, external_id, content_version=1):
    scenario = Scenario(
        domain_id=domain.id, external_id=external_id, title="t", setup_text="s",
        difficulty=2, is_active=True, content_version=content_version,
    )
    db.add(scenario)
    db.flush()
    return scenario


def add_exam_attempt_evidence(
    db, *, user, track, domain, item_count, correct_count,
    mastery_band=None, submitted_at=NOW, status=AttemptStatus.SUBMITTED,
):
    """Directly persists an ExamAttempt + its AttemptItems (+ AttemptDomainScore if
    submitted) -- bypasses the scoring engine and the router entirely, since this
    file tests evidence AGGREGATION, not grading (already covered by
    test_scoring.py/test_grading.py)."""
    attempt = ExamAttempt(
        user_id=user.id, track_id=track.id, mode=AttemptMode.PRACTICE,
        status=status, seed=1,
        submitted_at=submitted_at if status == AttemptStatus.SUBMITTED else None,
    )
    db.add(attempt)
    db.flush()
    for i in range(item_count):
        q = Question(
            domain_id=domain.id, external_id=f"Q-{attempt.id}-{i}", stem="s",
            question_type=QuestionType.MCQ, difficulty=2, static_explanation="e",
            is_active=True,
        )
        db.add(q)
        db.flush()
        opt = AnswerOption(question_id=q.id, label="A", text="t", is_correct=True, position=1)
        db.add(opt)
        db.flush()
        is_correct = i < correct_count
        db.add(
            AttemptItem(
                attempt_id=attempt.id, question_id=q.id, domain_id=domain.id,
                position=i + 1, selected_option_ids=[opt.id] if is_correct else [],
                is_correct=is_correct,
            )
        )
    if status == AttemptStatus.SUBMITTED:
        pct = (correct_count / item_count * 100) if item_count else 0.0
        band = mastery_band or MasteryBand.from_percentage(pct)
        db.add(
            AttemptDomainScore(
                attempt_id=attempt.id, domain_id=domain.id,
                correct=correct_count, total=item_count, percentage=pct,
                mastery_band=band.value,
            )
        )
    db.commit()
    return attempt


def add_scenario_attempt_evidence(
    db, *, user, scenario, status="submitted", submitted_at=NOW,
    mastery_band=MasteryBand.STRONG, score_pct=95.0,
):
    attempt = ScenarioAttempt(
        user_id=user.id, scenario_id=scenario.id, status=status,
        scenario_content_version=scenario.content_version,
        submitted_at=submitted_at if status == "submitted" else None,
        score_pct=score_pct if status == "submitted" else None,
        mastery_band=mastery_band.value if (status == "submitted" and mastery_band) else None,
    )
    db.add(attempt)
    db.commit()
    return attempt


def make_ready_domain(db, *, user, track, domain, submitted_at=NOW):
    """Full sufficiency: 5 strong practice items, 2 distinct strong scenarios, all
    recent -- the smallest evidence set that should legitimately reach `ready`."""
    add_exam_attempt_evidence(
        db, user=user, track=track, domain=domain,
        item_count=MIN_PRACTICE_ITEMS_FOR_SUFFICIENCY,
        correct_count=MIN_PRACTICE_ITEMS_FOR_SUFFICIENCY,
        mastery_band=MasteryBand.STRONG, submitted_at=submitted_at,
    )
    for i in range(MIN_SCENARIO_ATTEMPTS_FOR_SUFFICIENCY):
        scenario = make_scenario(db, domain=domain, external_id=f"SCN-{domain.code}-{i}")
        add_scenario_attempt_evidence(
            db, user=user, scenario=scenario, submitted_at=submitted_at,
            mastery_band=MasteryBand.STRONG,
        )


# --- Practice evidence aggregation ------------------------------------------------


class TestPracticeEvidenceAggregation:
    def test_counts_every_eligible_attempt_item(self, db_session):
        track = make_track(db_session)
        domain = make_domain(db_session, track, "PTE")
        user = make_user(db_session)
        add_exam_attempt_evidence(db_session, user=user, track=track, domain=domain,
                                   item_count=7, correct_count=4)
        summary = build_domain_evidence_summary(
            db_session, user_id=user.id, track_id=track.id, domain_id=domain.id, now=NOW
        )
        assert summary.practice_evidence_count == 7

    def test_repeated_question_across_two_attempts_counts_independently_no_cap(self, db_session):
        """Plan Section 6: exam evidence gets no anti-inflation cap -- re-answering
        the same underlying content across two sittings is ordinary practice."""
        track = make_track(db_session)
        domain = make_domain(db_session, track, "PTE")
        user = make_user(db_session)
        add_exam_attempt_evidence(db_session, user=user, track=track, domain=domain,
                                   item_count=3, correct_count=3)
        add_exam_attempt_evidence(db_session, user=user, track=track, domain=domain,
                                   item_count=3, correct_count=3)
        summary = build_domain_evidence_summary(
            db_session, user_id=user.id, track_id=track.id, domain_id=domain.id, now=NOW
        )
        assert summary.practice_evidence_count == 6

    def test_in_progress_attempt_contributes_nothing(self, db_session):
        track = make_track(db_session)
        domain = make_domain(db_session, track, "PTE")
        user = make_user(db_session)
        add_exam_attempt_evidence(db_session, user=user, track=track, domain=domain,
                                   item_count=5, correct_count=5, status=AttemptStatus.IN_PROGRESS)
        summary = build_domain_evidence_summary(
            db_session, user_id=user.id, track_id=track.id, domain_id=domain.id, now=NOW
        )
        assert summary.practice_evidence_count == 0
        assert summary.recent_practice_mastery_band is None

    def test_abandoned_attempt_contributes_nothing(self, db_session):
        track = make_track(db_session)
        domain = make_domain(db_session, track, "PTE")
        user = make_user(db_session)
        add_exam_attempt_evidence(db_session, user=user, track=track, domain=domain,
                                   item_count=5, correct_count=5, status=AttemptStatus.ABANDONED)
        summary = build_domain_evidence_summary(
            db_session, user_id=user.id, track_id=track.id, domain_id=domain.id, now=NOW
        )
        assert summary.practice_evidence_count == 0

    def test_recent_practice_mastery_band_is_most_recent_submitted_attempt(self, db_session):
        track = make_track(db_session)
        domain = make_domain(db_session, track, "PTE")
        user = make_user(db_session)
        add_exam_attempt_evidence(
            db_session, user=user, track=track, domain=domain, item_count=5, correct_count=1,
            mastery_band=MasteryBand.CRITICAL, submitted_at=NOW - timedelta(days=10),
        )
        add_exam_attempt_evidence(
            db_session, user=user, track=track, domain=domain, item_count=5, correct_count=5,
            mastery_band=MasteryBand.STRONG, submitted_at=NOW,
        )
        summary = build_domain_evidence_summary(
            db_session, user_id=user.id, track_id=track.id, domain_id=domain.id, now=NOW
        )
        assert summary.recent_practice_mastery_band == MasteryBand.STRONG


# --- Scenario evidence aggregation ------------------------------------------------


class TestScenarioEvidenceAggregation:
    def test_counts_every_eligible_submitted_scenario_attempt(self, db_session):
        track = make_track(db_session)
        domain = make_domain(db_session, track, "PTE")
        user = make_user(db_session)
        scenario = make_scenario(db_session, domain=domain, external_id="SCN-1")
        add_scenario_attempt_evidence(db_session, user=user, scenario=scenario)
        add_scenario_attempt_evidence(db_session, user=user, scenario=scenario)
        summary = build_domain_evidence_summary(
            db_session, user_id=user.id, track_id=track.id, domain_id=domain.id, now=NOW
        )
        assert summary.scenario_evidence_count == 2

    def test_in_progress_scenario_attempt_contributes_nothing(self, db_session):
        track = make_track(db_session)
        domain = make_domain(db_session, track, "PTE")
        user = make_user(db_session)
        scenario = make_scenario(db_session, domain=domain, external_id="SCN-1")
        add_scenario_attempt_evidence(db_session, user=user, scenario=scenario, status="in_progress")
        summary = build_domain_evidence_summary(
            db_session, user_id=user.id, track_id=track.id, domain_id=domain.id, now=NOW
        )
        assert summary.scenario_evidence_count == 0

    def test_isolates_by_domain_via_scenario_domain_id(self, db_session):
        track = make_track(db_session)
        pte = make_domain(db_session, track, "PTE", position=1)
        oev = make_domain(db_session, track, "OEV", position=2)
        user = make_user(db_session)
        pte_scenario = make_scenario(db_session, domain=pte, external_id="SCN-PTE")
        oev_scenario = make_scenario(db_session, domain=oev, external_id="SCN-OEV")
        add_scenario_attempt_evidence(db_session, user=user, scenario=pte_scenario)
        add_scenario_attempt_evidence(db_session, user=user, scenario=oev_scenario)
        summary = build_domain_evidence_summary(
            db_session, user_id=user.id, track_id=track.id, domain_id=pte.id, now=NOW
        )
        assert summary.scenario_evidence_count == 1

    def test_recent_scenario_mastery_band_is_most_recent_submitted_attempt(self, db_session):
        track = make_track(db_session)
        domain = make_domain(db_session, track, "PTE")
        user = make_user(db_session)
        scenario = make_scenario(db_session, domain=domain, external_id="SCN-1")
        add_scenario_attempt_evidence(
            db_session, user=user, scenario=scenario,
            mastery_band=MasteryBand.CRITICAL, submitted_at=NOW - timedelta(days=5),
        )
        add_scenario_attempt_evidence(
            db_session, user=user, scenario=scenario,
            mastery_band=MasteryBand.STRONG, submitted_at=NOW,
        )
        summary = build_domain_evidence_summary(
            db_session, user_id=user.id, track_id=track.id, domain_id=domain.id, now=NOW
        )
        assert summary.recent_scenario_mastery_band == MasteryBand.STRONG


class TestDistinctScenarioContentVersions:
    def test_same_scenario_same_version_retried_is_one_distinct_unit(self, db_session):
        track = make_track(db_session)
        domain = make_domain(db_session, track, "PTE")
        user = make_user(db_session)
        scenario = make_scenario(db_session, domain=domain, external_id="SCN-1", content_version=1)
        add_scenario_attempt_evidence(db_session, user=user, scenario=scenario)
        add_scenario_attempt_evidence(db_session, user=user, scenario=scenario)
        add_scenario_attempt_evidence(db_session, user=user, scenario=scenario)
        summary = build_domain_evidence_summary(
            db_session, user_id=user.id, track_id=track.id, domain_id=domain.id, now=NOW
        )
        assert summary.scenario_evidence_count == 3
        assert summary.distinct_scenario_content_versions == 1

    def test_different_scenarios_are_distinct(self, db_session):
        track = make_track(db_session)
        domain = make_domain(db_session, track, "PTE")
        user = make_user(db_session)
        s1 = make_scenario(db_session, domain=domain, external_id="SCN-1")
        s2 = make_scenario(db_session, domain=domain, external_id="SCN-2")
        add_scenario_attempt_evidence(db_session, user=user, scenario=s1)
        add_scenario_attempt_evidence(db_session, user=user, scenario=s2)
        summary = build_domain_evidence_summary(
            db_session, user_id=user.id, track_id=track.id, domain_id=domain.id, now=NOW
        )
        assert summary.distinct_scenario_content_versions == 2

    def test_content_version_bump_on_same_scenario_is_distinct(self, db_session):
        """An attempt's own scenario_content_version snapshot (taken at start time)
        is what identity is keyed on -- not the scenario's CURRENT content_version --
        so a historical attempt against an old version stays a distinct content unit
        from a later attempt against a bumped version."""
        track = make_track(db_session)
        domain = make_domain(db_session, track, "PTE")
        user = make_user(db_session)
        scenario = make_scenario(db_session, domain=domain, external_id="SCN-1", content_version=1)
        add_scenario_attempt_evidence(db_session, user=user, scenario=scenario)
        scenario.content_version = 2
        db_session.commit()
        add_scenario_attempt_evidence(db_session, user=user, scenario=scenario)
        summary = build_domain_evidence_summary(
            db_session, user_id=user.id, track_id=track.id, domain_id=domain.id, now=NOW
        )
        assert summary.scenario_evidence_count == 2
        assert summary.distinct_scenario_content_versions == 2


# --- Timestamp derivation -----------------------------------------------------------


class TestMostRecentEvidenceAt:
    def test_none_when_no_evidence(self, db_session):
        track = make_track(db_session)
        domain = make_domain(db_session, track, "PTE")
        user = make_user(db_session)
        summary = build_domain_evidence_summary(
            db_session, user_id=user.id, track_id=track.id, domain_id=domain.id, now=NOW
        )
        assert summary.most_recent_evidence_at is None

    def test_is_the_max_across_practice_and_scenario(self, db_session):
        track = make_track(db_session)
        domain = make_domain(db_session, track, "PTE")
        user = make_user(db_session)
        add_exam_attempt_evidence(
            db_session, user=user, track=track, domain=domain, item_count=1, correct_count=1,
            submitted_at=NOW - timedelta(days=30),
        )
        scenario = make_scenario(db_session, domain=domain, external_id="SCN-1")
        add_scenario_attempt_evidence(db_session, user=user, scenario=scenario, submitted_at=NOW)
        summary = build_domain_evidence_summary(
            db_session, user_id=user.id, track_id=track.id, domain_id=domain.id, now=NOW
        )
        assert summary.most_recent_evidence_at == NOW

    def test_is_not_projection_or_request_time(self, db_session):
        track = make_track(db_session)
        domain = make_domain(db_session, track, "PTE")
        user = make_user(db_session)
        evidence_time = NOW - timedelta(days=45)
        add_exam_attempt_evidence(
            db_session, user=user, track=track, domain=domain, item_count=1, correct_count=1,
            submitted_at=evidence_time,
        )
        summary = build_domain_evidence_summary(
            db_session, user_id=user.id, track_id=track.id, domain_id=domain.id, now=NOW
        )
        assert summary.most_recent_evidence_at == evidence_time
        assert summary.most_recent_evidence_at != NOW


# --- Misconception interim behavior -------------------------------------------------


class TestMisconceptionInterimBehavior:
    def test_always_zero_in_slice_3(self, db_session):
        track = make_track(db_session)
        domain = make_domain(db_session, track, "PTE")
        user = make_user(db_session)
        summary = build_domain_evidence_summary(
            db_session, user_id=user.id, track_id=track.id, domain_id=domain.id, now=NOW
        )
        assert summary.unresolved_misconception_count == INTERIM_UNRESOLVED_MISCONCEPTION_COUNT
        assert INTERIM_UNRESOLVED_MISCONCEPTION_COUNT == 0


# --- Recompute upsert / idempotency / replayability ---------------------------------


class TestRecomputeUpsert:
    def test_creates_row_when_none_exists(self, db_session):
        track = make_track(db_session)
        domain = make_domain(db_session, track, "PTE")
        user = make_user(db_session)
        row = recompute_learner_domain_state(
            db_session, user_id=user.id, track_id=track.id, domain_id=domain.id, now=NOW
        )
        assert row.id is not None
        assert row.readiness_state == STATE_INSUFFICIENT_EVIDENCE

    def test_updates_existing_row_in_place_not_a_duplicate(self, db_session):
        track = make_track(db_session)
        domain = make_domain(db_session, track, "PTE")
        user = make_user(db_session)
        first = recompute_learner_domain_state(
            db_session, user_id=user.id, track_id=track.id, domain_id=domain.id, now=NOW
        )
        make_ready_domain(db_session, user=user, track=track, domain=domain, submitted_at=NOW)
        second = recompute_learner_domain_state(
            db_session, user_id=user.id, track_id=track.id, domain_id=domain.id, now=NOW
        )
        assert second.id == first.id
        rows = db_session.scalars(select(LearnerDomainState)).all()
        assert len(rows) == 1
        assert second.readiness_state == STATE_READY

    def test_idempotent_repeated_recompute_same_semantic_output(self, db_session):
        track = make_track(db_session)
        domain = make_domain(db_session, track, "PTE")
        user = make_user(db_session)
        make_ready_domain(db_session, user=user, track=track, domain=domain, submitted_at=NOW)
        first = recompute_learner_domain_state(
            db_session, user_id=user.id, track_id=track.id, domain_id=domain.id, now=NOW
        )
        second = recompute_learner_domain_state(
            db_session, user_id=user.id, track_id=track.id, domain_id=domain.id, now=NOW
        )
        assert first.readiness_state == second.readiness_state
        assert first.reason_codes == second.reason_codes
        assert first.practice_evidence_count == second.practice_evidence_count
        assert first.scenario_evidence_count == second.scenario_evidence_count
        assert first.calculated_at == second.calculated_at  # same injected `now`
        assert len(db_session.scalars(select(LearnerDomainState)).all()) == 1

    def test_no_duplicate_row_across_many_recomputes(self, db_session):
        track = make_track(db_session)
        domain = make_domain(db_session, track, "PTE")
        user = make_user(db_session)
        for _ in range(5):
            recompute_learner_domain_state(
                db_session, user_id=user.id, track_id=track.id, domain_id=domain.id, now=NOW
            )
        assert len(db_session.scalars(select(LearnerDomainState)).all()) == 1

    def test_projection_version_is_the_centralized_constant(self, db_session):
        track = make_track(db_session)
        domain = make_domain(db_session, track, "PTE")
        user = make_user(db_session)
        row = recompute_learner_domain_state(
            db_session, user_id=user.id, track_id=track.id, domain_id=domain.id, now=NOW
        )
        assert row.projection_version == PROJECTION_VERSION == 1

    def test_calculated_at_differs_from_most_recent_evidence_at(self, db_session):
        track = make_track(db_session)
        domain = make_domain(db_session, track, "PTE")
        user = make_user(db_session)
        evidence_time = NOW - timedelta(days=20)
        add_exam_attempt_evidence(
            db_session, user=user, track=track, domain=domain, item_count=1, correct_count=1,
            submitted_at=evidence_time,
        )
        evaluation_time = NOW
        row = recompute_learner_domain_state(
            db_session, user_id=user.id, track_id=track.id, domain_id=domain.id, now=evaluation_time
        )
        assert row.most_recent_evidence_at == evidence_time
        assert row.calculated_at == evaluation_time
        assert row.calculated_at != row.most_recent_evidence_at


class TestReplayability:
    def test_delete_and_recompute_reproduces_the_same_projection(self, db_session):
        track = make_track(db_session)
        domain = make_domain(db_session, track, "PTE")
        user = make_user(db_session)
        make_ready_domain(db_session, user=user, track=track, domain=domain, submitted_at=NOW)

        original = recompute_learner_domain_state(
            db_session, user_id=user.id, track_id=track.id, domain_id=domain.id, now=NOW
        )
        semantic_before = (
            original.readiness_state, original.reason_codes,
            original.practice_evidence_count, original.scenario_evidence_count,
            original.distinct_scenario_content_versions,
            original.recent_practice_mastery_band, original.recent_scenario_mastery_band,
            original.most_recent_evidence_at, original.unresolved_misconception_count,
            original.projection_version,
        )

        db_session.delete(original)
        db_session.commit()
        assert db_session.scalars(select(LearnerDomainState)).all() == []

        rebuilt = recompute_learner_domain_state(
            db_session, user_id=user.id, track_id=track.id, domain_id=domain.id, now=NOW
        )
        semantic_after = (
            rebuilt.readiness_state, rebuilt.reason_codes,
            rebuilt.practice_evidence_count, rebuilt.scenario_evidence_count,
            rebuilt.distinct_scenario_content_versions,
            rebuilt.recent_practice_mastery_band, rebuilt.recent_scenario_mastery_band,
            rebuilt.most_recent_evidence_at, rebuilt.unresolved_misconception_count,
            rebuilt.projection_version,
        )
        assert semantic_after == semantic_before


# --- Failure behavior ----------------------------------------------------------------


class TestFailureBehavior:
    def test_zero_evidence_is_not_an_error(self, db_session):
        track = make_track(db_session)
        domain = make_domain(db_session, track, "PTE")
        user = make_user(db_session)
        row = recompute_learner_domain_state(
            db_session, user_id=user.id, track_id=track.id, domain_id=domain.id, now=NOW
        )
        assert row.readiness_state == STATE_INSUFFICIENT_EVIDENCE

    def test_unknown_user_raises(self, db_session):
        track = make_track(db_session)
        domain = make_domain(db_session, track, "PTE")
        with pytest.raises(ReadinessComputationError):
            recompute_learner_domain_state(
                db_session, user_id=999999, track_id=track.id, domain_id=domain.id, now=NOW
            )

    def test_unknown_track_raises(self, db_session):
        track = make_track(db_session)
        domain = make_domain(db_session, track, "PTE")
        user = make_user(db_session)
        with pytest.raises(ReadinessComputationError):
            recompute_learner_domain_state(
                db_session, user_id=user.id, track_id=999999, domain_id=domain.id, now=NOW
            )

    def test_unknown_domain_raises(self, db_session):
        track = make_track(db_session)
        user = make_user(db_session)
        with pytest.raises(ReadinessComputationError):
            recompute_learner_domain_state(
                db_session, user_id=user.id, track_id=track.id, domain_id=999999, now=NOW
            )

    def test_domain_not_belonging_to_track_raises(self, db_session):
        track_a = make_track(db_session, code="CCAO-F")
        track_b = make_track(db_session, code="CCDV-F")
        domain_on_b = make_domain(db_session, track_b, "OEV")
        user = make_user(db_session)
        with pytest.raises(ReadinessComputationError):
            recompute_learner_domain_state(
                db_session, user_id=user.id, track_id=track_a.id, domain_id=domain_on_b.id, now=NOW
            )
        # No projection row must be created on a rejected identity.
        assert db_session.scalars(select(LearnerDomainState)).all() == []


# --- Production-shaped regression ----------------------------------------------------


class TestProductionShapedRegression:
    def test_ccao_f_pte_scn_001_shape_cannot_produce_readiness(self, db_session):
        """Mirrors the real production ScenarioAttempt #1: one strong, submitted
        scenario attempt, zero exam/practice evidence, no attempt-id special-casing
        anywhere in the aggregation or classifier."""
        track = make_track(db_session, code="CCAO-F")
        domain = make_domain(db_session, track, "PTE")
        user = make_user(db_session)
        scenario = make_scenario(db_session, domain=domain, external_id="CCAO-F-PTE-SCN-001")
        attempt = add_scenario_attempt_evidence(
            db_session, user=user, scenario=scenario,
            mastery_band=MasteryBand.STRONG, score_pct=100.0, submitted_at=NOW,
        )
        before = (attempt.status, attempt.score_pct, attempt.mastery_band, attempt.scenario_id)

        row = recompute_learner_domain_state(
            db_session, user_id=user.id, track_id=track.id, domain_id=domain.id, now=NOW
        )

        assert row.scenario_evidence_count == 1
        assert row.practice_evidence_count == 0
        assert row.readiness_state == STATE_INSUFFICIENT_EVIDENCE
        assert len(row.reason_codes) > 0

        db_session.expire_all()
        reloaded = db_session.get(ScenarioAttempt, attempt.id)
        after = (reloaded.status, reloaded.score_pct, reloaded.mastery_band, reloaded.scenario_id)
        assert after == before


class TestPracticeOnlyRegression:
    def test_strong_practice_zero_scenario_stays_insufficient_evidence(self, db_session):
        track = make_track(db_session)
        domain = make_domain(db_session, track, "PTE")
        user = make_user(db_session)
        add_exam_attempt_evidence(
            db_session, user=user, track=track, domain=domain,
            item_count=20, correct_count=20, mastery_band=MasteryBand.STRONG, submitted_at=NOW,
        )
        row = recompute_learner_domain_state(
            db_session, user_id=user.id, track_id=track.id, domain_id=domain.id, now=NOW
        )
        assert row.practice_evidence_count == 20
        assert row.scenario_evidence_count == 0
        assert row.readiness_state == STATE_INSUFFICIENT_EVIDENCE


class TestSufficientMixedEvidence:
    def test_recompute_materializes_ready(self, db_session):
        track = make_track(db_session)
        domain = make_domain(db_session, track, "PTE")
        user = make_user(db_session)
        make_ready_domain(db_session, user=user, track=track, domain=domain, submitted_at=NOW)
        row = recompute_learner_domain_state(
            db_session, user_id=user.id, track_id=track.id, domain_id=domain.id, now=NOW
        )
        assert row.readiness_state == STATE_READY
        assert row.reason_codes == []


class TestStaleMixedEvidence:
    def test_evidence_beyond_ninety_days_demotes_to_approaching_ready(self, db_session):
        track = make_track(db_session)
        domain = make_domain(db_session, track, "PTE")
        user = make_user(db_session)
        stale_time = NOW - timedelta(days=STALENESS_THRESHOLD_DAYS + 1)
        make_ready_domain(db_session, user=user, track=track, domain=domain, submitted_at=stale_time)
        row = recompute_learner_domain_state(
            db_session, user_id=user.id, track_id=track.id, domain_id=domain.id, now=NOW
        )
        assert row.readiness_state == STATE_APPROACHING_READY
        assert "STALE_EVIDENCE" in row.reason_codes


# --- Isolation -------------------------------------------------------------------


class TestIsolation:
    def test_evidence_for_one_user_does_not_affect_another(self, db_session):
        track = make_track(db_session)
        domain = make_domain(db_session, track, "PTE")
        user_a = make_user(db_session, email="a@example.com")
        user_b = make_user(db_session, email="b@example.com")
        make_ready_domain(db_session, user=user_a, track=track, domain=domain, submitted_at=NOW)

        row_a = recompute_learner_domain_state(
            db_session, user_id=user_a.id, track_id=track.id, domain_id=domain.id, now=NOW
        )
        row_b = recompute_learner_domain_state(
            db_session, user_id=user_b.id, track_id=track.id, domain_id=domain.id, now=NOW
        )
        assert row_a.readiness_state == STATE_READY
        assert row_b.readiness_state == STATE_INSUFFICIENT_EVIDENCE

    def test_evidence_for_one_domain_does_not_affect_another(self, db_session):
        track = make_track(db_session)
        pte = make_domain(db_session, track, "PTE", position=1)
        oev = make_domain(db_session, track, "OEV", position=2)
        user = make_user(db_session)
        make_ready_domain(db_session, user=user, track=track, domain=pte, submitted_at=NOW)

        row_pte = recompute_learner_domain_state(
            db_session, user_id=user.id, track_id=track.id, domain_id=pte.id, now=NOW
        )
        row_oev = recompute_learner_domain_state(
            db_session, user_id=user.id, track_id=track.id, domain_id=oev.id, now=NOW
        )
        assert row_pte.readiness_state == STATE_READY
        assert row_oev.readiness_state == STATE_INSUFFICIENT_EVIDENCE
        assert row_oev.scenario_evidence_count == 0
        assert row_oev.practice_evidence_count == 0

    def test_evidence_for_one_track_does_not_affect_another(self, db_session):
        track_a = make_track(db_session, code="CCAO-F")
        track_b = make_track(db_session, code="CCDV-F")
        domain_a = make_domain(db_session, track_a, "PTE")
        domain_b = make_domain(db_session, track_b, "PTE")
        user = make_user(db_session)
        make_ready_domain(db_session, user=user, track=track_a, domain=domain_a, submitted_at=NOW)

        row_a = recompute_learner_domain_state(
            db_session, user_id=user.id, track_id=track_a.id, domain_id=domain_a.id, now=NOW
        )
        row_b = recompute_learner_domain_state(
            db_session, user_id=user.id, track_id=track_b.id, domain_id=domain_b.id, now=NOW
        )
        assert row_a.readiness_state == STATE_READY
        assert row_b.readiness_state == STATE_INSUFFICIENT_EVIDENCE


# --- Historical evidence immutability ------------------------------------------------


def snapshot_table(db, model) -> list[tuple]:
    cols = [c.name for c in model.__table__.columns]
    rows = db.scalars(select(model)).all()
    return sorted(tuple(getattr(row, c) for c in cols) for row in rows)


class TestHistoricalEvidenceImmutability:
    def test_recompute_never_mutates_evidence_tables(self, db_session):
        track = make_track(db_session)
        domain = make_domain(db_session, track, "PTE")
        user = make_user(db_session)
        make_ready_domain(db_session, user=user, track=track, domain=domain, submitted_at=NOW)

        db_session.expire_all()
        before = {
            model.__name__: snapshot_table(db_session, model)
            for model in (ExamAttempt, AttemptItem, AttemptDomainScore, Scenario, ScenarioAttempt,
                          Question, AnswerOption)
        }

        recompute_learner_domain_state(
            db_session, user_id=user.id, track_id=track.id, domain_id=domain.id, now=NOW
        )
        recompute_learner_domain_state(
            db_session, user_id=user.id, track_id=track.id, domain_id=domain.id, now=NOW
        )

        db_session.expire_all()
        after = {
            model.__name__: snapshot_table(db_session, model)
            for model in (ExamAttempt, AttemptItem, AttemptDomainScore, Scenario, ScenarioAttempt,
                          Question, AnswerOption)
        }
        assert after == before


# --- No external providers -----------------------------------------------------------


class TestIsolationFromProviders:
    def test_learner_readiness_module_has_no_forbidden_import(self):
        import ast
        import inspect

        from app.services import learner_readiness as module

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
                f"learner_readiness.py must not import anything referencing {forbidden!r}"
            )
