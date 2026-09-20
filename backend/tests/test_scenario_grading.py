"""Scenario Lab deterministic engine: grade_item() reuse, hint-penalty composition,
narrative/mechanics separation, and misconception-evidence distinctness.

Pure-function tests, no database -- mirrors test_grading.py's table-driven,
class-grouped style exactly.
"""

from __future__ import annotations

import pytest

from app.services.scenario_scoring import (
    RevealedHint,
    ScenarioScoringError,
    StepOption,
    aggregate_scenario_score,
    build_scenario_event_payload,
    evaluate_step_decision,
    highest_revealed_hint_penalty,
)
from app.services.scoring import MasteryBand, ScoringError


# --- fixtures: one MCQ step (three options, two pedagogically distinct wrong ones) ---
# and one MR step, used throughout this file.

def mcq_options():
    return [
        StepOption(
            id=1, label="A", text="Route to human review.",
            is_correct=True,
            rationale="Targets the actual blind spot without discarding a working system.",
        ),
        StepOption(
            id=2, label="B", text="Send automatically -- it sounded confident.",
            is_correct=False,
            rationale="Fluent, confident prose is not evidence the model is grounded.",
            misconception_tag="confidence_as_correctness",
        ),
        StepOption(
            id=3, label="C", text="Turn the whole pipeline off.",
            is_correct=False,
            rationale="A disproportionate response that discards a system that otherwise works.",
            misconception_tag="overcorrection_discards_working_system",
        ),
    ]


def mr_options():
    return [
        StepOption(id=10, label="A", text="Check the context freshness.", is_correct=True,
                   rationale="Directly addresses the blind spot."),
        StepOption(id=11, label="B", text="Add a human review gate.", is_correct=True,
                   rationale="Bounds the risk while the gap is closed."),
        StepOption(id=12, label="C", text="Ignore it, ship anyway.", is_correct=False,
                   rationale="Trusts output without validating it against anything.",
                   misconception_tag="trusts_model_output_without_validation"),
    ]


def evaluate_mcq(selected, hints=(), step_id=1):
    return evaluate_step_decision(
        step_id=step_id, domain_code="WISD", step_type="mcq",
        options=mcq_options(), selected_option_ids=selected, revealed_hints=hints,
    )


def evaluate_mr(selected, hints=(), step_id=2):
    return evaluate_step_decision(
        step_id=step_id, domain_code="WISD", step_type="mr",
        options=mr_options(), selected_option_ids=selected, revealed_hints=hints,
    )


class TestCorrectDecision:
    def test_produces_deterministic_correct_evaluation(self):
        r = evaluate_mcq({1})
        assert r.is_correct is True
        assert r.step_credit == 1.0

    def test_produces_no_false_misconception_evidence(self):
        r = evaluate_mcq({1})
        assert r.misconception_tags == frozenset()

    def test_selected_options_carry_reflection_rationale(self):
        r = evaluate_mcq({1})
        assert len(r.selected_options) == 1
        assert "blind spot" in r.selected_options[0].rationale

    def test_correct_options_are_always_reported_regardless_of_selection(self):
        r = evaluate_mcq({1})
        assert {o.id for o in r.correct_options} == {1}


class TestIncorrectDecision:
    def test_produces_deterministic_incorrect_evaluation(self):
        r = evaluate_mcq({2})
        assert r.is_correct is False
        assert r.step_credit == 0.0

    def test_carries_the_correct_conceptual_misconception_tag(self):
        r = evaluate_mcq({2})
        assert r.misconception_tags == frozenset({"confidence_as_correctness"})

    def test_selected_options_carry_reflection_rationale(self):
        r = evaluate_mcq({2})
        assert "Fluent" in r.selected_options[0].rationale


class TestDifferentWrongDecisionsProduceDifferentEvidence:
    """This is the important case (Gate C1 Slice 1 Section 11): two pedagogically
    distinct wrong options must not collapse into a generic 'incorrect'."""

    def test_option_b_and_option_c_carry_different_misconception_tags(self):
        wrong_b = evaluate_mcq({2})
        wrong_c = evaluate_mcq({3})
        assert wrong_b.misconception_tags != wrong_c.misconception_tags
        assert wrong_b.misconception_tags == frozenset({"confidence_as_correctness"})
        assert wrong_c.misconception_tags == frozenset(
            {"overcorrection_discards_working_system"}
        )

    def test_both_wrong_decisions_are_scored_incorrect(self):
        assert evaluate_mcq({2}).is_correct is False
        assert evaluate_mcq({3}).is_correct is False


class TestRepeatability:
    def test_same_decision_same_inputs_yields_identical_evaluation(self):
        r1 = evaluate_mcq({2}, hints=(RevealedHint(1, 1000),))
        r2 = evaluate_mcq({2}, hints=(RevealedHint(1, 1000),))
        assert r1 == r2

    def test_same_scenario_version_same_decision_yields_identical_payload(self):
        r = evaluate_mcq({1})
        p1 = build_scenario_event_payload(r, scenario_content_version=3)
        p2 = build_scenario_event_payload(r, scenario_content_version=3)
        assert p1 == p2


