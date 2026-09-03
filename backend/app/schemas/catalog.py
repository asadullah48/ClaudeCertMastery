"""Response models for track, blueprint and question data."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class DomainOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    code: str
    name: str
    description: str
    weight_bps: int
    position: int

    @property
    def weight_pct(self) -> float:
        return self.weight_bps / 100


class TrackOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    code: str
    name: str
    description: str
    item_count: int
    duration_minutes: int
    pass_scaled_score: int
    price_usd: float
    validity_months: int
    is_seeded: bool
    question_count: int = 0
    domains: list[DomainOut] = Field(default_factory=list)


class BlueprintDomainOut(BaseModel):
    """One domain row of a blueprint, including how many items it contributes."""

    code: str
    name: str
    weight_pct: float
    items_at_full_length: int
    questions_available: int


class BlueprintOut(BaseModel):
    track_code: str
    item_count: int
    total_weight_bps: int
    domains: list[BlueprintDomainOut]


class AnswerOptionOut(BaseModel):
    """An option as shown to a candidate.

    is_correct is deliberately absent: this model is serialised into the exam payload,
    and including the answer key would put it in the browser.
    """

    model_config = ConfigDict(from_attributes=True)

    id: int
    label: str
    text: str
    position: int


class QuestionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    external_id: str
    stem: str
    question_type: str
    difficulty: int
    domain_code: str = ""
    options: list[AnswerOptionOut] = Field(default_factory=list)
