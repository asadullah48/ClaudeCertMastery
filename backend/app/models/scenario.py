"""Scenario Lab: durable schema for narrative decision scenarios and their evidence.

Gate C1, Slice 1 (2026-09-20): schema + deterministic engine only. No router, no
frontend, no AI evaluation, no KSOR -- see docs/GATE-C1-SCENARIO-LAB-PLAN.md Sections
3, 16, and 19 for the approved design this file implements exactly.

Seven tables, purely additive -- no existing table is altered. Kept physically
separate from the exam schema (attempt.py, catalog.py) for the same reason D-9 already
established for zia_learner_links/concept_curriculum_map (zia.py): scenario options
must never be drawn into exam_generator.py's blueprint-weighted bank query, and
scenario evidence must never depend on the (currently dormant, per Gate C2B/C2C)
Zia integration. Neither dependency exists anywhere in this file.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class Scenario(Base):
    """One narrative exercise, tied to exactly one blueprint domain."""

    __tablename__ = "scenarios"

    id: Mapped[int] = mapped_column(primary_key=True)
    domain_id: Mapped[int] = mapped_column(
        ForeignKey("domains.id", ondelete="CASCADE")
    )

    # Stable authoring key (e.g. "CCAO-F-WISD-SCN-001"), matching Question.external_id's
    # convention -- seeding matches on this.
    external_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    title: Mapped[str] = mapped_column(String(160))
    setup_text: Mapped[str] = mapped_column(Text)
    difficulty: Mapped[int] = mapped_column(Integer, default=2)  # reuses Question's 1-3 scale
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    # Plan Section 16.9: bumped by the author only for an evidence-affecting change
    # (altered meaning/constraints/decision semantics/correct answer/consequence/
    # misconception interpretation) -- never for an editorial-only change (typography,
    # punctuation, formatting). Not read by scoring; exists purely so a future evidence
    # consumer can tell which version of the scenario a given attempt was recorded
    # against. This is not a content-versioning framework: there is no history table,
    # no diffing, no rollback -- one integer, bumped by discipline, not by code.
    content_version: Mapped[int] = mapped_column(Integer, default=1)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )

    steps: Mapped[list["ScenarioStep"]] = relationship(
        back_populates="scenario",
        cascade="all, delete-orphan",
        order_by="ScenarioStep.position",
    )

    def __repr__(self) -> str:
        return f"<Scenario {self.external_id} v{self.content_version}>"


class ScenarioStep(Base):
    """One ordered decision point within a scenario."""

    __tablename__ = "scenario_steps"
    __table_args__ = (
        UniqueConstraint("scenario_id", "position", name="uq_scenario_step_position"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    scenario_id: Mapped[int] = mapped_column(
        ForeignKey("scenarios.id", ondelete="CASCADE")
    )
    position: Mapped[int] = mapped_column(Integer)  # 1-based order
    prompt_text: Mapped[str] = mapped_column(Text)

    # Reuses QuestionType's values ("mcq"/"mr") -- no new enum, so scenario_scoring.py
    # can call grade_item() with the exact same type it already understands.
    step_type: Mapped[str] = mapped_column(String(8))

    scenario: Mapped["Scenario"] = relationship(back_populates="steps")
    options: Mapped[list["ScenarioStepOption"]] = relationship(
        back_populates="step",
        cascade="all, delete-orphan",
        order_by="ScenarioStepOption.position",
    )
    hints: Mapped[list["ScenarioStepHint"]] = relationship(
        back_populates="step",
        cascade="all, delete-orphan",
        order_by="ScenarioStepHint.position",
    )

    @property
    def correct_option_ids(self) -> set[int]:
        return {o.id for o in self.options if o.is_correct}

    def __repr__(self) -> str:
        return f"<ScenarioStep scenario={self.scenario_id} pos={self.position}>"


class ScenarioStepOption(Base):
    """One selectable option on a scenario step.

    Structurally parallel to AnswerOption, plus two fields the 2026-09-20 C1
    reconciliation promoted from deferred to MVP (plan Sections 3.3, 7.5, 16.2):
    `rationale` and `misconception_tag`. These are authored content, never generated
    -- no code path in this file or in scenario_scoring.py infers, computes, or calls
    out to anything to produce them.
    """

    __tablename__ = "scenario_step_options"

    id: Mapped[int] = mapped_column(primary_key=True)
    step_id: Mapped[int] = mapped_column(
        ForeignKey("scenario_steps.id", ondelete="CASCADE")
    )
    label: Mapped[str] = mapped_column(String(4))  # A, B, C, ...
    text: Mapped[str] = mapped_column(Text)
    is_correct: Mapped[bool] = mapped_column(Boolean, default=False)
    position: Mapped[int] = mapped_column(Integer)

    # The Reflection beat (plan Section 16.2/16.8): explains why the decision produces
    # its consequence and which underlying principle applies. Authoring contract (not
    # machine-enforceable beyond non-blank, see docs/GATE-C1-SCENARIO-LAB-PLAN.md
    # Section 16.3's authoring-quality checklist): must not merely restate
    # "Correct."/"Incorrect.".
    rationale: Mapped[str] = mapped_column(Text)

    # Authored ONLY on incorrect options: a short, stable, concept-oriented slug naming
    # the learner's inferred conceptual error -- NOT the option's identity. Null on
    # every correct option, where there is no misconception to name.
    misconception_tag: Mapped[str | None] = mapped_column(String(64), nullable=True)

    step: Mapped["ScenarioStep"] = relationship(back_populates="options")

    def __repr__(self) -> str:
        marker = "*" if self.is_correct else (self.misconception_tag or "")
        return f"<ScenarioStepOption {self.label} {marker}>"


class ScenarioStepHint(Base):
    """One progressive, cost-bearing hint for a scenario step."""

    __tablename__ = "scenario_step_hints"
    __table_args__ = (
        UniqueConstraint("step_id", "position", name="uq_scenario_hint_position"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    step_id: Mapped[int] = mapped_column(
        ForeignKey("scenario_steps.id", ondelete="CASCADE")
    )
    position: Mapped[int] = mapped_column(Integer)  # 1..3, least- to most-revealing
    text: Mapped[str] = mapped_column(Text)
    penalty_bps: Mapped[int] = mapped_column(Integer)  # integer bps, same D-3 rationale

    step: Mapped["ScenarioStep"] = relationship(back_populates="hints")

    def __repr__(self) -> str:
        return f"<ScenarioStepHint step={self.step_id} pos={self.position}>"


class ScenarioAttempt(Base):
    """One candidate sitting of one scenario."""

    __tablename__ = "scenario_attempts"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    scenario_id: Mapped[int] = mapped_column(
        ForeignKey("scenarios.id", ondelete="CASCADE")
    )

    # Reuses AttemptStatus's values ("in_progress"/"submitted"/"abandoned") -- no new
    # enum.
    status: Mapped[str] = mapped_column(String(16), default="in_progress")

    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    submitted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # Deliberately 0-100, not the 100-1000 exam scale, so a formative Lab score is
    # never visually confused with a certification-scaled score (plan Section 3.5).
    score_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    mastery_band: Mapped[str | None] = mapped_column(String(16), nullable=True)

    def __repr__(self) -> str:
        return f"<ScenarioAttempt {self.id} user={self.user_id} scenario={self.scenario_id}>"


class ScenarioStepAttempt(Base):
    """One graded step within a scenario sitting."""

    __tablename__ = "scenario_step_attempts"
    __table_args__ = (
        UniqueConstraint(
            "scenario_attempt_id", "step_id", name="uq_scenario_attempt_step"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    scenario_attempt_id: Mapped[int] = mapped_column(
        ForeignKey("scenario_attempts.id", ondelete="CASCADE")
    )
    step_id: Mapped[int] = mapped_column(ForeignKey("scenario_steps.id"))

    # Denormalised from the step, same rationale as AttemptItem.domain_id: rollups
    # don't need a join.
    position: Mapped[int] = mapped_column(Integer)

    selected_option_ids: Mapped[list[int]] = mapped_column(JSON, default=list)
    is_correct: Mapped[bool | None] = mapped_column(Boolean, nullable=True)

    # Ordered list of scenario_step_hints.id, append-only.
    hints_revealed: Mapped[list[int]] = mapped_column(JSON, default=list)

    step_credit: Mapped[float | None] = mapped_column(Float, nullable=True)
    time_spent_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)

    def __repr__(self) -> str:
        return f"<ScenarioStepAttempt attempt={self.scenario_attempt_id} step={self.step_id}>"


class ScenarioEvent(Base):
    """Append-only evidence log. No update or delete path is ever exposed anywhere
    in this codebase for this table -- INSERT only, by design (plan Section 6)."""

    __tablename__ = "scenario_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    scenario_attempt_id: Mapped[int] = mapped_column(
        ForeignKey("scenario_attempts.id", ondelete="CASCADE")
    )

    # Denormalised, same rationale as AttemptItem.domain_id: fast per-learner queries
    # without a join.
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))

    event_type: Mapped[str] = mapped_column(String(24))
    step_id: Mapped[int | None] = mapped_column(
        ForeignKey("scenario_steps.id"), nullable=True
    )

    # Flat object only -- no nested opaque blobs -- so a future evidence-ingestion job
    # can validate it against a typed schema without guessing its shape.
    payload: Mapped[dict] = mapped_column(JSON, default=dict)

    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )

    def __repr__(self) -> str:
        return f"<ScenarioEvent {self.event_type} attempt={self.scenario_attempt_id}>"
