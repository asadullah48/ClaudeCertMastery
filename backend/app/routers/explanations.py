"""AI explanation generation for a submitted attempt (spec section 6).

Why this is a separate request from ``POST /attempts/{id}/submit``
-----------------------------------------------------------------
Submission is on the candidate's critical path: they have just spent two hours and want
their score. Fanning out twenty Claude calls before returning it would add tens of
seconds to the one request that must feel instant. Scoring returns immediately with the
authored explanations; the review screen then asks for AI remediation for the items the
candidate actually chooses to expand.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.config import settings
from app.database import get_db
from app.models import (
    AttemptStatus,
    Domain,
    ExamAttempt,
    Explanation,
    Question,
    Track,
)
from app.schemas import ExplanationOut, ExplanationRequest, ExplanationResponse
from app.services.explanation_engine import (
    DomainContext,
    ExplanationEngine,
    QuestionContext,
    build_engine,
)

router = APIRouter(prefix="/attempts", tags=["explanations"])


def get_engine() -> ExplanationEngine:
    """Dependency seam so tests can inject a stubbed engine with no API key."""
    return build_engine()


def _domain_context(
    track: Track, domain: Domain, all_domains: list[Domain]
) -> DomainContext:
    """Assemble the cacheable per-domain prefix context.

    ``blueprint`` is built from ``all_domains`` sorted by published position rather than
    from a dict, because the prefix must be byte-identical across calls for prompt
    caching to hit. Iteration order that varied between processes would silently halve
    the cache hit rate with no error anywhere.
    """
    return DomainContext(
        track_code=track.code,
        track_name=track.name,
        domain_code=domain.code,
        domain_name=domain.name,
        domain_description=domain.description,
        weight_bps=domain.weight_bps,
        blueprint=[
            (d.code, d.name, d.weight_bps)
            for d in sorted(all_domains, key=lambda d: d.position)
        ],
    )


def _question_context(question: Question, selected: set[int]) -> QuestionContext:
    return QuestionContext(
        question_id=question.id,
        external_id=question.external_id,
        stem=question.stem,
        question_type=(
            question.question_type.value
            if hasattr(question.question_type, "value")
            else str(question.question_type)
        ),
        options=[(o.id, o.label, o.text) for o in question.options],
        correct_option_ids=question.correct_option_ids,
        selected_option_ids=selected,
    )


def _from_row(
    row: Explanation, question: Question, domain_code: str, reused: bool
) -> ExplanationOut:
    return ExplanationOut(
        question_id=question.id,
        external_id=question.external_id,
        domain_code=domain_code,
        source="ai",
        reused=reused,
        why_correct=row.why_correct,
        why_your_answer_wrong=row.why_your_answer_wrong,
        key_concept=row.key_concept,
        blueprint_link=row.blueprint_link,
        study_tip=row.study_tip,
        static_explanation=question.static_explanation,
    )


@router.post("/{attempt_id}/explanations", response_model=ExplanationResponse)
def generate_explanations(
    attempt_id: int,
    payload: ExplanationRequest | None = None,
    db: Session = Depends(get_db),
    engine: ExplanationEngine = Depends(get_engine),
) -> ExplanationResponse:
    """Return remediation for the wrong answers in a submitted attempt.

    Refuses an attempt that has not been submitted. An explanation states the correct
    answer outright, so serving one mid-exam would hand back through this route exactly
    the answer key that ``/exams/generate`` is careful never to send -- it serialises
    options through a schema with no ``is_correct`` field. The 409 is what keeps that
    guarantee whole.

    Never fails because of the AI layer. An unconfigured key, a rate limit or a network
    error all produce ``source="static"`` entries carrying the authored explanation, and
    the response is still a 200.
    """
    request = payload or ExplanationRequest()

    attempt = db.scalar(
        select(ExamAttempt)
        .options(selectinload(ExamAttempt.items))
        .where(ExamAttempt.id == attempt_id)
    )
    if attempt is None:
        raise HTTPException(404, f"Attempt {attempt_id} not found.")
    if attempt.status != AttemptStatus.SUBMITTED:
        raise HTTPException(
            409,
            "Attempt has not been submitted. Explanations reveal the correct answer and "
            "are only available after grading.",
        )

    track = db.get(Track, attempt.track_id)
    all_domains = list(
        db.scalars(select(Domain).where(Domain.track_id == track.id)).all()
    )
    domains_by_id = {d.id: d for d in all_domains}

    # Only wrong answers earn remediation: explaining an item the candidate got right is
    # spend with no pedagogical return.
    wanted = set(request.question_ids) if request.question_ids else None
    targets = [
        item
        for item in sorted(attempt.items, key=lambda i: i.position)
        if not item.is_correct and (wanted is None or item.question_id in wanted)
    ]
    if not targets:
        return ExplanationResponse(
            attempt_id=attempt.id, ai_enabled=settings.ai_explanations_enabled
        )

    questions = db.scalars(
        select(Question)
        .options(selectinload(Question.options))
        .where(Question.id.in_([i.question_id for i in targets]))
    ).all()
    questions_by_id = {q.id: q for q in questions}

    out: list[ExplanationOut] = []
    generated = reused = fell_back = 0

    for item in targets:
        question = questions_by_id[item.question_id]
        domain = domains_by_id[item.domain_id]
        selected = set(item.selected_option_ids or [])
        signature = Explanation.signature_for(sorted(selected))

        # Dedup (spec section 6): the same wrong answer is generated once and reused for
        # every candidate who makes it.
        cached = db.scalar(
            select(Explanation).where(
                Explanation.question_id == question.id,
                Explanation.selected_option_signature == signature,
            )
        )
        if cached is not None and not request.force_regenerate:
            reused += 1
            out.append(_from_row(cached, question, domain.code, reused=True))
            continue

        result = engine.generate(
            _domain_context(track, domain, all_domains),
            _question_context(question, selected),
        )

        if not result.ok or result.payload is None:
            fell_back += 1
            out.append(
                ExplanationOut(
                    question_id=question.id,
                    external_id=question.external_id,
                    domain_code=domain.code,
                    source="static",
                    static_explanation=question.static_explanation,
                    detail=result.detail,
                )
            )
            continue

        row = cached or Explanation(
            question_id=question.id, selected_option_signature=signature
        )
        row.why_correct = result.payload.why_correct
        row.why_your_answer_wrong = result.payload.why_your_answer_wrong
        row.key_concept = result.payload.key_concept
        row.blueprint_link = result.payload.blueprint_link
        row.study_tip = result.payload.study_tip
        row.model = engine.model
        row.input_tokens = result.usage.input_tokens
        row.output_tokens = result.usage.output_tokens
        row.cache_read_tokens = result.usage.cache_read_tokens
        db.add(row)

        generated += 1
        out.append(_from_row(row, question, domain.code, reused=False))

    db.commit()

    return ExplanationResponse(
        attempt_id=attempt.id,
        ai_enabled=settings.ai_explanations_enabled,
        generated=generated,
        reused=reused,
        fell_back=fell_back,
        explanations=out,
    )
