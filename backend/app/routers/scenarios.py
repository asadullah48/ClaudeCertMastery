"""Scenario Lab: attempt lifecycle (Gate C1 Slice 2).

Trust boundary, stated once and enforced everywhere in this file: **client owns
interaction, server owns truth.** Every route re-derives correctness, consequence,
and progression from server-held state and the Slice 1 deterministic engine; nothing
a client sends is ever trusted as an evaluation result.

No AI. No MCP. No Zia. No KSOR. Every route here works identically whether
Anthropic/Zia are configured or not, because neither is imported.
"""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.database import get_db
from app.models import Domain, Scenario, ScenarioAttempt, ScenarioEvent, ScenarioStep, ScenarioStepAttempt, User
from app.schemas import (
    ScenarioAttemptOut,
    ScenarioHintResponse,
    ScenarioListItemOut,
    ScenarioResultOut,
    ScenarioStartResponse,
    ScenarioStepAnswerRequest,
    ScenarioStepAnswerResponse,
    ScenarioStepOptionOut,
    ScenarioStepOut,
    SelectedOptionFeedback,
)
from app.services.scenario_scoring import (
    RevealedHint,
    ScenarioScoringError,
    StepOption,
    aggregate_scenario_score,
    build_scenario_event_payload,
    evaluate_step_decision,
)

router = APIRouter(tags=["scenarios"])

DEV_USER_EMAIL = "dev@certmastery.local"


def _current_user(db: Session) -> User:
    """Stand-in for authentication, matching exams.py/zia.py exactly (D-7).

    Section 13 of this slice's brief: document the actual repository state rather
    than pretend stronger security exists. There is no real learner auth anywhere in
    this codebase yet -- every route below still enforces the OWNERSHIP check
    (attempt.user_id == this user's id), which is meaningful groundwork even under a
    single dev user, and becomes load-bearing the moment real auth lands, exactly as
    the approved plan states (Section 4).
    """
    user = db.scalar(select(User).where(User.email == DEV_USER_EMAIL))
    if user is None:
        raise HTTPException(500, "Dev user missing. Run: python seed.py")
    return user


# --- read-only discovery -----------------------------------------------------------


@router.get("/scenarios", response_model=list[ScenarioListItemOut])
def list_scenarios(track_code: str, db: Session = Depends(get_db)) -> list[ScenarioListItemOut]:
    """Active scenarios for a track. No step content, no options -- discovery only."""
    rows = db.scalars(
        select(Scenario)
        .join(Domain, Scenario.domain_id == Domain.id)
        .where(Domain.track.has(code=track_code), Scenario.is_active.is_(True))
    ).all()
    return [
        ScenarioListItemOut(
            external_id=s.external_id,
            title=s.title,
            domain_code=db.get(Domain, s.domain_id).code,
            difficulty=s.difficulty,
        )
        for s in rows
    ]


# --- shared helpers ------------------------------------------------------------------


def _load_owned_attempt(db: Session, attempt_id: int, user: User) -> ScenarioAttempt:
    """404 (never 403) on a missing OR mismatched-owner attempt -- the exact
    information-hiding posture the approved plan specifies (Section 4): a caller
    cannot distinguish "does not exist" from "exists but is not yours".
    """
    attempt = db.get(ScenarioAttempt, attempt_id)
    if attempt is None or attempt.user_id != user.id:
        raise HTTPException(404, f"Scenario attempt {attempt_id} not found.")
    return attempt


def _current_step_position(db: Session, attempt: ScenarioAttempt) -> int:
    """Server-authoritative progression, derived (no stored 'current position'
    column -- the smallest state machine necessary, per Section 6). One past the
    highest ANSWERED step's position; 1 if none answered yet.
    """
    answered_positions = db.scalars(
        select(ScenarioStepAttempt.position)
        .where(
            ScenarioStepAttempt.scenario_attempt_id == attempt.id,
            ScenarioStepAttempt.is_correct.is_not(None),
        )
    ).all()
    return (max(answered_positions) + 1) if answered_positions else 1


def _step_out(step: ScenarioStep, total_steps: int) -> ScenarioStepOut:
    return ScenarioStepOut(
        position=step.position,
        total_steps=total_steps,
        prompt_text=step.prompt_text,
        step_type=step.step_type,
        options=[
            ScenarioStepOptionOut(id=o.id, label=o.label, text=o.text, position=o.position)
            for o in step.options
        ],
        hints_available=len(step.hints),
    )


