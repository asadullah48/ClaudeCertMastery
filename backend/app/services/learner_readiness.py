"""KSOR Slice 3/4: evidence aggregation + misconception lifecycle + projection
recompute.

Gate C3, Slices 3-4. See docs/GATE-C3-KSOR-READINESS-IMPLEMENTATION-PLAN.md
Sections 5, 6, 10, 12, 13 for the approved contract this module implements.

This is the one place in KSOR that touches the ORM. It reads already-committed,
immutable evidence (ExamAttempt/AttemptItem/AttemptDomainScore, ScenarioAttempt,
ScenarioEvent, ScenarioStepOption), never writes to any evidence table, and writes
only LearnerDomainState -- the disposable, fully-rebuildable projection. All
classification logic stays in app/services/readiness_policy.py (Slice 2); this
module never duplicates a readiness rule, it only assembles the pure
DomainEvidenceSummary that module's classifier consumes. Not wired into any live
request path yet (Slice 7) -- callable only from tests/scripts at this point.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import (
    AttemptDomainScore,
    AttemptItem,
    AttemptStatus,
    Domain,
    ExamAttempt,
    LearnerDomainState,
    Scenario,
    ScenarioAttempt,
    ScenarioEvent,
    ScenarioStep,
    ScenarioStepOption,
    Track,
    User,
)
from app.services.readiness_policy import DomainEvidenceSummary, classify_domain_readiness
from app.services.scoring import MasteryBand

# Mirrors the Slice 1 schema's own default/server_default (readiness.py,
# cecda87bf72a) -- represents projection *policy/schema* semantics, never git/migration
# state. Set explicitly on every upsert (not left to the ORM column default) so an
# UPDATE path is just as deterministic as an INSERT path. A future policy-version bump
# changes this one constant, never migration revisions.
PROJECTION_VERSION = 1


def _as_utc(dt: datetime | None) -> datetime | None:
    """SQLite (test-only; production runs Postgres) does not round-trip tzinfo even
    for DateTime(timezone=True) columns -- every timestamp this codebase ever writes
    is already UTC (datetime.now(timezone.utc), everywhere), so a naive value read
    back is safely reinterpreted as UTC, never as local time. Applied at this
    module's ORM boundary so the pure Slice 2 classifier only ever receives
    tz-aware datetimes, regardless of which database produced them."""
    if dt is None or dt.tzinfo is not None:
        return dt
    return dt.replace(tzinfo=timezone.utc)


@dataclass(frozen=True)
class _MisconceptionEventRecord:
    """One eligible step_answered event, reduced to only what the lifecycle
    reducer needs -- decoupled from the ORM row, mirroring
    readiness_policy.DomainEvidenceSummary's own decoupled-from-the-ORM pattern."""

    step_id: int
    occurred_at: datetime
    event_id: int
    tags_in_event: frozenset[str]
    is_correct: bool


def _reduce_unresolved_tags(
    tag_to_steps: dict[str, set[int]],
    events: list[_MisconceptionEventRecord],
) -> set[str]:
    """Pure reduction over an explicitly-ordered evidence stream -- no DB, no
    datetime.now(), deterministic given the same inputs (plan Section 10; gate
    Section 13's determinism requirement).

    For each candidate tag, walks only the events touching one of that tag's
    authored steps, in (occurred_at, event_id) order, and keeps the LATEST
    state-changing outcome:

      - the tag reappearing in that event's selected-incorrect-option tags -> active
        (an occurrence, whether the first or a recurrence after a prior resolution);
      - a CORRECT answer on one of the tag's steps -> resolved (successful
        performance against content explicitly associated with the tag -- gate
        Section 6's stricter bar, not merely "this tag wasn't reproduced this time").

    An incorrect answer that does NOT reproduce this specific tag (the learner
    picked a *different* wrong option on a step that authors more than one
    misconception) is a no-op for this tag's state: it neither confirms nor clears
    it, since no successful performance against this tag's content was
    demonstrated. This is what stops an unrelated near-miss from silently forging
    a resolution.
    """
    ordered = sorted(events, key=lambda e: (e.occurred_at, e.event_id))
    unresolved: set[str] = set()
    for tag, steps in tag_to_steps.items():
        state: str | None = None  # None = this tag was authored but never touched
        for event in ordered:
            if event.step_id not in steps:
                continue
            if tag in event.tags_in_event:
                state = "active"
            elif event.is_correct:
                state = "resolved"
            # else: wrong via a different option on this step -- no-op, see above.
        if state == "active":
            unresolved.add(tag)
    return unresolved


