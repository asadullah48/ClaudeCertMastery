"""Gate C3-B2: practice evidence policy v2.

Pure-helper tests (readiness_policy: attempt_qualifies_for_practice,
select_practice_window, practice_band_for_window) plus service-level tests
(learner_readiness: build_domain_evidence_summary / recompute_learner_domain_state)
against a throwaway SQLite database, same fixture pattern as test_learner_readiness.py.

v2 in one paragraph: a SUBMITTED exam attempt qualifies as readiness evidence only if
>= MIN_ATTEMPT_COMPLETION_RATIO of ALL its items (whole exam, every domain) were
answered (non-empty selected_option_ids). Only qualifying attempts feed
practice_evidence_count, the practice band, and practice freshness. The band pools
whole qualifying attempts newest-first until >= PRACTICE_BAND_MIN_ITEMS domain items.
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy import create_engine, func, select
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
from app.services.learner_readiness import (  # noqa: E402
    PROJECTION_VERSION,
    build_domain_evidence_summary,
    recompute_learner_domain_state,
)
from app.services.readiness_policy import (  # noqa: E402
    INSUFFICIENT_PRACTICE_EVIDENCE,
    MIN_ATTEMPT_COMPLETION_RATIO,
    MIN_PRACTICE_ITEMS_FOR_SUFFICIENCY,
    MIN_SCENARIO_ATTEMPTS_FOR_SUFFICIENCY,
    NO_APPLIED_SCENARIO_EVIDENCE,
    PRACTICE_BAND_MIN_ITEMS,
    STALENESS_THRESHOLD_DAYS,
    STATE_INSUFFICIENT_EVIDENCE,
    DomainEvidenceSummary,
    PracticeAttemptGroup,
    attempt_qualifies_for_practice,
    classify_domain_readiness,
    practice_band_for_window,
    select_practice_window,
)
from app.services.scoring import MasteryBand  # noqa: E402

NOW = datetime(2026, 1, 15, 12, 0, 0, tzinfo=timezone.utc)


# --- constants are the approved v2 values -------------------------------------------


def test_v2_policy_constants_are_the_approved_values():
    assert MIN_ATTEMPT_COMPLETION_RATIO == 0.80
    assert PRACTICE_BAND_MIN_ITEMS == 20
    assert MIN_PRACTICE_ITEMS_FOR_SUFFICIENCY == 20
    assert MIN_SCENARIO_ATTEMPTS_FOR_SUFFICIENCY == 2  # unchanged by B2
    assert STALENESS_THRESHOLD_DAYS == 90  # unchanged by B2
    # Bumped to 3 by Gate C3-C2 (scenario-diversity semantics); the practice policy
    # these tests pin is unchanged since v2.
    assert PROJECTION_VERSION == 3


# =====================================================================================
# Pure helpers
# =====================================================================================


def g(attempt_id, *, items, correct, at=NOW):
    return PracticeAttemptGroup(
        attempt_id=attempt_id, submitted_at=at, item_count=items, correct_count=correct
    )


class TestCompletionQualification:
    def test_a_fully_complete_attempt_qualifies(self):
        assert attempt_qualifies_for_practice(60, 60)

    @pytest.mark.parametrize("answered,total", [(8, 10), (48, 60), (12, 15), (24, 30), (4, 5)])
    def test_b_exactly_eighty_percent_qualifies(self, answered, total):
        # 12/15 is the classic float trap (0.7999999...); exact Fraction comparison.
        assert attempt_qualifies_for_practice(answered, total)

    @pytest.mark.parametrize("answered,total", [(79, 100), (47, 60), (7, 10)])
    def test_c_below_eighty_percent_does_not_qualify(self, answered, total):
        assert not attempt_qualifies_for_practice(answered, total)

    def test_d_blank_attempt_does_not_qualify(self):
        assert not attempt_qualifies_for_practice(0, 60)

    def test_attempt_with_no_items_does_not_qualify(self):
        assert not attempt_qualifies_for_practice(0, 0)


class TestPracticeWindow:
    def test_a_newest_attempts_consumed_first(self):
        old = g(1, items=20, correct=0, at=NOW - timedelta(days=2))
        new = g(2, items=20, correct=20, at=NOW)
        assert select_practice_window([old, new]) == [new]

    def test_b_whole_attempts_retained_never_split(self):
        window = select_practice_window([g(1, items=13, correct=5, at=NOW - timedelta(days=1)),
                                         g(2, items=13, correct=13, at=NOW)])
        assert [x.item_count for x in window] == [13, 13]  # 26, not truncated to 20

    def test_c_stops_once_accumulated_items_reach_minimum(self):
        groups = [g(i, items=10, correct=10, at=NOW - timedelta(days=i)) for i in range(1, 6)]
        window = select_practice_window(groups)
        assert [x.attempt_id for x in window] == [1, 2]  # 10 + 10 = 20 -> stop
        assert sum(x.item_count for x in window) == PRACTICE_BAND_MIN_ITEMS

    def test_d_final_attempt_may_push_sample_above_minimum(self):
        groups = [g(1, items=15, correct=15, at=NOW), g(2, items=8, correct=0, at=NOW - timedelta(days=1)),
                  g(3, items=8, correct=0, at=NOW - timedelta(days=2))]
        window = select_practice_window(groups)
        assert sum(x.item_count for x in window) == 23
        assert [x.attempt_id for x in window] == [1, 2]

    def test_f_same_timestamp_tie_breaks_on_higher_attempt_id_first(self):
        a = g(7, items=20, correct=0, at=NOW)
        b = g(9, items=20, correct=20, at=NOW)
        assert select_practice_window([a, b]) == [b]
        assert select_practice_window([b, a]) == [b]  # input order irrelevant

    def test_g_no_qualifying_evidence_produces_none(self):
        assert select_practice_window([]) == []
        assert practice_band_for_window([]) is None

    def test_h_sparse_window_still_yields_descriptive_band(self):
        window = select_practice_window([g(1, items=3, correct=3), g(2, items=2, correct=2,
                                                                     at=NOW - timedelta(days=1))])
        assert sum(x.item_count for x in window) == 5  # < 20: everything available
        assert practice_band_for_window(window) == MasteryBand.STRONG

    def test_band_uses_existing_threshold_table_on_pooled_window(self):
        # 14/20 = 70% -> proficient (boundary of the existing table, no new thresholds)
        assert practice_band_for_window([g(1, items=20, correct=14)]) == MasteryBand.PROFICIENT
        assert practice_band_for_window([g(1, items=20, correct=13)]) == MasteryBand.DEVELOPING


class TestPracticeSufficiencyBoundaries:
    """MIN_PRACTICE_ITEMS_FOR_SUFFICIENCY = 20; scenario sufficiency stays independent."""

    def summary(self, practice, scenario=MIN_SCENARIO_ATTEMPTS_FOR_SUFFICIENCY):
        return DomainEvidenceSummary(
            practice_evidence_count=practice, scenario_evidence_count=scenario,
            distinct_scenario_content_versions=scenario,
            recent_practice_mastery_band=MasteryBand.STRONG if practice else None,
            recent_scenario_mastery_band=MasteryBand.STRONG,
            most_recent_evidence_at=NOW, unresolved_misconception_count=0, now=NOW,
        )

    @pytest.mark.parametrize("practice", [0, 19])
    def test_below_twenty_is_practice_insufficient(self, practice):
        result = classify_domain_readiness(self.summary(practice))
        assert result.state == STATE_INSUFFICIENT_EVIDENCE
        assert INSUFFICIENT_PRACTICE_EVIDENCE in result.reason_codes

    @pytest.mark.parametrize("practice", [20, 21, 250])
    def test_twenty_or_more_is_practice_sufficient(self, practice):
        result = classify_domain_readiness(self.summary(practice))
        assert INSUFFICIENT_PRACTICE_EVIDENCE not in result.reason_codes
        assert result.state != STATE_INSUFFICIENT_EVIDENCE

    def test_practice_sufficient_but_one_scenario_stays_insufficient(self):
        result = classify_domain_readiness(self.summary(40, scenario=1))
        assert result.state == STATE_INSUFFICIENT_EVIDENCE
        assert result.reason_codes == [NO_APPLIED_SCENARIO_EVIDENCE]


class TestFounderShapedHistoryPure:
    """Faithful fixture of production founder PTE evidence (Gate C3-B1 export):
    per attempt, chronological: (whole-exam answered, whole-exam total, PTE items,
    PTE correct). v1 derived PTE's band from #52 alone (0/1 -> critical)."""

    PTE = [  # attempt id, answered, total, pte_items, pte_correct
        (1, 1, 60, 8, 0), (2, 1, 60, 8, 0), (3, 10, 10, 1, 1), (5, 1, 30, 4, 0),
        (6, 3, 60, 8, 0), (9, 30, 30, 4, 2), (12, 1, 60, 8, 0), (14, 1, 60, 8, 0),
        (17, 60, 60, 8, 6), (18, 10, 10, 1, 1), (20, 10, 10, 1, 1), (23, 0, 60, 8, 0),
        (26, 1, 60, 8, 1), (27, 60, 60, 8, 6), (29, 10, 10, 1, 1), (30, 60, 60, 8, 3),
        (31, 30, 60, 8, 4), (32, 10, 10, 1, 1), (33, 1, 60, 8, 0), (35, 60, 60, 8, 8),
        (40, 1, 10, 1, 0), (43, 1, 60, 8, 0), (44, 60, 60, 8, 4), (46, 1, 10, 1, 0),
        (45, 60, 60, 8, 5), (49, 10, 10, 1, 1), (50, 59, 60, 8, 8), (52, 10, 10, 1, 0),
    ]

    def test_v2_excludes_test_submissions_and_pools_a_twenty_plus_window(self):
        groups = [
            g(aid, items=n, correct=k, at=NOW + timedelta(minutes=order))
            for order, (aid, answered, total, n, k) in enumerate(self.PTE)
            if attempt_qualifies_for_practice(answered, total)
        ]
        window = select_practice_window(groups)
        assert sum(x.item_count for x in groups) == 67  # was 153 incl. test submissions
        assert [x.attempt_id for x in window] == [52, 50, 49, 45, 44]
        assert sum(x.item_count for x in window) == 26
        assert sum(x.correct_count for x in window) == 18  # 69.2%
        assert practice_band_for_window(window) == MasteryBand.DEVELOPING  # not #52's critical


