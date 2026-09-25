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

from dataclasses import dataclass, field, replace

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    AttemptDomainScore,
    AttemptStatus,
    Domain,
    ExamAttempt,
    LearnerDomainState,
    Scenario,
    Track,
)
from app.services.learner_readiness import load_scenario_exposures
from app.services.readiness_policy import (
    INSUFFICIENT_DOMAIN_COVERAGE,
    MIN_PRACTICE_ITEMS_FOR_SUFFICIENCY,
    NO_EVIDENCE,
    STALE_EVIDENCE,
    STATE_APPROACHING_READY,
    STATE_INSUFFICIENT_EVIDENCE,
    STATE_READY,
    exposed_scenario_ids,
)

# The published blueprint's default entry point when a learner has no submitted exam
# attempt yet to derive a weakest domain from (plan Section 7.4, step 4).
DEFAULT_FALLBACK_POSITION = 1

# C3 Slice 5 next-action vocabulary (plan Section 11) -- bounded, five values, no
# synonyms. What to do, not why; `reason_codes` on the result carries the why, reusing
# readiness_policy's own bounded vocabulary rather than inventing a parallel one.
ACTION_COLLECT_PRACTICE_EVIDENCE = "COLLECT_PRACTICE_EVIDENCE"
ACTION_ATTEMPT_SCENARIO = "ATTEMPT_SCENARIO"
ACTION_REMEDIATE_MISCONCEPTION = "REMEDIATE_MISCONCEPTION"
ACTION_REASSESS_DOMAIN = "REASSESS_DOMAIN"
ACTION_PROCEED_TO_READINESS_EVALUATION = "PROCEED_TO_READINESS_EVALUATION"


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


# --- C3 Slice 5: evidence-based next-domain recommendation -------------------------
#
# Extends this module (plan Section 11: "add one new function... in the same module"
# -- additive, recommend_domain_code() above is untouched) with a second, richer
# entry point. Both functions answer the same underlying question this module has
# always owned -- "what should Scenario Lab point the learner at next" -- just from
# different evidence: recommend_domain_code() ranks by a single exam attempt's raw
# percentage; recommend_next_action() ranks by the learner's full KSOR readiness
# state (Slices 1-4) across every domain in the track. Kept as one module rather
# than a new one because they are the same responsibility maturing, not two
# responsibilities being conflated -- "what scenario within a domain" (unchanged,
# still recommend_domain_code()'s job when called directly) is deliberately left
# untouched, so a future caller can still compose readiness-driven domain choice
# with the existing scenario-level picker if that split is ever needed.
#
# Readiness classification remains owned exclusively by readiness_policy.py (Slice
# 2). Nothing below re-derives practice/scenario sufficiency thresholds, staleness,
# or misconception rules -- it only reads the already-classified `readiness_state`/
# `reason_codes` a LearnerDomainState row (or, for a domain with no row yet, the
# same INSUFFICIENT_DOMAIN_COVERAGE synthesis aggregate_track_readiness already
# defines in Slice 2) already committed to, and ranks across domains.


@dataclass(frozen=True)
class DomainReadinessCandidate:
    """One domain's readiness, decoupled from the ORM -- mirrors
    readiness_policy.DomainEvidenceSummary's own decoupled-from-the-ORM pattern, so
    the ranking algorithm below never needs a database to be tested."""

    domain_code: str
    domain_position: int
    readiness_state: str
    reason_codes: tuple[str, ...]
    practice_evidence_count: int
    scenario_evidence_count: int
    unresolved_misconception_count: int
    # Active scenarios in this domain whose answers the learner has NOT yet seen
    # (retake validity: only a first exposure is evidence), in recommendation order.
    # None = not supplied by the caller: treated as available, no specific scenario.
    unexposed_scenarios: tuple[str, ...] | None = None


@dataclass(frozen=True)
class NextActionRecommendation:
    action: str
    domain_code: str | None
    reason_codes: tuple[str, ...] = field(default_factory=tuple)
    # Set with ATTEMPT_SCENARIO when a specific unseen scenario is known.
    scenario_external_id: str | None = None


def _can_attempt_new_scenario(c: DomainReadinessCandidate) -> bool:
    """A scenario recommendation is only useful if a scenario the learner has not
    seen exists -- repeating a seen one cannot produce fresh evidence."""
    return c.unexposed_scenarios is None or len(c.unexposed_scenarios) > 0


