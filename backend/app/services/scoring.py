"""Scoring engine: item grading and raw-to-scaled score conversion.

Two responsibilities, both pure functions with no database dependency so they can be
tested directly:

  1. grade_item()   -- did the candidate answer this item correctly?
  2. scale_score()  -- map a raw proportion onto the 100-1000 reporting scale

Design decisions behind this module (full rationale in SPEC-CERT-MASTERY.md, section 11):

  D-1  Blueprint weights govern exam *composition*, not scoring. Every item counts
       equally toward the raw score. The weighting is already baked into the item mix,
       so re-applying it here would count it twice.
  D-2  Scaling is piecewise-linear on three anchors (0% -> 100, pass_raw -> 720,
       100% -> 1000) so that the pass line lands exactly on 720.
  D-4  Multi-response items are all-or-nothing for the score; partial credit is
       recorded separately for the mastery dashboard.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal
from enum import Enum

SCALE_MIN = 100
SCALE_MAX = 1000
PASS_SCALED = 720
DEFAULT_PASS_RAW = 0.70

# --- D-4 policy seam -------------------------------------------------------------
# When False (default), a multi-response item must be answered exactly right to earn
# credit toward the scaled score, mirroring how real certification exams grade MR items.
# Partial credit is still computed and stored on every item, and still drives the
# mastery dashboard and the SM-2 initial grade -- it just does not inflate the score.
#
# Flip to True to let partial credit count toward the scaled score. That is the whole
# change: score_attempt() reads this flag and nothing else does.
MR_PARTIAL_CREDIT_COUNTS_TOWARD_SCORE = False
# ---------------------------------------------------------------------------------


class QuestionType(str, Enum):
    MCQ = "mcq"  # exactly one correct option
    MR = "mr"    # two or more correct options


class MasteryBand(str, Enum):
    """Per-domain readiness, for the dashboard and for flashcard prioritisation."""

    CRITICAL = "critical"      # < 50%
    DEVELOPING = "developing"  # 50-69%
    PROFICIENT = "proficient"  # 70-84%
    STRONG = "strong"          # >= 85%

    @classmethod
    def from_percentage(cls, pct: float) -> "MasteryBand":
        if pct < 50:
            return cls.CRITICAL
        if pct < 70:
            return cls.DEVELOPING
        if pct < 85:
            return cls.PROFICIENT
        return cls.STRONG


class ScoringError(ValueError):
    """Raised when an item or attempt cannot be scored."""


@dataclass(frozen=True)
class ItemResult:
    """Outcome of grading a single item."""

    question_id: int
    domain_code: str
    question_type: QuestionType
    is_correct: bool
    partial_credit: float  # [0, 1] -- diagnostic; see D-4
    answered: bool


@dataclass(frozen=True)
class DomainScore:
    domain_code: str
    correct: int
    total: int
    percentage: float
    mastery_band: MasteryBand


@dataclass(frozen=True)
class AttemptScore:
    raw_correct: int
    raw_total: int
    raw_percentage: float
    scaled_score: int
    passed: bool
    domain_scores: list[DomainScore] = field(default_factory=list)


def _round_half_up(value: float) -> int:
    """Round half away from zero.

    Python's built-in round() is banker's rounding: round(719.5) == 720 but
    round(718.5) == 718. Score reporting needs conventional half-up so that equal
    fractional parts are never treated differently.
    """
    return int(Decimal(str(value)).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def compute_partial_credit(correct_ids: set[int], selected_ids: set[int]) -> float:
    """Fraction of an item's credit earned, in [0, 1].

        (hits - false_positives) / len(correct)

    Subtracting false positives is what stops "select every option" from scoring 1.0 --
    without it, the maximally uninformative answer would look like mastery. A candidate
    who selects all 5 options on a 2-correct item gets (2 - 3) / 2, clamped to 0.
    """
    if not correct_ids:
        raise ScoringError("Question has no correct options.")
    hits = len(selected_ids & correct_ids)
    false_positives = len(selected_ids - correct_ids)
    return max(0.0, (hits - false_positives) / len(correct_ids))


def grade_item(
    question_id: int,
    domain_code: str,
    question_type: QuestionType,
    correct_ids: set[int],
    selected_ids: set[int],
) -> ItemResult:
    """Grade one item.

    MCQ: exactly one option selected, and it is the correct one.
    MR:  set equality between selected and correct (D-4).

    An unanswered item is scored 0 rather than skipped, so raw_total always equals the
    number of items presented -- a blank and a wrong answer cost the same, as on the
    real exam.
    """
    if not correct_ids:
        raise ScoringError(f"Question {question_id} has no correct options.")
    if question_type is QuestionType.MCQ and len(correct_ids) != 1:
        raise ScoringError(
            f"MCQ question {question_id} must have exactly one correct option; "
            f"got {len(correct_ids)}."
        )
    if question_type is QuestionType.MR and len(correct_ids) < 2:
        raise ScoringError(
            f"MR question {question_id} must have at least two correct options; "
            f"got {len(correct_ids)}."
        )

    answered = bool(selected_ids)
    partial = compute_partial_credit(correct_ids, selected_ids) if answered else 0.0
    is_correct = answered and selected_ids == correct_ids

    return ItemResult(
        question_id=question_id,
        domain_code=domain_code,
        question_type=question_type,
        is_correct=is_correct,
        partial_credit=partial,
        answered=answered,
    )


def scale_score(
    raw_correct: float, raw_total: int, pass_raw: float = DEFAULT_PASS_RAW
) -> int:
    """Map a raw score onto the 100-1000 reporting scale.

    Piecewise-linear through three anchors (D-2):

        raw 0          -> 100
        raw pass_raw   -> 720   (the pass line, exact by construction)
        raw 1.0        -> 1000

    A single straight line from 0-100% onto 100-1000 would place the pass line at a raw
    68.9%, not 70%. Anchoring the midpoint is what makes the threshold meaningful.

    `raw_correct` is a float rather than an int so that the D-4 policy flag can feed
    summed partial credit through this same function unchanged.
    """
    if raw_total <= 0:
        raise ScoringError(f"raw_total must be positive; got {raw_total}.")
    if not 0 < pass_raw < 1:
        raise ScoringError(f"pass_raw must be strictly between 0 and 1; got {pass_raw}.")
    if raw_correct < 0 or raw_correct > raw_total:
        raise ScoringError(
            f"raw_correct must be within [0, {raw_total}]; got {raw_correct}."
        )

    raw = raw_correct / raw_total

    if raw <= pass_raw:
        scaled = SCALE_MIN + (raw / pass_raw) * (PASS_SCALED - SCALE_MIN)
    else:
        scaled = PASS_SCALED + ((raw - pass_raw) / (1 - pass_raw)) * (
            SCALE_MAX - PASS_SCALED
        )

    result = _round_half_up(scaled)

    # Rounding must not cross the pass line. A raw of 69.95% yields 719.55, which rounds
    # up to 720 and would report a pass the candidate did not earn. Clamp so that
    # `result >= PASS_SCALED` holds exactly when `raw >= pass_raw`.
    if raw < pass_raw:
        result = min(result, PASS_SCALED - 1)
    else:
        result = max(result, PASS_SCALED)

    return max(SCALE_MIN, min(SCALE_MAX, result))


def score_attempt(
    items: list[ItemResult], pass_raw: float = DEFAULT_PASS_RAW
) -> AttemptScore:
    """Aggregate graded items into a scaled score with a per-domain breakdown.

    Note what is deliberately absent: no domain weighting is applied here. The blueprint
    weights already determined how many items each domain contributed, so weighting the
    score too would double-count them (D-1).
    """
    if not items:
        raise ScoringError("Cannot score an attempt with no items.")

    raw_total = len(items)
    if MR_PARTIAL_CREDIT_COUNTS_TOWARD_SCORE:
        raw_credit: float = sum(
            i.partial_credit
            if i.question_type is QuestionType.MR
            else float(i.is_correct)
            for i in items
        )
    else:
        raw_credit = float(sum(1 for i in items if i.is_correct))

    scaled = scale_score(raw_credit, raw_total, pass_raw=pass_raw)

    # Domain rollup, ordered by first appearance so output is stable across runs.
    order: list[str] = []
    tally: dict[str, list[int]] = {}
    for item in items:
        if item.domain_code not in tally:
            tally[item.domain_code] = [0, 0]
            order.append(item.domain_code)
        tally[item.domain_code][1] += 1
        if item.is_correct:
            tally[item.domain_code][0] += 1

    domain_scores = []
    for code in order:
        correct, total = tally[code]
        pct = correct / total * 100
        domain_scores.append(
            DomainScore(
                domain_code=code,
                correct=correct,
                total=total,
                percentage=round(pct, 2),
                mastery_band=MasteryBand.from_percentage(pct),
            )
        )

    return AttemptScore(
        raw_correct=int(sum(1 for i in items if i.is_correct)),
        raw_total=raw_total,
        raw_percentage=round(raw_credit / raw_total * 100, 2),
        scaled_score=scaled,
        passed=scaled >= PASS_SCALED,
        domain_scores=domain_scores,
    )