# =====================================================================================
# Service level (SQLite)
# =====================================================================================


@pytest.fixture
def db(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'practice_v2.db'}")
    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)
    Base.metadata.create_all(engine)
    session = Session()
    yield session
    session.close()


@pytest.fixture
def world(db):
    track = Track(code="CCAO-F", name="t", item_count=60, duration_minutes=120,
                  pass_scaled_score=720, pass_raw_threshold=0.70, price_usd=99.0,
                  validity_months=12, is_seeded=True)
    db.add(track)
    db.flush()
    pte = Domain(track_id=track.id, code="PTE", name="PTE", description="", weight_bps=5000, position=1)
    oev = Domain(track_id=track.id, code="OEV", name="OEV", description="", weight_bps=5000, position=2)
    user = User(email="learner@example.com", display_name="L")
    db.add_all([pte, oev, user])
    db.commit()
    return {"track": track, "pte": pte, "oev": oev, "user": user}


def items(domain, *, right=0, wrong=0, blank=0):
    return [(domain, "right")] * right + [(domain, "wrong")] * wrong + [(domain, "blank")] * blank


_q_counter = [0]


def add_exam(db, world, spec, *, at=NOW, status=AttemptStatus.SUBMITTED):
    """Persist an exam the way routers/attempts.py::submit leaves it: right/wrong are
    real selections, blank is `[]` with is_correct False."""
    attempt = ExamAttempt(user_id=world["user"].id, track_id=world["track"].id,
                          mode=AttemptMode.EXAM, status=status, seed=1,
                          submitted_at=at if status == AttemptStatus.SUBMITTED else None)
    db.add(attempt)
    db.flush()
    for pos, (domain, outcome) in enumerate(spec, start=1):
        _q_counter[0] += 1
        q = Question(domain_id=domain.id, external_id=f"Q{_q_counter[0]}", stem="s",
                     question_type=QuestionType.MCQ, difficulty=2, static_explanation="e", is_active=True)
        db.add(q)
        db.flush()
        right = AnswerOption(question_id=q.id, label="A", text="r", is_correct=True, position=1)
        wrong = AnswerOption(question_id=q.id, label="B", text="w", is_correct=False, position=2)
        db.add_all([right, wrong])
        db.flush()
        selected = {"right": [right.id], "wrong": [wrong.id], "blank": []}[outcome]
        db.add(AttemptItem(attempt_id=attempt.id, question_id=q.id, domain_id=domain.id,
                           position=pos, selected_option_ids=selected,
                           is_correct=(outcome == "right")))
    db.commit()
    return attempt


