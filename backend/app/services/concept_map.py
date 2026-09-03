"""Resolving a question to an Agent Factory lesson.

The Ask Zia panel is driven entirely by whether a resolution succeeds. If a question's
concept has no mapping, the panel hides for that question -- there is no generic
fallback, because sending a candidate to an unrelated lesson is worse than sending them
nowhere.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import ConceptCurriculumMap, Domain, Question, Track


@dataclass(frozen=True)
class ResolvedConcept:
    """A question's mapped lesson, plus how the mapping was reached."""

    concept_tag: str
    label: str
    lesson_slug: str
    lesson_title: str | None
    lesson_section: str | None
    lesson_url: str | None
    search_query: str
    track_code: str
    # "tag" when an explicit question tag matched, "domain" when it fell back to the
    # question's domain code. Surfaced in the API so mappings can be audited.
    matched_by: str


def resolve_for_question(db: Session, question: Question) -> ResolvedConcept | None:
    """Resolve a question to a mapped lesson, or None if it has no mapping.

    Resolution order:
      1. an explicit tag on the question, in listed order
      2. the question's domain code

    Explicit tags win so a single question can point at a more specific lesson than its
    whole domain would.
    """
    domain = db.get(Domain, question.domain_id)
    if domain is None:
        return None
    track = db.get(Track, domain.track_id)
    if track is None:
        return None

    candidates: list[tuple[str, str]] = [
        (tag, "tag") for tag in (question.tags or [])
    ]
    candidates.append((domain.code, "domain"))

    for tag, matched_by in candidates:
        row = db.scalar(
            select(ConceptCurriculumMap).where(
                ConceptCurriculumMap.track_code == track.code,
                ConceptCurriculumMap.concept_tag == tag,
                ConceptCurriculumMap.is_mapped.is_(True),
            )
        )
        if row is not None and row.lesson_slug:
            return _to_resolved(row, matched_by)

    return None


def resolve_by_tag(
    db: Session, track_code: str, concept_tag: str
) -> ResolvedConcept | None:
    """Resolve a concept tag directly, without a question.

    CCAR-F and CCAR-P have no authored question bank yet, so this is how the panel and
    its tests are exercised for those tracks until Session 4 authors their items.
    """
    row = db.scalar(
        select(ConceptCurriculumMap).where(
            ConceptCurriculumMap.track_code == track_code,
            ConceptCurriculumMap.concept_tag == concept_tag,
            ConceptCurriculumMap.is_mapped.is_(True),
        )
    )
    if row is None or not row.lesson_slug:
        return None
    return _to_resolved(row, "tag")


def _to_resolved(row: ConceptCurriculumMap, matched_by: str) -> ResolvedConcept:
    return ResolvedConcept(
        concept_tag=row.concept_tag,
        label=row.label,
        lesson_slug=row.lesson_slug or "",
        lesson_title=row.lesson_title,
        lesson_section=row.lesson_section,
        lesson_url=row.lesson_url,
        search_query=row.search_query or row.label,
        track_code=row.track_code,
        matched_by=matched_by,
    )


def coverage_report(db: Session) -> list[dict]:
    """Per-track mapping coverage, for the session summary and for tests."""
    rows = db.scalars(
        select(ConceptCurriculumMap).order_by(
            ConceptCurriculumMap.track_code, ConceptCurriculumMap.concept_tag
        )
    ).all()
    return [
        {
            "track_code": r.track_code,
            "concept_tag": r.concept_tag,
            "label": r.label,
            "lesson_slug": r.lesson_slug,
            "is_mapped": r.is_mapped,
            "confidence": r.confidence,
        }
        for r in rows
    ]
