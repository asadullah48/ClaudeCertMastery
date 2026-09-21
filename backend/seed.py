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
    AttemptItem,
    ConceptCurriculumMap,
    Domain,
    ExamAttempt,
    Question,
    QuestionType,
    Scenario,
    ScenarioAttempt,
    ScenarioStep,
    ScenarioStepHint,
    ScenarioStepOption,
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


def _scenario_spec_signature(spec: dict) -> tuple:
    """Canonical, comparable shape of a scenario YAML spec's evidence-affecting
    content -- used to detect whether a re-seed actually changes anything (Gate
    C1-CONTENT Section 6)."""
    return (
        spec["title"].strip(),
        spec["setup_text"].strip(),
        bool(spec.get("active", True)),
        int(spec.get("difficulty", 2)),
        tuple(
            (
                step["position"],
                step["type"],
                step["prompt"].strip(),
                tuple(
                    (
                        opt["label"],
                        opt["text"].strip(),
                        bool(opt.get("correct", False)),
                        opt["rationale"].strip(),
                        opt.get("misconception_tag") or None,
                    )
                    for opt in step["options"]
                ),
                tuple(
                    (h["position"], h["penalty_bps"], h["text"].strip())
                    for h in step.get("hints", [])
                ),
            )
            for step in sorted(spec["steps"], key=lambda s: s["position"])
        ),
    )


def _scenario_db_signature(scenario: Scenario) -> tuple:
    """The same shape as `_scenario_spec_signature`, read back from the ORM."""
    return (
        scenario.title,
        scenario.setup_text,
        scenario.is_active,
        scenario.difficulty,
        tuple(
            (
                step.position,
                step.step_type,
                step.prompt_text,
                tuple(
                    (o.label, o.text, o.is_correct, o.rationale, o.misconception_tag)
                    for o in sorted(step.options, key=lambda o: o.position)
                ),
                tuple(
                    (h.position, h.penalty_bps, h.text)
                    for h in sorted(step.hints, key=lambda h: h.position)
                ),
            )
            for step in sorted(scenario.steps, key=lambda s: s.position)
        ),
    )


def _scenario_has_evidence(db: Session, scenario: Scenario) -> bool:
    """Whether any learner has ever started this scenario.

    Every ScenarioStepAttempt/ScenarioEvent row is only ever created (in
    app/routers/scenarios.py) after a ScenarioAttempt row already exists for the
    same scenario, so checking for a ScenarioAttempt is sufficient: zero attempts
    means zero evidence anywhere downstream of it.
    """
    return (
        db.scalar(
            select(ScenarioAttempt.id)
            .where(ScenarioAttempt.scenario_id == scenario.id)
            .limit(1)
        )
        is not None
    )