def summary_for(db, world, domain="pte"):
    return build_domain_evidence_summary(db, user_id=world["user"].id, track_id=world["track"].id,
                                         domain_id=world[domain].id, now=NOW)


class TestServiceCompletionRule:
    def test_e_in_progress_attempt_never_qualifies_even_fully_answered(self, db, world):
        add_exam(db, world, items(world["pte"], right=25), status=AttemptStatus.IN_PROGRESS)
        s = summary_for(db, world)
        assert (s.practice_evidence_count, s.recent_practice_mastery_band) == (0, None)
        assert s.most_recent_evidence_at is None

    def test_f_completion_is_whole_exam_not_per_domain(self, db, world):
        # PTE slice is 0% answered, but the whole exam is 8/10 = 80% -> qualifies.
        add_exam(db, world, items(world["pte"], blank=2) + items(world["oev"], right=8))
        assert summary_for(db, world).practice_evidence_count == 2
        # PTE slice 100% answered, whole exam 5/10 = 50% -> does NOT qualify.
        add_exam(db, world, items(world["pte"], right=5) + items(world["oev"], blank=5),
                 at=NOW + timedelta(hours=1))
        assert summary_for(db, world).practice_evidence_count == 2

    def test_g_unanswered_item_in_qualifying_attempt_stays_in_denominator(self, db, world):
        # 20 items, 16 answered (80%) -> qualifies; 4 blanks count as incorrect.
        add_exam(db, world, items(world["pte"], right=16, blank=4))
        s = summary_for(db, world)
        assert s.practice_evidence_count == 20
        assert s.recent_practice_mastery_band == MasteryBand.PROFICIENT  # 16/20 = 80%, not 100%

    def test_e_window_skips_attempts_below_completion_threshold(self, db, world):
        add_exam(db, world, items(world["pte"], right=20), at=NOW - timedelta(days=1))
        add_exam(db, world, items(world["pte"], right=1, blank=19), at=NOW)  # 5%: skipped
        s = summary_for(db, world)
        assert s.practice_evidence_count == 20
        assert s.recent_practice_mastery_band == MasteryBand.STRONG

    def test_window_h_one_to_nineteen_items_descriptive_band_but_insufficient(self, db, world):
        add_exam(db, world, items(world["pte"], right=7, wrong=3))
        s = summary_for(db, world)
        assert s.practice_evidence_count == 10
        assert s.recent_practice_mastery_band == MasteryBand.PROFICIENT
        assert classify_domain_readiness(s).state == STATE_INSUFFICIENT_EVIDENCE


