"""Ask Zia companion panel endpoints.

Three routes, all of which degrade to "panel hidden" rather than erroring:

    POST /api/zia/session        open or resume the learner's tutoring session
    GET  /api/zia/explain        curriculum explanation for a question or concept
    POST /api/zia/check-answer   record the learner's follow-up answer as evidence

The Claude API explanation engine remains the default everywhere. Zia is a companion,
so every failure path here returns HTTP 200 with `available: false` -- a tutor outage
must never surface to a candidate as a broken review screen.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.config import settings
from app.database import get_db
from app.models import Question, Track, User, ZiaLearnerLink
from app.schemas import (
    ZiaCheckAnswerRequest,
    ZiaCheckAnswerResponse,
    ZiaCitation,
    ZiaExplainResponse,
    ZiaSessionRequest,
    ZiaSessionResponse,
)
from app.services import concept_map
from app.services.zia_client import ZiaTutorClient, build_client, run_sync

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/zia", tags=["zia"])

DEV_USER_EMAIL = "dev@certmastery.local"

# A "visit" for the purposes of begin_session vs open_student_record. Within this
# window the learner is resumed; after it, a new session is begun.
VISIT_WINDOW = timedelta(hours=4)

# How much of the retrieved passage to show before linking out to the lesson.
MAX_EXPLANATION_CHARS = 1800


def _current_user(db: Session) -> User:
    """Stand-in for authentication, matching the rest of the API (D-7)."""
    user = db.scalar(select(User).where(User.email == DEV_USER_EMAIL))
    if user is None:
        raise HTTPException(500, "Dev user missing. Run: python seed.py")
    return user


def get_zia_client() -> ZiaTutorClient:
    """Injectable client factory, overridden in tests with a mock."""
    return build_client()


@router.post("/session", response_model=ZiaSessionResponse)
def open_session(
    payload: ZiaSessionRequest,
    db: Session = Depends(get_db),
    client: ZiaTutorClient = Depends(get_zia_client),
) -> ZiaSessionResponse:
    """Open or resume the learner's Zia session.

    First open of a visit calls begin_session; a later open within the visit window
    calls open_student_record, so Zia resumes the learner rather than restarting them
    and their mastery record stays continuous.
    """
    if not client.configured:
        return ZiaSessionResponse(
            ok=False,
            started_new_session=False,
            detail="Zia is not configured (CERTMASTERY_ZIA_MCP_TOKEN unset).",
        )

    user = _current_user(db)
    link = db.scalar(select(ZiaLearnerLink).where(ZiaLearnerLink.user_id == user.id))
    now = datetime.now(timezone.utc)

    def _is_returning(link: ZiaLearnerLink | None) -> bool:
        if link is None or link.session_started_at is None:
            return False
        started = link.session_started_at
        if started.tzinfo is None:  # SQLite returns naive datetimes
            started = started.replace(tzinfo=timezone.utc)
        return now - started < VISIT_WINDOW

    returning = _is_returning(link)

    if returning:
        result = run_sync(client.open_student_record(course=payload.note or None))
    else:
        result = run_sync(
            client.begin_session(goal=payload.goal, note=payload.note)
        )

    if not result.ok:
        logger.info("Zia session unavailable: %s", result.detail)
        return ZiaSessionResponse(
            ok=False, started_new_session=False, detail=result.detail
        )

    if link is None:
        link = ZiaLearnerLink(user_id=user.id, zia_identity=user.email)
        db.add(link)

    link.last_seen_at = now
    if not returning:
        link.session_started_at = now
        link.zia_session_handle = result.session_handle or link.zia_session_handle
    db.commit()

    return ZiaSessionResponse(
        ok=True,
        started_new_session=not returning,
        session_handle=link.zia_session_handle,
    )


def _unavailable(detail: str) -> ZiaExplainResponse:
    """The panel's hidden state. Always HTTP 200 -- absence is not an error."""
    return ZiaExplainResponse(ok=True, available=False, detail=detail)


