"""Request and response models for the C3 Slice 6 learner-readiness API.

Read-only, categorical-only (plan Section 12/13 of the Slice 6 gate): no field here
is or ever computes a percentage, confidence score, or pass-probability -- every
state field is one of the four bounded readiness_policy strings, and every
explanation is a bounded reason code, never generated prose. The backend never
translates a reason code into learner-facing copy; that stays a frontend concern.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class DomainReadinessOut(BaseModel):
    """One domain's readiness. `is_materialized=False` means no LearnerDomainState
    row exists yet for this domain -- every count/band/timestamp field is then a
    truthful zero/None synthesis (plan Section 9's missing-domain semantics), never
    a fabricated value, and `calculated_at` is None rather than request time."""

    domain_code: str
    domain_name: str
    domain_position: int
    readiness_state: str
    reason_codes: list[str]
    is_materialized: bool
    practice_evidence_count: int
    # Raw submitted scenario attempts, repeats included.
    scenario_evidence_count: int
    # Legacy name kept for API compatibility. Projection v3: distinct submitted
    # scenario IDs (independent scenario evidence units), not content versions.
    distinct_scenario_content_versions: int
    recent_practice_mastery_band: str | None = None
    # Legacy name kept for API compatibility. Projection v5: the limiting (weakest,
    # non-cleared) band across each scenario's first-exposure (fresh) attempt.
    recent_scenario_mastery_band: str | None = None
    most_recent_evidence_at: datetime | None = None
    # None only in an Explorer preview (TrackReadinessOut.depth == "preview"), where
    # misconception detection is withheld -- never None in a full response.
    unresolved_misconception_count: int | None
    calculated_at: datetime | None = None


class NextActionOut(BaseModel):
    """A direct serialization of Slice 5's NextActionRecommendation -- the router
    never re-derives this, only shapes it for the wire."""

    action: str
    domain_code: str | None = None
    reason_codes: list[str]
    # Additive: with ATTEMPT_SCENARIO / REMEDIATE_MISCONCEPTION, the specific scenario
    # the learner has not yet seen (only a first exposure counts as evidence). Null otherwise.
    scenario_external_id: str | None = None
    # Additive, entitlement: the recommended scenario exists but this learner's plan
    # does not cover starting it. The recommendation itself is never altered.
    scenario_locked: bool = False


class TrackReadinessOut(BaseModel):
    """GET /me/tracks/{track_code}/readiness -- the whole learner-facing KSOR
    surface in one response: track identity, overall track readiness (Slice 2's
    aggregate_track_readiness, never re-implemented here), every domain in
    blueprint position order, and the Slice 5 next action."""

    track_code: str
    track_name: str
    overall_readiness_state: str
    overall_reason_codes: list[str]
    domains: list[DomainReadinessOut]
    next_action: NextActionOut
    # Additive, entitlement: "full" (Readiness Pass) or "preview" (Explorer). A preview
    # is the SAME computed readiness with depth fields withheld (mastery bands and
    # misconception counts set to None) -- never a different readiness rule.
    depth: str = "full"