def validate_scenario_spec(spec: dict, domain_codes: set[str]) -> None:
    """Fail loudly on malformed scenario content before it reaches the database.

    Mirrors validate_bank()'s philosophy exactly, extended for scenario-specific
    invariants: step/option structure, the misconception_tag authoring contract
    (conceptual, not an option identifier), and the source_reference provenance
    requirement (Gate C1-CONTENT Section 5 -- see that report for why this is a
    required YAML field rather than a new database column).
    """
    ext_id = spec.get("external_id", "<missing external_id>")

    if spec.get("domain_code") not in domain_codes:
        raise ValueError(
            f"{ext_id}: domain_code {spec.get('domain_code')!r} is not a seeded "
            f"domain for this track. Known domains: {sorted(domain_codes)}."
        )
    if not spec.get("title", "").strip():
        raise ValueError(f"{ext_id}: title is required.")
    if not spec.get("setup_text", "").strip():
        raise ValueError(f"{ext_id}: setup_text is required.")
    if not spec.get("source_reference", "").strip():
        raise ValueError(
            f"{ext_id}: source_reference is required -- every production scenario "
            "must record what grounds it, even when 'originally authored'."
        )
    if not isinstance(spec.get("content_version"), int) or spec["content_version"] < 1:
        raise ValueError(f"{ext_id}: content_version must be a positive integer.")

    steps = spec.get("steps") or []
    if not steps:
        raise ValueError(f"{ext_id}: at least one step is required.")

    positions = [s["position"] for s in steps]
    if positions != list(range(1, len(steps) + 1)):
        raise ValueError(
            f"{ext_id}: step positions must be 1..N with no gaps or duplicates, "
            f"got {positions}."
        )

    for step in steps:
        step_id = f"{ext_id} step {step['position']}"
        if step.get("type") not in ("mcq", "mr"):
            raise ValueError(f"{step_id}: type must be 'mcq' or 'mr'.")
        if not step.get("prompt", "").strip():
            raise ValueError(f"{step_id}: prompt is required.")

        options = step.get("options") or []
        if len(options) < 2:
            raise ValueError(f"{step_id}: needs at least 2 options.")
        labels = [o["label"] for o in options]
        if len(set(labels)) != len(labels):
            raise ValueError(f"{step_id}: duplicate option labels {labels}.")

        correct = [o for o in options if o.get("correct")]
        if step["type"] == "mcq" and len(correct) != 1:
            raise ValueError(
                f"{step_id}: mcq must have exactly 1 correct option, found {len(correct)}."
            )
        if step["type"] == "mr" and len(correct) < 2:
            raise ValueError(
                f"{step_id}: mr must have at least 2 correct options, found {len(correct)}."
            )

        for opt in options:
            opt_id = f"{step_id} option {opt.get('label')}"
            if not opt.get("text", "").strip():
                raise ValueError(f"{opt_id}: text is required.")
            rationale = opt.get("rationale", "").strip()
            if not rationale:
                raise ValueError(f"{opt_id}: rationale is required.")
            if rationale.rstrip(".").lower() in ("correct", "incorrect"):
                raise ValueError(
                    f"{opt_id}: rationale must not merely restate correct/incorrect "
                    "(Scenario Lab authoring contract -- see ScenarioStepOption's "
                    "own docstring)."
                )
            tag = opt.get("misconception_tag")
            if opt.get("correct"):
                if tag:
                    raise ValueError(
                        f"{opt_id}: a correct option must not carry a misconception_tag."
                    )
            else:
                if not tag or not tag.strip():
                    raise ValueError(
                        f"{opt_id}: an incorrect option must carry a misconception_tag."
                    )
                if tag.strip().lower() == opt["label"].strip().lower() or len(tag.strip()) < 4:
                    raise ValueError(
                        f"{opt_id}: misconception_tag {tag!r} looks like an option "
                        "identifier, not a conceptual label -- it must name the "
                        "learner's inferred error, never the option itself."
                    )

        hints = step.get("hints") or []
        hint_positions = [h["position"] for h in hints]
        if hint_positions != list(range(1, len(hints) + 1)):
            raise ValueError(
                f"{step_id}: hint positions must be 1..N with no gaps, got {hint_positions}."
            )
        for hint in hints:
            if not hint.get("text", "").strip():
                raise ValueError(f"{step_id} hint {hint['position']}: text is required.")
            if not isinstance(hint.get("penalty_bps"), int) or hint["penalty_bps"] <= 0:
                raise ValueError(
                    f"{step_id} hint {hint['position']}: penalty_bps must be a "
                    "positive integer."
                )


def _write_scenario_steps(db: Session, scenario: Scenario, spec: dict) -> None:
    """Replace a scenario's steps/options/hints wholesale from the spec.

    Only ever called for a brand-new scenario, or one confirmed (by the caller,
    upsert_scenario) to have zero recorded learner evidence -- see that function's
    docstring for why this ordering matters.
    """
    for existing in list(scenario.steps):
        db.delete(existing)
    db.flush()

    for step_spec in sorted(spec["steps"], key=lambda s: s["position"]):
        step = ScenarioStep(
            scenario_id=scenario.id,
            position=step_spec["position"],
            prompt_text=step_spec["prompt"].strip(),
            step_type=step_spec["type"],
        )
        db.add(step)
        db.flush()

        for position, opt in enumerate(step_spec["options"], start=1):
            db.add(
                ScenarioStepOption(
                    step_id=step.id,
                    label=opt["label"],
                    text=opt["text"].strip(),
                    is_correct=bool(opt.get("correct", False)),
                    position=position,
                    rationale=opt["rationale"].strip(),
                    misconception_tag=(opt.get("misconception_tag") or None),
                )
            )

        for hint in step_spec.get("hints", []):
            db.add(
                ScenarioStepHint(
                    step_id=step.id,
                    position=hint["position"],
                    text=hint["text"].strip(),
                    penalty_bps=hint["penalty_bps"],
                )
            )
    db.flush()