@router.get("/explain", response_model=ZiaExplainResponse)
def explain(
    question_id: int | None = Query(default=None),
    track_code: str | None = Query(default=None),
    concept_tag: str | None = Query(default=None),
    db: Session = Depends(get_db),
    client: ZiaTutorClient = Depends(get_zia_client),
) -> ZiaExplainResponse:
    """Curriculum explanation for a question's concept.

    Resolve by `question_id` normally. `track_code` + `concept_tag` is the direct form,
    used for tracks whose question bank is not authored yet (CCAR-F, CCAR-P) and by the
    tests.
    """
    if not client.configured:
        return _unavailable("Zia is not configured.")

    # --- resolve the concept ---------------------------------------------------
    if question_id is not None:
        question = db.scalar(
            select(Question)
            .options(selectinload(Question.options))
            .where(Question.id == question_id)
        )
        if question is None:
            raise HTTPException(404, f"Question {question_id} not found.")
        resolved = concept_map.resolve_for_question(db, question)
    elif track_code and concept_tag:
        resolved = concept_map.resolve_by_tag(db, track_code, concept_tag)
    else:
        raise HTTPException(
            422, "Provide either question_id, or both track_code and concept_tag."
        )

    if resolved is None:
        # Not an error: this concept simply has no Agent Factory lesson behind it.
        # Sending the candidate to an unrelated lesson would be worse than nothing.
        return _unavailable("No curriculum mapping for this concept.")

    # --- search the corpus -----------------------------------------------------
    search = run_sync(client.search(resolved.search_query, grain="passage", k=3))
    if not search.ok:
        return _unavailable(f"Tutor unavailable: {search.detail}")
    if not search.hits:
        return _unavailable(search.detail or "Curriculum does not cover this concept.")

    top = search.hits[0]
    explanation = top.content.strip()
    citations = [
        ZiaCitation(
            slug=hit.slug,
            title=resolved.lesson_title if hit.slug == resolved.lesson_slug else None,
            heading_path=hit.heading_path,
            url=hit.url,
        )
        for hit in search.hits
    ]

    # If the top hit is a thin snippet, read the mapped lesson section for real teaching
    # material rather than showing the candidate a fragment.
    if len(explanation) < 400:
        lesson = run_sync(
            client.read_lesson(resolved.lesson_slug, section=resolved.lesson_section)
        )
        if lesson.ok and lesson.lesson_text:
            explanation = lesson.lesson_text.strip()
            if lesson.lesson_url and not any(c.url == lesson.lesson_url for c in citations):
                citations.insert(
                    0,
                    ZiaCitation(
                        slug=resolved.lesson_slug,
                        title=lesson.lesson_title or resolved.lesson_title,
                        heading_path=resolved.lesson_section or "",
                        url=lesson.lesson_url,
                    ),
                )

    if len(explanation) > MAX_EXPLANATION_CHARS:
        explanation = explanation[:MAX_EXPLANATION_CHARS].rstrip() + "..."

    return ZiaExplainResponse(
        ok=True,
        available=True,
        concept_tag=resolved.concept_tag,
        concept_label=resolved.label,
        matched_by=resolved.matched_by,
        explanation=explanation,
        citations=citations,
        follow_up_question=(
            f"In your own words: {resolved.label.lower()} - what is the one idea you "
            "would carry into a real project?"
        ),
    )


@router.post("/check-answer", response_model=ZiaCheckAnswerResponse)
def check_answer(
    payload: ZiaCheckAnswerRequest,
    db: Session = Depends(get_db),
    client: ZiaTutorClient = Depends(get_zia_client),
) -> ZiaCheckAnswerResponse:
    """Record the candidate's follow-up answer on their Zia learner record.

    What is reported is strictly what the platform observed: the candidate's own words,
    passed as `attempt`, with the evidence basis stated. Cert Mastery does not assert
    mastery it did not witness -- an unverified mastery claim would corrupt the tutor's
    record of what this learner can actually do.
    """
    if not client.configured:
        return ZiaCheckAnswerResponse(
            ok=False, recorded=False, detail="Zia is not configured."
        )

    answer = payload.learner_answer.strip()
    if not answer:
        return ZiaCheckAnswerResponse(
            ok=False, recorded=False, detail="No answer supplied."
        )

    user = _current_user(db)
    link = db.scalar(select(ZiaLearnerLink).where(ZiaLearnerLink.user_id == user.id))
    if link is None:
        return ZiaCheckAnswerResponse(
            ok=False,
            recorded=False,
            detail="No Zia session for this learner. Open the panel first.",
        )

    track = db.scalar(select(Track).where(Track.code == payload.track_code))
    course = track.name if track else payload.track_code

    note_parts = [f"Cert Mastery ({payload.track_code}) concept: {payload.concept_tag}"]
    if payload.question_id is not None:
        note_parts.append(f"question_id={payload.question_id}")
    if payload.follow_up_question:
        note_parts.append(f"asked: {payload.follow_up_question}")

    result = run_sync(
        client.update_student_record(
            verb="mastery",
            course=course,
            step=payload.concept_tag,
            note="; ".join(note_parts),
            evidence="teacher_reported_observed_in_chat",
            attempt=answer,
        )
    )

    if not result.ok:
        logger.info("Zia record update failed: %s", result.detail)
        return ZiaCheckAnswerResponse(ok=False, recorded=False, detail=result.detail)

    link.last_seen_at = datetime.now(timezone.utc)
    db.commit()

    return ZiaCheckAnswerResponse(ok=True, recorded=True)
