"""Catalog: tracks, blueprint domains, questions and answer options.

This is the static content side of the schema -- what a candidate is tested on, as
opposed to what happened during a sitting (see attempt.py).
"""

from __future__ import annotations

import enum
from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class QuestionType(str, enum.Enum):
    MCQ = "mcq"
    MR = "mr"


class Track(Base):
    """A certification track (CCAO-F, CCDV-F, CCAR-F, CCAR-P)."""

    __tablename__ = "tracks"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(16), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(160))
    description: Mapped[str] = mapped_column(Text, default="")

    item_count: Mapped[int] = mapped_column(Integer, default=60)
    duration_minutes: Mapped[int] = mapped_column(Integer, default=120)
    pass_scaled_score: Mapped[int] = mapped_column(Integer, default=720)

    # The raw proportion that maps to the 720 pass line. Anthropic does not publish the
    # raw-to-scaled mapping, so this is a per-track assumption (D-2) that can be
    # corrected in data rather than in code.
    pass_raw_threshold: Mapped[float] = mapped_column(default=0.70)

    price_usd: Mapped[float] = mapped_column(Numeric(8, 2), default=99.00)
    validity_months: Mapped[int] = mapped_column(Integer, default=12)

    # False for tracks whose blueprint exists but whose question bank does not yet.
    # The selector shows these as "content coming" rather than hiding them (D-9).
    is_seeded: Mapped[bool] = mapped_column(Boolean, default=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )

    domains: Mapped[list["Domain"]] = relationship(
        back_populates="track",
        cascade="all, delete-orphan",
        order_by="Domain.position",
    )

    def __repr__(self) -> str:
        return f"<Track {self.code}>"


class Domain(Base):
    """One blueprint domain within a track, carrying its exam weight."""

    __tablename__ = "domains"
    __table_args__ = (UniqueConstraint("track_id", "code", name="uq_domain_track_code"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    track_id: Mapped[int] = mapped_column(ForeignKey("tracks.id", ondelete="CASCADE"))
    code: Mapped[str] = mapped_column(String(16), index=True)
    name: Mapped[str] = mapped_column(String(160))
    description: Mapped[str] = mapped_column(Text, default="")

    # Integer basis points, not a float percentage: 1400 == 14.00%. Seven floats
    # summing to 1.0 is not exactly representable; seven integers summing to 10000 is,
    # so tests can assert equality with no epsilon (D-3).
    weight_bps: Mapped[int] = mapped_column(Integer)

    # Published blueprint order. Also the deterministic tie-break during apportionment.
    position: Mapped[int] = mapped_column(Integer)

    track: Mapped["Track"] = relationship(back_populates="domains")
    questions: Mapped[list["Question"]] = relationship(
        back_populates="domain", cascade="all, delete-orphan"
    )

    @property
    def weight_pct(self) -> float:
        return self.weight_bps / 100

    def __repr__(self) -> str:
        return f"<Domain {self.code} {self.weight_pct:.0f}%>"


class Question(Base):
    """One exam item."""

    __tablename__ = "questions"

    id: Mapped[int] = mapped_column(primary_key=True)
    domain_id: Mapped[int] = mapped_column(ForeignKey("domains.id", ondelete="CASCADE"))

    # Stable authoring key (e.g. "CCAO-F-OEV-004"). Seeding matches on this, which is
    # what makes seed.py idempotent across re-runs.
    external_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)

    stem: Mapped[str] = mapped_column(Text)
    question_type: Mapped[QuestionType] = mapped_column(String(8))
    difficulty: Mapped[int] = mapped_column(Integer, default=2)  # 1 easy .. 3 hard

    # Always present. The AI engine augments this; it does not replace it, so the
    # platform explains every answer even with no ANTHROPIC_API_KEY set.
    static_explanation: Mapped[str] = mapped_column(Text, default="")

    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )

    domain: Mapped["Domain"] = relationship(back_populates="questions")
    options: Mapped[list["AnswerOption"]] = relationship(
        back_populates="question",
        cascade="all, delete-orphan",
        order_by="AnswerOption.position",
    )

    @property
    def correct_option_ids(self) -> set[int]:
        return {o.id for o in self.options if o.is_correct}

    def __repr__(self) -> str:
        return f"<Question {self.external_id}>"


class AnswerOption(Base):
    """One selectable option on a question."""

    __tablename__ = "answer_options"

    id: Mapped[int] = mapped_column(primary_key=True)
    question_id: Mapped[int] = mapped_column(
        ForeignKey("questions.id", ondelete="CASCADE")
    )
    label: Mapped[str] = mapped_column(String(4))  # A, B, C, ...
    text: Mapped[str] = mapped_column(Text)
    is_correct: Mapped[bool] = mapped_column(Boolean, default=False)
    position: Mapped[int] = mapped_column(Integer)

    question: Mapped["Question"] = relationship(back_populates="options")

    def __repr__(self) -> str:
        return f"<AnswerOption {self.label}{'*' if self.is_correct else ''}>"
