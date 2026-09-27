"""Entitlement: WHAT an authenticated learner may access.

Three concerns, three modules, never mixed:

* app/auth.py            -- WHO is this learner?        (verified Clerk subject)
* app/entitlements.py    -- WHAT can they access?        (this module)
* readiness services     -- WHAT does their evidence say? (untouched by this module)

Every access decision in the API is made here. Routes ask this module; they never test
a plan string themselves. The backend is authoritative -- the frontend only renders
the `AccessState` it is given.

Plans:

* ``free`` (Explorer): the absence of a currently-valid grant. One diagnostic exam per
  track, a small fixed set of sample scenarios, and a readiness *preview*.
* ``readiness_pass``: a time-boxed grant (90 days) in learner_entitlements. Everything
  -- for the grant's own track only. Access is always resolved for ONE track: a CCAO-F
  pass never unlocks another current or future track.

An expired or revoked pass is simply "no valid grant" -> free. Evidence created while
paid stays evidence forever; losing access only narrows what can be *started* or *seen
in depth*, never what was recorded.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import Domain, ExamAttempt, LearnerEntitlement, Scenario, Track, User
from app.models.attempt import AttemptStatus

PLAN_FREE = "free"
PLAN_READINESS_PASS = "readiness_pass"

READINESS_PASS_DAYS = 90

# Explorer allowances, per track. One submitted diagnostic; a few starts so a learner
# who closes the tab mid-exam is not locked out of their only diagnostic.
FREE_SUBMITTED_EXAMS_PER_TRACK = 1
FREE_EXAM_STARTS_PER_TRACK = 3
# The first N active scenarios in blueprint order (domain position, external_id) are
# the Explorer samples. Derived, not configured, so it follows the published catalog.
FREE_SAMPLE_SCENARIOS_PER_TRACK = 2


class Capability(str, enum.Enum):
    DIAGNOSTIC_EXAM = "diagnostic_exam"
    ADDITIONAL_PRACTICE_EXAMS = "additional_practice_exams"
    SAMPLE_SCENARIOS = "sample_scenarios"
    FULL_SCENARIO_LAB = "full_scenario_lab"
    READINESS_PREVIEW = "readiness_preview"
    FULL_READINESS = "full_readiness"
    REMEDIATION = "remediation"
    EVIDENCE_HISTORY = "evidence_history"


PLAN_CAPABILITIES: dict[str, frozenset[Capability]] = {
    PLAN_FREE: frozenset(
        {Capability.DIAGNOSTIC_EXAM, Capability.SAMPLE_SCENARIOS, Capability.READINESS_PREVIEW}
    ),
    PLAN_READINESS_PASS: frozenset(Capability),
}


@dataclass(frozen=True)
class AccessState:
    plan: str
    track_code: str | None = None
    starts_at: datetime | None = None
    expires_at: datetime | None = None
    entitlement_id: int | None = None

    def has(self, capability: Capability) -> bool:
        return capability in PLAN_CAPABILITIES[self.plan]

    @property
    def is_paid(self) -> bool:
        return self.plan == PLAN_READINESS_PASS


class EntitlementRequired(HTTPException):
    """402 with a machine-readable body. Distinct from 401 (who are you?) and 404
    (not yours): the learner is known and the object exists, the plan does not cover it."""

    def __init__(self, capability: Capability, message: str) -> None:
        super().__init__(
            status_code=402,
            detail={"code": "entitlement_required", "capability": capability.value, "message": message},
        )


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _aware(dt: datetime) -> datetime:
    # SQLite hands back naive datetimes even for timezone=True columns; they are UTC.
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


def current_entitlement(
    db: Session, user_id: int, track_code: str, now: datetime | None = None
) -> LearnerEntitlement | None:
    """This track's valid grant with the latest expiry, or None.
    Valid = active, started, not ended, and for exactly this track."""
    now = now or _utcnow()
    rows = db.scalars(
        select(LearnerEntitlement).where(
            LearnerEntitlement.user_id == user_id,
            LearnerEntitlement.track_code == track_code,
            LearnerEntitlement.plan == PLAN_READINESS_PASS,
            LearnerEntitlement.status == "active",
        )
    ).all()
    valid = [r for r in rows if _aware(r.starts_at) <= now < _aware(r.expires_at)]
    return max(valid, key=lambda r: _aware(r.expires_at), default=None)


def get_access(db: Session, user: User, track_code: str, now: datetime | None = None) -> AccessState:
    """The single answer to: what access does this learner have in this track right now?"""
    grant = current_entitlement(db, user.id, track_code, now)
    if grant is None:
        return AccessState(plan=PLAN_FREE, track_code=track_code)
    return AccessState(
        plan=PLAN_READINESS_PASS,
        track_code=track_code,
        starts_at=_aware(grant.starts_at),
        expires_at=_aware(grant.expires_at),
        entitlement_id=grant.id,
    )


# --- exams ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ExamAllowance:
    allowed: bool
    submitted: int
    started: int
    capability: Capability


def exam_allowance(db: Session, user: User, access: AccessState, track_id: int) -> ExamAllowance:
    """Whether this learner may generate another exam in this track."""
    submitted, started = db.execute(
        select(
            func.count().filter(ExamAttempt.status == AttemptStatus.SUBMITTED),
            func.count(),
        ).where(ExamAttempt.user_id == user.id, ExamAttempt.track_id == track_id)
    ).one()
    if access.has(Capability.ADDITIONAL_PRACTICE_EXAMS):
        return ExamAllowance(True, submitted, started, Capability.ADDITIONAL_PRACTICE_EXAMS)
    allowed = (
        submitted < FREE_SUBMITTED_EXAMS_PER_TRACK and started < FREE_EXAM_STARTS_PER_TRACK
    )
    return ExamAllowance(allowed, submitted, started, Capability.DIAGNOSTIC_EXAM)


def check_exam_generation(db: Session, user: User, access: AccessState, track_id: int) -> None:
    if not exam_allowance(db, user, access, track_id).allowed:
        raise EntitlementRequired(
            Capability.ADDITIONAL_PRACTICE_EXAMS,
            "Your free diagnostic exam is complete. Readiness Pass unlocks further "
            "practice exams so you can build repeated evidence in every domain.",
        )


# --- scenarios -----------------------------------------------------------------------


def sample_scenario_ids(db: Session, track_id: int) -> set[int]:
    return set(
        db.scalars(
            select(Scenario.id)
            .join(Domain, Scenario.domain_id == Domain.id)
            .where(Domain.track_id == track_id, Scenario.is_active.is_(True))
            .order_by(Domain.position, Scenario.external_id)
            .limit(FREE_SAMPLE_SCENARIOS_PER_TRACK)
        ).all()
    )


def scenario_locked(access: AccessState, scenario_id: int, samples: set[int]) -> bool:
    return not access.has(Capability.FULL_SCENARIO_LAB) and scenario_id not in samples


def check_scenario_start(db: Session, user: User, scenario: Scenario) -> None:
    """Gate STARTING a scenario, against the pass for the scenario's own track.
    Continuing an attempt already started is never gated: a pass that expires
    mid-scenario must not strand half-recorded evidence."""
    track_id, track_code = db.execute(
        select(Track.id, Track.code)
        .join(Domain, Domain.track_id == Track.id)
        .where(Domain.id == scenario.domain_id)
    ).one()
    if get_access(db, user, track_code).has(Capability.FULL_SCENARIO_LAB):
        return
    if scenario.id not in sample_scenario_ids(db, track_id):
        raise EntitlementRequired(
            Capability.FULL_SCENARIO_LAB,
            "This scenario is part of the full Scenario Lab. Readiness Pass unlocks every "
            "scenario, so each domain can gain independent scenario evidence.",
        )


# --- readiness presentation -----------------------------------------------------------


def present_readiness(db: Session, access: AccessState, out, track_id: int):
    """Shape an already-computed TrackReadinessOut for this learner's plan.

    Full readiness is returned untouched. An Explorer preview keeps everything that
    answers "what evidence do I have, what is missing, what next?" -- every domain's
    state, reason codes, evidence counts and the next action -- and withholds the depth
    that Readiness Pass sells: mastery bands and misconception detection. Values are
    only ever removed, never altered, so a preview can never disagree with full.
    """
    next_action = out.next_action
    if next_action.scenario_external_id is not None and not access.has(Capability.FULL_SCENARIO_LAB):
        sample_ids = sample_scenario_ids(db, track_id)
        rec_id = db.scalar(select(Scenario.id).where(Scenario.external_id == next_action.scenario_external_id))
        if rec_id not in sample_ids:
            next_action = next_action.model_copy(update={"scenario_locked": True})
    if access.has(Capability.FULL_READINESS):
        return out.model_copy(update={"next_action": next_action, "depth": "full"})
    domains = [
        d.model_copy(
            update={
                "recent_practice_mastery_band": None,
                "recent_scenario_mastery_band": None,
                "unresolved_misconception_count": None,
            }
        )
        for d in out.domains
    ]
    return out.model_copy(update={"domains": domains, "next_action": next_action, "depth": "preview"})


def require(access: AccessState, capability: Capability, message: str) -> None:
    if not access.has(capability):
        raise EntitlementRequired(capability, message)


# --- granting ------------------------------------------------------------------------


def plan_readiness_pass(
    db: Session, user: User, track_code: str, *, days: int = READINESS_PASS_DAYS,
    now: datetime | None = None,
) -> tuple[datetime, datetime]:
    """The (starts_at, expires_at) a new grant for this track would get -- read-only.
    Time left on THIS track's pass is extended rather than overlapped; another track's
    pass is irrelevant."""
    now = now or _utcnow()
    current = current_entitlement(db, user.id, track_code, now)
    starts_at = _aware(current.expires_at) if current is not None else now
    return starts_at, starts_at + timedelta(days=days)


def activate_readiness_pass(
    db: Session,
    user: User,
    track_code: str,
    *,
    source: str,
    days: int = READINESS_PASS_DAYS,
    provider: str | None = None,
    external_reference: str | None = None,
    granted_by: str | None = None,
    note: str | None = None,
    now: datetime | None = None,
) -> LearnerEntitlement:
    """Grant a Readiness Pass for one track. The only way access is ever created.

    Idempotent on external_reference (a re-delivered purchase returns the existing
    grant). A learner who still has time left is extended, not overlapped: the new
    grant starts when the current one ends. Flushes; the caller owns the commit.
    """
    now = now or _utcnow()
    if db.scalar(select(Track.id).where(Track.code == track_code)) is None:
        raise ValueError(f"unknown track {track_code!r}")
    if external_reference is not None:
        existing = db.scalar(
            select(LearnerEntitlement).where(LearnerEntitlement.external_reference == external_reference)
        )
        if existing is not None:
            if existing.user_id != user.id or existing.track_code != track_code:
                raise ValueError("external_reference already belongs to a different grant")
            return existing

    starts_at, expires_at = plan_readiness_pass(db, user, track_code, days=days, now=now)
    grant = LearnerEntitlement(
        user_id=user.id,
        plan=PLAN_READINESS_PASS,
        track_code=track_code,
        status="active",
        starts_at=starts_at,
        expires_at=expires_at,
        source=source,
        provider=provider,
        external_reference=external_reference,
        granted_by=granted_by,
        note=note,
        created_at=now,
    )
    db.add(grant)
    db.flush()
    return grant