def _get_or_create_step_attempt(
    db: Session, attempt: ScenarioAttempt, step: ScenarioStep
) -> ScenarioStepAttempt:
    """The row that carries a step's in-progress state (hints revealed so far, then
    the final evaluation). Created on first touch -- either the first hint request or
    the answer itself -- keyed on the same (scenario_attempt_id, step_id) uniqueness
    the schema already enforces.
    """
    row = db.scalar(
        select(ScenarioStepAttempt).where(
            ScenarioStepAttempt.scenario_attempt_id == attempt.id,
            ScenarioStepAttempt.step_id == step.id,
        )
    )
    if row is None:
        row = ScenarioStepAttempt(
            scenario_attempt_id=attempt.id, step_id=step.id, position=step.position,
            selected_option_ids=[], hints_revealed=[],
        )
        db.add(row)
        db.flush()
    return row


def _load_scenario_and_steps(db: Session, scenario_id: int) -> tuple[Scenario, list[ScenarioStep]]:
    scenario = db.scalar(
        select(Scenario)
        .options(selectinload(Scenario.steps).selectinload(ScenarioStep.options))
        .options(selectinload(Scenario.steps).selectinload(ScenarioStep.hints))
        .where(Scenario.id == scenario_id)
    )
    steps = sorted(scenario.steps, key=lambda s: s.position)
    return scenario, steps


# --- attempt lifecycle ---------------------------------------------------------------


@router.post("/scenarios/{external_id}/start", response_model=ScenarioStartResponse, status_code=201)
def start_scenario(
    external_id: str, db: Session = Depends(get_db)
) -> ScenarioStartResponse:
    scenario = db.scalar(
        select(Scenario)
        .options(selectinload(Scenario.steps).selectinload(ScenarioStep.options))
        .options(selectinload(Scenario.steps).selectinload(ScenarioStep.hints))
        .where(Scenario.external_id == external_id, Scenario.is_active.is_(True))
    )
    if scenario is None:
        raise HTTPException(404, f"Scenario {external_id} not found.")
    steps = sorted(scenario.steps, key=lambda s: s.position)
    if not steps:
        raise HTTPException(409, f"Scenario {external_id} has no authored steps.")

    user = _current_user(db)
    domain = db.get(Domain, scenario.domain_id)

    # The client dictates nothing here beyond WHICH scenario -- content_version,
    # initial state and step 1 are entirely server-determined (Section 3).
    attempt = ScenarioAttempt(
        user_id=user.id, scenario_id=scenario.id, status="in_progress",
        scenario_content_version=scenario.content_version,
        started_at=datetime.now(timezone.utc),
    )
    db.add(attempt)
    db.flush()

    db.add(
        ScenarioEvent(
            scenario_attempt_id=attempt.id, user_id=user.id,
            event_type="scenario_started", step_id=None,
            payload={
                "scenario_external_id": scenario.external_id,
                "scenario_content_version": scenario.content_version,
            },
        )
    )
    db.commit()

    return ScenarioStartResponse(
        attempt_id=attempt.id,
        scenario_external_id=scenario.external_id,
        title=scenario.title,
        setup_text=scenario.setup_text,
        domain_code=domain.code,
        status=attempt.status,
        current_step=_step_out(steps[0], len(steps)),
    )


@router.post(
    "/scenario-attempts/{attempt_id}/steps/{position}/hint",
    response_model=ScenarioHintResponse,
)
def reveal_hint(
    attempt_id: int, position: int, db: Session = Depends(get_db)
) -> ScenarioHintResponse:
    user = _current_user(db)
    attempt = _load_owned_attempt(db, attempt_id, user)
    if attempt.status != "in_progress":
        raise HTTPException(409, "This scenario attempt has already been completed.")

    scenario, steps = _load_scenario_and_steps(db, attempt.scenario_id)
    if scenario.content_version != attempt.scenario_content_version:
        raise HTTPException(
            409, "This scenario's content has changed since the attempt started."
        )

    current_position = _current_step_position(db, attempt)
    if position != current_position:
        raise HTTPException(
            409, f"Step {position} is not the current step ({current_position})."
        )

    step = next((s for s in steps if s.position == position), None)
    if step is None:
        raise HTTPException(404, f"Step {position} not found.")

    row = _get_or_create_step_attempt(db, attempt, step)
    if row.is_correct is not None:
        raise HTTPException(409, "This step has already been answered.")

    hints = sorted(step.hints, key=lambda h: h.position)
    revealed_count = len(row.hints_revealed)
    if revealed_count >= len(hints):
        raise HTTPException(409, "No more hints available for this step.")

    next_hint = hints[revealed_count]
    row.hints_revealed = [*row.hints_revealed, next_hint.id]
    db.add(
        ScenarioEvent(
            scenario_attempt_id=attempt.id, user_id=user.id,
            event_type="hint_revealed", step_id=step.id,
            payload={
                "hint_position": next_hint.position,
                "penalty_bps": next_hint.penalty_bps,
                "scenario_content_version": scenario.content_version,
            },
        )
    )
    db.commit()

    return ScenarioHintResponse(
        step_position=step.position,
        hint_position=next_hint.position,
        text=next_hint.text,
        penalty_bps=next_hint.penalty_bps,
        hints_remaining=len(hints) - len(row.hints_revealed),
    )