def derive_unresolved_misconceptions(
    db: Session, *, user_id: int, domain_id: int
) -> int:
    """Number of distinct misconception tags whose latest qualifying lifecycle
    state is unresolved, for this user + domain (plan Section 10; gate Sections
    4-9). Read-only over ScenarioStepOption/ScenarioEvent/ScenarioAttempt/Scenario
    -- writes nothing, mutates nothing.

    Identity: the authored `ScenarioStepOption.misconception_tag` string itself --
    never inferred, merged, or semantically compared (gate Section 4/17).

    Scope: only misconception evidence whose Scenario belongs to `domain_id`
    (which implies the correct track too, since a domain belongs to exactly one
    track -- no separate track filter needed, matching build_domain_evidence_summary's
    own scenario-side scoping) and whose ScenarioEvent.user_id matches `user_id`
    contributes -- proven isolated across users/domains/tracks in tests.

    Eligibility: only step_answered events belonging to a SUBMITTED ScenarioAttempt
    count (mirrors Slice 3's own scenario eligibility rule) -- an in_progress
    attempt's events (which do exist in the DB the moment any step is answered,
    per scenarios.py's per-step event write) are excluded until/unless that attempt
    is later submitted.
    """
    tag_rows = db.execute(
        select(ScenarioStepOption.misconception_tag, ScenarioStepOption.step_id)
        .join(ScenarioStep, ScenarioStepOption.step_id == ScenarioStep.id)
        .join(Scenario, ScenarioStep.scenario_id == Scenario.id)
        .where(
            Scenario.domain_id == domain_id,
            ScenarioStepOption.misconception_tag.is_not(None),
        )
    ).all()
    tag_to_steps: dict[str, set[int]] = {}
    for tag, step_id in tag_rows:
        tag_to_steps.setdefault(tag, set()).add(step_id)
    if not tag_to_steps:
        return 0

    event_rows = db.execute(
        select(
            ScenarioEvent.step_id,
            ScenarioEvent.occurred_at,
            ScenarioEvent.id,
            ScenarioEvent.payload,
        )
        .join(ScenarioAttempt, ScenarioEvent.scenario_attempt_id == ScenarioAttempt.id)
        .join(Scenario, ScenarioAttempt.scenario_id == Scenario.id)
        .where(
            ScenarioEvent.user_id == user_id,
            ScenarioEvent.event_type == "step_answered",
            Scenario.domain_id == domain_id,
            ScenarioAttempt.status == "submitted",
        )
    ).all()
    events = [
        _MisconceptionEventRecord(
            step_id=step_id,
            occurred_at=_as_utc(occurred_at),
            event_id=event_id,
            tags_in_event=frozenset(payload.get("misconception_tags") or []),
            is_correct=bool(payload.get("is_correct")),
        )
        for step_id, occurred_at, event_id, payload in event_rows
    ]

    return len(_reduce_unresolved_tags(tag_to_steps, events))


class ReadinessComputationError(Exception):
    """Raised for a user/track/domain combination the service cannot resolve.

    Matches ScenarioRecommenderError's role in scenario_recommender.py: a genuine
    data/identity error, not a "zero evidence" case (which is not an error -- it
    produces an ordinary insufficient_evidence projection).
    """


def _validate_identity(db: Session, *, user_id: int, track_id: int, domain_id: int) -> None:
    if db.get(User, user_id) is None:
        raise ReadinessComputationError(f"Unknown user_id: {user_id}")
    track = db.get(Track, track_id)
    if track is None:
        raise ReadinessComputationError(f"Unknown track_id: {track_id}")
    domain = db.get(Domain, domain_id)
    if domain is None:
        raise ReadinessComputationError(f"Unknown domain_id: {domain_id}")
    if domain.track_id != track_id:
        raise ReadinessComputationError(
            f"Domain {domain_id} does not belong to track {track_id}."
        )