def _next_scenario(c: DomainReadinessCandidate) -> str | None:
    return c.unexposed_scenarios[0] if c.unexposed_scenarios else None


def _has_zero_evidence(c: DomainReadinessCandidate) -> bool:
    return c.practice_evidence_count == 0 and c.scenario_evidence_count == 0


def _has_sufficient_practice_no_scenario(c: DomainReadinessCandidate) -> bool:
    return (
        c.practice_evidence_count >= MIN_PRACTICE_ITEMS_FOR_SUFFICIENCY
        and c.scenario_evidence_count == 0
    )


def _is_stale(c: DomainReadinessCandidate) -> bool:
    return c.readiness_state == STATE_APPROACHING_READY and STALE_EVIDENCE in c.reason_codes


def _fallback_action_for(c: DomainReadinessCandidate) -> str:
    """Plan Section 11, step 6's literal rule: attack whichever raw count is
    lower. A tie (including the 0/0 case already claimed by an earlier tier, kept
    here only for completeness) defaults to practice, matching this module's
    existing position/lowest-code-wins style of deterministic default."""
    if c.scenario_evidence_count < c.practice_evidence_count:
        return ACTION_ATTEMPT_SCENARIO
    return ACTION_COLLECT_PRACTICE_EVIDENCE


def recommend_next_action_from_candidates(
    candidates: list[DomainReadinessCandidate],
) -> NextActionRecommendation:
    """Pure ranking core -- no database, no network, no provider, fully
    unit-testable on its own (gate Section 15).

    Priority tiers, evaluated in this fixed order (plan Section 11); the first tier
    with any matching candidate wins; within a tier, the lowest `domain_position`
    is the deterministic tie-break (the same tie-break recommend_domain_code()
    already uses -- gate Section 12 forbids inventing a new one):

      1. COLLECT_PRACTICE_EVIDENCE -- truly zero evidence (never started at all).
      2. REMEDIATE_MISCONCEPTION -- any unresolved misconception (gate Section 7:
         concrete, actionable evidence outranks a generic evidence-gap nudge).
      3. ATTEMPT_SCENARIO -- practice sufficient, zero scenario evidence.
      4. REASSESS_DOMAIN -- approaching_ready via STALE_EVIDENCE (classify_domain_
         readiness has no other route to approaching_ready, so this tier covers
         every stale domain by construction).
      5. Catch-all -- everything not yet claimed (partial insufficient_evidence
         shapes tiers 1/3 don't cover, e.g. practice=0/scenario=1, and ordinary
         `developing` domains blocked only by a weak mastery band or non-diverse
         scenario evidence): action per _fallback_action_for's raw-count comparison.
      6. If nothing above matched at all, every domain is `ready` (the only state
         classify_domain_readiness ever leaves unclaimed by tiers 1-5) ->
         PROCEED_TO_READINESS_EVALUATION, track-level, no specific domain.
    """
    ordered = sorted(candidates, key=lambda c: c.domain_position)

    def _pick(matches, action):
        winner = min(matches, key=lambda c: c.domain_position)
        return NextActionRecommendation(
            action=action, domain_code=winner.domain_code, reason_codes=winner.reason_codes,
            scenario_external_id=_next_scenario(winner) if action == ACTION_ATTEMPT_SCENARIO else None,
        )

    zero_evidence = [c for c in ordered if _has_zero_evidence(c)]
    if zero_evidence:
        return _pick(zero_evidence, ACTION_COLLECT_PRACTICE_EVIDENCE)

    with_misconception = [c for c in ordered if c.unresolved_misconception_count > 0]
    if with_misconception:
        return _pick(with_misconception, ACTION_REMEDIATE_MISCONCEPTION)

    needs_scenario = [
        c for c in ordered
        if _has_sufficient_practice_no_scenario(c) and _can_attempt_new_scenario(c)
    ]
    if needs_scenario:
        return _pick(needs_scenario, ACTION_ATTEMPT_SCENARIO)

    stale = [c for c in ordered if _is_stale(c)]
    if stale:
        return _pick(stale, ACTION_REASSESS_DOMAIN)

    remaining = [c for c in ordered if c.readiness_state != STATE_READY]
    if remaining:
        winner = min(remaining, key=lambda c: c.domain_position)
        action = _fallback_action_for(winner)
        if action == ACTION_ATTEMPT_SCENARIO and not _can_attempt_new_scenario(winner):
            # Every scenario in this domain has been seen: a repeat cannot add
            # evidence, so practice is the only evidence still available here.
            action = ACTION_COLLECT_PRACTICE_EVIDENCE
        return NextActionRecommendation(
            action=action,
            domain_code=winner.domain_code,
            reason_codes=winner.reason_codes,
            scenario_external_id=_next_scenario(winner) if action == ACTION_ATTEMPT_SCENARIO else None,
        )

    return NextActionRecommendation(
        action=ACTION_PROCEED_TO_READINESS_EVALUATION, domain_code=None, reason_codes=()
    )


