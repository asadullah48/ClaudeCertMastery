"""Request and response models for Scenario Lab (Gate C1 Slice 2).

Threat-model boundary (Slice 2 Section 2): every *Out model in this file that can be
returned BEFORE a step is answered is audited to contain none of is_correct,
misconception_tag, rationale, or any field that would let a client infer them.
ScenarioStepOptionOut is the single model used for every pre-decision options list --
deliberately, so there is exactly one place that decision is made, not one per route.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class ScenarioListItemOut(BaseModel):
    """One row of GET /scenarios -- discovery only, no step content."""

    external_id: str
    title: str
    domain_code: str
    difficulty: int


class ScenarioStepOptionOut(BaseModel):
    """An option as shown to a learner before they decide.

    is_correct, rationale and misconception_tag are deliberately absent -- this is
    the exam schema's AnswerOptionOut precedent (app/schemas/catalog.py), applied to
    scenarios. There is no field here, under any name, that reveals or helps infer
    correctness.
    """

    id: int
    label: str
    text: str
    position: int


class ScenarioStepOut(BaseModel):
    """The learner's current step: everything needed to decide, nothing that reveals
    the answer. Never carries hint text (hints are requested explicitly) and never
    carries any step beyond the current one."""

    position: int
    total_steps: int
    prompt_text: str
    step_type: str
    options: list[ScenarioStepOptionOut]
    hints_available: int  # count only -- text is never sent unrequested


class ScenarioStartResponse(BaseModel):
    attempt_id: int
    scenario_external_id: str
    title: str
    setup_text: str
    domain_code: str
    status: str
    current_step: ScenarioStepOut


class ScenarioHintRequest(BaseModel):
    # Empty body: the endpoint reveals "the next unrevealed hint" server-side. Nothing
    # for the client to dictate -- included as an explicit model (not just a bare
    # POST) so the route signature stays consistent with every other endpoint here.
    pass


class ScenarioHintResponse(BaseModel):
    step_position: int
    hint_position: int
    text: str
    penalty_bps: int
    hints_remaining: int


class ScenarioStepAnswerRequest(BaseModel):
    """Client input is deliberately narrow: only what the learner is legitimately
    choosing. No correctness, score, rationale, misconception, or consequence field
    exists here for a client to forge -- there is nowhere to put one."""

    selected_option_ids: list[int] = Field(default_factory=list)
    time_spent_seconds: int | None = None


class SelectedOptionFeedback(BaseModel):
    """Reflection material for ONE option the learner actually selected. Never built
    for an option they did not choose (Slice 2 Section 9)."""

    option_id: int
    label: str
    is_correct: bool
    rationale: str
    misconception_tag: str | None = None


class ScenarioResultOut(BaseModel):
    """Only present once the scenario is complete."""

    score_pct: int
    mastery_band: str


class ScenarioStepAnswerResponse(BaseModel):
    step_position: int
    is_correct: bool
    step_credit: float
    selected_option_ids: list[int]
    correct_option_ids: list[int]  # IDs only -- not the correct option's rationale
    feedback: list[SelectedOptionFeedback]  # one entry per option the learner chose
    attempt_status: str  # "in_progress" | "submitted"
    next_step: ScenarioStepOut | None = None  # None once the scenario is complete
    result: ScenarioResultOut | None = None  # populated only on the completing answer


class ScenarioAttemptOut(BaseModel):
    """GET /scenario-attempts/{id}: current state, learner-safe."""

    attempt_id: int
    scenario_external_id: str
    status: str
    current_step: ScenarioStepOut | None = None  # None once submitted
    result: ScenarioResultOut | None = None  # None until submitted
