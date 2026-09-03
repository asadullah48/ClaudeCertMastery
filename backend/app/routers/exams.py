"""Exam generation endpoint."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.database import get_db
from app.models import (
    AttemptItem,
    AttemptMode,
    ExamAttempt,
    Question,
    Track,
    User,
)
from app.schemas import (
    AnswerOptionOut,
    ExamGenerateRequest,
    ExamGenerateResponse,
    QuestionOut,
)
from app.services.blueprint import DomainWeight
from app.services.exam_generator import ExamGenerationError, generate_exam

router = APIRouter(prefix="/exams", tags=["exams"])

DEV_USER_EMAIL = "dev@certmastery.local"


def _current_user(db: Session) -> User:
    """Stand-in for authentication until Session 3 (D-7)."""
    user = db.scalar(select(User).where(User.email == DEV_USER_EMAIL))
    if user is None:
        raise HTTPException(
            status_code=500, detail="Dev user missing. Run: python seed.py"
        )
    return user


@router.post("/generate", response_model=ExamGenerateResponse, status_code=201)
def generate(
    payload: ExamGenerateRequest, db: Session = Depends(get_db)
) -> ExamGenerateResponse:
    """Compose a blueprint-weighted exam and persist it as an in-progress attempt."""
    track = db.scalar(
        select(Track)
        .options(selectinload(Track.domains))
        .where(Track.code == payload.track_code)
    )
    if track is None:
        raise HTTPException(404, f"Track {payload.track_code} not found.")
    if not track.domains:
        raise HTTPException(
            409,
            f"Track {track.code} has no published blueprint yet. "
            "Its question bank is not authored.",
        )

    weights = [DomainWeight(d.code, d.weight_bps, d.position) for d in track.domains]
    domain_by_code = {d.code: d for d in track.domains}

    # Build the bank of eligible questions, one query for the whole track.
    rows = db.execute(
        select(Question.id, Question.domain_id)
        .where(
            Question.domain_id.in_([d.id for d in track.domains]),
            Question.is_active.is_(True),
        )
        .order_by(Question.id)  # stable input order keeps seeded draws reproducible
    ).all()

    bank: dict[str, list[int]] = {d.code: [] for d in track.domains}
    id_to_code = {d.id: d.code for d in track.domains}
    for qid, domain_id in rows:
        bank[id_to_code[domain_id]].append(qid)

    total_items = payload.item_count or track.item_count
    try:
        exam = generate_exam(weights, bank, total_items, seed=payload.seed)
    except ExamGenerationError as exc:
        raise HTTPException(409, str(exc)) from exc

    attempt = ExamAttempt(
        user_id=_current_user(db).id,
        track_id=track.id,
        mode=AttemptMode(payload.mode),
        seed=exam.seed,
        composition_warning=exam.composition_warning,
    )
    db.add(attempt)
    db.flush()

    for position, qid in enumerate(exam.question_ids, start=1):
        db.add(
            AttemptItem(
                attempt_id=attempt.id,
                question_id=qid,
                domain_id=domain_by_code[exam.domain_of_question[qid]].id,
                position=position,
                selected_option_ids=[],
            )
        )

    questions = db.scalars(
        select(Question)
        .options(selectinload(Question.options))
        .where(Question.id.in_(exam.question_ids))
    ).all()
    by_id = {q.id: q for q in questions}

    ordered = []
    for qid in exam.question_ids:
        q = by_id[qid]
        ordered.append(
            QuestionOut(
                id=q.id,
                external_id=q.external_id,
                stem=q.stem,
                question_type=q.question_type.value
                if hasattr(q.question_type, "value")
                else str(q.question_type),
                difficulty=q.difficulty,
                domain_code=exam.domain_of_question[qid],
                # AnswerOptionOut omits is_correct, so the answer key never leaves the
                # server with the exam payload.
                options=[AnswerOptionOut.model_validate(o) for o in q.options],
            )
        )

    db.commit()

    return ExamGenerateResponse(
        attempt_id=attempt.id,
        track_code=track.code,
        seed=exam.seed,
        item_count=exam.item_count,
        duration_minutes=track.duration_minutes,
        per_domain=exam.per_domain,
        composition_warning=exam.composition_warning,
        questions=ordered,
    )
