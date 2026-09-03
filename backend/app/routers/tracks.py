"""Track catalog and blueprint endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.database import get_db
from app.models import Domain, Question, Track
from app.schemas import BlueprintDomainOut, BlueprintOut, TrackOut
from app.services.blueprint import DomainWeight, allocate_items

router = APIRouter(prefix="/tracks", tags=["tracks"])


def _question_counts(db: Session) -> dict[int, int]:
    """Active question count per domain, in one query rather than N."""
    rows = db.execute(
        select(Question.domain_id, func.count(Question.id))
        .where(Question.is_active.is_(True))
        .group_by(Question.domain_id)
    ).all()
    return {domain_id: count for domain_id, count in rows}


@router.get("", response_model=list[TrackOut])
def list_tracks(db: Session = Depends(get_db)) -> list[TrackOut]:
    """All tracks, including those whose question bank is not yet authored (D-9)."""
    tracks = db.scalars(
        select(Track).options(selectinload(Track.domains)).order_by(Track.code)
    ).all()
    counts = _question_counts(db)

    out = []
    for track in tracks:
        payload = TrackOut.model_validate(track)
        payload.question_count = sum(counts.get(d.id, 0) for d in track.domains)
        out.append(payload)
    return out


@router.get("/{code}", response_model=TrackOut)
def get_track(code: str, db: Session = Depends(get_db)) -> TrackOut:
    track = db.scalar(
        select(Track).options(selectinload(Track.domains)).where(Track.code == code)
    )
    if track is None:
        raise HTTPException(status_code=404, detail=f"Track {code} not found.")

    counts = _question_counts(db)
    payload = TrackOut.model_validate(track)
    payload.question_count = sum(counts.get(d.id, 0) for d in track.domains)
    return payload


@router.get("/{code}/blueprint", response_model=BlueprintOut)
def get_blueprint(code: str, db: Session = Depends(get_db)) -> BlueprintOut:
    """The blueprint with the item count each domain contributes at full exam length."""
    track = db.scalar(
        select(Track).options(selectinload(Track.domains)).where(Track.code == code)
    )
    if track is None:
        raise HTTPException(status_code=404, detail=f"Track {code} not found.")
    if not track.domains:
        raise HTTPException(
            status_code=404,
            detail=f"Track {code} has no published blueprint yet.",
        )

    weights = [DomainWeight(d.code, d.weight_bps, d.position) for d in track.domains]
    allocation = allocate_items(weights, track.item_count) if track.item_count else {}
    counts = _question_counts(db)

    return BlueprintOut(
        track_code=track.code,
        item_count=track.item_count,
        total_weight_bps=sum(d.weight_bps for d in track.domains),
        domains=[
            BlueprintDomainOut(
                code=d.code,
                name=d.name,
                weight_pct=d.weight_bps / 100,
                items_at_full_length=allocation.get(d.code, 0),
                questions_available=counts.get(d.id, 0),
            )
            for d in sorted(track.domains, key=lambda d: d.position)
        ],
    )
