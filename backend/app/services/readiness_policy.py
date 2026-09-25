"""KSOR Slice 2: the pure deterministic evidence summarizer / readiness classifier.

Gate C3, Slice 2. See docs/GATE-C3-KSOR-READINESS-IMPLEMENTATION-PLAN.md Sections
7-9 and 16 for the approved policy contract this module implements.

Pure functions only. No SQLAlchemy import, no database access, no Anthropic/Zia/MCP
call anywhere in this module -- readiness is a deterministic function of an
already-summarized evidence snapshot, never a model judgment and never something this
module fetches for itself. Collecting real evidence from the ORM is Slice 3's job
(a future app/services/learner_readiness.py); this module only classifies.

Mirrors app/services/scoring.py's own "pure functions, no database dependency"
pattern, and DomainEvidenceSummary mirrors scenario_scoring.StepOption's
decoupled-from-the-ORM shape for the same testability reason.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from fractions import Fraction

from app.services.scoring import MasteryBand

# --- v1 policy constants (plan Section 7/24) --------------------------------------
# These are v1 deterministic PRODUCT-POLICY decisions, not scientifically validated
# psychometric thresholds. They are centralized here -- not in the database schema,
# not in an environment variable, not in a migration, not in a frontend constant --
# specifically so future outcome data can calibrate them without touching anything
# else that currently depends on their values.
# Policy v2 (Gate C3-B2): raised from 5 to PRACTICE_BAND_MIN_ITEMS so a practice band
# that can block readiness always rests on at least a full mastery window of items.
MIN_PRACTICE_ITEMS_FOR_SUFFICIENCY = 20
MIN_SCENARIO_ATTEMPTS_FOR_SUFFICIENCY = 2
STALENESS_THRESHOLD_DAYS = 90

# --- v2 practice-evidence policy (Gate C3-B2) ----------------------------------------
# A submitted exam attempt only qualifies as readiness evidence when at least this
# share of ALL its items (whole exam, every domain -- never per domain) was answered.
# Guards against near-blank/test submissions counting as practice evidence or
# refreshing practice freshness. Compared exactly (see attempt_qualifies_for_practice),
# so 0.80 itself qualifies.
MIN_ATTEMPT_COMPLETION_RATIO = 0.80
# The practice mastery window grows by whole attempts, newest first, until it holds
# at least this many domain items -- so a 1-2 item domain slice of the latest exam can
# never decide the band on its own.
PRACTICE_BAND_MIN_ITEMS = 20

# --- readiness state vocabulary -----------------------------------------------------
# Plain string constants, not an import of app.models.readiness.ReadinessState --
# that model module pulls in app.database.Base (SQLAlchemy), which would violate this
# module's "zero ORM import" requirement. Values are cross-checked against the ORM
# enum in tests (test_readiness_policy.py::TestStateVocabularyMatchesModel), so drift
# between the two is caught, not silently allowed.
STATE_INSUFFICIENT_EVIDENCE = "insufficient_evidence"
STATE_DEVELOPING = "developing"
STATE_APPROACHING_READY = "approaching_ready"
STATE_READY = "ready"

# Ordinal scale for track-level aggregation (plan Section 9) -- never averaged.
READINESS_STATE_ORDER = [
    STATE_INSUFFICIENT_EVIDENCE,
    STATE_DEVELOPING,
    STATE_APPROACHING_READY,
    STATE_READY,
]

# --- reason-code vocabulary (plan Section 16) -- exactly eight, bounded ------------
NO_EVIDENCE = "NO_EVIDENCE"
INSUFFICIENT_PRACTICE_EVIDENCE = "INSUFFICIENT_PRACTICE_EVIDENCE"
NO_APPLIED_SCENARIO_EVIDENCE = "NO_APPLIED_SCENARIO_EVIDENCE"
DOMAIN_BELOW_THRESHOLD = "DOMAIN_BELOW_THRESHOLD"
REPEATED_MISCONCEPTION = "REPEATED_MISCONCEPTION"
REPEATED_SCENARIO_NOT_DIVERSE_EVIDENCE = "REPEATED_SCENARIO_NOT_DIVERSE_EVIDENCE"
STALE_EVIDENCE = "STALE_EVIDENCE"
INSUFFICIENT_DOMAIN_COVERAGE = "INSUFFICIENT_DOMAIN_COVERAGE"

_WEAK_BANDS = {MasteryBand.CRITICAL, MasteryBand.DEVELOPING}


class ReadinessPolicyError(ValueError):
    """Raised for malformed classifier input the policy cannot evaluate."""


@dataclass(frozen=True)
class DomainEvidenceSummary:
    """Everything classify_domain_readiness() needs, already extracted from the ORM
    by a future recompute service (Slice 3). No ORM object is ever accepted here --
    only plain counts, bands, and timestamps.

    `now` is an injected reference time, never read from datetime.now() inside the
    classifier -- this is what keeps the classifier deterministic and testable
    (plan Section 8/10).
    """

    practice_evidence_count: int
    scenario_evidence_count: int  # raw submitted attempts, repeats included
    # Legacy name; projection v3 meaning: distinct submitted scenario IDs. This --
    # not the raw count -- is what scenario sufficiency is judged on.
    distinct_scenario_content_versions: int
    recent_practice_mastery_band: MasteryBand | None
    # Legacy name; projection v4 meaning: the limiting (weakest) band across each
    # independent scenario's latest submitted attempt (aggregate_scenario_mastery).
    recent_scenario_mastery_band: MasteryBand | None
    most_recent_evidence_at: datetime | None
    unresolved_misconception_count: int
    now: datetime


@dataclass(frozen=True)
class ReadinessAssessment:
    """The classifier's output. `evidence_sufficient` is False only for
    insufficient_evidence -- exposed separately from `state` so a caller can
    distinguish "not enough data yet" from "we have data and it's not great" without
    string-matching the state value (plan Section 8)."""

    state: str
    evidence_sufficient: bool
    reason_codes: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class PracticeAttemptGroup:
    """One qualifying submitted exam attempt's items for ONE domain, already reduced
    to counts -- the unit the v2 mastery window is built from. Decoupled from the ORM
    (same pattern as DomainEvidenceSummary) so window/band rules are testable pure.

    `item_count` includes every domain item of the attempt, answered or not: once the
    whole attempt qualifies, an unanswered item stays in the denominator as incorrect
    (a learner must not raise a band by skipping hard questions).
    """

    attempt_id: int
    submitted_at: datetime
    item_count: int
    correct_count: int


def attempt_qualifies_for_practice(answered_item_count: int, total_item_count: int) -> bool:
    """Whole-exam completion rule (policy v2). Callers pass counts across ALL of the
    attempt's items (every domain), never a per-domain slice. Exact rational
    comparison, not float division, so a ratio of exactly MIN_ATTEMPT_COMPLETION_RATIO
    (e.g. 48/60 or 12/15) qualifies regardless of binary floating-point rounding.
    An attempt with no items never qualifies."""
    if total_item_count <= 0:
        return False
    return Fraction(answered_item_count, total_item_count) >= Fraction(
        str(MIN_ATTEMPT_COMPLETION_RATIO)
    )


def select_practice_window(groups: list[PracticeAttemptGroup]) -> list[PracticeAttemptGroup]:
    """The v2 practice mastery window: qualifying attempts newest first
    (submitted_at DESC, attempt_id DESC -- the same ordering the v1 rule used), each
    added WHOLE, until the accumulated domain item count reaches
    PRACTICE_BAND_MIN_ITEMS or attempts run out. An attempt is never split to land on
    exactly the minimum, so the window may exceed it. Empty groups are ignored."""
    ordered = sorted(
        (g for g in groups if g.item_count > 0),
        key=lambda g: (g.submitted_at, g.attempt_id),
        reverse=True,
    )
    window: list[PracticeAttemptGroup] = []
    accumulated = 0
    for group in ordered:
        if accumulated >= PRACTICE_BAND_MIN_ITEMS:
            break
        window.append(group)
        accumulated += group.item_count
    return window


def practice_band_for_window(window: list[PracticeAttemptGroup]) -> MasteryBand | None:
    """Band over the pooled window using the one existing threshold table
    (MasteryBand.from_percentage) -- no second scoring system. Descriptive even when
    the window is below PRACTICE_BAND_MIN_ITEMS (sparse history); sufficiency is
    decided separately by MIN_PRACTICE_ITEMS_FOR_SUFFICIENCY in the classifier.
    None only when there is no qualifying practice evidence at all."""
    total = sum(g.item_count for g in window)
    if total == 0:
        return None
    correct = sum(g.correct_count for g in window)
    return MasteryBand.from_percentage(correct / total * 100)


# Weakest-first, the same ordinal meaning MasteryBand.from_percentage's thresholds give.
_BAND_WEAKEST_FIRST = (
    MasteryBand.CRITICAL,
    MasteryBand.DEVELOPING,
    MasteryBand.PROFICIENT,
    MasteryBand.STRONG,
)


@dataclass(frozen=True)
class ScenarioObservation:
    """One submitted scenario attempt, reduced to what scenario-mastery aggregation
    needs -- decoupled from the ORM like PracticeAttemptGroup. `submitted_at` must be
    tz-aware and non-null (every submitted attempt has one; see
    aggregate_scenario_mastery)."""

    scenario_id: int
    attempt_id: int
    submitted_at: datetime
    mastery_band: MasteryBand | None


def aggregate_scenario_mastery(observations) -> MasteryBand | None:
    """Projection v4 scenario mastery (Gate C3-C4/C3-C5).

    1. Each scenario_id is one independent assessment unit: keep only its latest
       submitted attempt, by (submitted_at, attempt_id) -- i.e. submitted_at DESC,
       id DESC. Content-version revisions and repeats of the same scenario never
       add weight; a later attempt replaces an earlier one (remediation AND
       regression are both represented).
    2. Project the WEAKEST band across those latest-per-scenario observations. No
       averaging, no scenario or attempt weighting.

    Cross-scenario attempt order cannot change the result. None when there is no
    observation, or when any scenario's latest attempt carries no band (it cannot
    demonstrate mastery, so nothing is ranked -- the classifier treats None as weak).
    A submitted observation without submitted_at is a broken invariant, not
    something to order by guesswork, so it raises.
    """
    latest: dict[int, ScenarioObservation] = {}
    for obs in observations:
        if obs.submitted_at is None:
            raise ReadinessPolicyError(
                f"Submitted scenario attempt {obs.attempt_id} has no submitted_at."
            )
        current = latest.get(obs.scenario_id)
        if current is None or (obs.submitted_at, obs.attempt_id) > (
            current.submitted_at,
            current.attempt_id,
        ):
            latest[obs.scenario_id] = obs
    if not latest:
        return None
    bands = [obs.mastery_band for obs in latest.values()]
    if any(band is None for band in bands):
        return None
    return min(bands, key=_BAND_WEAKEST_FIRST.index)


def _is_stale(summary: DomainEvidenceSummary) -> bool:
    """A missing timestamp must never be interpreted as recent evidence -- treated
    as stale rather than as an implicit pass, so a domain can never reach `ready`
    just because recency couldn't be verified."""
    if summary.most_recent_evidence_at is None:
        return True
    age = summary.now - summary.most_recent_evidence_at
    return age > timedelta(days=STALENESS_THRESHOLD_DAYS)


