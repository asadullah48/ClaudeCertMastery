"""Deterministic recommendation: which domain Scenario Lab should point a learner at
next.

Gate C1 Slice 1 implements domain-level recommendation only (plan Section 7.4, steps
1-2 and 4). Step 3 ("recommend the scenario in that domain") is Slice 2+, once
scenario content actually exists to recommend -- there are no seeded Scenario rows in
this slice, so recommending one would have nothing real to point at.

No model call anywhere in this module. Matches this project's stated policy for AI's
role in progression (CLAUDE-CODE-MASTER-IMPLEMENTATION-PROMPT.md:226): AI may
recommend among already-permitted activities; it never decides them.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import AttemptDomainScore, AttemptStatus, Domain, ExamAttempt, Track

# The published blueprint's default entry point when a learner has no submitted exam
# attempt yet to derive a weakest domain from (plan Section 7.4, step 4).
DEFAULT_FALLBACK_POSITION = 1


class ScenarioRecommenderError(Exception):
    """Raised for a track/domain configuration the recommender cannot resolve."""


def recommend_domain_code(db: Session, *, user_id: int, track_code: str) -> str:
    """The domain code Scenario Lab should recommend next for this learner+track.

    1. Load the candidate's most recent SUBMITTED exam attempt for the track, if any.
    2. If found, take its AttemptDomainScore rows, sort by percentage ascending,
       tie-break by the domain's published `position` ascending (the same
       deterministic tie-break blueprint.py's allocate_items already uses), and
       recommend the lowest.
    3. If no submitted attempt exists yet, fall back to the domain at
       `position == DEFAULT_FALLBACK_POSITION` -- a stated, deterministic default,
       not an arbitrary one.

    No model call anywhere in this path.
    """
    track = db.scalar(select(Track).where(Track.code == track_code))
    if track is None:
        raise ScenarioRecommenderError(f"Unknown track: {track_code}")

    latest_attempt = db.scalar(
        select(ExamAttempt)
        .where(
            ExamAttempt.user_id == user_id,
            ExamAttempt.track_id == track.id,
            ExamAttempt.status == AttemptStatus.SUBMITTED,
        )
        .order_by(ExamAttempt.submitted_at.desc())
        .limit(1)
    )

    if latest_attempt is not None:
        domain_scores = db.scalars(
            select(AttemptDomainScore).where(
                AttemptDomainScore.attempt_id == latest_attempt.id
            )
        ).all()
        if domain_scores:
            domains_by_id = {
                d.id: d
                for d in db.scalars(
                    select(Domain).where(Domain.track_id == track.id)
                ).all()
            }
            weakest = min(
                domain_scores,
                key=lambda s: (s.percentage, domains_by_id[s.domain_id].position),
            )
            return domains_by_id[weakest.domain_id].code

    fallback = db.scalar(
        select(Domain).where(
            Domain.track_id == track.id,
            Domain.position == DEFAULT_FALLBACK_POSITION,
        )
    )
    if fallback is None:
        raise ScenarioRecommenderError(
            f"Track {track_code} has no domain at position {DEFAULT_FALLBACK_POSITION}."
        )
    return fallback.code
