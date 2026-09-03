"""Request and response models for exam generation and submission."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.catalog import QuestionOut


class ExamGenerateRequest(BaseModel):
    track_code: str
    # Defaults to the track's own item_count when omitted.
    item_count: int | None = Field(default=None, ge=1, le=200)
    seed: int | None = None
    mode: str = "exam"


class ExamGenerateResponse(BaseModel):
    attempt_id: int
    track_code: str
    seed: int
    item_count: int
    duration_minutes: int
    per_domain: dict[str, int]
    composition_warning: str | None = None
    questions: list[QuestionOut]


class SubmitAnswer(BaseModel):
    question_id: int
    selected_option_ids: list[int] = Field(default_factory=list)
    time_spent_seconds: int | None = None
    flagged_for_review: bool = False


class SubmitRequest(BaseModel):
    answers: list[SubmitAnswer] = Field(default_factory=list)


class DomainScoreOut(BaseModel):
    domain_code: str
    domain_name: str
    correct: int
    total: int
    percentage: float
    mastery_band: str


class ItemResultOut(BaseModel):
    question_id: int
    external_id: str
    domain_code: str
    is_correct: bool
    partial_credit: float
    selected_option_ids: list[int]
    correct_option_ids: list[int]
    # Always populated from the question's authored explanation, so results are useful
    # with no ANTHROPIC_API_KEY configured. Session 2 layers AI explanations on top.
    explanation: str


class SubmitResponse(BaseModel):
    attempt_id: int
    track_code: str
    raw_correct: int
    raw_total: int
    raw_percentage: float
    scaled_score: int
    pass_scaled_score: int
    passed: bool
    domain_scores: list[DomainScoreOut]
    items: list[ItemResultOut]
    composition_warning: str | None = None


class AttemptOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    track_code: str
    mode: str
    status: str
    seed: int
    started_at: datetime
    submitted_at: datetime | None
    scaled_score: int | None
    passed: bool | None
    item_count: int
