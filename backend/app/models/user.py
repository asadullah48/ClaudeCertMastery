"""Candidate identity.

Every table that holds evidence carries `user_id`; `auth_subject` binds a row to a
verified sign-in (app/auth.py). The seeded dev user is only used in dev auth mode.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import DateTime, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    # Verified identity-provider subject (Clerk user id). NULL for the pre-auth dev
    # user until the founder backfill links it; unique so one sign-in = one learner.
    auth_subject: Mapped[str | None] = mapped_column(
        String(255), unique=True, index=True, nullable=True
    )
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    display_name: Mapped[str] = mapped_column(String(120))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )

    attempts: Mapped[list["ExamAttempt"]] = relationship(  # noqa: F821
        back_populates="user", cascade="all, delete-orphan"
    )
    flashcards: Mapped[list["Flashcard"]] = relationship(  # noqa: F821
        back_populates="user", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<User {self.email}>"