def unexposed_scenarios_by_domain(db: Session, *, user_id: int, track_id: int) -> dict[int, tuple[str, ...]]:
    """For every domain in the track: its ACTIVE scenarios whose answers this learner
    has not yet seen, ordered by external_id (so SCN-001 before SCN-002). Read-only:
    one scenario query plus one grouped exposure query."""
    exposed = exposed_scenario_ids(load_scenario_exposures(db, user_id=user_id, track_id=track_id))
    rows = db.execute(
        select(Scenario.domain_id, Scenario.external_id, Scenario.id)
        .join(Domain, Scenario.domain_id == Domain.id)
        .where(Domain.track_id == track_id, Scenario.is_active.is_(True))
        .order_by(Scenario.external_id)
    ).all()
    result: dict[int, list[str]] = {}
    for domain_id, external_id, scenario_id in rows:
        result.setdefault(domain_id, [])
        if scenario_id not in exposed:
            result[domain_id].append(external_id)
    return {domain_id: tuple(ids) for domain_id, ids in result.items()}


def recommend_next_action(
    db: Session, *, user_id: int, track_code: str
) -> NextActionRecommendation:
    """ORM adapter: loads the track's domains and the learner's existing
    LearnerDomainState rows, builds the pure candidate list, and delegates all
    ranking to recommend_next_action_from_candidates(). Read-only -- never writes
    LearnerDomainState or any other table; a recommendation is not evidence.

    A domain with no materialized projection row is never silently dropped or
    treated as nonexistent (gate Section 11): it becomes a candidate with zero
    evidence and the same INSUFFICIENT_DOMAIN_COVERAGE reason code
    aggregate_track_readiness (Slice 2) already defines for exactly this case --
    reused verbatim, not re-derived, so this remains "the approved deterministic
    boundary," never a second readiness rule living in the recommender.
    """
    track = db.scalar(select(Track).where(Track.code == track_code))
    if track is None:
        raise ScenarioRecommenderError(f"Unknown track: {track_code}")

    domains = db.scalars(
        select(Domain).where(Domain.track_id == track.id).order_by(Domain.position)
    ).all()
    if not domains:
        raise ScenarioRecommenderError(f"Track {track_code} has no domains.")

    states_by_domain_id = {
        row.domain_id: row
        for row in db.scalars(
            select(LearnerDomainState).where(
                LearnerDomainState.user_id == user_id,
                LearnerDomainState.track_id == track.id,
            )
        ).all()
    }

    candidates = []
    for domain in domains:
        row = states_by_domain_id.get(domain.id)
        if row is None:
            candidates.append(
                DomainReadinessCandidate(
                    domain_code=domain.code,
                    domain_position=domain.position,
                    readiness_state=STATE_INSUFFICIENT_EVIDENCE,
                    reason_codes=(NO_EVIDENCE, INSUFFICIENT_DOMAIN_COVERAGE),
                    practice_evidence_count=0,
                    scenario_evidence_count=0,
                    unresolved_misconception_count=0,
                )
            )
        else:
            candidates.append(
                DomainReadinessCandidate(
                    domain_code=domain.code,
                    domain_position=domain.position,
                    readiness_state=row.readiness_state,
                    reason_codes=tuple(row.reason_codes),
                    practice_evidence_count=row.practice_evidence_count,
                    scenario_evidence_count=row.scenario_evidence_count,
                    unresolved_misconception_count=row.unresolved_misconception_count,
                )
            )

    unexposed = unexposed_scenarios_by_domain(db, user_id=user_id, track_id=track.id)
    code_to_id = {d.code: d.id for d in domains}
    candidates = [
        replace(c, unexposed_scenarios=unexposed.get(code_to_id[c.domain_code], ()))
        for c in candidates
    ]
    return recommend_next_action_from_candidates(candidates)
