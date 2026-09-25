"""KSOR Slice 3/4: evidence aggregation, misconception lifecycle, and projection
recompute tests.

Builds real ExamAttempt/AttemptItem/AttemptDomainScore, ScenarioAttempt, and (for
Slice 4) ScenarioStep/ScenarioStepOption/ScenarioEvent rows directly against a
throwaway SQLite database (same fixture pattern as test_readiness_model.py), then
proves the service (build_domain_evidence_summary / derive_unresolved_misconceptions
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
    ScenarioEvent,
    ScenarioStep,
    ScenarioStepOption,
    Track,
    User,
)
from app.services.learner_readiness import (
    PROJECTION_VERSION,
    ReadinessComputationError,
    build_domain_evidence_summary,
    derive_unresolved_misconceptions,
    recompute_learner_domain_state,
)
from app.services.readiness_policy import (
    DOMAIN_BELOW_THRESHOLD,
    MIN_PRACTICE_ITEMS_FOR_SUFFICIENCY,
    MIN_SCENARIO_ATTEMPTS_FOR_SUFFICIENCY,
    NO_APPLIED_SCENARIO_EVIDENCE,
    REPEATED_MISCONCEPTION,
    REPEATED_SCENARIO_NOT_DIVERSE_EVIDENCE,
    STALENESS_THRESHOLD_DAYS,
    STATE_APPROACHING_READY,
    STATE_DEVELOPING,
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
    answered_count=None,
):
    """Directly persists an ExamAttempt + its AttemptItems (+ AttemptDomainScore if
    submitted) -- bypasses the scoring engine and the router entirely, since this
    file tests evidence AGGREGATION, not grading (already covered by
    test_scoring.py/test_grading.py).

    Items persist the way routers/attempts.py::submit does: the first `correct_count`
    select the right option, the rest up to `answered_count` (default: every item)
    select a WRONG option, and any beyond that are blank (`[]`). Wrong answers are
    real selections, not blanks -- policy v2 judges attempt completion from
    selected_option_ids, so conflating "wrong" with "unanswered" would silently
    disqualify every fixture attempt below the completion ratio."""
    answered_count = item_count if answered_count is None else answered_count
    assert correct_count <= answered_count <= item_count
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
        wrong = AnswerOption(question_id=q.id, label="B", text="w", is_correct=False, position=2)
        db.add_all([opt, wrong])
        db.flush()
        is_correct = i < correct_count
        if is_correct:
            selected = [opt.id]
        elif i < answered_count:
            selected = [wrong.id]
        else:
            selected = []
        db.add(
            AttemptItem(
                attempt_id=attempt.id, question_id=q.id, domain_id=domain.id,
                position=i + 1, selected_option_ids=selected,
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
    """Full sufficiency: MIN_PRACTICE_ITEMS_FOR_SUFFICIENCY strong practice items in
    one fully-answered attempt, 2 distinct strong scenarios, all recent -- the
    smallest evidence set that should legitimately reach `ready`."""
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

    def test_recent_practice_mastery_band_pools_the_qualifying_window(self, db_session):
        """Policy v2 (replaces v1's "newest submitted attempt's band"): the newest
        attempt's 5 items are below PRACTICE_BAND_MIN_ITEMS, so the older attempt is
        pooled in too -- 1/5 + 5/5 = 6/10 = 60% -> developing, not the newest
        attempt's strong."""
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
        assert summary.recent_practice_mastery_band == MasteryBand.DEVELOPING


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

    def test_single_scenario_band_is_its_latest_attempt(self, db_session):
        """Projection v4: with one independent scenario, the band is that
        scenario's latest submitted attempt (critical -> strong reads strong)."""
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

    def test_content_version_bump_on_same_scenario_is_not_a_new_unit(self, db_session):
        """Projection v3 (Gate C3-C2): identity is the scenario, not the
        (scenario, content_version) pair. Attempts snapshot different versions here
        -- a state the seeder guard normally prevents, constructed directly -- and
        still count as ONE independent scenario."""
        track = make_track(db_session)
        domain = make_domain(db_session, track, "PTE")
        user = make_user(db_session)
        scenario = make_scenario(db_session, domain=domain, external_id="SCN-1", content_version=1)
        add_scenario_attempt_evidence(db_session, user=user, scenario=scenario)
        scenario.content_version = 2
        db_session.commit()
        add_scenario_attempt_evidence(db_session, user=user, scenario=scenario)
        versions = {
            a.scenario_content_version for a in db_session.scalars(select(ScenarioAttempt)).all()
        }
        assert versions == {1, 2}  # the two attempts really do differ in version
        summary = build_domain_evidence_summary(
            db_session, user_id=user.id, track_id=track.id, domain_id=domain.id, now=NOW
        )
        assert summary.scenario_evidence_count == 2
        assert summary.distinct_scenario_content_versions == 1

    def test_different_scenarios_with_same_version_integer_count_as_two(self, db_session):
        track = make_track(db_session)
        domain = make_domain(db_session, track, "PTE")
        user = make_user(db_session)
        s1 = make_scenario(db_session, domain=domain, external_id="SCN-1", content_version=1)
        s2 = make_scenario(db_session, domain=domain, external_id="SCN-2", content_version=1)
        add_scenario_attempt_evidence(db_session, user=user, scenario=s1)
        add_scenario_attempt_evidence(db_session, user=user, scenario=s2)
        summary = build_domain_evidence_summary(
            db_session, user_id=user.id, track_id=track.id, domain_id=domain.id, now=NOW
        )
        assert summary.scenario_evidence_count == 2
        assert summary.distinct_scenario_content_versions == 2

    def test_repeats_raise_raw_count_but_not_independent_count(self, db_session):
        track = make_track(db_session)
        domain = make_domain(db_session, track, "PTE")
        user = make_user(db_session)
        s1 = make_scenario(db_session, domain=domain, external_id="SCN-1")
        s2 = make_scenario(db_session, domain=domain, external_id="SCN-2")
        observed = []
        for scenario in (s1, s1, s1, s2, s2):
            add_scenario_attempt_evidence(db_session, user=user, scenario=scenario)
            summary = build_domain_evidence_summary(
                db_session, user_id=user.id, track_id=track.id, domain_id=domain.id, now=NOW
            )
            observed.append(
                (summary.scenario_evidence_count, summary.distinct_scenario_content_versions)
            )
        assert observed == [(1, 1), (2, 1), (3, 1), (4, 2), (5, 2)]


class TestScenarioSufficiencyEndToEnd:
    """Gate C3-C2 Section 11 through the real recompute path: practice evidence is
    fully sufficient and strong in every case, so scenario sufficiency alone decides
    whether the domain can leave insufficient_evidence."""

    def _world(self, db):
        track = make_track(db)
        domain = make_domain(db, track, "PTE")
        user = make_user(db)
        add_exam_attempt_evidence(
            db, user=user, track=track, domain=domain,
            item_count=MIN_PRACTICE_ITEMS_FOR_SUFFICIENCY,
            correct_count=MIN_PRACTICE_ITEMS_FOR_SUFFICIENCY,
            mastery_band=MasteryBand.STRONG, submitted_at=NOW,
        )
        return track, domain, user

    def _recompute(self, db, track, domain, user):
        return recompute_learner_domain_state(
            db, user_id=user.id, track_id=track.id, domain_id=domain.id, now=NOW
        )

    def test_zero_scenarios_is_insufficient(self, db_session):
        track, domain, user = self._world(db_session)
        row = self._recompute(db_session, track, domain, user)
        assert row.readiness_state == STATE_INSUFFICIENT_EVIDENCE
        assert row.reason_codes == [NO_APPLIED_SCENARIO_EVIDENCE]

    def test_one_scenario_is_insufficient(self, db_session):
        track, domain, user = self._world(db_session)
        add_scenario_attempt_evidence(
            db_session, user=user,
            scenario=make_scenario(db_session, domain=domain, external_id="SCN-1"),
        )
        row = self._recompute(db_session, track, domain, user)
        assert row.readiness_state == STATE_INSUFFICIENT_EVIDENCE
        assert row.reason_codes == [NO_APPLIED_SCENARIO_EVIDENCE]

    @pytest.mark.parametrize("repeats", [2, 10])
    def test_same_scenario_repeated_is_still_insufficient(self, db_session, repeats):
        track, domain, user = self._world(db_session)
        scenario = make_scenario(db_session, domain=domain, external_id="SCN-1")
        for _ in range(repeats):
            add_scenario_attempt_evidence(db_session, user=user, scenario=scenario)
        row = self._recompute(db_session, track, domain, user)
        assert (row.scenario_evidence_count, row.distinct_scenario_content_versions) == (repeats, 1)
        assert row.readiness_state == STATE_INSUFFICIENT_EVIDENCE
        assert row.reason_codes == [
            NO_APPLIED_SCENARIO_EVIDENCE, REPEATED_SCENARIO_NOT_DIVERSE_EVIDENCE,
        ]

    def test_same_scenario_across_two_content_versions_is_still_insufficient(self, db_session):
        track, domain, user = self._world(db_session)
        scenario = make_scenario(db_session, domain=domain, external_id="SCN-1", content_version=1)
        add_scenario_attempt_evidence(db_session, user=user, scenario=scenario)
        scenario.content_version = 2
        db_session.commit()
        add_scenario_attempt_evidence(db_session, user=user, scenario=scenario)
        row = self._recompute(db_session, track, domain, user)
        assert (row.scenario_evidence_count, row.distinct_scenario_content_versions) == (2, 1)
        assert row.readiness_state == STATE_INSUFFICIENT_EVIDENCE

    def test_two_different_scenarios_satisfy_sufficiency(self, db_session):
        track, domain, user = self._world(db_session)
        for ext in ("SCN-1", "SCN-2"):
            add_scenario_attempt_evidence(
                db_session, user=user,
                scenario=make_scenario(db_session, domain=domain, external_id=ext, content_version=1),
            )
        row = self._recompute(db_session, track, domain, user)
        assert (row.scenario_evidence_count, row.distinct_scenario_content_versions) == (2, 2)
        assert row.readiness_state == STATE_READY

    def test_two_different_scenarios_other_gates_still_apply(self, db_session):
        track, domain, user = self._world(db_session)
        for ext in ("SCN-1", "SCN-2"):
            add_scenario_attempt_evidence(
                db_session, user=user,
                scenario=make_scenario(db_session, domain=domain, external_id=ext),
                mastery_band=MasteryBand.DEVELOPING, score_pct=50.0,
            )
        row = self._recompute(db_session, track, domain, user)
        assert row.readiness_state == STATE_DEVELOPING
        assert row.reason_codes == [DOMAIN_BELOW_THRESHOLD]


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


# --- Misconception lifecycle (Slice 4) fixture helpers ------------------------------


def make_scenario_step(db, *, scenario, position=1, options):
    """options: list of dicts with keys label/text/is_correct/misconception_tag
    (misconception_tag omitted or None on correct options, matching the authoring
    contract in scenario.py)."""
    step = ScenarioStep(scenario_id=scenario.id, position=position, prompt_text="p", step_type="mcq")
    db.add(step)
    db.flush()
    for i, opt in enumerate(options, start=1):
        db.add(
            ScenarioStepOption(
                step_id=step.id, label=opt.get("label", chr(64 + i)), text=opt.get("text", "t"),
                is_correct=opt["is_correct"], position=i, rationale="r",
                misconception_tag=opt.get("misconception_tag"),
            )
        )
    db.commit()
    return step


def add_step_answered_event(
    db, *, user, scenario_attempt, step, is_correct, misconception_tags=(), occurred_at=NOW
):
    """Mirrors build_scenario_event_payload()'s real shape exactly -- a flat dict
    with `is_correct` and `misconception_tags`, the only two fields
    derive_unresolved_misconceptions reads."""
    event = ScenarioEvent(
        scenario_attempt_id=scenario_attempt.id, user_id=user.id,
        event_type="step_answered", step_id=step.id,
        payload={"is_correct": is_correct, "misconception_tags": sorted(misconception_tags)},
        occurred_at=occurred_at,
    )
    db.add(event)
    db.commit()
    return event


# --- Misconception lifecycle: occurrence, resolution, recurrence, isolation --------


class TestMisconceptionLifecycle:
    def test_no_misconceptions_is_zero(self, db_session):
        track = make_track(db_session)
        domain = make_domain(db_session, track, "PTE")
        user = make_user(db_session)
        assert derive_unresolved_misconceptions(db_session, user_id=user.id, domain_id=domain.id) == 0

    def test_single_occurrence_is_one_unresolved(self, db_session):
        track = make_track(db_session)
        domain = make_domain(db_session, track, "PTE")
        user = make_user(db_session)
        scenario = make_scenario(db_session, domain=domain, external_id="SCN-1")
        step = make_scenario_step(db_session, scenario=scenario, options=[
            {"is_correct": True}, {"is_correct": False, "misconception_tag": "tag_a"},
        ])
        attempt = add_scenario_attempt_evidence(db_session, user=user, scenario=scenario, submitted_at=NOW)
        add_step_answered_event(db_session, user=user, scenario_attempt=attempt, step=step,
                                 is_correct=False, misconception_tags={"tag_a"}, occurred_at=NOW)
        assert derive_unresolved_misconceptions(db_session, user_id=user.id, domain_id=domain.id) == 1

    def test_wrong_answer_with_no_misconception_tag_fabricates_nothing(self, db_session):
        track = make_track(db_session)
        domain = make_domain(db_session, track, "PTE")
        user = make_user(db_session)
        scenario = make_scenario(db_session, domain=domain, external_id="SCN-1")
        step = make_scenario_step(db_session, scenario=scenario, options=[
            {"is_correct": True}, {"is_correct": False},  # no misconception_tag authored
        ])
        attempt = add_scenario_attempt_evidence(db_session, user=user, scenario=scenario, submitted_at=NOW)
        add_step_answered_event(db_session, user=user, scenario_attempt=attempt, step=step,
                                 is_correct=False, misconception_tags=(), occurred_at=NOW)
        assert derive_unresolved_misconceptions(db_session, user_id=user.id, domain_id=domain.id) == 0

    def test_repeated_same_misconception_counts_once(self, db_session):
        track = make_track(db_session)
        domain = make_domain(db_session, track, "PTE")
        user = make_user(db_session)
        scenario = make_scenario(db_session, domain=domain, external_id="SCN-1")
        step = make_scenario_step(db_session, scenario=scenario, options=[
            {"is_correct": True}, {"is_correct": False, "misconception_tag": "tag_a"},
        ])
        attempt1 = add_scenario_attempt_evidence(db_session, user=user, scenario=scenario, submitted_at=NOW)
        add_step_answered_event(db_session, user=user, scenario_attempt=attempt1, step=step,
                                 is_correct=False, misconception_tags={"tag_a"}, occurred_at=NOW)
        attempt2 = add_scenario_attempt_evidence(db_session, user=user, scenario=scenario, submitted_at=NOW + timedelta(hours=1))
        add_step_answered_event(db_session, user=user, scenario_attempt=attempt2, step=step,
                                 is_correct=False, misconception_tags={"tag_a"}, occurred_at=NOW + timedelta(hours=1))
        assert derive_unresolved_misconceptions(db_session, user_id=user.id, domain_id=domain.id) == 1

    def test_multiple_distinct_misconceptions_count_two(self, db_session):
        track = make_track(db_session)
        domain = make_domain(db_session, track, "PTE")
        user = make_user(db_session)
        scenario = make_scenario(db_session, domain=domain, external_id="SCN-1")
        step_a = make_scenario_step(db_session, scenario=scenario, position=1, options=[
            {"is_correct": True}, {"is_correct": False, "misconception_tag": "tag_a"},
        ])
        step_b = make_scenario_step(db_session, scenario=scenario, position=2, options=[
            {"is_correct": True}, {"is_correct": False, "misconception_tag": "tag_b"},
        ])
        attempt = add_scenario_attempt_evidence(db_session, user=user, scenario=scenario, submitted_at=NOW)
        add_step_answered_event(db_session, user=user, scenario_attempt=attempt, step=step_a,
                                 is_correct=False, misconception_tags={"tag_a"}, occurred_at=NOW)
        add_step_answered_event(db_session, user=user, scenario_attempt=attempt, step=step_b,
                                 is_correct=False, misconception_tags={"tag_b"}, occurred_at=NOW)
        assert derive_unresolved_misconceptions(db_session, user_id=user.id, domain_id=domain.id) == 2

    def test_unrelated_success_does_not_resolve(self, db_session):
        track = make_track(db_session)
        domain = make_domain(db_session, track, "PTE")
        user = make_user(db_session)
        scenario = make_scenario(db_session, domain=domain, external_id="SCN-1")
        step_a = make_scenario_step(db_session, scenario=scenario, position=1, options=[
            {"is_correct": True}, {"is_correct": False, "misconception_tag": "tag_a"},
        ])
        step_b = make_scenario_step(db_session, scenario=scenario, position=2, options=[
            {"is_correct": True}, {"is_correct": False, "misconception_tag": "tag_b"},
        ])
        attempt = add_scenario_attempt_evidence(db_session, user=user, scenario=scenario, submitted_at=NOW)
        add_step_answered_event(db_session, user=user, scenario_attempt=attempt, step=step_a,
                                 is_correct=False, misconception_tags={"tag_a"}, occurred_at=NOW)
        # Later correct answer on an UNRELATED step (tag_b's step) must not resolve tag_a.
        add_step_answered_event(db_session, user=user, scenario_attempt=attempt, step=step_b,
                                 is_correct=True, misconception_tags=(), occurred_at=NOW + timedelta(hours=1))
        assert derive_unresolved_misconceptions(db_session, user_id=user.id, domain_id=domain.id) == 1

    def test_same_tag_resolution(self, db_session):
        track = make_track(db_session)
        domain = make_domain(db_session, track, "PTE")
        user = make_user(db_session)
        scenario = make_scenario(db_session, domain=domain, external_id="SCN-1")
        step = make_scenario_step(db_session, scenario=scenario, options=[
            {"is_correct": True}, {"is_correct": False, "misconception_tag": "tag_a"},
        ])
        attempt1 = add_scenario_attempt_evidence(db_session, user=user, scenario=scenario, submitted_at=NOW)
        add_step_answered_event(db_session, user=user, scenario_attempt=attempt1, step=step,
                                 is_correct=False, misconception_tags={"tag_a"}, occurred_at=NOW)
        attempt2 = add_scenario_attempt_evidence(db_session, user=user, scenario=scenario, submitted_at=NOW + timedelta(hours=1))
        add_step_answered_event(db_session, user=user, scenario_attempt=attempt2, step=step,
                                 is_correct=True, misconception_tags=(), occurred_at=NOW + timedelta(hours=1))
        assert derive_unresolved_misconceptions(db_session, user_id=user.id, domain_id=domain.id) == 0

    def test_recurrence_reopens_after_resolution(self, db_session):
        track = make_track(db_session)
        domain = make_domain(db_session, track, "PTE")
        user = make_user(db_session)
        scenario = make_scenario(db_session, domain=domain, external_id="SCN-1")
        step = make_scenario_step(db_session, scenario=scenario, options=[
            {"is_correct": True}, {"is_correct": False, "misconception_tag": "tag_a"},
        ])
        a1 = add_scenario_attempt_evidence(db_session, user=user, scenario=scenario, submitted_at=NOW)
        add_step_answered_event(db_session, user=user, scenario_attempt=a1, step=step,
                                 is_correct=False, misconception_tags={"tag_a"}, occurred_at=NOW)
        a2 = add_scenario_attempt_evidence(db_session, user=user, scenario=scenario, submitted_at=NOW + timedelta(hours=1))
        add_step_answered_event(db_session, user=user, scenario_attempt=a2, step=step,
                                 is_correct=True, misconception_tags=(), occurred_at=NOW + timedelta(hours=1))
        # Resolved after a2.
        assert derive_unresolved_misconceptions(db_session, user_id=user.id, domain_id=domain.id) == 0
        a3 = add_scenario_attempt_evidence(db_session, user=user, scenario=scenario, submitted_at=NOW + timedelta(hours=2))
        add_step_answered_event(db_session, user=user, scenario_attempt=a3, step=step,
                                 is_correct=False, misconception_tags={"tag_a"}, occurred_at=NOW + timedelta(hours=2))
        # Reopened after a3.
        assert derive_unresolved_misconceptions(db_session, user_id=user.id, domain_id=domain.id) == 1

    def test_temporal_ordering_earlier_success_does_not_erase_later_misconception(self, db_session):
        """Insertion order is deliberately the OPPOSITE of temporal order here --
        the correct event is written to the DB first, the incorrect one second --
        to prove the reducer orders by `occurred_at`, not by row/insertion order."""
        track = make_track(db_session)
        domain = make_domain(db_session, track, "PTE")
        user = make_user(db_session)
        scenario = make_scenario(db_session, domain=domain, external_id="SCN-1")
        step = make_scenario_step(db_session, scenario=scenario, options=[
            {"is_correct": True}, {"is_correct": False, "misconception_tag": "tag_a"},
        ])
        a1 = add_scenario_attempt_evidence(db_session, user=user, scenario=scenario, submitted_at=NOW)
        add_step_answered_event(db_session, user=user, scenario_attempt=a1, step=step,
                                 is_correct=True, misconception_tags=(), occurred_at=NOW)
        a2 = add_scenario_attempt_evidence(db_session, user=user, scenario=scenario, submitted_at=NOW + timedelta(hours=1))
        add_step_answered_event(db_session, user=user, scenario_attempt=a2, step=step,
                                 is_correct=False, misconception_tags={"tag_a"}, occurred_at=NOW + timedelta(hours=1))
        assert derive_unresolved_misconceptions(db_session, user_id=user.id, domain_id=domain.id) == 1

    def test_tie_break_on_identical_timestamp_is_deterministic(self, db_session):
        track = make_track(db_session)
        domain = make_domain(db_session, track, "PTE")
        user = make_user(db_session)
        scenario = make_scenario(db_session, domain=domain, external_id="SCN-1")
        step = make_scenario_step(db_session, scenario=scenario, options=[
            {"is_correct": True}, {"is_correct": False, "misconception_tag": "tag_a"},
        ])
        a1 = add_scenario_attempt_evidence(db_session, user=user, scenario=scenario, submitted_at=NOW)
        add_step_answered_event(db_session, user=user, scenario_attempt=a1, step=step,
                                 is_correct=False, misconception_tags={"tag_a"}, occurred_at=NOW)
        a2 = add_scenario_attempt_evidence(db_session, user=user, scenario=scenario, submitted_at=NOW)
        # Same occurred_at as the first event -- the event `id` (insertion order)
        # must be the deterministic tie-break, and repeated calls must agree.
        add_step_answered_event(db_session, user=user, scenario_attempt=a2, step=step,
                                 is_correct=True, misconception_tags=(), occurred_at=NOW)
        first = derive_unresolved_misconceptions(db_session, user_id=user.id, domain_id=domain.id)
        second = derive_unresolved_misconceptions(db_session, user_id=user.id, domain_id=domain.id)
        assert first == second == 0  # later-inserted (higher id) event is the correct one

    def test_in_progress_attempt_evidence_is_not_eligible(self, db_session):
        track = make_track(db_session)
        domain = make_domain(db_session, track, "PTE")
        user = make_user(db_session)
        scenario = make_scenario(db_session, domain=domain, external_id="SCN-1")
        step = make_scenario_step(db_session, scenario=scenario, options=[
            {"is_correct": True}, {"is_correct": False, "misconception_tag": "tag_a"},
        ])
        attempt = add_scenario_attempt_evidence(db_session, user=user, scenario=scenario, status="in_progress")
        add_step_answered_event(db_session, user=user, scenario_attempt=attempt, step=step,
                                 is_correct=False, misconception_tags={"tag_a"}, occurred_at=NOW)
        assert derive_unresolved_misconceptions(db_session, user_id=user.id, domain_id=domain.id) == 0

    def test_user_isolation(self, db_session):
        track = make_track(db_session)
        domain = make_domain(db_session, track, "PTE")
        user_a = make_user(db_session, email="a@example.com")
        user_b = make_user(db_session, email="b@example.com")
        scenario = make_scenario(db_session, domain=domain, external_id="SCN-1")
        step = make_scenario_step(db_session, scenario=scenario, options=[
            {"is_correct": True}, {"is_correct": False, "misconception_tag": "tag_a"},
        ])
        attempt = add_scenario_attempt_evidence(db_session, user=user_a, scenario=scenario, submitted_at=NOW)
        add_step_answered_event(db_session, user=user_a, scenario_attempt=attempt, step=step,
                                 is_correct=False, misconception_tags={"tag_a"}, occurred_at=NOW)
        assert derive_unresolved_misconceptions(db_session, user_id=user_a.id, domain_id=domain.id) == 1
        assert derive_unresolved_misconceptions(db_session, user_id=user_b.id, domain_id=domain.id) == 0

    def test_domain_isolation_even_with_reused_tag_string(self, db_session):
        track = make_track(db_session)
        pte = make_domain(db_session, track, "PTE", position=1)
        oev = make_domain(db_session, track, "OEV", position=2)
        user = make_user(db_session)
        pte_scenario = make_scenario(db_session, domain=pte, external_id="SCN-PTE")
        pte_step = make_scenario_step(db_session, scenario=pte_scenario, options=[
            {"is_correct": True}, {"is_correct": False, "misconception_tag": "shared_tag"},
        ])
        oev_scenario = make_scenario(db_session, domain=oev, external_id="SCN-OEV")
        make_scenario_step(db_session, scenario=oev_scenario, options=[
            {"is_correct": True}, {"is_correct": False, "misconception_tag": "shared_tag"},
        ])
        attempt = add_scenario_attempt_evidence(db_session, user=user, scenario=pte_scenario, submitted_at=NOW)
        add_step_answered_event(db_session, user=user, scenario_attempt=attempt, step=pte_step,
                                 is_correct=False, misconception_tags={"shared_tag"}, occurred_at=NOW)
        assert derive_unresolved_misconceptions(db_session, user_id=user.id, domain_id=pte.id) == 1
        assert derive_unresolved_misconceptions(db_session, user_id=user.id, domain_id=oev.id) == 0

    def test_track_isolation_even_with_reused_tag_string(self, db_session):
        track_a = make_track(db_session, code="CCAO-F")
        track_b = make_track(db_session, code="CCDV-F")
        domain_a = make_domain(db_session, track_a, "PTE")
        domain_b = make_domain(db_session, track_b, "PTE")
        user = make_user(db_session)
        scenario_a = make_scenario(db_session, domain=domain_a, external_id="SCN-A")
        step_a = make_scenario_step(db_session, scenario=scenario_a, options=[
            {"is_correct": True}, {"is_correct": False, "misconception_tag": "shared_tag"},
        ])
        scenario_b = make_scenario(db_session, domain=domain_b, external_id="SCN-B")
        make_scenario_step(db_session, scenario=scenario_b, options=[
            {"is_correct": True}, {"is_correct": False, "misconception_tag": "shared_tag"},
        ])
        attempt = add_scenario_attempt_evidence(db_session, user=user, scenario=scenario_a, submitted_at=NOW)
        add_step_answered_event(db_session, user=user, scenario_attempt=attempt, step=step_a,
                                 is_correct=False, misconception_tags={"shared_tag"}, occurred_at=NOW)
        assert derive_unresolved_misconceptions(db_session, user_id=user.id, domain_id=domain_a.id) == 1
        assert derive_unresolved_misconceptions(db_session, user_id=user.id, domain_id=domain_b.id) == 0


# --- Misconception <-> projection integration ---------------------------------------


class TestMisconceptionProjectionIntegration:
    def test_otherwise_ready_with_unresolved_misconception_is_developing(self, db_session):
        track = make_track(db_session)
        domain = make_domain(db_session, track, "PTE")
        user = make_user(db_session)
        make_ready_domain(db_session, user=user, track=track, domain=domain, submitted_at=NOW)

        mis_scenario = make_scenario(db_session, domain=domain, external_id="SCN-MISCONCEPTION")
        step = make_scenario_step(db_session, scenario=mis_scenario, options=[
            {"is_correct": True}, {"is_correct": False, "misconception_tag": "tag_x"},
        ])
        attempt = add_scenario_attempt_evidence(db_session, user=user, scenario=mis_scenario, submitted_at=NOW)
        add_step_answered_event(db_session, user=user, scenario_attempt=attempt, step=step,
                                 is_correct=False, misconception_tags={"tag_x"}, occurred_at=NOW)

        row = recompute_learner_domain_state(
            db_session, user_id=user.id, track_id=track.id, domain_id=domain.id, now=NOW
        )
        assert row.readiness_state == STATE_DEVELOPING
        assert REPEATED_MISCONCEPTION in row.reason_codes
        assert row.unresolved_misconception_count == 1

    def test_resolved_misconception_allows_ready_again(self, db_session):
        track = make_track(db_session)
        domain = make_domain(db_session, track, "PTE")
        user = make_user(db_session)
        make_ready_domain(db_session, user=user, track=track, domain=domain, submitted_at=NOW)

        mis_scenario = make_scenario(db_session, domain=domain, external_id="SCN-MISCONCEPTION")
        step = make_scenario_step(db_session, scenario=mis_scenario, options=[
            {"is_correct": True}, {"is_correct": False, "misconception_tag": "tag_x"},
        ])
        a1 = add_scenario_attempt_evidence(db_session, user=user, scenario=mis_scenario, submitted_at=NOW)
        add_step_answered_event(db_session, user=user, scenario_attempt=a1, step=step,
                                 is_correct=False, misconception_tags={"tag_x"}, occurred_at=NOW)
        a2 = add_scenario_attempt_evidence(db_session, user=user, scenario=mis_scenario, submitted_at=NOW + timedelta(hours=1))
        add_step_answered_event(db_session, user=user, scenario_attempt=a2, step=step,
                                 is_correct=True, misconception_tags=(), occurred_at=NOW + timedelta(hours=1))

        row = recompute_learner_domain_state(
            db_session, user_id=user.id, track_id=track.id, domain_id=domain.id,
            now=NOW + timedelta(hours=1),
        )
        assert row.unresolved_misconception_count == 0
        assert row.readiness_state == STATE_READY


class TestMisconceptionReplayability:
    def test_delete_and_recompute_reproduces_same_misconception_count(self, db_session):
        track = make_track(db_session)
        domain = make_domain(db_session, track, "PTE")
        user = make_user(db_session)
        make_ready_domain(db_session, user=user, track=track, domain=domain, submitted_at=NOW)
        scenario = make_scenario(db_session, domain=domain, external_id="SCN-MISCONCEPTION")
        step = make_scenario_step(db_session, scenario=scenario, options=[
            {"is_correct": True}, {"is_correct": False, "misconception_tag": "tag_x"},
        ])
        attempt = add_scenario_attempt_evidence(db_session, user=user, scenario=scenario, submitted_at=NOW)
        add_step_answered_event(db_session, user=user, scenario_attempt=attempt, step=step,
                                 is_correct=False, misconception_tags={"tag_x"}, occurred_at=NOW)

        original = recompute_learner_domain_state(
            db_session, user_id=user.id, track_id=track.id, domain_id=domain.id, now=NOW
        )
        assert original.unresolved_misconception_count == 1

        db_session.delete(original)
        db_session.commit()

        rebuilt = recompute_learner_domain_state(
            db_session, user_id=user.id, track_id=track.id, domain_id=domain.id, now=NOW
        )
        assert rebuilt.unresolved_misconception_count == 1
        assert rebuilt.readiness_state == original.readiness_state
        assert rebuilt.reason_codes == original.reason_codes


class TestMisconceptionSourceImmutability:
    def test_recompute_never_mutates_scenario_event_or_option_tables(self, db_session):
        track = make_track(db_session)
        domain = make_domain(db_session, track, "PTE")
        user = make_user(db_session)
        scenario = make_scenario(db_session, domain=domain, external_id="SCN-1")
        step = make_scenario_step(db_session, scenario=scenario, options=[
            {"is_correct": True}, {"is_correct": False, "misconception_tag": "tag_a"},
        ])
        attempt = add_scenario_attempt_evidence(db_session, user=user, scenario=scenario, submitted_at=NOW)
        add_step_answered_event(db_session, user=user, scenario_attempt=attempt, step=step,
                                 is_correct=False, misconception_tags={"tag_a"}, occurred_at=NOW)

        db_session.expire_all()
        before = {
            model.__name__: snapshot_table(db_session, model)
            for model in (Scenario, ScenarioStep, ScenarioStepOption, ScenarioAttempt, ScenarioEvent)
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
            for model in (Scenario, ScenarioStep, ScenarioStepOption, ScenarioAttempt, ScenarioEvent)
        }
        assert after == before


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
        assert row.projection_version == PROJECTION_VERSION == 4

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


# --- Projection v2 -> v3 in-place upgrade (Gate C3-C2) --------------------------------


class TestV2ToV3InPlaceRecompute:
    EVIDENCE_MODELS = (ExamAttempt, AttemptItem, AttemptDomainScore, Scenario,
                       ScenarioAttempt, ScenarioEvent, Question, AnswerOption)

    def test_v2_row_is_upgraded_in_place_to_v3_without_touching_evidence(self, db_session):
        """A row written under v2 pair semantics (one scenario attempted at two
        content versions -> 2 "distinct" units) is recomputed in place: same row,
        same identity, version 3, one independent scenario, still insufficient."""
        track = make_track(db_session)
        domain = make_domain(db_session, track, "PTE")
        user = make_user(db_session)
        add_exam_attempt_evidence(
            db_session, user=user, track=track, domain=domain,
            item_count=MIN_PRACTICE_ITEMS_FOR_SUFFICIENCY,
            correct_count=MIN_PRACTICE_ITEMS_FOR_SUFFICIENCY,
            mastery_band=MasteryBand.STRONG, submitted_at=NOW,
        )
        scenario = make_scenario(db_session, domain=domain, external_id="SCN-1", content_version=1)
        add_scenario_attempt_evidence(db_session, user=user, scenario=scenario)
        scenario.content_version = 2
        db_session.commit()
        add_scenario_attempt_evidence(db_session, user=user, scenario=scenario)

        legacy = LearnerDomainState(
            user_id=user.id, track_id=track.id, domain_id=domain.id,
            practice_evidence_count=MIN_PRACTICE_ITEMS_FOR_SUFFICIENCY,
            scenario_evidence_count=2, distinct_scenario_content_versions=2,
            recent_practice_mastery_band="strong", recent_scenario_mastery_band="strong",
            most_recent_evidence_at=NOW, unresolved_misconception_count=0,
            readiness_state=STATE_READY, reason_codes=[],
            calculated_at=NOW - timedelta(days=1), projection_version=2,
        )
        db_session.add(legacy)
        db_session.commit()
        legacy_id = legacy.id

        db_session.expire_all()
        before = {m.__name__: snapshot_table(db_session, m) for m in self.EVIDENCE_MODELS}

        row = recompute_learner_domain_state(
            db_session, user_id=user.id, track_id=track.id, domain_id=domain.id, now=NOW
        )

        assert row.id == legacy_id
        assert (row.user_id, row.track_id, row.domain_id) == (user.id, track.id, domain.id)
        assert row.projection_version == PROJECTION_VERSION
        assert (row.scenario_evidence_count, row.distinct_scenario_content_versions) == (2, 1)
        assert row.readiness_state == STATE_INSUFFICIENT_EVIDENCE
        assert row.reason_codes == [
            NO_APPLIED_SCENARIO_EVIDENCE, REPEATED_SCENARIO_NOT_DIVERSE_EVIDENCE,
        ]
        assert len(db_session.scalars(select(LearnerDomainState)).all()) == 1

        db_session.expire_all()
        after = {m.__name__: snapshot_table(db_session, m) for m in self.EVIDENCE_MODELS}
        assert after == before

    def test_production_shaped_evidence_keeps_expected_independent_counts(self, db_session):
        """Today's production shape: one submitted PTE-001 attempt, no scenario
        evidence in the other six domains, sufficient practice everywhere -> v3
        independent counts PTE=1, others 0, all insufficient_evidence."""
        track = make_track(db_session)
        user = make_user(db_session)
        codes = ["PTE", "OEV", "PMS", "WISD", "CKM", "GRR", "TRO"]
        domains = {c: make_domain(db_session, track, c, position=i + 1) for i, c in enumerate(codes)}
        for domain in domains.values():
            add_exam_attempt_evidence(
                db_session, user=user, track=track, domain=domain,
                item_count=MIN_PRACTICE_ITEMS_FOR_SUFFICIENCY,
                correct_count=MIN_PRACTICE_ITEMS_FOR_SUFFICIENCY,
                mastery_band=MasteryBand.STRONG, submitted_at=NOW,
            )
            make_scenario(db_session, domain=domain, external_id=f"CCAO-F-{domain.code}-SCN-001")
        pte_scenario = db_session.scalar(
            select(Scenario).where(Scenario.external_id == "CCAO-F-PTE-SCN-001")
        )
        add_scenario_attempt_evidence(db_session, user=user, scenario=pte_scenario)

        observed = {}
        for code, domain in domains.items():
            row = recompute_learner_domain_state(
                db_session, user_id=user.id, track_id=track.id, domain_id=domain.id, now=NOW
            )
            observed[code] = (row.distinct_scenario_content_versions, row.readiness_state,
                              row.projection_version)
        assert observed == {
            c: (1 if c == "PTE" else 0, STATE_INSUFFICIENT_EVIDENCE, PROJECTION_VERSION) for c in codes
        }


# --- Projection v4: scenario mastery aggregation through the service (Gate C3-C5) ----

from app.models import ScenarioStepAttempt  # noqa: E402


def _v4_world(db):
    """Practice sufficient + strong, so scenario evidence alone moves the outcome."""
    track = make_track(db)
    domain = make_domain(db, track, "OEV")
    user = make_user(db)
    add_exam_attempt_evidence(
        db, user=user, track=track, domain=domain,
        item_count=MIN_PRACTICE_ITEMS_FOR_SUFFICIENCY,
        correct_count=MIN_PRACTICE_ITEMS_FOR_SUFFICIENCY,
        mastery_band=MasteryBand.STRONG, submitted_at=NOW - timedelta(days=30),
    )
    return track, domain, user


def _attempts(db, user, history, *, base=NOW - timedelta(days=10)):
    """history: (scenario, band) oldest first, one minute apart."""
    return [
        add_scenario_attempt_evidence(
            db, user=user, scenario=scenario, mastery_band=band,
            submitted_at=base + timedelta(minutes=i),
            score_pct={MasteryBand.STRONG: 100.0, MasteryBand.PROFICIENT: 75.0,
                       MasteryBand.DEVELOPING: 50.0, MasteryBand.CRITICAL: 0.0}[band],
        )
        for i, (scenario, band) in enumerate(history)
    ]


def _summary(db, track, domain, user):
    return build_domain_evidence_summary(
        db, user_id=user.id, track_id=track.id, domain_id=domain.id, now=NOW
    )


class TestScenarioMasteryAggregationService:
    S, P, D, C = (MasteryBand.STRONG, MasteryBand.PROFICIENT,
                  MasteryBand.DEVELOPING, MasteryBand.CRITICAL)

    def test_no_scenarios_is_none(self, db_session):
        track, domain, user = _v4_world(db_session)
        assert _summary(db_session, track, domain, user).recent_scenario_mastery_band is None

    @pytest.mark.parametrize("reverse", [False, True])
    def test_strong_then_critical_is_critical_in_either_order(self, db_session, reverse):
        track, domain, user = _v4_world(db_session)
        a = make_scenario(db_session, domain=domain, external_id="SCN-A")
        b = make_scenario(db_session, domain=domain, external_id="SCN-B")
        history = [(a, self.S), (b, self.C)]
        _attempts(db_session, user, history[::-1] if reverse else history)
        assert _summary(db_session, track, domain, user).recent_scenario_mastery_band == self.C

    def test_same_scenario_remediation_and_regression(self, db_session):
        track, domain, user = _v4_world(db_session)
        a = make_scenario(db_session, domain=domain, external_id="SCN-A")
        _attempts(db_session, user, [(a, self.C), (a, self.S)])
        assert _summary(db_session, track, domain, user).recent_scenario_mastery_band == self.S
        _attempts(db_session, user, [(a, self.C)], base=NOW - timedelta(days=1))
        assert _summary(db_session, track, domain, user).recent_scenario_mastery_band == self.C

    def test_independent_scenario_remediation(self, db_session):
        track, domain, user = _v4_world(db_session)
        a = make_scenario(db_session, domain=domain, external_id="SCN-A")
        b = make_scenario(db_session, domain=domain, external_id="SCN-B")
        _attempts(db_session, user, [(a, self.C), (a, self.S), (b, self.D)])
        assert _summary(db_session, track, domain, user).recent_scenario_mastery_band == self.D
        _attempts(db_session, user, [(b, self.S)], base=NOW - timedelta(days=1))
        assert _summary(db_session, track, domain, user).recent_scenario_mastery_band == self.S

    def test_repeated_strong_scenario_cannot_hide_independent_developing(self, db_session):
        track, domain, user = _v4_world(db_session)
        a = make_scenario(db_session, domain=domain, external_id="SCN-A")
        b = make_scenario(db_session, domain=domain, external_id="SCN-B")
        _attempts(db_session, user, [(b, self.D)] + [(a, self.S)] * 5)
        s = _summary(db_session, track, domain, user)
        assert (s.scenario_evidence_count, s.distinct_scenario_content_versions) == (6, 2)
        assert s.recent_scenario_mastery_band == self.D

    def test_three_scenarios_then_the_limiting_one_is_remediated(self, db_session):
        track, domain, user = _v4_world(db_session)
        a, b, c = (make_scenario(db_session, domain=domain, external_id=f"SCN-{x}") for x in "ABC")
        _attempts(db_session, user, [(a, self.S), (b, self.S), (c, self.C)])
        assert _summary(db_session, track, domain, user).recent_scenario_mastery_band == self.C
        _attempts(db_session, user, [(c, self.S)], base=NOW - timedelta(days=1))
        assert _summary(db_session, track, domain, user).recent_scenario_mastery_band == self.S

    @pytest.mark.parametrize("first,second", [("C", "S"), ("S", "C")])
    def test_content_version_change_stays_one_unit(self, db_session, first, second):
        track, domain, user = _v4_world(db_session)
        a = make_scenario(db_session, domain=domain, external_id="SCN-A", content_version=1)
        _attempts(db_session, user, [(a, getattr(self, first))])
        a.content_version = 2
        db_session.commit()
        _attempts(db_session, user, [(a, getattr(self, second))], base=NOW - timedelta(days=1))
        s = _summary(db_session, track, domain, user)
        assert (s.scenario_evidence_count, s.distinct_scenario_content_versions) == (2, 1)
        assert s.recent_scenario_mastery_band == getattr(self, second)

    def test_timestamp_tie_on_one_scenario_resolved_by_attempt_id(self, db_session):
        track, domain, user = _v4_world(db_session)
        a = make_scenario(db_session, domain=domain, external_id="SCN-A")
        at = NOW - timedelta(days=2)
        first = add_scenario_attempt_evidence(db_session, user=user, scenario=a,
                                              mastery_band=self.C, submitted_at=at, score_pct=0.0)
        second = add_scenario_attempt_evidence(db_session, user=user, scenario=a,
                                               mastery_band=self.S, submitted_at=at, score_pct=100.0)
        assert second.id > first.id
        assert _summary(db_session, track, domain, user).recent_scenario_mastery_band == self.S

    def test_shared_misconception_tag_counterexample_stays_below_threshold(self, db_session):
        """Gate C3-C4 counterexample. A (latest: critical) and B (latest: strong) each
        author a wrong option tagged `shared_x`. A's wrong answer activates the tag;
        B's LATER correct answer on its own shared_x step resolves it. The retired
        latest-overall rule saw only B (strong) and reached `ready`; v4 keeps A's
        critical latest state in view."""
        track, domain, user = _v4_world(db_session)
        steps = {}
        scenarios = {}
        for x in "AB":
            scenarios[x] = make_scenario(db_session, domain=domain, external_id=f"SCN-{x}")
            steps[x] = make_scenario_step(db_session, scenario=scenarios[x], options=[
                {"is_correct": True}, {"is_correct": False, "misconception_tag": "shared_x"}])
        a_at, b_at = NOW - timedelta(days=3), NOW - timedelta(days=2)
        a_att = add_scenario_attempt_evidence(db_session, user=user, scenario=scenarios["A"],
                                              mastery_band=self.C, submitted_at=a_at, score_pct=0.0)
        add_step_answered_event(db_session, user=user, scenario_attempt=a_att, step=steps["A"],
                                is_correct=False, misconception_tags=["shared_x"], occurred_at=a_at)
        b_att = add_scenario_attempt_evidence(db_session, user=user, scenario=scenarios["B"],
                                              mastery_band=self.S, submitted_at=b_at, score_pct=100.0)
        add_step_answered_event(db_session, user=user, scenario_attempt=b_att, step=steps["B"],
                                is_correct=True, occurred_at=b_at)

        row = recompute_learner_domain_state(
            db_session, user_id=user.id, track_id=track.id, domain_id=domain.id, now=NOW
        )
        assert row.unresolved_misconception_count == 0  # the tag really was cleared via B
        assert row.distinct_scenario_content_versions == 2  # sufficiency is met
        assert b_att.mastery_band == "strong"  # the newest attempt overall is B's strong
        assert row.recent_scenario_mastery_band == "critical"
        assert row.readiness_state == STATE_DEVELOPING
        assert row.reason_codes == [DOMAIN_BELOW_THRESHOLD]

    @pytest.mark.parametrize("limiting_is_newest", [False, True])
    def test_freshness_is_newest_attempt_overall_not_the_limiting_scenario(
        self, db_session, limiting_is_newest
    ):
        track, domain, user = _v4_world(db_session)
        a = make_scenario(db_session, domain=domain, external_id="SCN-A")
        b = make_scenario(db_session, domain=domain, external_id="SCN-B")
        history = [(b, self.S), (a, self.C)] if limiting_is_newest else [(a, self.C), (b, self.S)]
        attempts = _attempts(db_session, user, history)
        s = _summary(db_session, track, domain, user)
        assert s.recent_scenario_mastery_band == self.C  # A limits either way
        assert s.most_recent_evidence_at == attempts[-1].submitted_at  # newest overall

    def test_raw_and_distinct_counts_are_unchanged_by_v4(self, db_session):
        track, domain, user = _v4_world(db_session)
        a = make_scenario(db_session, domain=domain, external_id="SCN-A")
        b = make_scenario(db_session, domain=domain, external_id="SCN-B")
        _attempts(db_session, user, [(a, self.C), (a, self.S), (a, self.S), (b, self.D)])
        s = _summary(db_session, track, domain, user)
        assert (s.scenario_evidence_count, s.distinct_scenario_content_versions) == (4, 2)


class TestV3ToV4InPlaceRecompute:
    def test_v3_row_is_upgraded_in_place_to_v4_without_touching_evidence(self, db_session):
        """A row stamped v3 with the retired latest-overall band (strong: B was taken
        last) is recomputed in place: same row and identity, version 4, band now the
        weakest latest-per-scenario state (critical), evidence tables untouched."""
        track, domain, user = _v4_world(db_session)
        a = make_scenario(db_session, domain=domain, external_id="SCN-A")
        b = make_scenario(db_session, domain=domain, external_id="SCN-B")
        step = make_scenario_step(db_session, scenario=a, options=[
            {"is_correct": True}, {"is_correct": False, "misconception_tag": "tag_a"}])
        a_att, _ = _attempts(db_session, user, [(a, MasteryBand.CRITICAL), (b, MasteryBand.STRONG)])
        db_session.add(ScenarioStepAttempt(scenario_attempt_id=a_att.id, step_id=step.id, position=1,
                                           selected_option_ids=[], is_correct=False, step_credit=0.0))
        db_session.commit()
        add_step_answered_event(db_session, user=user, scenario_attempt=a_att, step=step,
                                is_correct=False, misconception_tags=["tag_a"],
                                occurred_at=a_att.submitted_at)

        legacy = LearnerDomainState(
            user_id=user.id, track_id=track.id, domain_id=domain.id,
            practice_evidence_count=MIN_PRACTICE_ITEMS_FOR_SUFFICIENCY,
            scenario_evidence_count=2, distinct_scenario_content_versions=2,
            recent_practice_mastery_band="strong", recent_scenario_mastery_band="strong",
            most_recent_evidence_at=NOW, unresolved_misconception_count=1,
            readiness_state=STATE_DEVELOPING, reason_codes=["REPEATED_MISCONCEPTION"],
            calculated_at=NOW - timedelta(days=1), projection_version=3,
        )
        db_session.add(legacy)
        db_session.commit()
        legacy_id = legacy.id

        models = (ExamAttempt, AttemptItem, AttemptDomainScore, ScenarioAttempt,
                  ScenarioStepAttempt, ScenarioEvent)
        db_session.expire_all()
        before = {m.__name__: snapshot_table(db_session, m) for m in models}
        assert all(before[m.__name__] for m in models)  # every evidence table populated

        row = recompute_learner_domain_state(
            db_session, user_id=user.id, track_id=track.id, domain_id=domain.id, now=NOW
        )

        assert row.id == legacy_id
        assert (row.user_id, row.track_id, row.domain_id) == (user.id, track.id, domain.id)
        assert row.projection_version == PROJECTION_VERSION == 4
        assert row.recent_scenario_mastery_band == "critical"
        assert (row.scenario_evidence_count, row.distinct_scenario_content_versions) == (2, 2)
        assert row.readiness_state == STATE_DEVELOPING
        assert row.reason_codes == [DOMAIN_BELOW_THRESHOLD, REPEATED_MISCONCEPTION]
        assert len(db_session.scalars(select(LearnerDomainState)).all()) == 1

        db_session.expire_all()
        after = {m.__name__: snapshot_table(db_session, m) for m in models}
        assert after == before
