"""Scenario Lab HTTP API (Gate C1 Slice 2): safe payloads, server-authoritative
evaluation, ownership, idempotency, and the full attempt lifecycle end to end.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


@pytest.fixture
def scenario_env(tmp_path):
    """A TestClient with one minimal, hand-built two-step scenario -- not via
    seed.py (Slice 2 does not touch seed data), so this is fully isolated.
    """
    db_path = tmp_path / "scenario_api.db"
    engine = create_engine(
        f"sqlite:///{db_path}", connect_args={"check_same_thread": False}
    )
    TestingSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    from app import models as _models  # noqa: F401
    from app.database import Base, get_db
    from app.main import app
    from app.models import (
        Domain, Scenario, ScenarioStep, ScenarioStepHint, ScenarioStepOption,
        Track, User,
    )

    Base.metadata.create_all(engine)
    with TestingSession() as db:
        track = Track(code="CCAO-F", name="AI Operator, Foundation")
        db.add(track)
        db.flush()
        domain = Domain(track_id=track.id, code="WISD", name="Workflow", weight_bps=1600, position=1)
        db.add(domain)
        db.flush()
        user = User(email="dev@certmastery.local", display_name="Dev")
        db.add(user)

        scenario = Scenario(
            domain_id=domain.id, external_id="CCAO-F-WISD-SCN-001",
            title="The Silent Handoff", setup_text="An operator wired Claude into a ticketing system.",
            difficulty=2, is_active=True, content_version=1,
        )
        db.add(scenario)
        db.flush()

        step1 = ScenarioStep(scenario_id=scenario.id, position=1, prompt_text="What now?", step_type="mcq")
        db.add(step1)
        db.flush()
        opt_a = ScenarioStepOption(step_id=step1.id, label="A", text="Route to review.", is_correct=True, position=1, rationale="Targets the blind spot.")
        opt_b = ScenarioStepOption(step_id=step1.id, label="B", text="Send automatically.", is_correct=False, position=2, rationale="Confidence is not grounding.", misconception_tag="confidence_as_correctness")
        opt_c = ScenarioStepOption(step_id=step1.id, label="C", text="Shut it off.", is_correct=False, position=3, rationale="Disproportionate response.", misconception_tag="overcorrection_discards_working_system")
        db.add_all([opt_a, opt_b, opt_c])
        db.add(ScenarioStepHint(step_id=step1.id, position=1, text="Consider what changed.", penalty_bps=1000))
        db.add(ScenarioStepHint(step_id=step1.id, position=2, text="Confidence isn't grounding.", penalty_bps=2000))

        step2 = ScenarioStep(scenario_id=scenario.id, position=2, prompt_text="Three months later...", step_type="mcq")
        db.add(step2)
        db.flush()
        opt_x = ScenarioStepOption(step_id=step2.id, label="A", text="Still always route to review.", is_correct=False, position=1, rationale="Premise changed.", misconception_tag="ignores_failure_isolation")
        opt_y = ScenarioStepOption(step_id=step2.id, label="B", text="Route only when confidence is low.", is_correct=True, position=2, rationale="Matches rigor to freshness.")
        db.add_all([opt_x, opt_y])

        db.commit()
        ids = {
            "scenario_id": scenario.id, "step1_id": step1.id, "step2_id": step2.id,
            "opt_a": opt_a.id, "opt_b": opt_b.id, "opt_c": opt_c.id,
            "opt_x": opt_x.id, "opt_y": opt_y.id, "user_id": user.id,
        }

    def override_get_db():
        db = TestingSession()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as client:
        yield client, ids
    app.dependency_overrides.clear()


# --- Safe start payload --------------------------------------------------------------

class TestSafeStartPayload:
    def test_start_returns_201_with_current_step(self, scenario_env):
        client, ids = scenario_env
        r = client.post("/scenarios/CCAO-F-WISD-SCN-001/start")
        assert r.status_code == 201
        body = r.json()
        assert body["status"] == "in_progress"
        assert body["current_step"]["position"] == 1
        assert body["current_step"]["total_steps"] == 2

    def test_start_payload_contains_no_grading_metadata(self, scenario_env):
        client, ids = scenario_env
        body = client.post("/scenarios/CCAO-F-WISD-SCN-001/start").json()
        raw = str(body).lower()
        for leak in ("is_correct", "misconception", "rationale", "penalty_bps"):
            assert leak not in raw

    def test_start_options_carry_no_position_based_correctness_signal(self, scenario_env):
        client, ids = scenario_env
        body = client.post("/scenarios/CCAO-F-WISD-SCN-001/start").json()
        options = body["current_step"]["options"]
        # Exactly the fields AnswerOptionOut's own precedent allows -- nothing else.
        for o in options:
            assert set(o.keys()) == {"id", "label", "text", "position"}

    def test_hints_available_is_a_count_not_hint_text(self, scenario_env):
        client, ids = scenario_env
        body = client.post("/scenarios/CCAO-F-WISD-SCN-001/start").json()
        assert body["current_step"]["hints_available"] == 2

    def test_unknown_scenario_is_404(self, scenario_env):
        client, ids = scenario_env
        assert client.post("/scenarios/NOPE/start").status_code == 404


# --- Correct / incorrect decisions ----------------------------------------------------

class TestDecisionEvaluation:
    def test_correct_decision_is_evaluated_server_side(self, scenario_env):
        client, ids = scenario_env
        attempt_id = client.post("/scenarios/CCAO-F-WISD-SCN-001/start").json()["attempt_id"]
        r = client.post(
            f"/scenario-attempts/{attempt_id}/steps/1/answer",
            json={"selected_option_ids": [ids["opt_a"]]},
        )
        assert r.status_code == 200
        body = r.json()
        assert body["is_correct"] is True
        assert body["step_credit"] == 1.0
        assert body["feedback"][0]["misconception_tag"] is None

    def test_incorrect_decision_carries_misconception_evidence(self, scenario_env):
        client, ids = scenario_env
        attempt_id = client.post("/scenarios/CCAO-F-WISD-SCN-001/start").json()["attempt_id"]
        r = client.post(
            f"/scenario-attempts/{attempt_id}/steps/1/answer",
            json={"selected_option_ids": [ids["opt_b"]]},
        )
        body = r.json()
        assert body["is_correct"] is False
        assert body["feedback"][0]["misconception_tag"] == "confidence_as_correctness"

    def test_two_distinct_wrong_options_yield_distinct_misconceptions(self, scenario_env):
        client, ids = scenario_env
        a1 = client.post("/scenarios/CCAO-F-WISD-SCN-001/start").json()["attempt_id"]
        r1 = client.post(f"/scenario-attempts/{a1}/steps/1/answer", json={"selected_option_ids": [ids["opt_b"]]})
        a2 = client.post("/scenarios/CCAO-F-WISD-SCN-001/start").json()["attempt_id"]
        r2 = client.post(f"/scenario-attempts/{a2}/steps/1/answer", json={"selected_option_ids": [ids["opt_c"]]})
        assert r1.json()["feedback"][0]["misconception_tag"] != r2.json()["feedback"][0]["misconception_tag"]

    def test_feedback_contains_only_selected_options_not_the_full_answer_key(self, scenario_env):
        client, ids = scenario_env
        attempt_id = client.post("/scenarios/CCAO-F-WISD-SCN-001/start").json()["attempt_id"]
        body = client.post(
            f"/scenario-attempts/{attempt_id}/steps/1/answer",
            json={"selected_option_ids": [ids["opt_b"]]},
        ).json()
        assert len(body["feedback"]) == 1
        assert body["feedback"][0]["option_id"] == ids["opt_b"]
        # correct_option_ids is bare IDs -- no rationale text for the unselected
        # correct option anywhere in the response.
        assert body["correct_option_ids"] == [ids["opt_a"]]
        assert "Targets the blind spot" not in str(body)


class TestClientCannotForgeCorrectness:
    def test_spoofed_extra_fields_are_ignored(self, scenario_env):
        client, ids = scenario_env
        attempt_id = client.post("/scenarios/CCAO-F-WISD-SCN-001/start").json()["attempt_id"]
        r = client.post(
            f"/scenario-attempts/{attempt_id}/steps/1/answer",
            json={
                "selected_option_ids": [ids["opt_b"]],
                "is_correct": True, "step_credit": 1.0,  # not part of the request schema
            },
        )
        assert r.status_code == 200
        assert r.json()["is_correct"] is False  # server truth wins regardless


class TestCrossStepAndFutureStep:
    def test_option_from_another_step_is_rejected(self, scenario_env):
        client, ids = scenario_env
        attempt_id = client.post("/scenarios/CCAO-F-WISD-SCN-001/start").json()["attempt_id"]
        r = client.post(
            f"/scenario-attempts/{attempt_id}/steps/1/answer",
            json={"selected_option_ids": [ids["opt_x"]]},  # belongs to step 2
        )
        assert r.status_code == 422

    def test_future_step_submission_is_rejected(self, scenario_env):
        client, ids = scenario_env
        attempt_id = client.post("/scenarios/CCAO-F-WISD-SCN-001/start").json()["attempt_id"]
        r = client.post(
            f"/scenario-attempts/{attempt_id}/steps/2/answer",  # step 1 not answered yet
            json={"selected_option_ids": [ids["opt_x"]]},
        )
        assert r.status_code == 409

    def test_nonexistent_step_is_404(self, scenario_env):
        client, ids = scenario_env
        attempt_id = client.post("/scenarios/CCAO-F-WISD-SCN-001/start").json()["attempt_id"]
        r = client.post(
            f"/scenario-attempts/{attempt_id}/steps/99/answer",
            json={"selected_option_ids": []},
        )
        assert r.status_code == 404


class TestDuplicateSubmission:
    def test_identical_replay_returns_same_result_without_duplicating_evidence(self, scenario_env):
        client, ids = scenario_env
        attempt_id = client.post("/scenarios/CCAO-F-WISD-SCN-001/start").json()["attempt_id"]
        first = client.post(f"/scenario-attempts/{attempt_id}/steps/1/answer", json={"selected_option_ids": [ids["opt_a"]]})
        second = client.post(f"/scenario-attempts/{attempt_id}/steps/1/answer", json={"selected_option_ids": [ids["opt_a"]]})
        assert first.status_code == second.status_code == 200
        assert first.json() == second.json()

    def test_different_selection_on_an_answered_step_is_rejected(self, scenario_env):
        client, ids = scenario_env
        attempt_id = client.post("/scenarios/CCAO-F-WISD-SCN-001/start").json()["attempt_id"]
        client.post(f"/scenario-attempts/{attempt_id}/steps/1/answer", json={"selected_option_ids": [ids["opt_a"]]})
        r = client.post(f"/scenario-attempts/{attempt_id}/steps/1/answer", json={"selected_option_ids": [ids["opt_b"]]})
        assert r.status_code == 409


class TestCompletion:
    def test_full_lifecycle_completes_on_final_step(self, scenario_env):
        client, ids = scenario_env
        attempt_id = client.post("/scenarios/CCAO-F-WISD-SCN-001/start").json()["attempt_id"]
        step1 = client.post(f"/scenario-attempts/{attempt_id}/steps/1/answer", json={"selected_option_ids": [ids["opt_a"]]}).json()
        assert step1["attempt_status"] == "in_progress"
        assert step1["next_step"]["position"] == 2
        assert step1["result"] is None

        step2 = client.post(f"/scenario-attempts/{attempt_id}/steps/2/answer", json={"selected_option_ids": [ids["opt_y"]]}).json()
        assert step2["attempt_status"] == "submitted"
        assert step2["next_step"] is None
        assert step2["result"] == {"score_pct": 100, "mastery_band": "strong"}

    def test_completed_attempt_cannot_continue(self, scenario_env):
        client, ids = scenario_env
        attempt_id = client.post("/scenarios/CCAO-F-WISD-SCN-001/start").json()["attempt_id"]
        client.post(f"/scenario-attempts/{attempt_id}/steps/1/answer", json={"selected_option_ids": [ids["opt_a"]]})
        client.post(f"/scenario-attempts/{attempt_id}/steps/2/answer", json={"selected_option_ids": [ids["opt_y"]]})
        r = client.post(f"/scenario-attempts/{attempt_id}/steps/1/hint")
        assert r.status_code == 409

    def test_get_attempt_reflects_completion(self, scenario_env):
        client, ids = scenario_env
        attempt_id = client.post("/scenarios/CCAO-F-WISD-SCN-001/start").json()["attempt_id"]
        client.post(f"/scenario-attempts/{attempt_id}/steps/1/answer", json={"selected_option_ids": [ids["opt_a"]]})
        client.post(f"/scenario-attempts/{attempt_id}/steps/2/answer", json={"selected_option_ids": [ids["opt_y"]]})
        body = client.get(f"/scenario-attempts/{attempt_id}").json()
        assert body["status"] == "submitted"
        assert body["current_step"] is None
        assert body["result"]["score_pct"] == 100


class TestFutureContentSecrecy:
    def test_step_1_response_never_reveals_step_2_content(self, scenario_env):
        client, ids = scenario_env
        body = client.post("/scenarios/CCAO-F-WISD-SCN-001/start").json()
        assert "Three months later" not in str(body)

    def test_hint_response_carries_only_the_earned_hint(self, scenario_env):
        client, ids = scenario_env
        attempt_id = client.post("/scenarios/CCAO-F-WISD-SCN-001/start").json()["attempt_id"]
        r = client.post(f"/scenario-attempts/{attempt_id}/steps/1/hint")
        body = r.json()
        assert body["hint_position"] == 1
        assert "Confidence isn't grounding" not in body["text"]  # hint 2's text
        assert body["hints_remaining"] == 1

    def test_hint_after_step_answered_is_rejected(self, scenario_env):
        client, ids = scenario_env
        attempt_id = client.post("/scenarios/CCAO-F-WISD-SCN-001/start").json()["attempt_id"]
        client.post(f"/scenario-attempts/{attempt_id}/steps/1/answer", json={"selected_option_ids": [ids["opt_a"]]})
        r = client.post(f"/scenario-attempts/{attempt_id}/steps/1/hint")
        assert r.status_code == 409


class TestHintPenaltyAffectsCredit:
    def test_revealed_hint_discounts_step_credit(self, scenario_env):
        client, ids = scenario_env
        attempt_id = client.post("/scenarios/CCAO-F-WISD-SCN-001/start").json()["attempt_id"]
        client.post(f"/scenario-attempts/{attempt_id}/steps/1/hint")  # 1000 bps
        body = client.post(f"/scenario-attempts/{attempt_id}/steps/1/answer", json={"selected_option_ids": [ids["opt_a"]]}).json()
        assert body["step_credit"] == pytest.approx(0.9)


class TestOwnership:
    def test_nonexistent_attempt_is_404_not_leaked(self, scenario_env):
        client, ids = scenario_env
        r = client.get("/scenario-attempts/999999")
        assert r.status_code == 404


class TestContentVersionIntegrity:
    def test_bumping_content_version_mid_attempt_blocks_further_progress(self, scenario_env, monkeypatch):
        client, ids = scenario_env
        attempt_id = client.post("/scenarios/CCAO-F-WISD-SCN-001/start").json()["attempt_id"]

        from app.database import get_db
        from app.main import app as fastapi_app
        from app.models import Scenario

        gen = fastapi_app.dependency_overrides[get_db]()
        db = next(gen)
        scenario = db.get(Scenario, ids["scenario_id"])
        scenario.content_version = 2
        db.commit()
        db.close()

        r = client.post(f"/scenario-attempts/{attempt_id}/steps/1/answer", json={"selected_option_ids": [ids["opt_a"]]})
        assert r.status_code == 409


class TestNoExternalDependency:
    def test_router_module_imports_no_ai_or_mcp_client(self):
        """Checks actual import statements, not prose -- this module's own docstring
        legitimately discusses Zia/Anthropic in English, which a bare substring check
        would misfire on."""
        import ast
        import inspect

        from app.routers import scenarios as scenarios_router

        tree = ast.parse(inspect.getsource(scenarios_router))
        imported_names: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported_names.update(a.name for a in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported_names.add(node.module)

        for forbidden in ("anthropic", "mcp", "app.services.zia_client"):
            assert not any(name.startswith(forbidden) for name in imported_names)


# --- Retake validity surfaced to the learner -------------------------------------------

class TestRetakeValidityApi:
    SCN = "CCAO-F-WISD-SCN-001"

    def _listing(self, client):
        rows = client.get("/scenarios", params={"track_code": "CCAO-F"}).json()
        return {r["external_id"]: r for r in rows}

    def test_fresh_scenario_is_listed_as_evidence_eligible(self, scenario_env):
        client, _ = scenario_env
        row = self._listing(client)[self.SCN]
        assert (row["learner_status"], row["counts_as_evidence"], row["evidence_band"]) == (
            "not_started", True, None)

    def test_answering_a_step_reveals_answers_and_ends_eligibility(self, scenario_env):
        client, ids = scenario_env
        start = client.post(f"/scenarios/{self.SCN}/start").json()
        assert start["counts_as_evidence"] is True
        client.post(f"/scenario-attempts/{start['attempt_id']}/steps/1/answer",
                    json={"selected_option_ids": [ids["opt_a"]]})
        row = self._listing(client)[self.SCN]
        assert (row["learner_status"], row["counts_as_evidence"]) == ("in_progress", False)
        retake = client.post(f"/scenarios/{self.SCN}/start").json()
        assert retake["counts_as_evidence"] is False

    def test_opening_without_answering_keeps_eligibility(self, scenario_env):
        client, _ = scenario_env
        client.post(f"/scenarios/{self.SCN}/start")
        assert self._listing(client)[self.SCN]["counts_as_evidence"] is True
        assert client.post(f"/scenarios/{self.SCN}/start").json()["counts_as_evidence"] is True

    def test_recall_retake_cannot_change_the_evidence_band(self, scenario_env):
        client, ids = scenario_env
        first = client.post(f"/scenarios/{self.SCN}/start").json()["attempt_id"]
        client.post(f"/scenario-attempts/{first}/steps/1/answer", json={"selected_option_ids": [ids["opt_b"]]})
        client.post(f"/scenario-attempts/{first}/steps/2/answer", json={"selected_option_ids": [ids["opt_x"]]})
        retake = client.post(f"/scenarios/{self.SCN}/start").json()["attempt_id"]
        client.post(f"/scenario-attempts/{retake}/steps/1/answer", json={"selected_option_ids": [ids["opt_a"]]})
        done = client.post(f"/scenario-attempts/{retake}/steps/2/answer",
                           json={"selected_option_ids": [ids["opt_y"]]}).json()
        assert done["result"]["mastery_band"] == "strong"  # the retake itself scored strong
        row = self._listing(client)[self.SCN]
        assert (row["learner_status"], row["counts_as_evidence"], row["evidence_band"]) == (
            "completed", False, "critical")