@router.post(
    "/scenario-attempts/{attempt_id}/steps/{position}/answer",
    response_model=ScenarioStepAnswerResponse,
)
def answer_step(
    attempt_id: int, position: int, payload: ScenarioStepAnswerRequest,
    db: Session = Depends(get_db),
) -> ScenarioStepAnswerResponse:
    user = _current_user(db)
    attempt = _load_owned_attempt(db, attempt_id, user)
    if attempt.status != "in_progress":
        raise HTTPException(409, "This scenario attempt has already been completed.")

    scenario, steps = _load_scenario_and_steps(db, attempt.scenario_id)
    if scenario.content_version != attempt.scenario_content_version:
        raise HTTPException(
            409, "This scenario's content has changed since the attempt started."
        )

    step = next((s for s in steps if s.position == position), None)
    if step is None:
        raise HTTPException(404, f"Step {position} not found.")

    row = _get_or_create_step_attempt(db, attempt, step)

    # --- idempotent replay vs. genuine re-answer attempt ---------------------------
    # Section 7's chosen policy, stated once: an exact replay of an already-recorded
    # decision (a network retry re-sending the same request) returns the existing
    # result without writing new evidence. A DIFFERENT selection on an already-
    # answered step is rejected outright -- an attempt cannot change its answer.
    if row.is_correct is not None:
        if sorted(row.selected_option_ids) == sorted(payload.selected_option_ids):
            return _answer_response_from_row(attempt, steps, step, row)
        raise HTTPException(409, "This step has already been answered.")

    current_position = _current_step_position(db, attempt)
    if position != current_position:
        raise HTTPException(
            409, f"Step {position} is not the current step ({current_position})."
        )

    valid_option_ids = {o.id for o in step.options}
    if not set(payload.selected_option_ids).issubset(valid_option_ids):
        raise HTTPException(422, "One or more selected options do not belong to this step.")

    options = [
        StepOption(
            id=o.id, label=o.label, text=o.text, is_correct=o.is_correct,
            rationale=o.rationale, misconception_tag=o.misconception_tag,
        )
        for o in sorted(step.options, key=lambda o: o.position)
    ]
    hints = sorted(step.hints, key=lambda h: h.position)
    revealed = [
        RevealedHint(position=h.position, penalty_bps=h.penalty_bps)
        for h in hints if h.id in row.hints_revealed
    ]

    try:
        evaluation = evaluate_step_decision(
            step_id=step.id,
            domain_code=db.get(Domain, scenario.domain_id).code,
            step_type=step.step_type,
            options=options,
            selected_option_ids=payload.selected_option_ids,
            revealed_hints=revealed,
        )
    except ScenarioScoringError as exc:
        raise HTTPException(409, str(exc)) from exc

    row.selected_option_ids = sorted(payload.selected_option_ids)
    row.is_correct = evaluation.is_correct
    row.step_credit = evaluation.step_credit
    row.time_spent_seconds = payload.time_spent_seconds

    db.add(
        ScenarioEvent(
            scenario_attempt_id=attempt.id, user_id=user.id,
            event_type="step_answered", step_id=step.id,
            payload=build_scenario_event_payload(
                evaluation, scenario_content_version=scenario.content_version,
            ),
        )
    )

    is_last_step = step.position == steps[-1].position
    result: ScenarioResultOut | None = None
    if is_last_step:
        all_rows = db.scalars(
            select(ScenarioStepAttempt).where(
                ScenarioStepAttempt.scenario_attempt_id == attempt.id
            )
        ).all()
        score_pct, band = aggregate_scenario_score([r.step_credit for r in all_rows])
        attempt.status = "submitted"
        attempt.submitted_at = datetime.now(timezone.utc)
        attempt.score_pct = float(score_pct)
        attempt.mastery_band = band.value
        db.add(
            ScenarioEvent(
                scenario_attempt_id=attempt.id, user_id=user.id,
                event_type="scenario_submitted", step_id=None,
                payload={
                    "score_pct": score_pct, "mastery_band": band.value,
                    "scenario_content_version": scenario.content_version,
                },
            )
        )
        result = ScenarioResultOut(score_pct=score_pct, mastery_band=band.value)

    db.commit()

    return _build_answer_response(step, evaluation, attempt.status, steps, result=result)


