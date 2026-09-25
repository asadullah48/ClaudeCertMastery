"""Gate C1-CONTENT: the production Scenario Lab content loader must be idempotent
and must never silently corrupt or discard learner evidence.

Follows test_seed_safety.py's own pattern exactly: each test builds its own
throwaway SQLite engine and monkeypatches seed.py's module-level engine/SessionLocal,
so nothing here ever touches the database the app is actually configured against.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import seed  # noqa: E402
from app import models as _models  # noqa: E402,F401  registers metadata
from app.database import Base  # noqa: E402
from app.models import (  # noqa: E402
    AnswerOption,
    AttemptItem,
    AttemptMode,
    AttemptStatus,
    Domain,
    ExamAttempt,
    Question,
    QuestionType,
    Scenario,
    ScenarioAttempt,
    ScenarioStep,
    ScenarioStepOption,
    Track,
    User,
)


@pytest.fixture
def seed_db(tmp_path, monkeypatch):
    db_path = tmp_path / "scenario_seeding.db"
    test_engine = create_engine(
        f"sqlite:///{db_path}", connect_args={"check_same_thread": False}
    )
    TestSession = sessionmaker(bind=test_engine, autoflush=False, autocommit=False)
    Base.metadata.create_all(test_engine)
    monkeypatch.setattr(seed, "engine", test_engine)
    monkeypatch.setattr(seed, "SessionLocal", TestSession)
    yield TestSession
    test_engine.dispose()


def run_seed(monkeypatch, *args: str) -> int:
    monkeypatch.setattr(sys, "argv", ["seed.py", *args])
    return seed.main()


def make_track_and_domain(db, domain_code="WISD"):
    track = Track(
        code="CCAO-F", name="t", item_count=60, duration_minutes=120,
        pass_scaled_score=720, pass_raw_threshold=0.70, price_usd=99.0,
        validity_months=12, is_seeded=True,
    )
    db.add(track)
    db.flush()
    domain = Domain(track_id=track.id, code=domain_code, name="d", description="", weight_bps=1000, position=1)
    db.add(domain)
    db.flush()
    return track, domain


def base_spec(**overrides) -> dict:
    spec = {
        "external_id": "CCAO-F-WISD-SCN-TEST",
        "domain_code": "WISD",
        "title": "Test Scenario",
        "content_version": 1,
        "active": True,
        "difficulty": 2,
        "source_reference": "Originally authored for a unit test.",
        "setup_text": "A test situation unfolds.",
        "steps": [
            {
                "position": 1,
                "type": "mcq",
                "prompt": "What should happen?",
                "options": [
                    {"label": "A", "text": "Right thing.", "correct": True, "rationale": "Because it addresses the root cause directly."},
                    {"label": "B", "text": "Wrong thing.", "correct": False, "rationale": "Because it treats a symptom, not the cause.", "misconception_tag": "symptom_treatment"},
                ],
            }
        ],
    }
    spec.update(overrides)
    return spec


class TestUpsertScenarioLifecycle:
    """Cases A-E from the Gate C1-CONTENT report, exercised directly against
    upsert_scenario() with synthetic specs."""

    def test_a_scenario_does_not_exist_is_created(self, seed_db):
        with seed_db() as db:
            _, domain = make_track_and_domain(db)
            scenario, action = seed.upsert_scenario(db, domain, base_spec())
            db.commit()
            assert action == "created"
            assert scenario.content_version == 1
            assert len(scenario.steps) == 1
            assert len(scenario.steps[0].options) == 2

    def test_b_identical_reseed_is_a_true_noop(self, seed_db):
        with seed_db() as db:
            _, domain = make_track_and_domain(db)
            seed.upsert_scenario(db, domain, base_spec())
            db.commit()

        with seed_db() as db:
            scenario = db.scalar(select(Scenario))
            step_ids_before = sorted(o.id for s in scenario.steps for o in s.options)
            domain = db.scalar(select(Domain))
            scenario, action = seed.upsert_scenario(db, domain, base_spec())
            db.commit()
            assert action == "unchanged"
            assert scenario.content_version == 1

        with seed_db() as db:
            scenario = db.scalar(select(Scenario))
            assert len(db.scalars(select(Scenario)).all()) == 1  # no duplicate scenario
            assert len(scenario.steps) == 1  # no duplicate step
            step_ids_after = sorted(o.id for s in scenario.steps for o in s.options)
            assert step_ids_after == step_ids_before  # no duplicate/replaced options

    def test_c_content_drift_without_version_bump_is_refused(self, seed_db):
        with seed_db() as db:
            _, domain = make_track_and_domain(db)
            seed.upsert_scenario(db, domain, base_spec())
            db.commit()

        with seed_db() as db:
            domain = db.scalar(select(Domain))
            drifted = base_spec()
            drifted["steps"][0]["prompt"] = "A materially different question."
            with pytest.raises(ValueError, match="content_version was not bumped"):
                seed.upsert_scenario(db, domain, drifted)

    def test_d_version_bump_with_no_evidence_replaces_content(self, seed_db):
        with seed_db() as db:
            _, domain = make_track_and_domain(db)
            seed.upsert_scenario(db, domain, base_spec())
            db.commit()

        with seed_db() as db:
            domain = db.scalar(select(Domain))
            bumped = base_spec(content_version=2)
            bumped["steps"][0]["prompt"] = "A revised question."
            scenario, action = seed.upsert_scenario(db, domain, bumped)
            db.commit()
            assert action == "updated"
            assert scenario.content_version == 2
            assert scenario.steps[0].prompt_text == "A revised question."

    def test_d_version_bump_with_existing_evidence_is_refused_and_nondestructive(self, seed_db):
        with seed_db() as db:
            _, domain = make_track_and_domain(db)
            scenario, _ = seed.upsert_scenario(db, domain, base_spec())
            user = User(email="learner@example.com", display_name="Learner")
            db.add(user)
            db.flush()
            db.add(ScenarioAttempt(
                user_id=user.id, scenario_id=scenario.id, status="in_progress",
                scenario_content_version=scenario.content_version,
            ))
            db.commit()
            original_prompt = scenario.steps[0].prompt_text
            original_option_ids = sorted(o.id for o in scenario.steps[0].options)

        with seed_db() as db:
            domain = db.scalar(select(Domain))
            bumped = base_spec(content_version=2)
            bumped["steps"][0]["prompt"] = "A revised question that must not land."
            with pytest.raises(ValueError, match="existing learner evidence"):
                seed.upsert_scenario(db, domain, bumped)

        # Historical evidence and content are untouched by the refused attempt.
        with seed_db() as db:
            scenario = db.scalar(select(Scenario))
            attempt = db.scalar(select(ScenarioAttempt))
            assert scenario.content_version == 1
            assert scenario.steps[0].prompt_text == original_prompt
            assert sorted(o.id for o in scenario.steps[0].options) == original_option_ids
            assert attempt.scenario_content_version == 1  # snapshot preserved

    def test_e_version_may_never_move_backward(self, seed_db):
        with seed_db() as db:
            _, domain = make_track_and_domain(db)
            seed.upsert_scenario(db, domain, base_spec(content_version=2))
            db.commit()

        with seed_db() as db:
            domain = db.scalar(select(Domain))
            with pytest.raises(ValueError, match="must never move backward"):
                seed.upsert_scenario(db, domain, base_spec(content_version=1))


class TestValidateScenarioSpec:
    def test_valid_spec_passes(self):
        seed.validate_scenario_spec(base_spec(), {"WISD"})

    def test_invalid_domain_reference_fails_safely(self):
        with pytest.raises(ValueError, match="not a seeded domain"):
            seed.validate_scenario_spec(base_spec(domain_code="NOPE"), {"WISD"})

    def test_missing_source_reference_fails(self):
        spec = base_spec()
        spec["source_reference"] = ""
        with pytest.raises(ValueError, match="source_reference is required"):
            seed.validate_scenario_spec(spec, {"WISD"})

    def test_mcq_requires_exactly_one_correct_option(self):
        spec = base_spec()
        spec["steps"][0]["options"][1]["correct"] = True  # now both are correct
        with pytest.raises(ValueError, match="exactly 1 correct option"):
            seed.validate_scenario_spec(spec, {"WISD"})

    def test_incorrect_option_requires_misconception_tag(self):
        spec = base_spec()
        del spec["steps"][0]["options"][1]["misconception_tag"]
        with pytest.raises(ValueError, match="must carry a misconception_tag"):
            seed.validate_scenario_spec(spec, {"WISD"})

    def test_correct_option_must_not_carry_a_misconception_tag(self):
        spec = base_spec()
        spec["steps"][0]["options"][0]["misconception_tag"] = "should_not_be_here"
        with pytest.raises(ValueError, match="must not carry a misconception_tag"):
            seed.validate_scenario_spec(spec, {"WISD"})

    def test_misconception_tag_must_not_be_an_option_identifier(self):
        spec = base_spec()
        spec["steps"][0]["options"][1]["misconception_tag"] = "B"
        with pytest.raises(ValueError, match="looks like an option identifier"):
            seed.validate_scenario_spec(spec, {"WISD"})

    def test_rationale_must_not_merely_restate_correct_incorrect(self):
        spec = base_spec()
        spec["steps"][0]["options"][0]["rationale"] = "Correct."
        with pytest.raises(ValueError, match="must not merely restate"):
            seed.validate_scenario_spec(spec, {"WISD"})

    def test_step_positions_must_be_sequential_with_no_gaps(self):
        spec = base_spec()
        spec["steps"][0]["position"] = 2
        with pytest.raises(ValueError, match="1..N with no gaps"):
            seed.validate_scenario_spec(spec, {"WISD"})

    def test_content_version_must_be_a_positive_integer(self):
        spec = base_spec(content_version=0)
        with pytest.raises(ValueError, match="positive integer"):
            seed.validate_scenario_spec(spec, {"WISD"})

    def test_needs_at_least_two_options(self):
        spec = base_spec()
        spec["steps"][0]["options"] = spec["steps"][0]["options"][:1]
        with pytest.raises(ValueError, match="at least 2 options"):
            seed.validate_scenario_spec(spec, {"WISD"})


class TestRealProductionScenarioContent:
    """The 7 authored production scenarios (Gate C1-CONTENT), loaded through the
    real seed pipeline exactly as production would run it."""

    def test_all_seven_domains_get_one_scenario_each(self, seed_db, monkeypatch):
        run_seed(monkeypatch)
        with seed_db() as db:
            scenarios = db.scalars(select(Scenario)).all()
            assert len(scenarios) == 7
            domains = {db.get(Domain, s.domain_id).code for s in scenarios}
            assert domains == {"PTE", "OEV", "PMS", "WISD", "CKM", "GRR", "TRO"}

    def test_reseeding_the_real_content_is_fully_idempotent(self, seed_db, monkeypatch):
        run_seed(monkeypatch)
        with seed_db() as db:
            before = sorted(
                (s.external_id, s.content_version, len(s.steps))
                for s in db.scalars(select(Scenario)).all()
            )
            option_ids_before = sorted(
                o.id
                for s in db.scalars(select(Scenario)).all()
                for step in s.steps
                for o in step.options
            )

        run_seed(monkeypatch)
        with seed_db() as db:
            after = sorted(
                (s.external_id, s.content_version, len(s.steps))
                for s in db.scalars(select(Scenario)).all()
            )
            option_ids_after = sorted(
                o.id
                for s in db.scalars(select(Scenario)).all()
                for step in s.steps
                for o in step.options
            )
        assert after == before
        assert option_ids_after == option_ids_before

    def test_every_authored_scenario_passes_structural_validation(self):
        import yaml

        domain_codes = {"PTE", "OEV", "PMS", "WISD", "CKM", "GRR", "TRO"}
        scenario_dir = ROOT / "seed_data" / "ccao_f_scenarios"
        files = sorted(scenario_dir.glob("*.yaml"))
        assert len(files) == 7
        for path in files:
            spec = yaml.safe_load(path.read_text(encoding="utf-8"))["scenario"]
            seed.validate_scenario_spec(spec, domain_codes)  # raises on any violation

    def test_every_authored_scenario_has_a_non_option_identifier_misconception_tag(self):
        import yaml

        scenario_dir = ROOT / "seed_data" / "ccao_f_scenarios"
        for path in sorted(scenario_dir.glob("*.yaml")):
            spec = yaml.safe_load(path.read_text(encoding="utf-8"))["scenario"]
            for step in spec["steps"]:
                for opt in step["options"]:
                    if not opt.get("correct"):
                        tag = opt["misconception_tag"]
                        assert tag.lower() != opt["label"].lower()
                        assert "_" in tag or len(tag) > 8  # a real conceptual slug


ALL_CCAO_F_DOMAIN_CODES = ["PTE", "OEV", "PMS", "WISD", "CKM", "GRR", "TRO"]


def make_full_ccao_f(db) -> Track:
    """A Track with all seven real CCAO-F domains, so the real seed_data YAMLs
    (which reference every one of those codes) can be loaded without a
    'not a seeded domain' validation failure."""
    track = Track(
        code="CCAO-F", name="t", item_count=60, duration_minutes=120,
        pass_scaled_score=720, pass_raw_threshold=0.70, price_usd=99.0,
        validity_months=12, is_seeded=True,
    )
    db.add(track)
    db.flush()
    for position, code in enumerate(ALL_CCAO_F_DOMAIN_CODES, start=1):
        db.add(Domain(
            track_id=track.id, code=code, name=code, description="",
            weight_bps=1428, position=position,
        ))
    db.commit()
    return track


def make_exam_history(db, track: Track) -> None:
    """One real exam sitting: a Question with two AnswerOptions, a User, an
    ExamAttempt, and its AttemptItem -- the exact shape check_no_production_attempts()
    exists to protect."""
    domain = db.scalar(
        select(Domain).where(Domain.track_id == track.id, Domain.code == "WISD")
    )
    question = Question(
        domain_id=domain.id, external_id="CCAO-F-WISD-001", stem="A real exam question.",
        question_type=QuestionType.MCQ, difficulty=2, static_explanation="Because reasons.",
        is_active=True,
    )
    db.add(question)
    db.flush()
    opt_a = AnswerOption(question_id=question.id, label="A", text="Right", is_correct=True, position=1)
    opt_b = AnswerOption(question_id=question.id, label="B", text="Wrong", is_correct=False, position=2)
    db.add_all([opt_a, opt_b])
    db.flush()

    user = User(email="candidate@example.com", display_name="Candidate")
    db.add(user)
    db.flush()

    attempt = ExamAttempt(
        user_id=user.id, track_id=track.id, mode=AttemptMode.EXAM,
        status=AttemptStatus.SUBMITTED, seed=42,
        raw_correct=1, raw_total=1, scaled_score=1000, passed=True,
    )
    db.add(attempt)
    db.flush()

    db.add(AttemptItem(
        attempt_id=attempt.id, question_id=question.id, domain_id=domain.id,
        position=1, selected_option_ids=[opt_a.id], is_correct=True,
    ))
    db.commit()


def snapshot_table(db, model) -> list[tuple]:
    """Every row of `model` as a sorted tuple of every column's value -- a
    logical (not just row-count) before/after equality check."""
    cols = [c.name for c in model.__table__.columns]
    rows = db.scalars(select(model)).all()
    return sorted(tuple(getattr(row, c) for c in cols) for row in rows)


HISTORY_MODELS = (User, Track, Domain, Question, AnswerOption, ExamAttempt, AttemptItem)


def snapshot_history(db) -> dict[str, list[tuple]]:
    return {model.__name__: snapshot_table(db, model) for model in HISTORY_MODELS}


class TestScenariosOnlyMode:
    """Gate C1-SCENARIO-ONLY-SEEDER: `python seed.py --scenarios-only` must load only
    Scenario Lab content -- even with historical ExamAttempt/AttemptItem evidence
    already present -- without mutating any table the foundational seed path owns."""

    def test_succeeds_with_historical_exam_evidence_and_leaves_it_unchanged(
        self, seed_db, monkeypatch
    ):
        with seed_db() as db:
            track = make_full_ccao_f(db)
            make_exam_history(db, track)

        with seed_db() as db:
            before = snapshot_history(db)

        code = run_seed(monkeypatch, "--scenarios-only")
        assert code == 0

        with seed_db() as db:
            after = snapshot_history(db)
            assert len(db.scalars(select(Scenario)).all()) == 7

        for name, rows in before.items():
            assert after[name] == rows, f"{name} rows changed by --scenarios-only"

    def test_does_not_modify_unrelated_tables(self, seed_db, monkeypatch):
        with seed_db() as db:
            track = make_full_ccao_f(db)
            make_exam_history(db, track)
            before = {
                model.__name__: snapshot_table(db, model)
                for model in (User, Track, Domain, Question, AnswerOption, ExamAttempt, AttemptItem)
            }

        run_seed(monkeypatch, "--scenarios-only")

        with seed_db() as db:
            for name, rows in before.items():
                model = next(m for m in HISTORY_MODELS if m.__name__ == name)
                assert snapshot_table(db, model) == rows

    def test_loads_all_seven_real_scenarios(self, seed_db, monkeypatch):
        with seed_db() as db:
            make_full_ccao_f(db)

        code = run_seed(monkeypatch, "--scenarios-only")
        assert code == 0

        with seed_db() as db:
            scenarios = db.scalars(select(Scenario)).all()
            assert len(scenarios) == 7
            domains = {db.get(Domain, s.domain_id).code for s in scenarios}
            assert domains == set(ALL_CCAO_F_DOMAIN_CODES)

    def test_is_idempotent_and_repeated_run_still_preserves_history(self, seed_db, monkeypatch):
        with seed_db() as db:
            track = make_full_ccao_f(db)
            make_exam_history(db, track)

        run_seed(monkeypatch, "--scenarios-only")
        with seed_db() as db:
            history_first = snapshot_history(db)
            scenarios = db.scalars(select(Scenario)).all()
            option_ids_first = sorted(
                o.id for s in scenarios for step in s.steps for o in step.options
            )

        run_seed(monkeypatch, "--scenarios-only")
        with seed_db() as db:
            scenarios = db.scalars(select(Scenario)).all()
            assert len(scenarios) == 7  # no duplicates
            option_ids_second = sorted(
                o.id for s in scenarios for step in s.steps for o in step.options
            )
            history_second = snapshot_history(db)

        assert option_ids_second == option_ids_first  # stable IDs, no wholesale replace
        for name, rows in history_first.items():
            assert history_second[name] == rows


class TestScenariosOnlyControlFlow:
    """Proves --scenarios-only's call boundaries precisely, at the seed.main()
    control-flow level -- not just its net DB effect -- using spies on the real
    module-level functions (each spy still calls through to the original)."""

    def _spy(self, monkeypatch, name: str) -> list:
        calls: list = []
        original = getattr(seed, name)

        def wrapper(*args, **kwargs):
            calls.append((args, kwargs))
            return original(*args, **kwargs)

        monkeypatch.setattr(seed, name, wrapper)
        return calls

    def test_scenarios_only_reaches_scenario_loading_despite_exam_history(
        self, seed_db, monkeypatch
    ):
        with seed_db() as db:
            track = make_full_ccao_f(db)
            make_exam_history(db, track)

        scenario_calls = self._spy(monkeypatch, "seed_ccao_f_scenarios")
        guard_calls = self._spy(monkeypatch, "check_no_production_attempts")
        foundational_calls = self._spy(monkeypatch, "seed_ccao_f")
        placeholder_calls = self._spy(monkeypatch, "seed_placeholder_tracks")
        concept_calls = self._spy(monkeypatch, "seed_concept_map")
        dev_user_calls = self._spy(monkeypatch, "seed_dev_user")

        code = run_seed(monkeypatch, "--scenarios-only")

        assert code == 0
        assert len(scenario_calls) == 1
        assert guard_calls == []
        assert foundational_calls == []
        assert placeholder_calls == []
        assert concept_calls == []
        assert dev_user_calls == []

    def test_plain_seed_still_refuses_with_exam_attempt_history(self, seed_db, monkeypatch):
        with seed_db() as db:
            track, _domain = make_track_and_domain(db)
            user = User(email="y@example.com", display_name="Y")
            db.add(user)
            db.flush()
            db.add(ExamAttempt(
                user_id=user.id, track_id=track.id, mode=AttemptMode.EXAM,
                status=AttemptStatus.SUBMITTED, seed=1,
                raw_correct=1, raw_total=1, scaled_score=1000, passed=True,
            ))
            db.commit()

        scenario_calls = self._spy(monkeypatch, "seed_ccao_f_scenarios")
        foundational_calls = self._spy(monkeypatch, "seed_ccao_f")

        with pytest.raises(SystemExit) as exc:
            run_seed(monkeypatch)
        assert exc.value.code == 1
        assert scenario_calls == []
        assert foundational_calls == []

    def test_plain_seed_still_refuses_with_attempt_item_history_alone(self, seed_db, monkeypatch):
        # The guard checks ExamAttempt and AttemptItem independently and
        # unconditionally -- prove AttemptItem alone is sufficient to refuse, even
        # in this synthetic case with no matching ExamAttempt row.
        with seed_db() as db:
            _track, domain = make_track_and_domain(db)
            question = Question(
                domain_id=domain.id, external_id="CCAO-F-WISD-002", stem="Q",
                question_type=QuestionType.MCQ, difficulty=2, static_explanation="Because.",
                is_active=True,
            )
            db.add(question)
            db.flush()
            db.add(AttemptItem(
                attempt_id=999999, question_id=question.id, domain_id=domain.id,
                position=1, selected_option_ids=[], is_correct=None,
            ))
            db.commit()

        scenario_calls = self._spy(monkeypatch, "seed_ccao_f_scenarios")

        with pytest.raises(SystemExit) as exc:
            run_seed(monkeypatch)
        assert exc.value.code == 1
        assert scenario_calls == []

    def test_scenarios_only_combined_with_reset_fails_closed(self, seed_db, monkeypatch):
        with seed_db() as db:
            make_track_and_domain(db)
            db.commit()

        with seed_db() as db:
            before_codes = sorted(t.code for t in db.scalars(select(Track)).all())
            assert before_codes == ["CCAO-F"]  # fixture actually persisted

        scenario_calls = self._spy(monkeypatch, "seed_ccao_f_scenarios")

        with pytest.raises(SystemExit) as exc:
            run_seed(monkeypatch, "--scenarios-only", "--reset")
        assert exc.value.code == 1
        assert scenario_calls == []  # rejected before any mutation, including the drop

        with seed_db() as db:
            after_codes = sorted(t.code for t in db.scalars(select(Track)).all())
        assert after_codes == before_codes  # --reset's drop never ran either

    def test_scenarios_only_does_not_fall_through_to_normal_seeding(self, seed_db, monkeypatch):
        with seed_db() as db:
            make_full_ccao_f(db)

        foundational_calls = self._spy(monkeypatch, "seed_ccao_f")
        placeholder_calls = self._spy(monkeypatch, "seed_placeholder_tracks")
        concept_calls = self._spy(monkeypatch, "seed_concept_map")
        dev_user_calls = self._spy(monkeypatch, "seed_dev_user")

        code = run_seed(monkeypatch, "--scenarios-only")

        assert code == 0
        assert foundational_calls == []
        assert placeholder_calls == []
        assert concept_calls == []
        assert dev_user_calls == []

    def test_scenarios_only_path_makes_no_ai_provider_calls(self):
        # Scoped to exactly the call chain --scenarios-only exercises
        # (seed_ccao_f_scenarios -> upsert_scenario -> its helpers), not the whole
        # module -- seed_concept_map()'s unrelated docstring legitimately mentions
        # "Zia"/"MCP" in prose to explain that *it* doesn't call them either, which
        # would be a false positive for a whole-file substring scan.
        import inspect

        functions = (
            seed.seed_ccao_f_scenarios,
            seed.upsert_scenario,
            seed._write_scenario_steps,
            seed.validate_scenario_spec,
            seed._scenario_has_evidence,
            seed._scenario_spec_signature,
            seed._scenario_db_signature,
        )
        source = "\n".join(inspect.getsource(fn) for fn in functions)
        for forbidden in ("anthropic", "zia", "mcp", "requests.", "httpx."):
            assert forbidden not in source.lower(), (
                f"the --scenarios-only call chain must not reference {forbidden!r} -- "
                "Scenario Lab content loading must work fully offline, with no "
                "model-provider or MCP dependency."
            )


# --- Gate C3-C2: Scenario 001 answer-position repair ---------------------------------

import hashlib  # noqa: E402
import json  # noqa: E402

import yaml  # noqa: E402

SCENARIO_DIR = ROOT / "seed_data" / "ccao_f_scenarios"

# Correct-answer label per step after the C3-C2 repair. PTE-001 keeps A/A because
# founder evidence exists against it and it must stay byte-for-byte unchanged.
EXPECTED_CORRECT_LABELS = {
    "CCAO-F-PTE-SCN-001": ["A", "A"],
    "CCAO-F-OEV-SCN-001": ["B", "A"],
    "CCAO-F-PMS-SCN-001": ["C", "B"],
    "CCAO-F-WISD-SCN-001": ["A", "B"],
    "CCAO-F-CKM-SCN-001": ["C", "A"],
    "CCAO-F-GRR-SCN-001": ["B", "A"],
    "CCAO-F-TRO-SCN-001": ["C", "B"],
}

# Per-step digest of the UNORDERED (text, correct, rationale, misconception_tag)
# tuples, pinned from the content_version 1 files (identical to what production
# holds). Matching after the reorder proves only order moved: every rationale and
# misconception tag is still attached to the same option text, correctness included.
V1_SEMANTIC_STEP_DIGESTS = {
    "CCAO-F-PTE-SCN-001": ["386857ef1f267544", "217a585a0e50f83d"],
    "CCAO-F-OEV-SCN-001": ["9856c6c7705cd1b5", "3c14232d3e056399"],
    "CCAO-F-PMS-SCN-001": ["be6ef5ff165290f9", "2cbe404b1ff416bd"],
    "CCAO-F-WISD-SCN-001": ["ad89aa468eff423f", "f54d00dab88f3025"],
    "CCAO-F-CKM-SCN-001": ["295fc39b08c6eeed", "0f9e86c12568480e"],
    "CCAO-F-GRR-SCN-001": ["4bc2f28b2ef2c9b0", "44eab2f925dec795"],
    "CCAO-F-TRO-SCN-001": ["01065a60681babe6", "d4a5998a4232b6da"],
}

# Digest of each scenario's FULL ORDERED v1 spec. PTE's current file must still
# match exactly; for the other six, reconstructing v1 (correct option back to
# first, relabel, version 1) must reproduce it, proving the repair is a pure reorder.
V1_ORDERED_DIGESTS = {
    "CCAO-F-PTE-SCN-001": "5413e9d12cc7abe3",
    "CCAO-F-OEV-SCN-001": "3a2d8cb802efbc2f",
    "CCAO-F-PMS-SCN-001": "9a2478f7c48cedad",
    "CCAO-F-WISD-SCN-001": "627f6021027f36cc",
    "CCAO-F-CKM-SCN-001": "4a8bfb4183692027",
    "CCAO-F-GRR-SCN-001": "98f6cb444d7d1612",
    "CCAO-F-TRO-SCN-001": "68c5206aec9a954f",
}

REPAIRED = [k for k in EXPECTED_CORRECT_LABELS if k != "CCAO-F-PTE-SCN-001"]


def _authored_specs() -> dict[str, dict]:
    specs = {}
    for path in sorted(SCENARIO_DIR.glob("*.yaml")):
        spec = yaml.safe_load(path.read_text(encoding="utf-8"))["scenario"]
        specs[spec["external_id"]] = spec
    return specs


def _step_digest(step: dict) -> str:
    rows = sorted(
        [o["text"].strip(), bool(o.get("correct")), o["rationale"].strip(),
         o.get("misconception_tag") or ""]
        for o in step["options"]
    )
    return hashlib.sha256(json.dumps(rows).encode()).hexdigest()[:16]


def _ordered_digest(spec: dict) -> str:
    return hashlib.sha256(json.dumps(spec, sort_keys=True).encode()).hexdigest()[:16]


def _reconstruct_v1(spec: dict) -> dict:
    """The pre-repair shape: correct option first, distractors in their existing
    relative order, labels re-lettered, content_version 1."""
    v1 = json.loads(json.dumps(spec))
    v1["content_version"] = 1
    for step in v1["steps"]:
        correct = [o for o in step["options"] if o.get("correct")]
        rest = [o for o in step["options"] if not o.get("correct")]
        step["options"] = correct + rest
        for label, opt in zip("ABCD", step["options"]):
            opt["label"] = label
    return v1


def _correct_labels(spec: dict) -> list[str]:
    return [
        next(o["label"] for o in step["options"] if o.get("correct"))
        for step in sorted(spec["steps"], key=lambda s: s["position"])
    ]


class TestScenario001AnswerPositionRepair:
    def test_all_seven_scenario_001_files_load_and_validate(self):
        specs = _authored_specs()
        assert set(specs) == set(EXPECTED_CORRECT_LABELS)
        for spec in specs.values():
            seed.validate_scenario_spec(spec, {"PTE", "OEV", "PMS", "WISD", "CKM", "GRR", "TRO"})

    def test_correct_positions_match_the_intended_distribution(self):
        specs = _authored_specs()
        assert {k: _correct_labels(s) for k, s in specs.items()} == EXPECTED_CORRECT_LABELS

    def test_repaired_scenarios_are_not_positionally_predictable(self):
        specs = _authored_specs()
        repaired = [_correct_labels(specs[k]) for k in REPAIRED]
        flat = [label for labels in repaired for label in labels]
        assert set(flat) >= {"A", "B", "C"}  # not all A; every available slot used
        for labels in repaired:
            assert len(set(labels)) == len(labels), labels  # never same slot every step

    def test_semantics_rationales_and_tags_survive_reordering(self):
        specs = _authored_specs()
        for ext_id, digests in V1_SEMANTIC_STEP_DIGESTS.items():
            steps = sorted(specs[ext_id]["steps"], key=lambda s: s["position"])
            assert [_step_digest(s) for s in steps] == digests, ext_id

    def test_labels_are_sequential_in_display_order(self):
        for spec in _authored_specs().values():
            for step in spec["steps"]:
                labels = [o["label"] for o in step["options"]]
                assert labels == list("ABCD"[: len(labels)])

    def test_pte_001_is_unchanged(self):
        spec = _authored_specs()["CCAO-F-PTE-SCN-001"]
        assert spec["content_version"] == 1
        assert _ordered_digest(spec) == V1_ORDERED_DIGESTS["CCAO-F-PTE-SCN-001"]

    def test_repaired_scenarios_are_a_pure_reorder_at_content_version_2(self):
        specs = _authored_specs()
        for ext_id in REPAIRED:
            assert specs[ext_id]["content_version"] == 2, ext_id
            assert _ordered_digest(_reconstruct_v1(specs[ext_id])) == V1_ORDERED_DIGESTS[ext_id], ext_id


def _seed_v1_world(seed_db, monkeypatch):
    """A database holding exactly production's current scenario content: the v1
    shape of all seven Scenario 001s (PTE's file is already v1)."""
    run_seed(monkeypatch)  # track, domains, questions -- and the current scenarios
    with seed_db() as db:
        for scenario in db.scalars(select(Scenario)).all():
            db.delete(scenario)
        db.commit()
        domains = {d.code: d for d in db.scalars(select(Domain)).all()}
        for spec in _authored_specs().values():
            v1 = spec if spec["content_version"] == 1 else _reconstruct_v1(spec)
            seed.upsert_scenario(db, domains[spec["domain_code"]], v1)
        db.commit()
        return {s.external_id: s.id for s in db.scalars(select(Scenario)).all()}


def _option_snapshot(db) -> list[tuple]:
    return sorted(
        (s.external_id, s.content_version, step.position, o.id, o.label, o.is_correct,
         o.misconception_tag)
        for s in db.scalars(select(Scenario)).all()
        for step in s.steps
        for o in step.options
    )


class TestScenario001RepairSeeding:
    def test_scenarios_only_seed_upgrades_the_six_in_place_and_leaves_pte(self, seed_db, monkeypatch):
        ids_before = _seed_v1_world(seed_db, monkeypatch)
        with seed_db() as db:
            pte_before = [row for row in _option_snapshot(db) if row[0] == "CCAO-F-PTE-SCN-001"]

        assert run_seed(monkeypatch, "--scenarios-only") == 0

        with seed_db() as db:
            scenarios = {s.external_id: s for s in db.scalars(select(Scenario)).all()}
            assert {k: s.id for k, s in scenarios.items()} == ids_before  # same Scenario rows
            assert scenarios["CCAO-F-PTE-SCN-001"].content_version == 1
            for ext_id in REPAIRED:
                assert scenarios[ext_id].content_version == 2
            for ext_id, labels in EXPECTED_CORRECT_LABELS.items():
                got = [
                    next(o.label for o in step.options if o.is_correct)
                    for step in sorted(scenarios[ext_id].steps, key=lambda s: s.position)
                ]
                assert got == labels, ext_id
            pte_after = [row for row in _option_snapshot(db) if row[0] == "CCAO-F-PTE-SCN-001"]
            assert pte_after == pte_before  # PTE options not even re-created

    def test_scenarios_only_seed_is_idempotent_after_the_repair(self, seed_db, monkeypatch):
        _seed_v1_world(seed_db, monkeypatch)
        run_seed(monkeypatch, "--scenarios-only")
        with seed_db() as db:
            before = _option_snapshot(db)
            _, actions = seed.seed_ccao_f_scenarios(db)
            db.commit()
            assert set(actions.values()) == {"unchanged"}
            assert _option_snapshot(db) == before

    def test_scenarios_only_seed_never_touches_exam_answer_options(self, seed_db, monkeypatch):
        _seed_v1_world(seed_db, monkeypatch)
        with seed_db() as db:
            answer_options_before = sorted(
                (o.id, o.question_id, o.label, o.is_correct)
                for o in db.scalars(select(AnswerOption)).all()
            )
        run_seed(monkeypatch, "--scenarios-only")
        with seed_db() as db:
            answer_options_after = sorted(
                (o.id, o.question_id, o.label, o.is_correct)
                for o in db.scalars(select(AnswerOption)).all()
            )
        assert answer_options_after == answer_options_before

    def test_an_attempt_on_a_repaired_scenario_blocks_the_whole_run(self, seed_db, monkeypatch):
        """If production gains an attempt on any of the six before the repair is
        activated, the guard refuses the bump and nothing is written -- including the
        scenarios processed earlier in the same run (the loader commits once, after
        every file)."""
        _seed_v1_world(seed_db, monkeypatch)
        with seed_db() as db:
            user = User(email="founder@example.com", display_name="Founder")
            db.add(user)
            db.flush()
            tro = db.scalar(select(Scenario).where(Scenario.external_id == "CCAO-F-TRO-SCN-001"))
            db.add(ScenarioAttempt(user_id=user.id, scenario_id=tro.id, status="in_progress",
                                   scenario_content_version=1))
            db.commit()
            before = _option_snapshot(db)

        with pytest.raises(ValueError, match="CCAO-F-TRO-SCN-001"):
            run_seed(monkeypatch, "--scenarios-only")

        with seed_db() as db:
            assert _option_snapshot(db) == before
            assert {s.content_version for s in db.scalars(select(Scenario)).all()} == {1}