def build_domain_evidence_summary(
    db: Session,
    *,
    user_id: int,
    track_id: int,
    domain_id: int,
    now: datetime | None = None,
) -> DomainEvidenceSummary:
    """Read-only aggregation over already-committed evidence for one
    (user, track, domain). Every query below is a SELECT; nothing in this function
    ever writes to ExamAttempt/AttemptItem/AttemptDomainScore/ScenarioAttempt.

    Practice eligibility (plan Section 5): only AttemptItem rows belonging to a
    SUBMITTED ExamAttempt for this exact user+track, denormalized to this domain --
    an in_progress/abandoned attempt contributes nothing. `practice_evidence_count`
    counts every eligible AttemptItem with no cap and no distinct-question dedup
    (plan Section 6: exam evidence gets no special anti-inflation handling in v1 --
    a learner re-answering the same question across sittings is ordinary practice,
    not evidence farming).

    Scenario eligibility (plan Section 5): only ScenarioAttempt rows with
    status == "submitted" whose Scenario belongs to this domain, for this user --
    domain (and therefore track, since a domain belongs to exactly one track) is
    resolved via the Scenario -> Domain relationship, never a denormalized column
    that doesn't exist on ScenarioAttempt. `scenario_evidence_count` counts every
    eligible attempt (repeats included, honestly); `distinct_scenario_content_versions`
    separately counts distinct (scenario_id, scenario_content_version) pairs, per
    plan Section 6's duplicate-evidence policy -- raw count and diversity are kept as
    two different fields, never collapsed into one.
    """
    now = now or datetime.now(timezone.utc)

    practice_evidence_count = (
        db.scalar(
            select(func.count(AttemptItem.id))
            .join(ExamAttempt, AttemptItem.attempt_id == ExamAttempt.id)
            .where(
                ExamAttempt.user_id == user_id,
                ExamAttempt.track_id == track_id,
                ExamAttempt.status == AttemptStatus.SUBMITTED,
                AttemptItem.domain_id == domain_id,
            )
        )
        or 0
    )

    # Recent practice mastery band + its own evidence timestamp come from the SAME
    # row (the most recent submitted attempt's per-domain rollup) -- never two
    # independently-queried facts that could drift against each other.
    latest_practice = db.execute(
        select(AttemptDomainScore.mastery_band, ExamAttempt.submitted_at)
        .join(ExamAttempt, AttemptDomainScore.attempt_id == ExamAttempt.id)
        .where(
            ExamAttempt.user_id == user_id,
            ExamAttempt.track_id == track_id,
            ExamAttempt.status == AttemptStatus.SUBMITTED,
            AttemptDomainScore.domain_id == domain_id,
        )
        .order_by(ExamAttempt.submitted_at.desc(), ExamAttempt.id.desc())
        .limit(1)
    ).first()
    recent_practice_mastery_band = (
        MasteryBand(latest_practice[0]) if latest_practice is not None else None
    )
    practice_most_recent_at = _as_utc(latest_practice[1]) if latest_practice is not None else None

    scenario_attempts = db.scalars(
        select(ScenarioAttempt)
        .join(Scenario, ScenarioAttempt.scenario_id == Scenario.id)
        .where(
            ScenarioAttempt.user_id == user_id,
            Scenario.domain_id == domain_id,
            ScenarioAttempt.status == "submitted",
        )
    ).all()
    scenario_evidence_count = len(scenario_attempts)
    distinct_scenario_content_versions = len(
        {(sa.scenario_id, sa.scenario_content_version) for sa in scenario_attempts}
    )
    latest_scenario = max(
        scenario_attempts,
        key=lambda sa: (_as_utc(sa.submitted_at) or datetime.min.replace(tzinfo=timezone.utc), sa.id),
        default=None,
    )
    recent_scenario_mastery_band = (
        MasteryBand(latest_scenario.mastery_band)
        if latest_scenario is not None and latest_scenario.mastery_band
        else None
    )
    scenario_most_recent_at = (
        _as_utc(latest_scenario.submitted_at) if latest_scenario is not None else None
    )

    # most_recent_evidence_at: newest qualifying evidence timestamp across BOTH
    # sources (plan Section 10) -- never projection/request time, never a row's
    # creation time unrelated to when the evidence became eligible.
    candidates = [t for t in (practice_most_recent_at, scenario_most_recent_at) if t is not None]
    most_recent_evidence_at = max(candidates) if candidates else None

    # Slice 4: real evidence-backed derivation (see derive_unresolved_misconceptions'
    # own docstring for the occurrence/resolution rules) -- no longer the Slice 3
    # interim placeholder.
    unresolved_misconception_count = derive_unresolved_misconceptions(
        db, user_id=user_id, domain_id=domain_id
    )

    return DomainEvidenceSummary(
        practice_evidence_count=practice_evidence_count,
        scenario_evidence_count=scenario_evidence_count,
        distinct_scenario_content_versions=distinct_scenario_content_versions,
        recent_practice_mastery_band=recent_practice_mastery_band,
        recent_scenario_mastery_band=recent_scenario_mastery_band,
        most_recent_evidence_at=most_recent_evidence_at,
        unresolved_misconception_count=unresolved_misconception_count,
        now=now,
    )


