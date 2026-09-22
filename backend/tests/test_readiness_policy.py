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

    def test_sufficient_counts_but_insufficient_diversity_is_developing_not_ready(self):
        """Sufficient raw counts (practice>=5, scenario>=2) with repeated identical
        content does not reach insufficient_evidence (the counts are real) nor
        ready (not diverse) -- it lands in developing, per the plan's explicit
        `developing` rule and reason code."""
        r = classify_domain_readiness(
            make_summary(
                scenario_evidence_count=5, distinct_scenario_content_versions=1
            )
        )
        assert r.state == STATE_DEVELOPING
        assert r.reason_codes == [REPEATED_SCENARIO_NOT_DIVERSE_EVIDENCE]

    def test_multiple_developing_reasons_all_reported(self):
        r = classify_domain_readiness(
            make_summary(
                recent_practice_mastery_band=MasteryBand.CRITICAL,
                unresolved_misconception_count=2,
                distinct_scenario_content_versions=1,
            )
        )
        assert r.state == STATE_DEVELOPING
        assert r.reason_codes == [
            DOMAIN_BELOW_THRESHOLD,
            REPEATED_MISCONCEPTION,
            REPEATED_SCENARIO_NOT_DIVERSE_EVIDENCE,
        ]


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
            ({"distinct_scenario_content_versions": 1}, STATE_DEVELOPING),
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