def classify_domain_readiness(summary: DomainEvidenceSummary) -> ReadinessAssessment:
    """Rules evaluated in fixed order (plan Section 8); first blocking tier wins,
    each tier collecting every reason code that independently applies within it.

    Deliberate reading of the plan's `NO_APPLIED_SCENARIO_EVIDENCE` rule: the plan's
    own explanatory prose for the production `CCAO-F-PTE-SCN-001` case (one strong
    scenario, zero practice) states "both conditions fire," but its literal rule
    bullet gates that reason code on `scenario_evidence_count == 0` alone, which
    would only make ONE condition fire for that exact input. Gating it on
    `scenario_evidence_count < MIN_SCENARIO_ATTEMPTS_FOR_SUFFICIENCY` instead (this
    gate's own explicit test matrix requires "scenario below 2" and "perfect
    practice + one scenario" to both classify as insufficient_evidence) reconciles
    the prose with the rule and closes a gap where exactly one scenario attempt
    would otherwise fall through every tier unclassified. Reported as a deviation
    from the plan's literal bullet text, not a silent redesign -- see the C3 Slice 2
    gate report. Gate C3-C2 (projection v3) then moved that comparison from the raw
    attempt count to the distinct-scenario count, per plan Section 6, and moved
    REPEATED_SCENARIO_NOT_DIVERSE_EVIDENCE from the developing tier (now unreachable
    there) into this sufficiency tier.
    """
    reason_codes: list[str] = []

    if summary.practice_evidence_count == 0 and summary.scenario_evidence_count == 0:
        reason_codes.append(NO_EVIDENCE)
    if summary.practice_evidence_count < MIN_PRACTICE_ITEMS_FOR_SUFFICIENCY:
        reason_codes.append(INSUFFICIENT_PRACTICE_EVIDENCE)
    # Projection v3 (Gate C3-C2): scenario sufficiency counts INDEPENDENT scenarios
    # (distinct scenario IDs), never raw attempts -- repeating one scenario, however
    # often, is still one observation. When the raw count alone would have met the
    # minimum, the repeat cap is what binds, and it is stated plainly (plan Section 6).
    if summary.distinct_scenario_content_versions < MIN_SCENARIO_ATTEMPTS_FOR_SUFFICIENCY:
        reason_codes.append(NO_APPLIED_SCENARIO_EVIDENCE)
        if summary.scenario_evidence_count >= MIN_SCENARIO_ATTEMPTS_FOR_SUFFICIENCY:
            reason_codes.append(REPEATED_SCENARIO_NOT_DIVERSE_EVIDENCE)

    if reason_codes:
        return ReadinessAssessment(
            state=STATE_INSUFFICIENT_EVIDENCE,
            evidence_sufficient=False,
            reason_codes=reason_codes,
        )

    # Past this point: practice_evidence_count >= MIN_PRACTICE_ITEMS_FOR_SUFFICIENCY
    # and distinct scenarios >= MIN_SCENARIO_ATTEMPTS_FOR_SUFFICIENCY both hold, so
    # diversity is already satisfied -- it is no longer a separate developing rule.
    weak_practice = (
        summary.recent_practice_mastery_band is None
        or summary.recent_practice_mastery_band in _WEAK_BANDS
    )
    weak_scenario = (
        summary.recent_scenario_mastery_band is None
        or summary.recent_scenario_mastery_band in _WEAK_BANDS
    )

    developing_codes: list[str] = []
    if weak_practice or weak_scenario:
        developing_codes.append(DOMAIN_BELOW_THRESHOLD)
    if summary.unresolved_misconception_count > 0:
        developing_codes.append(REPEATED_MISCONCEPTION)

    if developing_codes:
        return ReadinessAssessment(
            state=STATE_DEVELOPING,
            evidence_sufficient=True,
            reason_codes=developing_codes,
        )

    # Both recent bands are PROFICIENT/STRONG, zero unresolved misconceptions, and
    # (from the sufficiency tier) at least MIN_SCENARIO_ATTEMPTS_FOR_SUFFICIENCY
    # distinct scenarios.
    if _is_stale(summary):
        return ReadinessAssessment(
            state=STATE_APPROACHING_READY,
            evidence_sufficient=True,
            reason_codes=[STALE_EVIDENCE],
        )

    return ReadinessAssessment(
        state=STATE_READY, evidence_sufficient=True, reason_codes=[]
    )


