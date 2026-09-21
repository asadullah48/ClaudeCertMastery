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
    Domain,
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