class TestHistoricalDefectRegressions:
    """The three B1 findings, as regressions."""

    def _strong_history(self, db, world, right, wrong):
        for day in range(5, 0, -1):  # 5 exams x 20 PTE items = 100 observations
            add_exam(db, world, items(world["pte"], right=right, wrong=wrong),
                     at=NOW - timedelta(days=day))

    def test_1_strong_history_plus_newest_one_question_wrong_test_submission(self, db, world):
        self._strong_history(db, world, right=20, wrong=0)
        add_exam(db, world, items(world["pte"], wrong=1) + items(world["oev"], blank=9), at=NOW)
        s = summary_for(db, world)
        assert s.practice_evidence_count == 100  # test submission excluded
        assert s.recent_practice_mastery_band == MasteryBand.STRONG
        assert s.most_recent_evidence_at == NOW - timedelta(days=1)  # freshness not refreshed

    def test_2_weak_history_plus_newest_one_question_correct_test_submission(self, db, world):
        self._strong_history(db, world, right=4, wrong=16)
        add_exam(db, world, items(world["pte"], right=1) + items(world["oev"], blank=9), at=NOW)
        s = summary_for(db, world)
        assert s.practice_evidence_count == 100
        assert s.recent_practice_mastery_band == MasteryBand.CRITICAL  # no jump to strong
        assert s.most_recent_evidence_at == NOW - timedelta(days=1)

    def test_3_legitimate_newest_exam_with_one_domain_item_cannot_decide_band(self, db, world):
        self._strong_history(db, world, right=20, wrong=0)
        # Fully answered exam; only 1 PTE item, and it is wrong.
        add_exam(db, world, items(world["pte"], wrong=1) + items(world["oev"], right=9), at=NOW)
        s = summary_for(db, world)
        assert s.practice_evidence_count == 101  # it qualifies, whole domain group included
        assert s.most_recent_evidence_at == NOW  # legitimately fresh
        # window: 1 (newest) + 20 (previous) = 21 items, 20 correct -> strong, not critical
        assert s.recent_practice_mastery_band == MasteryBand.STRONG