class TestHintPenalty:
    def test_no_hints_revealed_means_no_penalty(self):
        assert highest_revealed_hint_penalty([]) == 0

    def test_only_the_highest_position_hints_penalty_applies_not_additive(self):
        revealed = [RevealedHint(1, 1000), RevealedHint(2, 2000), RevealedHint(3, 3000)]
        assert highest_revealed_hint_penalty(revealed) == 3000

    def test_penalty_reduces_step_credit_on_a_correct_answer(self):
        r = evaluate_mcq({1}, hints=(RevealedHint(2, 2000),))
        assert r.step_credit == pytest.approx(0.8)

    def test_penalty_does_not_push_credit_negative(self):
        r = evaluate_mcq({2}, hints=(RevealedHint(3, 3000),))  # already 0 credit
        assert r.step_credit == 0.0


class TestMRStepComposesPartialCreditWithHintDiscount:
    def test_full_mr_credit_with_no_hint(self):
        assert evaluate_mr({10, 11}).step_credit == 1.0

    def test_partial_mr_credit_still_computed_and_discounted(self):
        r = evaluate_mr({10}, hints=(RevealedHint(1, 1000),))
        # partial_credit for 1 of 2 correct = 0.5; not is_correct (D-4 set-equality);
        # discounted by 10%.
        assert r.step_credit == pytest.approx(0.45)

    def test_mr_wrong_option_carries_its_own_misconception_tag(self):
        r = evaluate_mr({12})
        assert r.misconception_tags == frozenset({"trusts_model_output_without_validation"})


class TestNarrativeMechanicsSeparation:
    """The engine must never infer correctness from prose -- only from the authored
    `is_correct` boolean."""

    def test_rationale_text_never_influences_grading(self):
        misleading = [
            StepOption(id=1, label="A", text="X", is_correct=False,
                       rationale="This is definitely correct and obviously right!"),
            StepOption(id=2, label="B", text="Y", is_correct=True,
                       rationale="Sounds tentative, but this is the actual answer."),
        ]
        r = evaluate_step_decision(
            step_id=99, domain_code="TRO", step_type="mcq",
            options=misleading, selected_option_ids={1},
        )
        assert r.is_correct is False  # driven by is_correct, not by rationale wording


class TestConstraintMutationRepresentedWithoutChangingTheEngine:
    """Slice 1 Section 13: demonstrate that changing a constraint (the content) does
    not require changing the grading engine (the code)."""

    def test_base_scenario_step(self):
        r = evaluate_step_decision(
            step_id=1, domain_code="WISD", step_type="mcq",
            options=mcq_options(), selected_option_ids={1},
        )
        assert r.is_correct is True

    def test_mutated_scenario_step_same_engine_different_correct_answer(self):
        # The mutation from docs/GATE-C1-SCENARIO-LAB-PLAN.md Section 18: three months
        # later, auto-refresh exists, so "always route to human" is no longer
        # automatically correct -- a narrower trigger is. Represented purely as new
        # StepOption content; evaluate_step_decision() itself is untouched.
        mutated_options = [
            StepOption(id=1, label="A", text="Still route every ticket to review.",
                       is_correct=False,
                       rationale="The premise that made this correct -- manual-only "
                                 "context updates -- no longer holds.",
                       misconception_tag="ignores_failure_isolation"),
            StepOption(id=2, label="B",
                       text="Route only when confidence is low AND the topic postdates "
                            "the last refresh.",
                       is_correct=True,
                       rationale="Matches review rigor to actual, current context "
                                 "freshness rather than a blanket rule."),
        ]
        r = evaluate_step_decision(
            step_id=1, domain_code="WISD", step_type="mcq",
            options=mutated_options, selected_option_ids={1},
        )
        assert r.is_correct is False
        assert r.misconception_tags == frozenset({"ignores_failure_isolation"})


class TestInvalidScenarioStructureFailsSafely:
    def test_step_with_no_options_raises(self):
        with pytest.raises(ScenarioScoringError):
            evaluate_step_decision(
                step_id=1, domain_code="PTE", step_type="mcq",
                options=[], selected_option_ids={1},
            )

    def test_mcq_step_with_two_correct_options_raises_via_grade_item(self):
        bad = [
            StepOption(id=1, label="A", text="x", is_correct=True, rationale="r"),
            StepOption(id=2, label="B", text="y", is_correct=True, rationale="r"),
        ]
        with pytest.raises(ScoringError, match="exactly one"):
            evaluate_step_decision(
                step_id=1, domain_code="PTE", step_type="mcq",
                options=bad, selected_option_ids={1},
            )


class TestScenarioAggregate:
    def test_mean_of_step_credits_rounds_half_up(self):
        score_pct, band = aggregate_scenario_score([1.0, 0.5, 0.5])
        assert score_pct == 67  # mean = 0.6667 -> 66.67 -> rounds to 67
        assert band is MasteryBand.DEVELOPING  # 67% falls in [50, 70)

    def test_empty_scenario_is_rejected(self):
        with pytest.raises(ScenarioScoringError):
            aggregate_scenario_score([])


class TestEvidencePayloadCarriesContentVersion:
    def test_payload_states_the_scenario_content_version(self):
        r = evaluate_mcq({2})
        payload = build_scenario_event_payload(r, scenario_content_version=4)
        assert payload["scenario_content_version"] == 4

    def test_payload_includes_attempt_number_only_when_supplied(self):
        r = evaluate_mcq({1})
        without = build_scenario_event_payload(r, scenario_content_version=1)
        with_number = build_scenario_event_payload(
            r, scenario_content_version=1, attempt_number=2
        )
        assert "attempt_number" not in without
        assert with_number["attempt_number"] == 2

    def test_payload_is_flat_json_serialisable(self):
        import json

        r = evaluate_mcq({3})
        payload = build_scenario_event_payload(r, scenario_content_version=1)
        json.dumps(payload)  # must not raise
