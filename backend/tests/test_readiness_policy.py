"""KSOR Slice 2: pure deterministic readiness classifier tests.

Pure-function tests, no database -- mirrors test_scenario_grading.py's and
test_scoring.py's table-driven, class-grouped style exactly. Every test in this file
constructs a synthetic DomainEvidenceSummary by hand; none of it touches the ORM,
a live session, or any evidence table.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.services.readiness_policy import (
    DOMAIN_BELOW_THRESHOLD,
    INSUFFICIENT_DOMAIN_COVERAGE,
    INSUFFICIENT_PRACTICE_EVIDENCE,
    MIN_PRACTICE_ITEMS_FOR_SUFFICIENCY,
    MIN_SCENARIO_ATTEMPTS_FOR_SUFFICIENCY,
    NO_APPLIED_SCENARIO_EVIDENCE,
    NO_EVIDENCE,
    READINESS_STATE_ORDER,
    REPEATED_MISCONCEPTION,
    REPEATED_SCENARIO_NOT_DIVERSE_EVIDENCE,
    STALE_EVIDENCE,
    STALENESS_THRESHOLD_DAYS,
    STATE_APPROACHING_READY,
    STATE_DEVELOPING,
    STATE_INSUFFICIENT_EVIDENCE,
    STATE_READY,
    DomainEvidenceSummary,
    ReadinessAssessment,
    ReadinessPolicyError,
    aggregate_track_readiness,
    classify_domain_readiness,
)
from app.services.scoring import MasteryBand

NOW = datetime(2026, 1, 15, 12, 0, 0, tzinfo=timezone.utc)


def make_summary(**overrides) -> DomainEvidenceSummary:
    """A fully-qualifying baseline (would classify as `ready`) with named
    overrides -- every test below breaks exactly one field off this baseline so
    each test isolates a single classifier rule."""
    defaults = dict(
        practice_evidence_count=MIN_PRACTICE_ITEMS_FOR_SUFFICIENCY,
        scenario_evidence_count=MIN_SCENARIO_ATTEMPTS_FOR_SUFFICIENCY,
        distinct_scenario_content_versions=MIN_SCENARIO_ATTEMPTS_FOR_SUFFICIENCY,
        recent_practice_mastery_band=MasteryBand.STRONG,
        recent_scenario_mastery_band=MasteryBand.STRONG,
        most_recent_evidence_at=NOW,
        unresolved_misconception_count=0,
        now=NOW,
    )
    defaults.update(overrides)
    return DomainEvidenceSummary(**defaults)


# --- Empty / insufficient evidence -------------------------------------------------


class TestInsufficientEvidence:
    def test_no_evidence_at_all(self):
        r = classify_domain_readiness(
            make_summary(
                practice_evidence_count=0,
                scenario_evidence_count=0,
                distinct_scenario_content_versions=0,
                recent_practice_mastery_band=None,
                recent_scenario_mastery_band=None,
                most_recent_evidence_at=None,
            )
        )
        assert r.state == STATE_INSUFFICIENT_EVIDENCE
        assert r.evidence_sufficient is False
        assert NO_EVIDENCE in r.reason_codes
        assert INSUFFICIENT_PRACTICE_EVIDENCE in r.reason_codes
        assert NO_APPLIED_SCENARIO_EVIDENCE in r.reason_codes

    def test_practice_below_threshold(self):
        r = classify_domain_readiness(
            make_summary(practice_evidence_count=MIN_PRACTICE_ITEMS_FOR_SUFFICIENCY - 1)
        )
        assert r.state == STATE_INSUFFICIENT_EVIDENCE
        assert r.reason_codes == [INSUFFICIENT_PRACTICE_EVIDENCE]

    def test_scenario_below_threshold(self):
        r = classify_domain_readiness(
            make_summary(
                scenario_evidence_count=MIN_SCENARIO_ATTEMPTS_FOR_SUFFICIENCY - 1,
                distinct_scenario_content_versions=MIN_SCENARIO_ATTEMPTS_FOR_SUFFICIENCY - 1,
            )
        )
        assert r.state == STATE_INSUFFICIENT_EVIDENCE
        assert r.reason_codes == [NO_APPLIED_SCENARIO_EVIDENCE]

    def test_practice_sufficient_but_no_scenario(self):
        r = classify_domain_readiness(
            make_summary(scenario_evidence_count=0, distinct_scenario_content_versions=0)
        )
        assert r.state == STATE_INSUFFICIENT_EVIDENCE
        assert r.reason_codes == [NO_APPLIED_SCENARIO_EVIDENCE]

    def test_one_hundred_percent_single_scenario_is_not_ready(self):
        """The audit's central invariant, asserted by name: production's real
        CCAO-F-PTE-SCN-001 attempt (one strong scenario, zero practice evidence)
        must never classify above insufficient_evidence, regardless of score."""
        r = classify_domain_readiness(
            make_summary(
                practice_evidence_count=0,
                scenario_evidence_count=1,
                distinct_scenario_content_versions=1,
                recent_scenario_mastery_band=MasteryBand.STRONG,
            )
        )
        assert r.state == STATE_INSUFFICIENT_EVIDENCE
        assert r.evidence_sufficient is False
        assert INSUFFICIENT_PRACTICE_EVIDENCE in r.reason_codes
        assert NO_APPLIED_SCENARIO_EVIDENCE in r.reason_codes

    def test_perfect_practice_plus_one_scenario_is_insufficient(self):
        r = classify_domain_readiness(
            make_summary(scenario_evidence_count=1, distinct_scenario_content_versions=1)
        )
        assert r.state == STATE_INSUFFICIENT_EVIDENCE
        assert r.reason_codes == [NO_APPLIED_SCENARIO_EVIDENCE]

    def test_no_reason_code_duplicated(self):
        r = classify_domain_readiness(
            make_summary(practice_evidence_count=0, scenario_evidence_count=0)
        )
        assert len(r.reason_codes) == len(set(r.reason_codes))


# --- Developing ----------------------------------------------------------------


class TestDeveloping:
    def test_weak_practice_band_blocks_ready(self):
        r = classify_domain_readiness(
            make_summary(recent_practice_mastery_band=MasteryBand.DEVELOPING)
        )
        assert r.state == STATE_DEVELOPING
        assert r.evidence_sufficient is True
        assert DOMAIN_BELOW_THRESHOLD in r.reason_codes

    def test_critical_scenario_band_blocks_ready(self):
        r = classify_domain_readiness(
            make_summary(recent_scenario_mastery_band=MasteryBand.CRITICAL)
        )
        assert r.state == STATE_DEVELOPING
        assert DOMAIN_BELOW_THRESHOLD in r.reason_codes

    def test_unresolved_misconception_blocks_ready(self):
        r = classify_domain_readiness(make_summary(unresolved_misconception_count=1))
        assert r.state == STATE_DEVELOPING
        assert r.reason_codes == [REPEATED_MISCONCEPTION]

    def test_repeated_single_scenario_is_insufficient_not_developing(self):
        """Projection v3 (Gate C3-C2): five raw attempts of ONE scenario are one
        independent observation. Sufficiency is judged on distinct scenarios, so this
        stays insufficient_evidence, and the repeat cap is named explicitly because
        the raw count alone would have met the minimum."""
        r = classify_domain_readiness(
            make_summary(
                scenario_evidence_count=5, distinct_scenario_content_versions=1
            )
        )
        assert r.state == STATE_INSUFFICIENT_EVIDENCE
        assert r.evidence_sufficient is False
        assert r.reason_codes == [
            NO_APPLIED_SCENARIO_EVIDENCE,
            REPEATED_SCENARIO_NOT_DIVERSE_EVIDENCE,
        ]

    def test_multiple_developing_reasons_all_reported(self):
        r = classify_domain_readiness(
            make_summary(
                recent_practice_mastery_band=MasteryBand.CRITICAL,
                unresolved_misconception_count=2,
            )
        )
        assert r.state == STATE_DEVELOPING
        assert r.reason_codes == [DOMAIN_BELOW_THRESHOLD, REPEATED_MISCONCEPTION]


# --- Approaching ready / recency -------------------------------------------------


class TestApproachingReadyAndRecency:
    def test_recent_evidence_reaches_ready(self):
        r = classify_domain_readiness(make_summary(most_recent_evidence_at=NOW))
        assert r.state == STATE_READY

    def test_exactly_ninety_days_is_not_stale(self):
        r = classify_domain_readiness(
            make_summary(
                most_recent_evidence_at=NOW - timedelta(days=STALENESS_THRESHOLD_DAYS)
            )
        )
        assert r.state == STATE_READY

    def test_just_beyond_ninety_days_is_stale(self):
        r = classify_domain_readiness(
            make_summary(
                most_recent_evidence_at=NOW
                - timedelta(days=STALENESS_THRESHOLD_DAYS, seconds=1)
            )
        )
        assert r.state == STATE_APPROACHING_READY
        assert r.reason_codes == [STALE_EVIDENCE]

    def test_missing_timestamp_is_not_treated_as_recent(self):
        r = classify_domain_readiness(make_summary(most_recent_evidence_at=None))
        assert r.state == STATE_APPROACHING_READY
        assert r.reason_codes == [STALE_EVIDENCE]

    def test_stale_evidence_does_not_fabricate_insufficiency(self):
        r = classify_domain_readiness(
            make_summary(
                most_recent_evidence_at=NOW
                - timedelta(days=STALENESS_THRESHOLD_DAYS + 1)
            )
        )
        # Staleness demotes state; it must never fabricate insufficiency over
        # evidence that is real, just old.
        assert r.state == STATE_APPROACHING_READY
        assert r.evidence_sufficient is True


# --- Ready -----------------------------------------------------------------------


class TestReady:
    def test_sufficient_diverse_recent_evidence_reaches_ready(self):
        r = classify_domain_readiness(make_summary())
        assert r.state == STATE_READY
        assert r.evidence_sufficient is True
        assert r.reason_codes == []

    @pytest.mark.parametrize(
        "override,expected_state",
        [
            ({"practice_evidence_count": MIN_PRACTICE_ITEMS_FOR_SUFFICIENCY - 1}, STATE_INSUFFICIENT_EVIDENCE),
            ({"scenario_evidence_count": MIN_SCENARIO_ATTEMPTS_FOR_SUFFICIENCY - 1,
              "distinct_scenario_content_versions": MIN_SCENARIO_ATTEMPTS_FOR_SUFFICIENCY - 1},
             STATE_INSUFFICIENT_EVIDENCE),
            ({"recent_practice_mastery_band": MasteryBand.DEVELOPING}, STATE_DEVELOPING),
            ({"recent_scenario_mastery_band": MasteryBand.CRITICAL}, STATE_DEVELOPING),
            ({"unresolved_misconception_count": 1}, STATE_DEVELOPING),
            ({"distinct_scenario_content_versions": 1}, STATE_INSUFFICIENT_EVIDENCE),
            ({"most_recent_evidence_at": NOW - timedelta(days=STALENESS_THRESHOLD_DAYS + 1)},
             STATE_APPROACHING_READY),
        ],
    )
    def test_breaking_one_prerequisite_prevents_ready(self, override, expected_state):
        r = classify_domain_readiness(make_summary(**override))
        assert r.state != STATE_READY
        assert r.state == expected_state


# --- Misconceptions ----------------------------------------------------------------


class TestMisconceptions:
    def test_zero_unresolved_does_not_block(self):
        r = classify_domain_readiness(make_summary(unresolved_misconception_count=0))
        assert r.state == STATE_READY

    def test_one_unresolved_blocks(self):
        r = classify_domain_readiness(make_summary(unresolved_misconception_count=1))
        assert r.state == STATE_DEVELOPING
        assert REPEATED_MISCONCEPTION in r.reason_codes

    def test_multiple_unresolved_blocks(self):
        r = classify_domain_readiness(make_summary(unresolved_misconception_count=5))
        assert r.state == STATE_DEVELOPING
        assert REPEATED_MISCONCEPTION in r.reason_codes

    def test_otherwise_ready_evidence_with_unresolved_misconception(self):
        r = classify_domain_readiness(
            make_summary(
                practice_evidence_count=50,
                scenario_evidence_count=10,
                distinct_scenario_content_versions=10,
                unresolved_misconception_count=1,
            )
        )
        assert r.state == STATE_DEVELOPING
        assert r.reason_codes == [REPEATED_MISCONCEPTION]


# --- Determinism ---------------------------------------------------------------


class TestDeterminism:
    def test_same_input_same_reference_time_produces_identical_output(self):
        s = make_summary(unresolved_misconception_count=1, distinct_scenario_content_versions=1)
        r1 = classify_domain_readiness(s)
        r2 = classify_domain_readiness(s)
        assert r1 == r2
        assert r1.reason_codes == r2.reason_codes  # order, not just set membership

    def test_repeated_calls_across_full_matrix_are_stable(self):
        summaries = [
            make_summary(),
            make_summary(practice_evidence_count=0, scenario_evidence_count=0),
            make_summary(unresolved_misconception_count=3),
            make_summary(most_recent_evidence_at=None),
        ]
        for s in summaries:
            first = classify_domain_readiness(s)
            for _ in range(5):
                assert classify_domain_readiness(s) == first


# --- Isolation: no DB, no network, no provider credentials -----------------------


class TestIsolation:
    def test_readiness_policy_module_has_no_forbidden_import(self):
        import ast
        import inspect

        from app.services import readiness_policy as policy_module

        tree = ast.parse(inspect.getsource(policy_module))
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
        for forbidden in ("anthropic", "zia", "mcp", "sqlalchemy", "app.database"):
            assert not any(forbidden in name for name in imported_names), (
                f"readiness_policy.py must not import anything referencing {forbidden!r}"
            )

    def test_classifier_requires_no_arguments_beyond_the_pure_summary(self):
        import inspect

        sig = inspect.signature(classify_domain_readiness)
        assert list(sig.parameters) == ["summary"]


# --- Production-shaped regression ------------------------------------------------


class TestProductionShapedRegression:
    def test_synthetic_ccao_f_pte_scn_001_shape_cannot_produce_readiness(self):
        """Mirrors the real production ScenarioAttempt #1 shape without querying
        production: one strong, fully-submitted scenario attempt, zero exam/practice
        evidence."""
        summary = DomainEvidenceSummary(
            practice_evidence_count=0,
            scenario_evidence_count=1,
            distinct_scenario_content_versions=1,
            recent_practice_mastery_band=None,
            recent_scenario_mastery_band=MasteryBand.STRONG,
            most_recent_evidence_at=NOW,
            unresolved_misconception_count=0,
            now=NOW,
        )
        r = classify_domain_readiness(summary)
        assert r.state == STATE_INSUFFICIENT_EVIDENCE
        assert r.state != STATE_READY


# --- Property / monotonic safety --------------------------------------------------


class TestMonotonicSafetyProperties:
    def _ordinal(self, state: str) -> int:
        return READINESS_STATE_ORDER.index(state)

    def test_adding_second_qualifying_evidence_source_does_not_worsen_sufficiency(self):
        before = classify_domain_readiness(
            make_summary(scenario_evidence_count=1, distinct_scenario_content_versions=1)
        )
        after = classify_domain_readiness(make_summary())  # scenario count raised to 2
        assert self._ordinal(after.state) >= self._ordinal(before.state)

    def test_making_evidence_stale_does_not_improve_readiness(self):
        fresh = classify_domain_readiness(make_summary())
        stale = classify_domain_readiness(
            make_summary(
                most_recent_evidence_at=NOW - timedelta(days=STALENESS_THRESHOLD_DAYS + 1)
            )
        )
        assert self._ordinal(stale.state) <= self._ordinal(fresh.state)

    def test_adding_unresolved_misconception_does_not_improve_readiness(self):
        clean = classify_domain_readiness(make_summary(unresolved_misconception_count=0))
        flagged = classify_domain_readiness(make_summary(unresolved_misconception_count=1))
        assert self._ordinal(flagged.state) <= self._ordinal(clean.state)

    def test_removing_required_scenario_evidence_does_not_improve_readiness(self):
        full = classify_domain_readiness(make_summary())
        reduced = classify_domain_readiness(
            make_summary(scenario_evidence_count=0, distinct_scenario_content_versions=0)
        )
        assert self._ordinal(reduced.state) <= self._ordinal(full.state)


# --- Track-level aggregation -------------------------------------------------------


class TestAggregateTrackReadiness:
    def test_track_state_is_minimum_of_domain_states(self):
        assessments = {
            "PTE": ReadinessAssessment(state=STATE_READY, evidence_sufficient=True),
            "WISD": ReadinessAssessment(state=STATE_DEVELOPING, evidence_sufficient=True,
                                         reason_codes=[REPEATED_MISCONCEPTION]),
            "COST": ReadinessAssessment(state=STATE_READY, evidence_sufficient=True),
        }
        result = aggregate_track_readiness(assessments, {"PTE", "WISD", "COST"})
        assert result.state == STATE_DEVELOPING

    def test_missing_domain_blocks_track_ready(self):
        """A track with several strong domains and one completely untouched domain
        must never report track-level ready."""
        assessments = {
            code: ReadinessAssessment(state=STATE_READY, evidence_sufficient=True)
            for code in ["D1", "D2", "D3", "D4", "D5", "D6"]
        }
        result = aggregate_track_readiness(
            assessments, {"D1", "D2", "D3", "D4", "D5", "D6", "D7"}
        )
        assert result.state == STATE_INSUFFICIENT_EVIDENCE
        assert result.reason_codes == [INSUFFICIENT_DOMAIN_COVERAGE]

    def test_never_averages_never_treats_missing_as_passing(self):
        assessments = {
            "PTE": ReadinessAssessment(state=STATE_READY, evidence_sufficient=True),
        }
        result = aggregate_track_readiness(assessments, {"PTE", "GHOST"})
        assert result.state == STATE_INSUFFICIENT_EVIDENCE

    def test_raises_when_no_domains_at_all(self):
        with pytest.raises(ReadinessPolicyError):
            aggregate_track_readiness({}, set())

    def test_tie_break_among_equally_worst_domains_is_deterministic(self):
        assessments = {
            "ZETA": ReadinessAssessment(
                state=STATE_DEVELOPING, evidence_sufficient=True,
                reason_codes=[REPEATED_MISCONCEPTION],
            ),
            "ALPHA": ReadinessAssessment(
                state=STATE_DEVELOPING, evidence_sufficient=True,
                reason_codes=[DOMAIN_BELOW_THRESHOLD],
            ),
        }
        first = aggregate_track_readiness(assessments, {"ZETA", "ALPHA"})
        second = aggregate_track_readiness(assessments, {"ZETA", "ALPHA"})
        assert first == second
        assert first.reason_codes == [DOMAIN_BELOW_THRESHOLD]  # ALPHA sorts first


# --- Reason-code vocabulary bounds --------------------------------------------------


class TestReasonCodeVocabulary:
    KNOWN_CODES = {
        NO_EVIDENCE,
        INSUFFICIENT_PRACTICE_EVIDENCE,
        NO_APPLIED_SCENARIO_EVIDENCE,
        DOMAIN_BELOW_THRESHOLD,
        REPEATED_MISCONCEPTION,
        REPEATED_SCENARIO_NOT_DIVERSE_EVIDENCE,
        STALE_EVIDENCE,
        INSUFFICIENT_DOMAIN_COVERAGE,
    }

    def test_exactly_eight_known_codes(self):
        assert len(self.KNOWN_CODES) == 8

    @pytest.mark.parametrize(
        "summary_overrides",
        [
            {},
            {"practice_evidence_count": 0, "scenario_evidence_count": 0},
            {"unresolved_misconception_count": 1},
            {"most_recent_evidence_at": None},
            {"distinct_scenario_content_versions": 1},
        ],
    )
    def test_every_emitted_code_is_in_the_bounded_vocabulary(self, summary_overrides):
        r = classify_domain_readiness(make_summary(**summary_overrides))
        assert set(r.reason_codes) <= self.KNOWN_CODES


# --- Vocabulary cross-check against the Slice 1 ORM enum ---------------------------


class TestStateVocabularyMatchesModel:
    def test_local_state_constants_match_readiness_state_enum(self):
        from app.models.readiness import ReadinessState

        assert set(READINESS_STATE_ORDER) == {s.value for s in ReadinessState}
        assert STATE_INSUFFICIENT_EVIDENCE == ReadinessState.INSUFFICIENT_EVIDENCE.value
        assert STATE_DEVELOPING == ReadinessState.DEVELOPING.value
        assert STATE_APPROACHING_READY == ReadinessState.APPROACHING_READY.value
        assert STATE_READY == ReadinessState.READY.value


# --- Projection v3: scenario sufficiency counts independent scenarios (Gate C3-C2) ---


class TestScenarioSufficiencyIsDistinctScenarios:
    """Gate C3-C2 Section 11 matrix at the classifier level. `raw` is
    scenario_evidence_count (every submitted attempt); `distinct` is the legacy-named
    distinct_scenario_content_versions field, which under projection v3 holds
    distinct submitted scenario IDs. Everything else stays at the fully-qualifying
    baseline, so only scenario sufficiency can block `ready`."""

    @pytest.mark.parametrize(
        "raw,distinct,expected_codes",
        [
            (0, 0, [NO_APPLIED_SCENARIO_EVIDENCE]),  # zero scenarios
            (1, 1, [NO_APPLIED_SCENARIO_EVIDENCE]),  # one distinct scenario
            (2, 1, [NO_APPLIED_SCENARIO_EVIDENCE,
                    REPEATED_SCENARIO_NOT_DIVERSE_EVIDENCE]),  # same scenario twice
            (10, 1, [NO_APPLIED_SCENARIO_EVIDENCE,
                     REPEATED_SCENARIO_NOT_DIVERSE_EVIDENCE]),  # same scenario ten times
        ],
    )
    def test_fewer_than_two_distinct_scenarios_is_insufficient(self, raw, distinct, expected_codes):
        r = classify_domain_readiness(
            make_summary(scenario_evidence_count=raw, distinct_scenario_content_versions=distinct)
        )
        assert r.state == STATE_INSUFFICIENT_EVIDENCE
        assert r.evidence_sufficient is False
        assert r.reason_codes == expected_codes

    def test_two_distinct_scenarios_satisfy_scenario_sufficiency(self):
        r = classify_domain_readiness(
            make_summary(scenario_evidence_count=2, distinct_scenario_content_versions=2)
        )
        assert r.state == STATE_READY
        assert r.evidence_sufficient is True

    def test_repeats_on_top_of_two_distinct_scenarios_still_sufficient(self):
        r = classify_domain_readiness(
            make_summary(scenario_evidence_count=7, distinct_scenario_content_versions=2)
        )
        assert r.state == STATE_READY
        assert REPEATED_SCENARIO_NOT_DIVERSE_EVIDENCE not in r.reason_codes

    def test_two_distinct_scenarios_do_not_bypass_other_gates(self):
        """Scenario sufficiency is necessary, never sufficient: every other
        readiness gate still applies independently."""
        weak = classify_domain_readiness(
            make_summary(recent_scenario_mastery_band=MasteryBand.DEVELOPING)
        )
        assert weak.state == STATE_DEVELOPING
        assert weak.reason_codes == [DOMAIN_BELOW_THRESHOLD]

        thin_practice = classify_domain_readiness(
            make_summary(practice_evidence_count=MIN_PRACTICE_ITEMS_FOR_SUFFICIENCY - 1)
        )
        assert thin_practice.state == STATE_INSUFFICIENT_EVIDENCE
        assert thin_practice.reason_codes == [INSUFFICIENT_PRACTICE_EVIDENCE]

        misconception = classify_domain_readiness(make_summary(unresolved_misconception_count=1))
        assert misconception.state == STATE_DEVELOPING
        assert misconception.reason_codes == [REPEATED_MISCONCEPTION]

    def test_repeated_code_never_appears_in_developing_tier(self):
        """Once sufficiency passes, diversity is already met -- the repeat reason
        code is confined to the insufficient tier under v3."""
        for raw, distinct in [(2, 2), (3, 2), (10, 5)]:
            r = classify_domain_readiness(
                make_summary(
                    scenario_evidence_count=raw,
                    distinct_scenario_content_versions=distinct,
                    recent_practice_mastery_band=MasteryBand.CRITICAL,
                )
            )
            assert r.state == STATE_DEVELOPING
            assert REPEATED_SCENARIO_NOT_DIVERSE_EVIDENCE not in r.reason_codes


# --- Projection v5: retake validity + fresh scenario mastery -------------------------

from app.services.readiness_policy import (  # noqa: E402
    ReadinessPolicyError,
    ScenarioAttemptExposure,
    ScenarioObservation,
    aggregate_scenario_mastery,
    exposed_scenario_ids,
    select_fresh_scenario_observations,
)

_S, _P, _D, _C = (MasteryBand.STRONG, MasteryBand.PROFICIENT,
                  MasteryBand.DEVELOPING, MasteryBand.CRITICAL)


def _fresh(*results):
    """results: (scenario_id, band) in submission order -> one fresh observation each."""
    return [
        ScenarioObservation(scenario_id=sid, attempt_id=i + 1,
                            submitted_at=NOW + timedelta(minutes=i), mastery_band=band)
        for i, (sid, band) in enumerate(results)
    ]


def _exp(sid, aid, *, minute, band=None, submitted=True, answered=True):
    at = NOW + timedelta(minutes=minute)
    return ScenarioAttemptExposure(
        scenario_id=sid, attempt_id=aid, submitted=submitted,
        submitted_at=at if submitted else None,
        first_answer_at=at if answered else None, mastery_band=band if submitted else None,
    )


class TestFreshScenarioSelection:
    def test_first_exposure_is_the_only_fresh_attempt(self):
        fresh = select_fresh_scenario_observations([
            _exp(1, 10, minute=0, band=_C), _exp(1, 11, minute=5, band=_S), _exp(1, 12, minute=9, band=_S),
        ])
        assert [(o.attempt_id, o.mastery_band) for o in fresh] == [(10, _C)]

    def test_retake_cannot_lower_a_fresh_result_either(self):
        fresh = select_fresh_scenario_observations([_exp(1, 10, minute=0, band=_S), _exp(1, 11, minute=5, band=_C)])
        assert [o.mastery_band for o in fresh] == [_S]

    def test_answers_revealed_in_an_abandoned_attempt_leave_no_fresh_result(self):
        fresh = select_fresh_scenario_observations([
            _exp(1, 10, minute=0, submitted=False, answered=True),
            _exp(1, 11, minute=5, band=_S),
        ])
        assert fresh == []

    def test_unanswered_in_progress_attempt_does_not_expose(self):
        fresh = select_fresh_scenario_observations([
            _exp(1, 10, minute=0, submitted=False, answered=False),
            _exp(1, 11, minute=5, band=_S),
        ])
        assert [o.attempt_id for o in fresh] == [11]

    def test_exposure_tie_is_broken_by_lower_attempt_id(self):
        fresh = select_fresh_scenario_observations([_exp(1, 21, minute=0, band=_S), _exp(1, 20, minute=0, band=_C)])
        assert [o.attempt_id for o in fresh] == [20]

    def test_submitted_attempt_without_answer_event_uses_submission_time(self):
        legacy = ScenarioAttemptExposure(scenario_id=1, attempt_id=1, submitted=True,
                                         submitted_at=NOW, first_answer_at=None, mastery_band=_S)
        assert [o.attempt_id for o in select_fresh_scenario_observations([legacy])] == [1]

    def test_submitted_attempt_without_submitted_at_raises(self):
        broken = ScenarioAttemptExposure(scenario_id=1, attempt_id=1, submitted=True,
                                         submitted_at=None, first_answer_at=None, mastery_band=_S)
        with pytest.raises(ReadinessPolicyError):
            select_fresh_scenario_observations([broken])

    def test_one_fresh_result_per_scenario_ordered_by_submission(self):
        fresh = select_fresh_scenario_observations([
            _exp(2, 30, minute=3, band=_S), _exp(1, 31, minute=1, band=_D), _exp(2, 32, minute=8, band=_C),
        ])
        assert [(o.scenario_id, o.attempt_id) for o in fresh] == [(1, 31), (2, 30)]

    def test_exposed_scenarios(self):
        assert exposed_scenario_ids([
            _exp(1, 1, minute=0, band=_S),
            _exp(2, 2, minute=0, submitted=False, answered=True),
            _exp(3, 3, minute=0, submitted=False, answered=False),
        ]) == {1, 2}


class TestFreshScenarioMastery:
    def test_no_scenarios_is_none(self):
        assert aggregate_scenario_mastery([]) is None

    def test_one_scenario_is_its_fresh_band(self):
        assert aggregate_scenario_mastery(_fresh((1, _S))) == _S
        assert aggregate_scenario_mastery(_fresh((1, _C))) == _C

    @pytest.mark.parametrize(
        "first,second,expected",
        [
            (_S, _S, _S),
            (_S, _P, _P),
            (_S, _D, _D),   # failure AFTER success: regression limits
            (_S, _C, _C),
            (_P, _D, _D),
            (_D, _S, _S),   # success on unseen material AFTER failure: remediation
            (_C, _S, _S),
            (_C, _P, _P),
            (_D, _C, _C),   # a weak result never clears another weak result
            (_C, _D, _C),
        ],
    )
    def test_two_fresh_scenarios(self, first, second, expected):
        assert aggregate_scenario_mastery(_fresh((1, first), (2, second))) == expected

    def test_three_scenarios(self):
        assert aggregate_scenario_mastery(_fresh((1, _S), (2, _S), (3, _C))) == _C
        assert aggregate_scenario_mastery(_fresh((3, _C), (1, _S), (2, _S))) == _S
        assert aggregate_scenario_mastery(_fresh((1, _C), (2, _D), (3, _P))) == _P

    def test_rejects_more_than_one_result_per_scenario(self):
        with pytest.raises(ReadinessPolicyError):
            aggregate_scenario_mastery(_fresh((1, _C), (1, _S)))

    def test_retained_result_without_band_yields_none(self):
        obs = _fresh((1, _S))
        obs.append(ScenarioObservation(scenario_id=2, attempt_id=9,
                                       submitted_at=NOW + timedelta(hours=1), mastery_band=None))
        assert aggregate_scenario_mastery(obs) is None

    def test_input_order_is_irrelevant_only_submission_order_matters(self):
        obs = _fresh((1, _C), (2, _S))
        assert aggregate_scenario_mastery(obs) == aggregate_scenario_mastery(list(reversed(obs))) == _S

    def test_weak_aggregate_still_reads_domain_below_threshold(self):
        band = aggregate_scenario_mastery(_fresh((1, _S), (2, _D)))
        r = classify_domain_readiness(make_summary(recent_scenario_mastery_band=band))
        assert r.state == STATE_DEVELOPING
        assert r.reason_codes == [DOMAIN_BELOW_THRESHOLD]
