"""Attempt retrieval and submission."""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.database import get_db
from app.models import (
    AttemptDomainScore,
    AttemptItem,
    AttemptStatus,
    Domain,
    ExamAttempt,
    Question,
    Track,
)
from app.schemas import (
    AttemptOut,
    DomainScoreOut,
    ItemResultOut,
    SubmitRequest,
    SubmitResponse,
)
from app.services.scoring import QuestionType, grade_item, score_attempt

router = APIRouter(prefix="/attempts", tags=["attempts"])


def _load_attempt(db: Session, attempt_id: int) -> ExamAttempt:
    attempt = db.scalar(
        select(ExamAttempt)
        .options(selectinload(ExamAttempt.items))
        .where(ExamAttempt.id == attempt_id)
    )
    if attempt is None:
        raise HTTPException(404, f"Attempt {attempt_id} not found.")
    return attempt


@router.get("/{attempt_id}", response_model=AttemptOut)
def get_attempt(attempt_id: int, db: Session = Depends(get_db)) -> AttemptOut:
    attempt = _load_attempt(db, attempt_id)
    track = db.get(Track, attempt.track_id)
    return AttemptOut(
        id=attempt.id,
        track_code=track.code,
        mode=attempt.mode.value if hasattr(attempt.mode, "value") else str(attempt.mode),
        status=attempt.status.value
        if hasattr(attempt.status, "value")
        else str(attempt.status),
        seed=attempt.seed,
        started_at=attempt.started_at,
        submitted_at=attempt.submitted_at,
        scaled_score=attempt.scaled_score,
        passed=attempt.passed,
        item_count=len(attempt.items),
    )


@router.post("/{attempt_id}/submit", response_model=SubmitResponse)
def submit(
    attempt_id: int, payload: SubmitRequest, db: Session = Depends(get_db)
) -> SubmitResponse:
    """Grade an attempt, persist the results, and return the scaled score."""
    attempt = _load_attempt(db, attempt_id)
    if attempt.status == AttemptStatus.SUBMITTED:
        raise HTTPException(409, "Attempt has already been submitted.")

    track = db.get(Track, attempt.track_id)
    answers = {a.question_id: a for a in payload.answers}

    questions = db.scalars(
        select(Question)
        .options(selectinload(Question.options))
        .where(Question.id.in_([i.question_id for i in attempt.items]))
    ).all()
    by_id = {q.id: q for q in questions}
    domains = {d.id: d for d in db.scalars(select(Domain)).all()}

    graded = []
    item_rows = []
    for item in sorted(attempt.items, key=lambda i: i.position):
        question = by_id[item.question_id]
        answer = answers.get(item.question_id)
        selected = set(answer.selected_option_ids) if answer else set()
        correct_ids = question.correct_option_ids
        qtype = QuestionType(
            question.question_type.value
            if hasattr(question.question_type, "value")
            else str(question.question_type)
        )
        domain = domains[item.domain_id]

        result = grade_item(question.id, domain.code, qtype, correct_ids, selected)
        graded.append(result)

        item.selected_option_ids = sorted(selected)
        item.is_correct = result.is_correct
        item.partial_credit = result.partial_credit
        if answer:
            item.time_spent_seconds = answer.time_spent_seconds
            item.flagged_for_review = answer.flagged_for_review

        item_rows.append(
            ItemResultOut(
                question_id=question.id,
                external_id=question.external_id,
                domain_code=domain.code,
                is_correct=result.is_correct,
                partial_credit=result.partial_credit,
                selected_option_ids=sorted(selected),
                correct_option_ids=sorted(correct_ids),
                explanation=question.static_explanation,
            )
        )

    score = score_attempt(graded, pass_raw=track.pass_raw_threshold)

    attempt.status = AttemptStatus.SUBMITTED
    attempt.submitted_at = datetime.now(timezone.utc)
    attempt.raw_correct = score.raw_correct
    attempt.raw_total = score.raw_total
    attempt.scaled_score = score.scaled_score
    attempt.passed = score.passed

    code_to_domain = {d.code: d for d in domains.values() if d.track_id == track.id}
    db.query(AttemptDomainScore).filter(
        AttemptDomainScore.attempt_id == attempt.id
    ).delete()

    domain_rows = []
    for ds in score.domain_scores:
        domain = code_to_domain[ds.domain_code]
        db.add(
            AttemptDomainScore(
                attempt_id=attempt.id,
                domain_id=domain.id,
                correct=ds.correct,
                total=ds.total,
                percentage=ds.percentage,
                mastery_band=ds.mastery_band.value,
            )
        )
        domain_rows.append(
            DomainScoreOut(
                domain_code=ds.domain_code,
                domain_name=domain.name,
                correct=ds.correct,
                total=ds.total,
                percentage=ds.percentage,
                mastery_band=ds.mastery_band.value,
            )
        )

    db.commit()

    domain_rows.sort(key=lambda d: code_to_domain[d.domain_code].position)

    return SubmitResponse(
        attempt_id=attempt.id,
        track_code=track.code,
        raw_correct=score.raw_correct,
        raw_total=score.raw_total,
        raw_percentage=score.raw_percentage,
        scaled_score=score.scaled_score,
        pass_scaled_score=track.pass_scaled_score,
        passed=score.passed,
        domain_scores=domain_rows,
        items=item_rows,
        composition_warning=attempt.composition_warning,
    )