def upsert_scenario(db: Session, domain: Domain, spec: dict) -> tuple[Scenario, str]:
    """Create or update a scenario, matched on external_id. Returns (scenario, action)
    where action is one of "created" / "unchanged" / "updated".

    Content-version and evidence-safety contract (Gate C1-CONTENT Section 6):

    A. Scenario does not exist  -> created fresh at the YAML's content_version.
    B. Same content_version, identical content -> true no-op (only cosmetic,
       non-evidence-affecting fields like is_active/difficulty/title are refreshed).
    C. Same content_version, DIFFERENT content -> refused. An evidence-affecting
       edit without a version bump is an authoring mistake, not a valid state.
    D. Higher content_version (an intentional bump):
         - no recorded evidence yet for this scenario -> safe to replace steps/
           options/hints in place, matching upsert_question()'s own pattern.
         - evidence already exists -> refused. Replacing steps in place would
           either be rejected by the database's own foreign-key constraints (a
           ScenarioStep referenced by a ScenarioStepAttempt cannot be deleted) or,
           on an engine that doesn't enforce that FK, silently orphan/relabel the
           option IDs a past attempt's selected_option_ids point to -- exactly the
           silent evidence mutation this contract must never allow. Author a new
           scenario (new external_id) instead.
    E. Lower content_version than what's stored -> refused; content_version must
       never move backward.
    """
    ext_id = spec["external_id"]
    incoming_version = spec["content_version"]
    scenario = db.scalar(select(Scenario).where(Scenario.external_id == ext_id))

    if scenario is None:
        scenario = Scenario(
            external_id=ext_id,
            domain_id=domain.id,
            title=spec["title"].strip(),
            setup_text=spec["setup_text"].strip(),
            difficulty=spec.get("difficulty", 2),
            is_active=spec.get("active", True),
            content_version=incoming_version,
        )
        db.add(scenario)
        db.flush()
        _write_scenario_steps(db, scenario, spec)
        return scenario, "created"

    if incoming_version < scenario.content_version:
        raise ValueError(
            f"{ext_id}: YAML content_version {incoming_version} is behind the "
            f"stored version {scenario.content_version}. content_version must "
            "never move backward."
        )

    content_changed = _scenario_db_signature(scenario) != _scenario_spec_signature(spec)

    if incoming_version == scenario.content_version:
        if content_changed:
            raise ValueError(
                f"{ext_id}: authored content differs from what is stored, but "
                f"content_version was not bumped past {scenario.content_version}. "
                "Bump content_version for any evidence-affecting change, per "
                "Scenario.content_version's own contract (app/models/scenario.py)."
            )
        scenario.domain_id = domain.id
        scenario.title = spec["title"].strip()
        scenario.is_active = spec.get("active", True)
        scenario.difficulty = spec.get("difficulty", 2)
        db.flush()
        return scenario, "unchanged"

    # incoming_version > scenario.content_version: an intentional bump.
    if _scenario_has_evidence(db, scenario):
        raise ValueError(
            f"{ext_id}: content_version bumped to {incoming_version}, but existing "
            f"learner evidence already references version {scenario.content_version}. "
            "Replacing steps/options in place here would risk corrupting or being "
            "rejected for that historical evidence. Author a new scenario (a new "
            "external_id) instead of versioning this one further while evidence "
            "exists against it."
        )

    scenario.domain_id = domain.id
    scenario.title = spec["title"].strip()
    scenario.setup_text = spec["setup_text"].strip()
    scenario.difficulty = spec.get("difficulty", 2)
    scenario.is_active = spec.get("active", True)
    scenario.content_version = incoming_version
    db.flush()
    _write_scenario_steps(db, scenario, spec)
    return scenario, "updated"


def seed_ccao_f_scenarios(db: Session) -> tuple[int, dict[str, str]]:
    """Load production Scenario Lab content from YAML, one file per domain.

    Deliberately optional and separate from seed_ccao_f(): a missing or empty
    seed_data/ccao_f_scenarios/ directory must not fail seeding overall, mirroring
    seed_concept_map()'s own optional-file stance. Scenario Lab already handles
    zero scenarios honestly at the API and UI layers (Gate C1-INTEGRATION Section 4).
    """
    scenario_dir = SEED_DIR / "ccao_f_scenarios"
    if not scenario_dir.exists():
        print("  (no scenario content directory; skipping)")
        return 0, {}

    track = db.scalar(select(Track).where(Track.code == "CCAO-F"))
    if track is None:
        raise RuntimeError("CCAO-F track must be seeded before scenario content.")
    domains_by_code = {
        d.code: d
        for d in db.scalars(select(Domain).where(Domain.track_id == track.id))
    }

    files = sorted(scenario_dir.glob("*.yaml"))
    actions: dict[str, str] = {}
    for path in files:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        spec = data["scenario"]
        validate_scenario_spec(spec, set(domains_by_code))
        domain = domains_by_code[spec["domain_code"]]
        scenario, action = upsert_scenario(db, domain, spec)
        actions[scenario.external_id] = action
        print(f"  {spec['domain_code']:<5} {spec['external_id']:<24} {action}")

    return len(files), actions


