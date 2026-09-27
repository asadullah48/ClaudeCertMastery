"""Plan and offer endpoints.

GET /offer      -- public: the Readiness Pass offer (single source of truth for price).
GET /me/access  -- the signed-in learner's current plan and capabilities, as decided by
                   app/entitlements.py. The frontend renders this; it never infers access.
"""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import entitlements
from app.auth import get_current_user
from app.database import get_db
from app.models import Track, User
from app.services.billing import checkout_available

router = APIRouter(tags=["access"])

READINESS_PASS_NAME = "ClaudeCertMastery Readiness Pass"
READINESS_PASS_SUBTITLE = "90-Day CCAO-F Preparation"
READINESS_PASS_PRICE_USD = 29


class OfferOut(BaseModel):
    product_name: str
    subtitle: str
    price_usd: int
    duration_days: int
    recurring: bool
    checkout_available: bool


class ExamAllowanceOut(BaseModel):
    track_code: str
    allowed: bool
    submitted: int
    started: int
    free_submitted_limit: int | None


class AccessOut(BaseModel):
    plan: str  # "free" | "readiness_pass"
    starts_at: datetime | None = None
    expires_at: datetime | None = None
    capabilities: list[str]
    exam_allowance: ExamAllowanceOut | None = None
    offer: OfferOut


def _offer() -> OfferOut:
    return OfferOut(
        product_name=READINESS_PASS_NAME,
        subtitle=READINESS_PASS_SUBTITLE,
        price_usd=READINESS_PASS_PRICE_USD,
        duration_days=entitlements.READINESS_PASS_DAYS,
        recurring=False,
        checkout_available=checkout_available(),
    )


@router.get("/offer", response_model=OfferOut)
def get_offer() -> OfferOut:
    return _offer()


@router.get("/me/access", response_model=AccessOut)
def get_my_access(
    track_code: str | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> AccessOut:
    access = entitlements.get_access(db, user)
    allowance = None
    if track_code is not None:
        track = db.scalar(select(Track).where(Track.code == track_code))
        if track is None:
            raise HTTPException(404, f"Track {track_code} not found.")
        a = entitlements.exam_allowance(db, user, access, track.id)
        allowance = ExamAllowanceOut(
            track_code=track.code,
            allowed=a.allowed,
            submitted=a.submitted,
            started=a.started,
            free_submitted_limit=None if access.is_paid else entitlements.FREE_SUBMITTED_EXAMS_PER_TRACK,
        )
    return AccessOut(
        plan=access.plan,
        starts_at=access.starts_at,
        expires_at=access.expires_at,
        capabilities=sorted(c.value for c in entitlements.Capability if access.has(c)),
        exam_allowance=allowance,
        offer=_offer(),
    )
