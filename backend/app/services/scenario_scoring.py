"""Deterministic evaluation for Scenario Lab decisions.

Gate C1, Slice 1. Pure functions, no database dependency, no model call, no MCP call
-- anywhere in this module, ever. Mirrors scoring.py's own stated design principle: a
pure function can be tested directly, without a database.

Narrative/mechanics separation (plan Sections 3, 16.3, and this slice's Section 3):
this module only ever reads `is_correct`, `rationale`, and `misconception_tag` off
already-authored StepOption data. It never infers correctness from prose, and it never
generates rationale or misconception text -- those are always authored inputs here,
never computed outputs. The engine stays generic across any scenario content; only the
data passed to it changes (see test_scenario_grading.py's mutation-representation
check).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Sequence

from app.services.scoring import MasteryBand, QuestionType, _round_half_up, grade_item

# Section 7.2 of the plan: revealing hint N implies hints 1..N-1 were already seen, so
# only the highest-position revealed hint's penalty applies -- not additive. A single
# documented policy switch, mirroring scoring.py's own
# MR_PARTIAL_CREDIT_COUNTS_TOWARD_SCORE (scoring.py:39).
SCENARIO_HINT_PENALTY_MODE = "highest_only"


class ScenarioScoringError(Exception):
    """Raised when structurally invalid scenario data is graded (e.g. no options)."""


@dataclass(frozen=True)
class StepOption:
    """One authored option, decoupled from the ORM -- mirrors
    explanation_engine.py's QuestionContext/DomainContext pattern: testable without a
    database. Callers must pass options already in the step's authored `position`
    order (the same precondition ScenarioStep.options's ORM relationship already
    guarantees via its own order_by).
    """

    id: int
    label: str
    text: str
    is_correct: bool
    rationale: str
    misconception_tag: str | None = None


@dataclass(frozen=True)
class RevealedHint:
    position: int
    penalty_bps: int


@dataclass(frozen=True)
class StepEvaluation:
    """The deterministic result of one decision: Evaluation + the material for the
    Consequence/Reflection beats, ready to be shaped into evidence by
    build_scenario_event_payload().
    """

    step_id: int
    is_correct: bool
    step_credit: float
    correct_option_ids: frozenset[int]
    selected_option_ids: frozenset[int]
    selected_options: tuple[StepOption, ...]  # in authored `position` order
    correct_options: tuple[StepOption, ...]  # in authored `position` order
    misconception_tags: frozenset[str]  # from incorrect selected options only
    highest_revealed_hint_penalty_bps: int


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, value))


def highest_revealed_hint_penalty(revealed: Iterable[RevealedHint]) -> int:
    """Section 7.2: only the highest-position revealed hint's penalty applies."""
    revealed = list(revealed)
    if not revealed:
        return 0
    return max(revealed, key=lambda h: h.position).penalty_bps


def evaluate_step_decision(
    *,
    step_id: int,
    domain_code: str,
    step_type: str,
    options: Sequence[StepOption],
    selected_option_ids: Iterable[int],
    revealed_hints: Sequence[RevealedHint] = (),
) -> StepEvaluation:
    """Grade one decision.

    Reuses grade_item() verbatim (plan Section 7.1) for the correctness call -- no
    independent scoring philosophy, full inheritance of test_grading.py's existing
    guarantees (MCQ exact-match, MR set-equality, unanswered-scores-zero). The step
    credit formula is exactly the one specified in plan Section 7.2:

        step_credit = clamp(1.0 if is_correct else partial_credit, 0, 1)
                       * (1 - highest_revealed_penalty_bps / 10_000)
    """
    options = tuple(options)
    if not options:
        raise ScenarioScoringError(f"Step {step_id} has no options.")

    selected_ids = frozenset(selected_option_ids)
    correct_ids = frozenset(o.id for o in options if o.is_correct)

    # scoring.QuestionType, constructed BY VALUE -- not the ORM/catalog QuestionType,
    # and not passed through as a bare string. grade_item() checks
    # `question_type is QuestionType.MCQ` (identity, not equality); passing a
    # different-but-value-equal enum class would silently skip its MCQ/MR structural
    # validation instead of raising. This mirrors exactly how
    # routers/attempts.py:91-93 already converts before calling grade_item() for real
    # exam items.
    item_result = grade_item(
        step_id,
        domain_code,
        QuestionType(step_type),
        set(correct_ids),
        set(selected_ids),
    )

    credit_before_penalty = _clamp01(
        1.0 if item_result.is_correct else item_result.partial_credit
    )
    penalty_bps = highest_revealed_hint_penalty(revealed_hints)
    step_credit = _clamp01(credit_before_penalty * (1 - penalty_bps / 10_000))

    selected_options = tuple(o for o in options if o.id in selected_ids)
    correct_options = tuple(o for o in options if o.is_correct)
    misconception_tags = frozenset(
        o.misconception_tag
        for o in selected_options
        if not o.is_correct and o.misconception_tag
    )

    return StepEvaluation(
        step_id=step_id,
        is_correct=item_result.is_correct,
        step_credit=step_credit,
        correct_option_ids=correct_ids,
        selected_option_ids=selected_ids,
        selected_options=selected_options,
        correct_options=correct_options,
        misconception_tags=misconception_tags,
        highest_revealed_hint_penalty_bps=penalty_bps,
    )


def build_scenario_event_payload(
    evaluation: StepEvaluation,
    *,
    scenario_content_version: int,
    attempt_number: int | None = None,
) -> dict[str, object]:
    """Shape one deterministic evaluation into the flat, data-minimised payload for a
    `scenario_events` row (plan Section 6, reconciled in Section 16.5).

    Deliberately excludes anything not already reconciled as an evidence-contract
    field: no free-text learner reasoning (there is none in MVP -- every step is
    MCQ/MR), no PII, no redundant field already reachable by joining
    scenario_step_attempts to scenario_step_options (e.g. no separate
    "remediation_target" column -- that is the pair (step_id, misconception_tags)
    already present below). `attempt_number` is accepted as a caller-supplied,
    already-derived value rather than computed here, so this module stays free of any
    database dependency; per Section 16.5 it is intentionally not a persisted column
    anywhere -- it is COUNT(scenario_attempts) at read time, written into the payload
    only if a caller already has it cheaply.
    """
    payload: dict[str, object] = {
        "step_id": evaluation.step_id,
        "is_correct": evaluation.is_correct,
        "step_credit": evaluation.step_credit,
        "selected_option_ids": sorted(evaluation.selected_option_ids),
        "correct_option_ids": sorted(evaluation.correct_option_ids),
        "misconception_tags": sorted(evaluation.misconception_tags),
        "scenario_content_version": scenario_content_version,
        "hint_penalty_bps": evaluation.highest_revealed_hint_penalty_bps,
    }
    if attempt_number is not None:
        payload["attempt_number"] = attempt_number
    return payload


def aggregate_scenario_score(step_credits: Iterable[float]) -> tuple[int, MasteryBand]:
    """Scenario-level aggregate (plan Section 7.3).

    score_pct = round_half_up(mean(step_credit) * 100), reusing scoring.py's
    _round_half_up unchanged -- the plan explicitly calls for this exact reuse
    (Section 7.3), not a re-derived rounding rule. mastery_band reuses MasteryBand
    unchanged: one mastery vocabulary across exams and scenarios, not two.
    """
    credits = list(step_credits)
    if not credits:
        raise ScenarioScoringError("Scenario has no graded steps to aggregate.")
    score_pct = _round_half_up(sum(credits) / len(credits) * 100)
    return score_pct, MasteryBand.from_percentage(score_pct)