def seed_concept_map(db: Session) -> tuple[int, int]:
    """Load the Zia concept -> Agent Factory lesson mapping.

    Slugs in the YAML were confirmed against the live MCP; this loader does not call it,
    so seeding works offline and in CI. Re-confirm with:
        python scripts/verify_zia_connection.py
    """
    path = SEED_DIR / "zia" / "concept_map.yaml"
    if not path.exists():
        print("  (no concept map file; skipping)")
        return 0, 0

    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    mapped = unmapped = 0
    by_track: dict[str, int] = {}

    for spec in data.get("concepts", []):
        row = db.scalar(
            select(ConceptCurriculumMap).where(
                ConceptCurriculumMap.track_code == spec["track_code"],
                ConceptCurriculumMap.concept_tag == spec["concept_tag"],
            )
        )
        if row is None:
            row = ConceptCurriculumMap(
                track_code=spec["track_code"], concept_tag=spec["concept_tag"]
            )
            db.add(row)

        row.label = spec["label"]
        row.lesson_slug = spec.get("lesson_slug")
        row.lesson_title = spec.get("lesson_title")
        row.lesson_section = spec.get("lesson_section")
        row.lesson_url = spec.get("lesson_url")
        row.search_query = (spec.get("search_query") or spec["label"]).strip()
        row.confidence = float(spec.get("confidence", 1.0))
        row.notes = (spec.get("notes") or "").strip()
        # Mapped only if it actually names a lesson. An entry claiming mapped:true with
        # no slug would show the panel with nowhere to send the candidate.
        row.is_mapped = bool(spec.get("mapped", True)) and bool(spec.get("lesson_slug"))

        if row.is_mapped:
            mapped += 1
        else:
            unmapped += 1
        by_track[spec["track_code"]] = by_track.get(spec["track_code"], 0) + 1

    db.flush()
    for track_code in sorted(by_track):
        print(f"  {track_code:<8} {by_track[track_code]:>2} concept tags")
    return mapped, unmapped


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


def check_no_production_attempts(db: Session) -> None:
    """Refuse to proceed if a real candidate has already sat an exam.

    Once an attempt exists, upserting content by external_id (or --reset dropping
    tables outright) could change or destroy the questions and options underneath an
    already-graded sitting. There is no way to distinguish "safe to reseed" from
    "would corrupt candidate history" other than checking for attempt data first, so
    this runs before any table is dropped or any content row is touched -- including
    under --reset, which must stay destructive but must never destroy attempts by
    accident.
    """
    if db.scalar(select(ExamAttempt.id).limit(1)) is not None:
        print(
            "Refusing to seed: existing exam attempts found in this database. "
            "Seeding or --reset could alter or destroy candidate history. Aborting.",
            file=sys.stderr,
        )
        raise SystemExit(1)
    if db.scalar(select(AttemptItem.id).limit(1)) is not None:
        print(
            "Refusing to seed: existing attempt items found in this database. "
            "Seeding or --reset could alter or destroy candidate history. Aborting.",
            file=sys.stderr,
        )
        raise SystemExit(1)


def main() -> int:
    parser = argparse.ArgumentParser(description="Seed the Claude Cert Mastery database.")
    parser.add_argument(
        "--reset",
        action="store_true",
        help="Drop and recreate all tables before seeding (destroys existing data).",
    )
    args = parser.parse_args()

    # Tables must exist before they can be queried for attempts, so create_all runs
    # first -- that's schema DDL, not content mutation, and is a no-op on a database
    # that already has its tables. The guard then runs before --reset's drop_all and
    # before any seed function, so it cannot be bypassed by either path.
    Base.metadata.create_all(engine)
    with SessionLocal() as db:
        check_no_production_attempts(db)

    if args.reset:
        print("Dropping all tables...")
        Base.metadata.drop_all(engine)
        Base.metadata.create_all(engine)

    with SessionLocal() as db:
        print("\nSeeding CCAO-F:")
        domains, questions = seed_ccao_f(db)

        print("\nSeeding CCAO-F scenario content:")
        scenario_files, scenario_actions = seed_ccao_f_scenarios(db)

        print("\nRegistering placeholder tracks:")
        placeholders = seed_placeholder_tracks(db)

        print("\nSeeding Zia concept map:")
        mapped, unmapped = seed_concept_map(db)

        seed_dev_user(db)
        db.commit()

        print(
            f"\nDone. {1 + placeholders} tracks, {domains} CCAO-F domains, "
            f"{questions} questions, {scenario_files} scenarios, {mapped} mapped concepts"
            + (f" ({unmapped} unmapped)" if unmapped else "")
            + ", 1 dev user."
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
