"""Cached AI-generated explanations (populated in Session 2)."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class Explanation(Base):
    """One generated explanation, keyed by question *and* by the mistake made.

    The key insight is `selected_option_signature`: a sorted, comma-joined string of the
    option IDs the candidate chose. Two candidates who pick the same wrong answer need
    the same remediation, so the generation is done once and reused. Keying on
    question_id alone would give everyone the same generic text; keying on the attempt
    would regenerate identical content for every candidate.
    """

    __tablename__ = "explanations"
    __table_args__ = (
        UniqueConstraint(
            "question_id", "selected_option_signature", name="uq_explanation_response"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    question_id: Mapped[int] = mapped_column(
        ForeignKey("questions.id", ondelete="CASCADE"), index=True
    )
    selected_option_signature: Mapped[str] = mapped_column(String(128))

    # Structured payload from client.messages.parse() -- see spec section 6.
    why_correct: Mapped[str] = mapped_column(Text, default="")
    why_your_answer_wrong: Mapped[str] = mapped_column(Text, default="")
    key_concept: Mapped[str] = mapped_column(Text, default="")
    blueprint_link: Mapped[str] = mapped_column(Text, default="")
    study_tip: Mapped[str] = mapped_column(Text, default="")

    model: Mapped[str] = mapped_column(String(64), default="claude-opus-5")

    # Persisted so cache effectiveness is measurable in production. If
    # cache_read_tokens stays at 0 across requests, a silent prefix invalidator is at
    # work and the caching strategy in spec section 6 is not actually paying off.
    input_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    output_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cache_read_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)

    generated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )

    @staticmethod
    def signature_for(selected_option_ids: list[int]) -> str:
        """Order-independent signature for a set of selected options."""
        return ",".join(str(i) for i in sorted(selected_option_ids))

    def __repr__(self) -> str:
        return f"<Explanation q={self.question_id} sig={self.selected_option_signature}>"