class TestImprovementAndRegression:
    def test_recent_twenty_strong_after_weak_history_moves_band_up(self, db, world):
        for day in range(6, 2, -1):
            add_exam(db, world, items(world["pte"], right=6, wrong=14), at=NOW - timedelta(days=day))
        assert summary_for(db, world).recent_practice_mastery_band == MasteryBand.CRITICAL
        add_exam(db, world, items(world["pte"], right=10), at=NOW - timedelta(days=2))
        add_exam(db, world, items(world["pte"], right=9, wrong=1), at=NOW - timedelta(days=1))
        assert summary_for(db, world).recent_practice_mastery_band == MasteryBand.STRONG

    def test_recent_twenty_weak_after_strong_history_moves_band_down(self, db, world):
        for day in range(6, 2, -1):
            add_exam(db, world, items(world["pte"], right=19, wrong=1), at=NOW - timedelta(days=day))
        assert summary_for(db, world).recent_practice_mastery_band == MasteryBand.STRONG
        add_exam(db, world, items(world["pte"], right=4, wrong=6), at=NOW - timedelta(days=2))
        add_exam(db, world, items(world["pte"], right=3, wrong=7), at=NOW - timedelta(days=1))
        assert summary_for(db, world).recent_practice_mastery_band == MasteryBand.CRITICAL


def _snapshot(db, model):
    cols = [c.name for c in model.__table__.columns]
    return sorted(tuple(getattr(r, c) for c in cols) for r in db.scalars(select(model)).all())


EVIDENCE_MODELS = (ExamAttempt, AttemptItem, AttemptDomainScore, ScenarioAttempt, ScenarioEvent)


def _add_scenario_with_misconception(db, world):
    """One submitted PTE scenario attempt whose only step_answered event carries an
    authored misconception tag -> 1 unresolved misconception, scenario band critical."""
    scenario = Scenario(domain_id=world["pte"].id, external_id="SCN-1", title="t", setup_text="s",
                        difficulty=2, is_active=True, content_version=1)
    db.add(scenario)
    db.flush()
    step = ScenarioStep(scenario_id=scenario.id, position=1, prompt_text="p", step_type="mcq")
    db.add(step)
    db.flush()
    db.add(ScenarioStepOption(step_id=step.id, label="A", text="t", is_correct=False, position=1,
                              rationale="r", misconception_tag="tag_x"))
    attempt = ScenarioAttempt(user_id=world["user"].id, scenario_id=scenario.id, status="submitted",
                              scenario_content_version=1, submitted_at=NOW - timedelta(days=3),
                              score_pct=0.0, mastery_band="critical")
    db.add(attempt)
    db.flush()
    db.add(ScenarioEvent(scenario_attempt_id=attempt.id, user_id=world["user"].id,
                         event_type="step_answered", step_id=step.id, occurred_at=NOW - timedelta(days=3),
                         payload={"is_correct": False, "misconception_tags": ["tag_x"]}))
    db.commit()


