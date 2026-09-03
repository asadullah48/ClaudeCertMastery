"""Request and response models for the Ask Zia companion panel."""

from __future__ import annotations

from pydantic import BaseModel, Field


class ZiaSessionRequest(BaseModel):
    goal: str | None = None
    note: str | None = None


class ZiaSessionResponse(BaseModel):
    ok: bool
    # True on the first open of a visit (begin_session), False on a resume
    # (open_student_record).
    started_new_session: bool
    session_handle: str | None = None
    detail: str = ""


class ZiaCitation(BaseModel):
    """Where an explanation came from. Always rendered with the answer."""

    slug: str
    title: str | None = None
    heading_path: str = ""
    url: str | None = None


class ZiaExplainResponse(BaseModel):
    ok: bool
    # False means the panel hides itself: no mapping, tutor unavailable, or the corpus
    # does not cover this concept.
    available: bool
    concept_tag: str | None = None
    concept_label: str | None = None
    matched_by: str | None = None
    explanation: str = ""
    citations: list[ZiaCitation] = Field(default_factory=list)
    follow_up_question: str | None = None
    detail: str = ""


class ZiaCheckAnswerRequest(BaseModel):
    concept_tag: str
    track_code: str
    question_id: int | None = None
    follow_up_question: str = ""
    learner_answer: str


class ZiaCheckAnswerResponse(BaseModel):
    ok: bool
    recorded: bool
    detail: str = ""
