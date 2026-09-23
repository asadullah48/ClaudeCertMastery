"""KSOR Slice 6: the learner-facing readiness API.

Gate C3, Slice 6. A read-only adapter over the C3 system already built (Slices
1-5) -- it classifies nothing, recomputes nothing, and persists nothing. It only
reads already-materialized `LearnerDomainState` rows (or, for a domain with none
yet, synthesizes the exact same zero-evidence/INSUFFICIENT_DOMAIN_COVERAGE shape
Slice 5's own adapter already established) and hands them to the two existing pure
functions that own this logic: `aggregate_track_readiness` (Slice 2, overall track
state) and `recommend_next_action_from_candidates` (Slice 5, what to do next).
Neither rule set is reproduced here.

Deviation from the plan's original two-endpoint sketch (Section 15:
`GET /me/readiness` + `GET /me/next-action`), explicitly authorized by this gate's
own Section 6 ("prefer ONE cohesive track-level learner endpoint... do not invent
multiple APIs... if one bounded response can truthfully provide them together"):
one endpoint, `GET /me/tracks/{track_code}/readiness`, returns both.

No real per-request authentication exists anywhere in this codebase yet (every
router -- exams.py, scenarios.py, attempts.py -- resolves the same shared dev user
via `_current_user(db)`, documented as a stated limitation, not hidden). This
router inherits that identical, already-audited posture rather than inventing a
new one (plan Section 23: "this plan does not claim to fix it and does not need to
for founder-only validation to remain valid").
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Domain, LearnerDomainState, Track, User
from app.schemas import DomainReadinessOut, NextActionOut, TrackReadinessOut
from app.services.readiness_policy import (
    INSUFFICIENT_DOMAIN_COVERAGE,
    NO_EVIDENCE,
    STATE_INSUFFICIENT_EVIDENCE,
    ReadinessAssessment,
    aggregate_track_readiness,
)
from app.services.scenario_recommender import (
    DomainReadinessCandidate,
    recommend_next_action_from_candidates,
)

router = APIRouter(prefix="/me", tags=["readiness"])

DEV_USER_EMAIL = "dev@certmastery.local"


def _current_user(db: Session) -> User:
    """Stand-in for authentication, matching exams.py/scenarios.py exactly (D-7) --
    no new auth mechanism is introduced here."""
    user = db.scalar(select(User).where(User.email == DEV_USER_EMAIL))
    if user is None:
        raise HTTPException(500, "Dev user missing. Run: python seed.py")
    return user


@router.get("/tracks/{track_code}/readiness", response_model=TrackReadinessOut)
def get_track_readiness(track_code: str, db: Session = Depends(get_db)) -> TrackReadinessOut:
    """Everything currently known about this learner's readiness in this track,
    plus the deterministic next action -- read-only. Issues only SELECTs: no
    recompute, no upsert, no synthesized row is ever persisted.

    Query strategy (gate Section 28): one query for the track, one for its
    domains, one for the learner's existing projections -- then everything is
    mapped and aggregated in memory. No per-domain query.
    """
    user = _current_user(db)

    track = db.scalar(select(Track).where(Track.code == track_code))
    if track is None:
        raise HTTPException(status_code=404, detail=f"Track {track_code} not found.")

    domains = db.scalars(
        select(Domain).where(Domain.track_id == track.id).order_by(Domain.position)
    ).all()
    if not domains:
        raise HTTPException(
            status_code=404, detail=f"Track {track_code} has no published blueprint yet."
        )

    states_by_domain_id = {
        row.domain_id: row
        for row in db.scalars(
            select(LearnerDomainState).where(
                LearnerDomainState.user_id == user.id,
                LearnerDomainState.track_id == track.id,
            )
        ).all()
    }

    domain_entries: list[DomainReadinessOut] = []
    assessments: dict[str, ReadinessAssessment] = {}
    candidates: list[DomainReadinessCandidate] = []

    for domain in domains:
        row = states_by_domain_id.get(domain.id)
        if row is None:
            # Missing-domain synthesis (gate Section 8/9): the same shape Slice 5's
            # own adapter already establishes, reused verbatim -- never a second
            # readiness rule, never persisted, never a fabricated calculated_at.
            state = STATE_INSUFFICIENT_EVIDENCE
            reason_codes = [NO_EVIDENCE, INSUFFICIENT_DOMAIN_COVERAGE]
            domain_entries.append(
                DomainReadinessOut(
                    domain_code=domain.code,
                    domain_name=domain.name,
                    domain_position=domain.position,
                    readiness_state=state,
                    reason_codes=reason_codes,
                    is_materialized=False,
                    practice_evidence_count=0,
                    scenario_evidence_count=0,
                    distinct_scenario_content_versions=0,
                    recent_practice_mastery_band=None,
                    recent_scenario_mastery_band=None,
                    most_recent_evidence_at=None,
                    unresolved_misconception_count=0,
                    calculated_at=None,
                )
            )
            assessments[domain.code] = ReadinessAssessment(
                state=state, evidence_sufficient=False, reason_codes=reason_codes
            )
            candidates.append(
                DomainReadinessCandidate(
                    domain_code=domain.code, domain_position=domain.position,
                    readiness_state=state, reason_codes=tuple(reason_codes),
                    practice_evidence_count=0, scenario_evidence_count=0,
                    unresolved_misconception_count=0,
                )
            )
        else:
            domain_entries.append(
                DomainReadinessOut(
                    domain_code=domain.code,
                    domain_name=domain.name,
                    domain_position=domain.position,
                    readiness_state=row.readiness_state,
                    reason_codes=list(row.reason_codes),
                    is_materialized=True,
                    practice_evidence_count=row.practice_evidence_count,
                    scenario_evidence_count=row.scenario_evidence_count,
                    distinct_scenario_content_versions=row.distinct_scenario_content_versions,
                    recent_practice_mastery_band=row.recent_practice_mastery_band,
                    recent_scenario_mastery_band=row.recent_scenario_mastery_band,
                    most_recent_evidence_at=row.most_recent_evidence_at,
                    unresolved_misconception_count=row.unresolved_misconception_count,
                    calculated_at=row.calculated_at,
                )
            )
            assessments[domain.code] = ReadinessAssessment(
                state=row.readiness_state,
                evidence_sufficient=row.readiness_state != STATE_INSUFFICIENT_EVIDENCE,
                reason_codes=list(row.reason_codes),
            )
            candidates.append(
                DomainReadinessCandidate(
                    domain_code=domain.code, domain_position=domain.position,
                    readiness_state=row.readiness_state,
                    reason_codes=tuple(row.reason_codes),
                    practice_evidence_count=row.practice_evidence_count,
                    scenario_evidence_count=row.scenario_evidence_count,
                    unresolved_misconception_count=row.unresolved_misconception_count,
                )
            )

    overall = aggregate_track_readiness(assessments, {d.code for d in domains})
    next_action = recommend_next_action_from_candidates(candidates)

    return TrackReadinessOut(
        track_code=track.code,
        track_name=track.name,
        overall_readiness_state=overall.state,
        overall_reason_codes=list(overall.reason_codes),
        domains=domain_entries,
        next_action=NextActionOut(
            action=next_action.action,
            domain_code=next_action.domain_code,
            reason_codes=list(next_action.reason_codes),
        ),
    )