class TestRecomputeIntegration:
    def _recompute(self, db, world):
        return recompute_learner_domain_state(db, user_id=world["user"].id, track_id=world["track"].id,
                                              domain_id=world["pte"].id, now=NOW)

    def test_v2_end_to_end(self, db, world):
        add_exam(db, world, items(world["pte"], right=12, wrong=3), at=NOW - timedelta(days=4))
        add_exam(db, world, items(world["pte"], right=8, wrong=2), at=NOW - timedelta(days=2))
        add_exam(db, world, items(world["pte"], right=1) + items(world["oev"], blank=20),
                 at=NOW - timedelta(days=1))  # incomplete: ignored
        _add_scenario_with_misconception(db, world)
        before = {m.__name__: _snapshot(db, m) for m in EVIDENCE_MODELS}

        row = self._recompute(db, world)

        assert row.projection_version == PROJECTION_VERSION
        assert row.practice_evidence_count == 25  # 15 + 10, incomplete excluded
        assert row.recent_practice_mastery_band == "proficient"  # (12+8)/25 = 80%
        # newest qualifying practice (day -2) beats scenario (day -3); incomplete (day -1) ignored
        assert row.most_recent_evidence_at.replace(tzinfo=timezone.utc) == NOW - timedelta(days=2)
        # scenario + misconception semantics unchanged by v2
        assert (row.scenario_evidence_count, row.distinct_scenario_content_versions) == (1, 1)
        assert row.recent_scenario_mastery_band == "critical"
        assert row.unresolved_misconception_count == 1
        assert row.readiness_state == STATE_INSUFFICIENT_EVIDENCE
        assert row.reason_codes == [NO_APPLIED_SCENARIO_EVIDENCE]
        # source evidence untouched
        assert {m.__name__: _snapshot(db, m) for m in EVIDENCE_MODELS} == before

    def test_recompute_is_idempotent(self, db, world):
        add_exam(db, world, items(world["pte"], right=15, wrong=6))
        first = self._recompute(db, world)
        fields = lambda r: (r.practice_evidence_count, r.recent_practice_mastery_band,  # noqa: E731
                            r.most_recent_evidence_at, r.readiness_state, list(r.reason_codes),
                            r.projection_version)
        snap = fields(first)
        second = self._recompute(db, world)
        assert fields(second) == snap
        assert db.scalar(select(func.count()).select_from(LearnerDomainState)) == 1

    def test_v1_row_recomputed_in_place_to_v2_without_duplicate(self, db, world):
        add_exam(db, world, items(world["pte"], right=20))
        legacy = LearnerDomainState(
            user_id=world["user"].id, track_id=world["track"].id, domain_id=world["pte"].id,
            practice_evidence_count=1, recent_practice_mastery_band="critical",
            readiness_state=STATE_INSUFFICIENT_EVIDENCE, reason_codes=["NO_APPLIED_SCENARIO_EVIDENCE"],
            calculated_at=NOW - timedelta(days=1), projection_version=1,
        )
        db.add(legacy)
        db.commit()
        legacy_id = legacy.id
        before = {m.__name__: _snapshot(db, m) for m in EVIDENCE_MODELS}

        row = self._recompute(db, world)

        assert row.id == legacy_id
        assert (row.user_id, row.track_id, row.domain_id) == (
            world["user"].id, world["track"].id, world["pte"].id)
        assert row.projection_version == PROJECTION_VERSION
        assert (row.practice_evidence_count, row.recent_practice_mastery_band) == (20, "strong")
        assert db.scalar(select(func.count()).select_from(LearnerDomainState)) == 1
        assert {m.__name__: _snapshot(db, m) for m in EVIDENCE_MODELS} == before
