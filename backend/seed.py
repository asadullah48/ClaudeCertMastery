"""Seed the database from the YAML question bank.

Idempotent: every row is matched on a stable natural key (track code, domain code,
question external_id) and updated in place rather than duplicated, so this can be re-run
safely after editing the YAML.

    python seed.py            # create tables if needed, then seed
    python seed.py --reset    # drop and recreate everything first
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import yaml
from sqlalchemy import select
from sqlalchemy.orm import Session

sys.path.insert(0, str(Path(__file__).parent))

from app.database import Base, SessionLocal, engine  # noqa: E402
from app.models import (  # noqa: E402
    AnswerOption,
    Domain,
    Question,
    QuestionType,
    Track,
    User,
)

SEED_DIR = Path(__file__).parent / "seed_data"

DEV_USER_EMAIL = "dev@certmastery.local"

# CCAO-F is the only track with published blueprint weights and an authored bank.
CCAO_F = {
    "code": "CCAO-F",
    "name": "Claude Certified AI Operator - Foundation",
    "description": (
        "Foundation-level certification for operators who use Claude in day-to-day "
        "workflows: prompting, output validation, product selection, workflow design, "
        "configuration, governance and troubleshooting."
    ),
    "item_count": 60,
    "duration_minutes": 120,
    "pass_scaled_score": 720,
    "pass_raw_threshold": 0.70,
    "price_usd": 99.00,
    "validity_months": 12,
    "is_seeded": True,
}

# The remaining three tracks are seeded as visible-but-unseeded (D-9). Their published
# blueprints are not available, and inventing domain weights would fabricate the very
# ground truth this platform exists to mirror -- so no Domain rows are created for them
# until official weights are known. Subject scope below comes from the track outlines.
PLACEHOLDER_TRACKS = [
    {
        "code": "CCDV-F",
        "name": "Claude Certified Developer - Foundation",
        "description": (
            "TODO (question bank not yet authored). Scope: Python and TypeScript "
            "development against the Messages API; streaming and batch processing; tool "
            "schema design; agentic AI fundamentals."
        ),
    },
    {
        "code": "CCAR-F",
        "name": "Claude Certified Architect - Foundation",
        "description": (
            "TODO (question bank not yet authored). Scope: multi-agent supervisor and "
            "worker topologies; prompt-caching economics; CLAUDE.md configuration; CLI "
            "arguments; file-naming conventions; configuration flags."
        ),
    },
    {
        "code": "CCAR-P",
        "name": "Claude Certified Architect - Professional",
        "description": (
            "TODO (question bank not yet authored). Scope: enterprise RAG pipelines; "
            "automated evaluation frameworks; compliance, cost and latency trade-offs."
        ),
    },
]


def upsert_track(db: Session, spec: dict) -> Track:
    """Create or update a track, matched on its code."""
    track = db.scalar(select(Track).where(Track.code == spec["code"]))
    if track is None:
        track = Track(code=spec["code"])
        db.add(track)
    for key, value in spec.items():
        if key != "code":
            setattr(track, key, value)
    db.flush()
    return track


def upsert_domain(db: Session, track: Track, meta: dict) -> Domain:
    """Create or update a domain, matched on (track, code)."""
    domain = db.scalar(
        select(Domain).where(Domain.track_id == track.id, Domain.code == meta["code"])
    )
    if domain is None:
        domain = Domain(track_id=track.id, code=meta["code"])
        db.add(domain)
    domain.name = meta["name"]
    domain.description = meta.get("description", "").strip()
    domain.weight_bps = meta["weight_bps"]
    domain.position = meta["position"]
    db.flush()
    return domain


def upsert_question(db: Session, domain: Domain, spec: dict) -> Question:
    """Create or update a question and its options, matched on external_id.

    Options are replaced wholesale rather than diffed. They have no independent identity
    worth preserving, and a full replace guarantees the stored options exactly match the
    YAML -- a diff could silently leave a stale option behind.
    """
    question = db.scalar(
        select(Question).where(Question.external_id == spec["external_id"])
    )
    if question is None:
        question = Question(external_id=spec["external_id"])
        db.add(question)

    question.domain_id = domain.id
    question.stem = spec["stem"].strip()
    question.question_type = QuestionType(spec["type"])
    question.difficulty = spec.get("difficulty", 2)
    question.static_explanation = spec.get("explanation", "").strip()
    question.is_active = spec.get("active", True)
    db.flush()

    for existing in list(question.options):
        db.delete(existing)
    db.flush()

    for position, opt in enumerate(spec["options"], start=1):
        db.add(
            AnswerOption(
                question_id=question.id,
                label=opt["label"],
                text=opt["text"].strip(),
                is_correct=bool(opt.get("correct", False)),
                position=position,
            )
        )
    db.flush()
    return question


def validate_bank(domain_code: str, questions: list[dict]) -> None:
    """Fail loudly on malformed content before it reaches the database.

    Catching these at seed time rather than at exam time matters: a question with no
    correct option would otherwise surface as an item no candidate can ever get right.
    """
    for q in questions:
        qid = q["external_id"]
        correct = [o for o in q["options"] if o.get("correct")]
        if q["type"] == "mcq" and len(correct) != 1:
            raise ValueError(
                f"{qid}: MCQ must have exactly 1 correct option, found {len(correct)}."
            )
        if q["type"] == "mr" and len(correct) < 2:
            raise ValueError(
                f"{qid}: MR must have at least 2 correct options, found {len(correct)}."
            )
        if len(q["options"]) < 3:
            raise ValueError(f"{qid}: needs at least 3 options.")
        labels = [o["label"] for o in q["options"]]
        if len(set(labels)) != len(labels):
            raise ValueError(f"{qid}: duplicate option labels {labels}.")
        if not q.get("explanation", "").strip():
            raise ValueError(f"{qid}: static_explanation is required.")


def seed_ccao_f(db: Session) -> tuple[int, int]:
    """Load the CCAO-F blueprint and question bank from YAML."""
    track = upsert_track(db, CCAO_F)

    files = sorted((SEED_DIR / "ccao_f").glob("*.yaml"))
    if not files:
        raise FileNotFoundError(f"No YAML seed files found in {SEED_DIR / 'ccao_f'}.")

    domain_count = 0
    question_count = 0
    total_bps = 0

    for path in files:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        meta = data["domain"]
        questions = data["questions"]

        validate_bank(meta["code"], questions)

        domain = upsert_domain(db, track, meta)
        total_bps += meta["weight_bps"]
        domain_count += 1

        for spec in questions:
            upsert_question(db, domain, spec)
            question_count += 1

        print(f"  {meta['code']:<5} {meta['weight_bps']:>5} bps  {len(questions):>3} questions")

    # The blueprint is only faithful if the weights are complete. A silent 9900 would
    # skew every generated exam, so this is an error rather than a warning.
    if total_bps != 10_000:
        raise ValueError(
            f"CCAO-F domain weights sum to {total_bps} bps, expected 10000. "
            "The blueprint is incomplete or a weight is wrong."
        )

    return domain_count, question_count


def seed_placeholder_tracks(db: Session) -> int:
    """Register the three tracks whose question banks are not yet authored."""
    for spec in PLACEHOLDER_TRACKS:
        upsert_track(
            db,
            {
                **spec,
                "item_count": 0,
                "duration_minutes": 0,
                "pass_scaled_score": 720,
                "pass_raw_threshold": 0.70,
                "price_usd": 0.00,
                "validity_months": 12,
                "is_seeded": False,
            },
        )
        print(f"  {spec['code']:<8} registered (TODO: question bank)")
    return len(PLACEHOLDER_TRACKS)


def seed_dev_user(db: Session) -> User:
    """A single local user stands in until auth arrives in Session 3 (D-7)."""
    user = db.scalar(select(User).where(User.email == DEV_USER_EMAIL))
    if user is None:
        user = User(email=DEV_USER_EMAIL, display_name="Dev Candidate")
        db.add(user)
        db.flush()
    return user


def main() -> int:
    parser = argparse.ArgumentParser(description="Seed the Claude Cert Mastery database.")
    parser.add_argument(
        "--reset",
        action="store_true",
        help="Drop and recreate all tables before seeding (destroys existing data).",
    )
    args = parser.parse_args()

    if args.reset:
        print("Dropping all tables...")
        Base.metadata.drop_all(engine)

    Base.metadata.create_all(engine)

    with SessionLocal() as db:
        print("\nSeeding CCAO-F:")
        domains, questions = seed_ccao_f(db)

        print("\nRegistering placeholder tracks:")
        placeholders = seed_placeholder_tracks(db)

        seed_dev_user(db)
        db.commit()

        print(
            f"\nDone. {1 + placeholders} tracks, {domains} CCAO-F domains, "
            f"{questions} questions, 1 dev user."
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
