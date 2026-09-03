"""Spaced-repetition state (SM-2). Review logic lands in Session 3."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import DateTime, Float, ForeignKey, Integer, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

INITIAL_EASE_FACTOR = 2.5


class Flashcard(Base):
    """One question scheduled for review by one candidate.

    Cards are created from missed items, so the deck is automatically the candidate's
    own weak spots rather than a generic set.
    """

    __tablename__ = "flashcards"
    __table_args__ = (
        UniqueConstraint("user_id", "question_id", name="uq_flashcard_user_question"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    question_id: Mapped[int] = mapped_column(
        ForeignKey("questions.id", ondelete="CASCADE")
    )

    # SM-2 state.
    ease_factor: Mapped[float] = mapped_column(Float, default=INITIAL_EASE_FACTOR)
    interval_days: Mapped[int] = mapped_column(Integer, default=0)
    repetitions: Mapped[int] = mapped_column(Integer, default=0)
    lapses: Mapped[int] = mapped_column(Integer, default=0)

    due_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), index=True
    )
    last_reviewed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )

    user: Mapped["User"] = relationship(back_populates="flashcards")  # noqa: F821

    def __repr__(self) -> str:
        return f"<Flashcard u={self.user_id} q={self.question_id} due={self.due_at}>"