def aggregate_track_readiness(
    domain_assessments: dict[str, ReadinessAssessment], all_domain_codes: set[str]
) -> ReadinessAssessment:
    """Track-level readiness is the MINIMUM of per-domain states on the ordinal
    scale (plan Section 9) -- never an average. Any domain code present in the
    track's full blueprint but absent from `domain_assessments` (no
    learner_domain_states row exists at all for it) is treated as an implicit
    insufficient_evidence with reason INSUFFICIENT_DOMAIN_COVERAGE -- it is never
    silently dropped from the aggregate and never defaults to a passing state.
    """
    effective: dict[str, ReadinessAssessment] = dict(domain_assessments)
    for code in all_domain_codes:
        if code not in effective:
            effective[code] = ReadinessAssessment(
                state=STATE_INSUFFICIENT_EVIDENCE,
                evidence_sufficient=False,
                reason_codes=[INSUFFICIENT_DOMAIN_COVERAGE],
            )

    if not effective:
        raise ReadinessPolicyError(
            "Cannot aggregate track readiness with no domains."
        )

    # Sorted domain-code iteration keeps the tie-break deterministic regardless of
    # dict/set insertion order (all_domain_codes is a set, which has none).
    worst_code = min(
        sorted(effective),
        key=lambda code: READINESS_STATE_ORDER.index(effective[code].state),
    )
    worst = effective[worst_code]
    return ReadinessAssessment(
        state=worst.state,
        evidence_sufficient=worst.evidence_sufficient,
        reason_codes=list(worst.reason_codes),
    )
