"""KSOR Slice 1: the learner-domain-state projection.

Gate C3, Slice 1 -- schema only. See docs/GATE-C3-KSOR-READINESS-IMPLEMENTATION-PLAN.md
Section 3 for the approved design this file implements exactly.

This table is a *materialized projection*, never historical source truth. It answers
"what does ClaudeCertMastery currently believe about this learner in this domain,"
derived entirely from the immutable evidence tables (ExamAttempt, AttemptItem,
ScenarioAttempt, ScenarioStepAttempt, ScenarioEvent) that remain the sole answer to
"what actually happened." A row here is safely disposable and rebuildable at any time
from that evidence -- deleting a LearnerDomainState row must never delete, alter, or
depend on a single evidence row.

Readiness is deterministic. No Anthropic, Zia, or MCP call is ever involved in
producing or consuming a row in this table. No classification logic lives here --
this file is storage only; the classifier that decides what value belongs in
`readiness_state` is a future slice's pure function, not a method on this model.
"""

from __future__ import annotations

import enum
from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, Integer, JSON, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class ReadinessState(str, enum.Enum):
    """The bounded four-state readiness vocabulary (plan Section 9).

    A single ScenarioAttempt, even a perfect one, must never justify READY -- that
    invariant is enforced by the future classifier (Slice 2), not by this enum; this
    enum only bounds which strings are valid, matching AttemptStatus/AttemptMode's
    existing role for their own tables.
    """

    INSUFFICIENT_EVIDENCE = "insufficient_evidence"
    DEVELOPING = "developing"
    APPROACHING_READY = "approaching_ready"
    READY = "ready"


class LearnerDomainState(Base):
    """One learner's materialized, rebuildable projection for one (track, domain).

    Every column here is either a denormalized count/timestamp cheap to recompute
    from evidence, or the bounded classifier output itself (`readiness_state`,
    `reason_codes`) -- never a copy of evidence content. No relationship is declared
    back onto User/Track/Domain: this is deliberately not wired into their object
    graphs, so nothing about this table's existence makes it look authoritative from
    those models' own perspective (plan Section 4/11).
    """

    __tablename__ = "learner_domain_states"
    __table_args__ = (
        UniqueConstraint(
            "user_id", "track_id", "domain_id", name="uq_learner_domain_state"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)

    # Identity. CASCADE is safe here specifically -- unlike every evidence table's own
    # (separately reasoned, more conservative) cascade policy -- because this row
    # holds no historical evidence of its own; it is pure derived state.
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    track_id: Mapped[int] = mapped_column(ForeignKey("tracks.id", ondelete="CASCADE"))
    domain_id: Mapped[int] = mapped_column(ForeignKey("domains.id", ondelete="CASCADE"))

    # Evidence coverage (plan Section 3).
    practice_evidence_count: Mapped[int] = mapped_column(Integer, default=0)
    scenario_evidence_count: Mapped[int] = mapped_column(Integer, default=0)
    distinct_scenario_content_versions: Mapped[int] = mapped_column(Integer, default=0)

    # Recent performance, reusing scoring.MasteryBand's string values verbatim -- no
    # new vocabulary, same "no new enum" precedent ScenarioAttempt.status already
    # established for reusing AttemptStatus's values.
    recent_practice_mastery_band: Mapped[str | None] = mapped_column(
        String(16), nullable=True
    )
    recent_scenario_mastery_band: Mapped[str | None] = mapped_column(
        String(16), nullable=True
    )

    most_recent_evidence_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # Count only -- never the tag names themselves, which stay recoverable on demand
    # straight from ScenarioEvent rather than duplicated here (plan Section 3).
    unresolved_misconception_count: Mapped[int] = mapped_column(Integer, default=0)

    # The classifier's output. Defaults to the safest possible state for a
    # freshly-created row: a row that exists but has not yet been computed must never
    # be mistaken for readiness. No transition/classification logic lives here or
    # anywhere in this file -- Slice 1 only bounds the vocabulary.
    readiness_state: Mapped[str] = mapped_column(
        String(24), default=ReadinessState.INSUFFICIENT_EVIDENCE.value
    )

    # Bounded, short reason-code strings only (plan Section 16) -- never prose, never
    # LLM-generated text. Empty list is the correct default for an uncomputed row:
    # no fabricated conclusion about a learner who has no assessment yet.
    reason_codes: Mapped[list[str]] = mapped_column(JSON, default=list)

    calculated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )

    # Lets a future policy-constant change (plan Section 7/24) identify and
    # selectively recompute rows produced under an earlier policy version.
    projection_version: Mapped[int] = mapped_column(Integer, default=1)

    def __repr__(self) -> str:
        return (
            f"<LearnerDomainState user={self.user_id} domain={self.domain_id} "
            f"state={self.readiness_state}>"
        )