def _build_answer_response(
    step: ScenarioStep, evaluation, attempt_status: str,
    steps: list[ScenarioStep], result: ScenarioResultOut | None,
) -> ScenarioStepAnswerResponse:
    """Shapes the post-decision response: feedback ONLY for selected options
    (Section 9), correct_option_ids as bare IDs (not the unselected rationale), and
    the next step's learner-safe payload if the scenario is not yet complete.
    """
    feedback = [
        SelectedOptionFeedback(
            option_id=o.id, label=o.label, is_correct=o.is_correct,
            rationale=o.rationale, misconception_tag=o.misconception_tag,
        )
        for o in evaluation.selected_options
    ]
    next_step = None
    if attempt_status == "in_progress":
        following = next((s for s in steps if s.position == step.position + 1), None)
        if following is not None:
            next_step = _step_out(following, len(steps))

    return ScenarioStepAnswerResponse(
        step_position=step.position,
        is_correct=evaluation.is_correct,
        step_credit=evaluation.step_credit,
        selected_option_ids=sorted(evaluation.selected_option_ids),
        correct_option_ids=sorted(evaluation.correct_option_ids),
        feedback=feedback,
        attempt_status=attempt_status,
        next_step=next_step,
        result=result,
    )


def _answer_response_from_row(
    attempt: ScenarioAttempt, steps: list[ScenarioStep],
    step: ScenarioStep, row: ScenarioStepAttempt,
) -> ScenarioStepAnswerResponse:
    """Rebuilds the same response an idempotent replay would have produced the first
    time, from persisted state -- no re-grading, no new evidence.
    """
    options = {o.id: o for o in step.options}
    selected_options = tuple(
        StepOption(
            id=o.id, label=o.label, text=o.text, is_correct=o.is_correct,
            rationale=o.rationale, misconception_tag=o.misconception_tag,
        )
        for oid in row.selected_option_ids
        if (o := options.get(oid)) is not None
    )
    correct_ids = {o.id for o in step.options if o.is_correct}

    # A plain namespace, not a class body -- a class body does not close over the
    # enclosing function's locals the way a function does, so `x = x` inside one
    # silently resolves against the (not-yet-built) class namespace instead of the
    # outer variable.
    replay = SimpleNamespace(
        is_correct=row.is_correct,
        step_credit=row.step_credit,
        selected_option_ids=frozenset(row.selected_option_ids),
        correct_option_ids=frozenset(correct_ids),
        selected_options=selected_options,
    )

    result = None
    if attempt.status == "submitted" and step.position == steps[-1].position:
        result = ScenarioResultOut(
            score_pct=int(attempt.score_pct), mastery_band=attempt.mastery_band
        )

    return _build_answer_response(step, replay, attempt.status, steps, result=result)


@router.get("/scenario-attempts/{attempt_id}", response_model=ScenarioAttemptOut)
def get_scenario_attempt(
    attempt_id: int, db: Session = Depends(get_db)
) -> ScenarioAttemptOut:
    user = _current_user(db)
    attempt = _load_owned_attempt(db, attempt_id, user)
    scenario, steps = _load_scenario_and_steps(db, attempt.scenario_id)

    current_step = None
    result = None
    if attempt.status == "in_progress":
        position = _current_step_position(db, attempt)
        step = next((s for s in steps if s.position == position), None)
        if step is not None:
            current_step = _step_out(step, len(steps))
    else:
        result = ScenarioResultOut(
            score_pct=int(attempt.score_pct), mastery_band=attempt.mastery_band
        )

    return ScenarioAttemptOut(
        attempt_id=attempt.id,
        scenario_external_id=scenario.external_id,
        status=attempt.status,
        current_step=current_step,
        result=result,
    )
