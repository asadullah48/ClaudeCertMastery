"""Commercial entitlement: what a learner has paid for (or been granted), never what
their evidence says.

One row per grant of a time-boxed Readiness Pass. A learner with no currently-valid row
is on the free Explorer plan -- "free" is the absence of a grant, not a stored state, so
there is nothing to backfill for existing learners and nothing to expire by job.

This table is deliberately disjoint from every evidence table: no foreign key points
from evidence to here or back, and nothing in readiness, scoring or scenario aggregation
reads it. Granting, expiring or revoking access can therefore never alter evidence.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, Index, String
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class LearnerEntitlement(Base):
    __tablename__ = "learner_entitlements"
    __table_args__ = (
        Index("ix_learner_entitlements_user_expires", "user_id", "expires_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    # Only "readiness_pass" today. A string, not a DB enum, so adding a product later is
    # not a schema migration.
    plan: Mapped[str] = mapped_column(String(40))
    # "active" or "revoked". Expiry is NOT a status change -- it is read from expires_at
    # at request time, so an elapsed pass is free without any job having run.
    status: Mapped[str] = mapped_column(String(20), default="active")
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    # Who/what created the grant: "manual_grant" (scripts/grant_readiness_pass.py) or
    # "purchase" (a verified merchant-of-record event, app/services/billing.py).
    source: Mapped[str] = mapped_column(String(40))
    # Merchant of record ("lemonsqueezy" / "paddle") and its order id. Unique so a
    # provider re-delivering the same event can never grant twice.
    provider: Mapped[str | None] = mapped_column(String(40), nullable=True)
    external_reference: Mapped[str | None] = mapped_column(String(255), nullable=True, unique=True)
    # Operator identity for manual grants; free-text note for either source.
    granted_by: Mapped[str | None] = mapped_column(String(120), nullable=True)
    note: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )

    def __repr__(self) -> str:
        return f"<LearnerEntitlement user={self.user_id} {self.plan} {self.status} until {self.expires_at}>"
