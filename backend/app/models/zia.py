"""Zia Tutor AI integration tables.

Two tables, deliberately separate from the scoring schema:

  zia_learner_links      cert_mastery user -> Zia learner identity
  concept_curriculum_map concept tag      -> Agent Factory lesson slug

Keeping the identity mapping out of `users` and the curriculum mapping out of
`questions` means the Zia integration can be removed, re-pointed at a different tutor,
or fail entirely without touching a single row that scoring depends on.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class ZiaLearnerLink(Base):
    """Stable mapping from a Cert Mastery user to their Zia learner identity.

    Zia maintains its own per-learner mastery record, so the same person must resolve to
    the same Zia learner on every visit or their progress fragments across handles.
    """

    __tablename__ = "zia_learner_links"
    __table_args__ = (
        UniqueConstraint("user_id", name="uq_zia_link_user"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )

    # Opaque handle returned by the tutor. Never parsed, only echoed back.
    zia_identity: Mapped[str] = mapped_column(String(255))

    # Most recent session handle, refreshed by begin_session.
    zia_session_handle: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # Distinguishes a first visit (begin_session) from a return (open_student_record).
    session_started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_seen_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )

    def __repr__(self) -> str:
        return f"<ZiaLearnerLink user={self.user_id} zia={self.zia_identity}>"


class ConceptCurriculumMap(Base):
    """Maps a Cert Mastery concept tag onto an Agent Factory lesson.

    `is_mapped=False` rows are deliberate: an objective with no real lesson behind it is
    recorded explicitly rather than omitted, so coverage gaps are visible in the data
    instead of being indistinguishable from an unseeded table.
    """

    __tablename__ = "concept_curriculum_map"
    __table_args__ = (
        UniqueConstraint(
            "track_code", "concept_tag", name="uq_concept_map_track_tag"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)

    track_code: Mapped[str] = mapped_column(String(16), index=True)
    concept_tag: Mapped[str] = mapped_column(String(96), index=True)
    label: Mapped[str] = mapped_column(String(200))

    # Confirmed against the live MCP via outline_agent_factory / search_agent_factory.
    lesson_slug: Mapped[str | None] = mapped_column(String(160), nullable=True)
    lesson_title: Mapped[str | None] = mapped_column(String(240), nullable=True)
    # Optional heading_path for a specific section within the lesson.
    lesson_section: Mapped[str | None] = mapped_column(String(240), nullable=True)
    lesson_url: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Query handed to search_agent_factory when the panel opens.
    search_query: Mapped[str] = mapped_column(Text, default="")

    is_mapped: Mapped[bool] = mapped_column(Boolean, default=True)
    confidence: Mapped[float] = mapped_column(Float, default=1.0)
    notes: Mapped[str] = mapped_column(Text, default="")

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )

    def __repr__(self) -> str:
        state = self.lesson_slug if self.is_mapped else "UNMAPPED"
        return f"<ConceptCurriculumMap {self.track_code}/{self.concept_tag} -> {state}>"
