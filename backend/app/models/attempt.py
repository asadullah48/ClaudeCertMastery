"""Attempt records: one exam sitting and its per-item and per-domain results."""

from __future__ import annotations

import enum
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


class AttemptMode(str, enum.Enum):
    EXAM = "exam"            # full blueprint-weighted mock, timed
    PRACTICE = "practice"    # subset, untimed
    DOMAIN_DRILL = "drill"   # single-domain focus


class AttemptStatus(str, enum.Enum):
    IN_PROGRESS = "in_progress"
    SUBMITTED = "submitted"
    ABANDONED = "abandoned"


class ExamAttempt(Base):
    __tablename__ = "exam_attempts"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    track_id: Mapped[int] = mapped_column(ForeignKey("tracks.id", ondelete="CASCADE"))

    mode: Mapped[AttemptMode] = mapped_column(String(16), default=AttemptMode.EXAM)
    status: Mapped[AttemptStatus] = mapped_column(
        String(16), default=AttemptStatus.IN_PROGRESS
    )

    # The RNG seed used to compose this exam. Storing it makes any sitting exactly
    # reproducible from its ID alone -- useful for support and for regression tests.
    seed: Mapped[int] = mapped_column(Integer)

    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    submitted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    raw_correct: Mapped[int | None] = mapped_column(Integer, nullable=True)
    raw_total: Mapped[int | None] = mapped_column(Integer, nullable=True)
    scaled_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    passed: Mapped[bool | None] = mapped_column(Boolean, nullable=True)

    # Set when the bank could not satisfy a domain's quota and items were redistributed.
    # The exam is never silently short; the deviation is recorded here.
    composition_warning: Mapped[str | None] = mapped_column(Text, nullable=True)

    user: Mapped["User"] = relationship(back_populates="attempts")  # noqa: F821
    items: Mapped[list["AttemptItem"]] = relationship(
        back_populates="attempt",
        cascade="all, delete-orphan",
        order_by="AttemptItem.position",
    )
    domain_scores: Mapped[list["AttemptDomainScore"]] = relationship(
        back_populates="attempt", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<ExamAttempt {self.id} scaled={self.scaled_score}>"


class AttemptItem(Base):
    """One question as presented within one attempt, plus the candidate's response."""

    __tablename__ = "attempt_items"
    __table_args__ = (
        UniqueConstraint("attempt_id", "question_id", name="uq_attempt_question"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    attempt_id: Mapped[int] = mapped_column(
        ForeignKey("exam_attempts.id", ondelete="CASCADE")
    )
    question_id: Mapped[int] = mapped_column(ForeignKey("questions.id"))

    # Denormalised from the question so per-domain rollups and dashboard queries do not
    # need a three-table join on every read.
    domain_id: Mapped[int] = mapped_column(ForeignKey("domains.id"))

    position: Mapped[int] = mapped_column(Integer)

    # List of selected answer_option IDs. JSON rather than a join table: it is always
    # read and written whole, never queried by element.
    selected_option_ids: Mapped[list[int]] = mapped_column(JSON, default=list)

    is_correct: Mapped[bool | None] = mapped_column(Boolean, nullable=True)

    # Recorded for every item but excluded from the scaled score by default (D-4).
    # Feeds the mastery dashboard and the SM-2 initial grade.
    partial_credit: Mapped[float | None] = mapped_column(Float, nullable=True)

    time_spent_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    flagged_for_review: Mapped[bool] = mapped_column(Boolean, default=False)

    attempt: Mapped["ExamAttempt"] = relationship(back_populates="items")

    def __repr__(self) -> str:
        return f"<AttemptItem a={self.attempt_id} q={self.question_id}>"


class AttemptDomainScore(Base):
    """Per-domain rollup for one attempt.

    A deliberate denormalisation: the dashboard reads per-domain mastery on every page
    load, and recomputing it from attempt_items on each read would not scale.
    """

    __tablename__ = "attempt_domain_scores"
    __table_args__ = (
        UniqueConstraint("attempt_id", "domain_id", name="uq_attempt_domain"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    attempt_id: Mapped[int] = mapped_column(
        ForeignKey("exam_attempts.id", ondelete="CASCADE")
    )
    domain_id: Mapped[int] = mapped_column(ForeignKey("domains.id"))

    correct: Mapped[int] = mapped_column(Integer)
    total: Mapped[int] = mapped_column(Integer)
    percentage: Mapped[float] = mapped_column(Float)
    mastery_band: Mapped[str] = mapped_column(String(16))

    attempt: Mapped["ExamAttempt"] = relationship(back_populates="domain_scores")

    def __repr__(self) -> str:
        return f"<AttemptDomainScore d={self.domain_id} {self.percentage:.0f}%>"