def recompute_learner_domain_state(
    db: Session,
    *,
    user_id: int,
    track_id: int,
    domain_id: int,
    now: datetime | None = None,
) -> LearnerDomainState:
    """Full recompute, not incremental (plan Section 2). Reads all eligible evidence,
    builds a DomainEvidenceSummary, calls the pure Slice 2 classifier, upserts the
    (user, track, domain) projection row, commits, returns it.

    Transaction ownership: this function owns and commits its own transaction, in
    the caller's session, matching the plan's explicit Section 12 design ("one
    db.commit() at the end, in a session the caller controls") -- not a hidden
    commit of unrelated work, since the only writes in this session at this point
    are this function's own upsert. This keeps recompute a separate, subsequent
    transaction from whatever evidence write preceded it (plan Section 13): a
    future router caller (Slice 7) would call this only *after* its own evidence
    commit has already succeeded, never sharing a transaction with it, so a
    recompute failure can never roll back or invalidate an already-successful
    learner submission.

    Idempotent by construction: calling this twice in a row with no new evidence
    re-reads the same evidence, re-derives the same DomainEvidenceSummary, and
    upserts the same field values (only `calculated_at` differs, tracking when the
    evaluation ran) -- never a second row (the Slice 1 unique constraint on
    (user_id, track_id, domain_id) is respected by the upsert-by-lookup below), never
    an incremented counter, never accumulated reason codes.
    """
    _validate_identity(db, user_id=user_id, track_id=track_id, domain_id=domain_id)

    summary = build_domain_evidence_summary(
        db, user_id=user_id, track_id=track_id, domain_id=domain_id, now=now
    )
    assessment = classify_domain_readiness(summary)

    row = db.scalar(
        select(LearnerDomainState).where(
            LearnerDomainState.user_id == user_id,
            LearnerDomainState.track_id == track_id,
            LearnerDomainState.domain_id == domain_id,
        )
    )
    if row is None:
        row = LearnerDomainState(user_id=user_id, track_id=track_id, domain_id=domain_id)
        db.add(row)

    row.practice_evidence_count = summary.practice_evidence_count
    row.scenario_evidence_count = summary.scenario_evidence_count
    row.distinct_scenario_content_versions = summary.distinct_scenario_content_versions
    row.recent_practice_mastery_band = (
        summary.recent_practice_mastery_band.value
        if summary.recent_practice_mastery_band is not None
        else None
    )
    row.recent_scenario_mastery_band = (
        summary.recent_scenario_mastery_band.value
        if summary.recent_scenario_mastery_band is not None
        else None
    )
    row.most_recent_evidence_at = summary.most_recent_evidence_at
    row.unresolved_misconception_count = summary.unresolved_misconception_count
    row.readiness_state = assessment.state
    row.reason_codes = list(assessment.reason_codes)
    # calculated_at is when THIS evaluation ran -- deliberately distinct from
    # most_recent_evidence_at (the newest evidence's own timestamp, which can be far
    # older than the moment recompute happened to run).
    row.calculated_at = summary.now
    row.projection_version = PROJECTION_VERSION

    db.commit()
    return row
