"""API integration tests against a disposable SQLite database."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    """A TestClient backed by its own seeded SQLite file.

    The database dependency is overridden rather than the settings mutated, so the
    developer's own certmastery.db is never touched by a test run.
    """
    db_path = tmp_path_factory.mktemp("db") / "test.db"
    engine = create_engine(
        f"sqlite:///{db_path}", connect_args={"check_same_thread": False}
    )
    TestingSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    # `from app import models` rather than `import app.models`: the latter rebinds the
    # local name `app` to the package and shadows the FastAPI instance below.
    from app import models as _models  # noqa: F401  registers metadata
    from app.database import Base, get_db
    from app.main import app
    import seed

    Base.metadata.create_all(engine)

    with TestingSession() as db:
        seed.seed_ccao_f(db)
        seed.seed_placeholder_tracks(db)
        seed.seed_dev_user(db)
        db.commit()

    def override_get_db():
        db = TestingSession()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


class TestHealth:
    def test_health_reports_ok(self, client):
        r = client.get("/health")
        assert r.status_code == 200
        assert r.json()["status"] == "ok"

    def test_health_reports_ai_availability(self, client):
        assert "ai_explanations_enabled" in client.get("/health").json()


class TestTracks:
    def test_all_four_tracks_are_listed(self, client):
        codes = {t["code"] for t in client.get("/tracks").json()}
        assert codes == {"CCAO-F", "CCDV-F", "CCAR-F", "CCAR-P"}

    def test_unseeded_tracks_are_visible_but_flagged(self, client):
        tracks = {t["code"]: t for t in client.get("/tracks").json()}
        assert tracks["CCAO-F"]["is_seeded"] is True
        for code in ("CCDV-F", "CCAR-F", "CCAR-P"):
            assert tracks[code]["is_seeded"] is False

    def test_ccao_f_reports_its_full_question_count(self, client):
        assert client.get("/tracks/CCAO-F").json()["question_count"] == 112

    def test_ccao_f_exam_parameters_match_the_specification(self, client):
        t = client.get("/tracks/CCAO-F").json()
        assert t["item_count"] == 60
        assert t["duration_minutes"] == 120
        assert t["pass_scaled_score"] == 720
        assert t["validity_months"] == 12

    def test_unknown_track_returns_404(self, client):
        assert client.get("/tracks/NOPE").status_code == 404


class TestBlueprint:
    def test_blueprint_lists_all_seven_domains(self, client):
        bp = client.get("/tracks/CCAO-F/blueprint").json()
        assert len(bp["domains"]) == 7

    def test_blueprint_weights_total_100_percent(self, client):
        bp = client.get("/tracks/CCAO-F/blueprint").json()
        assert bp["total_weight_bps"] == 10_000
        assert sum(d["weight_pct"] for d in bp["domains"]) == 100.0

    def test_blueprint_item_counts_total_the_exam_length(self, client):
        bp = client.get("/tracks/CCAO-F/blueprint").json()
        assert sum(d["items_at_full_length"] for d in bp["domains"]) == 60

    def test_blueprint_matches_the_published_weights(self, client):
        bp = client.get("/tracks/CCAO-F/blueprint").json()
        actual = {d["code"]: d["weight_pct"] for d in bp["domains"]}
        assert actual == {
            "PTE": 14.0, "OEV": 21.0, "PMS": 12.0, "WISD": 16.0,
            "CKM": 12.0, "GRR": 15.0, "TRO": 10.0,
        }

    def test_unseeded_track_has_no_blueprint(self, client):
        assert client.get("/tracks/CCDV-F/blueprint").status_code == 404


class TestExamGeneration:
    def test_generated_exam_has_60_items(self, client):
        r = client.post("/exams/generate", json={"track_code": "CCAO-F", "seed": 42})
        assert r.status_code == 201
        assert len(r.json()["questions"]) == 60

    def test_generated_exam_matches_the_blueprint_split(self, client):
        r = client.post("/exams/generate", json={"track_code": "CCAO-F", "seed": 42})
        assert r.json()["per_domain"] == {
            "PTE": 8, "OEV": 13, "PMS": 7, "WISD": 10, "CKM": 7, "GRR": 9, "TRO": 6,
        }

    def test_answer_key_is_never_sent_to_the_client(self, client):
        # The exam payload must not contain is_correct on any option, or the answers
        # would be visible in the browser.
        r = client.post("/exams/generate", json={"track_code": "CCAO-F", "seed": 1})
        for q in r.json()["questions"]:
            for opt in q["options"]:
                assert "is_correct" not in opt

    def test_no_question_is_repeated_within_an_exam(self, client):
        r = client.post("/exams/generate", json={"track_code": "CCAO-F", "seed": 5})
        ids = [q["id"] for q in r.json()["questions"]]
        assert len(set(ids)) == len(ids)

    def test_healthy_bank_generates_without_a_warning(self, client):
        r = client.post("/exams/generate", json={"track_code": "CCAO-F", "seed": 9})
        assert r.json()["composition_warning"] is None

    def test_custom_item_count_is_honoured(self, client):
        r = client.post(
            "/exams/generate", json={"track_code": "CCAO-F", "item_count": 30, "seed": 3}
        )
        assert len(r.json()["questions"]) == 30

    def test_generating_for_an_unseeded_track_is_rejected(self, client):
        r = client.post("/exams/generate", json={"track_code": "CCAR-P"})
        assert r.status_code == 409

    def test_generating_for_an_unknown_track_returns_404(self, client):
        assert client.post("/exams/generate", json={"track_code": "NOPE"}).status_code == 404


def _answer_key(client, questions, n_correct):
    """Build a submission answering exactly `n_correct` questions correctly.

    The exam payload deliberately omits the answer key, so correctness is derived from
    the seeded YAML rather than from the API response.
    """
    import yaml

    correct_by_external_id = {}
    for path in sorted((ROOT / "seed_data" / "ccao_f").glob("*.yaml")):
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        for q in data["questions"]:
            correct_by_external_id[q["external_id"]] = {
                o["label"] for o in q["options"] if o.get("correct")
            }

    answers = []
    for i, q in enumerate(questions):
        correct_labels = correct_by_external_id[q["external_id"]]
        if i < n_correct:
            chosen = [o["id"] for o in q["options"] if o["label"] in correct_labels]
        else:
            # Deliberately wrong: pick the options that are not correct.
            wrong = [o["id"] for o in q["options"] if o["label"] not in correct_labels]
            chosen = wrong[:1]
        answers.append({"question_id": q["id"], "selected_option_ids": chosen})
    return answers


class TestSubmission:
    def test_exactly_42_correct_scores_exactly_the_pass_line(self, client):
        # The end-to-end assertion: 42/60 is exactly 70% raw, which must map to 720.
        gen = client.post("/exams/generate", json={"track_code": "CCAO-F", "seed": 101}).json()
        answers = _answer_key(client, gen["questions"], 42)
        r = client.post(f"/attempts/{gen['attempt_id']}/submit", json={"answers": answers})
        assert r.status_code == 200
        body = r.json()
        assert body["raw_correct"] == 42
        assert body["raw_total"] == 60
        assert body["scaled_score"] == 720
        assert body["passed"] is True

    def test_41_correct_falls_just_below_the_pass_line(self, client):
        gen = client.post("/exams/generate", json={"track_code": "CCAO-F", "seed": 102}).json()
        answers = _answer_key(client, gen["questions"], 41)
        body = client.post(
            f"/attempts/{gen['attempt_id']}/submit", json={"answers": answers}
        ).json()
        assert body["raw_correct"] == 41
        assert body["scaled_score"] == 705
        assert body["passed"] is False

    def test_a_perfect_score_reports_the_scale_maximum(self, client):
        gen = client.post("/exams/generate", json={"track_code": "CCAO-F", "seed": 103}).json()
        answers = _answer_key(client, gen["questions"], 60)
        body = client.post(
            f"/attempts/{gen['attempt_id']}/submit", json={"answers": answers}
        ).json()
        assert body["scaled_score"] == 1000 and body["passed"] is True

    def test_domain_scores_reconcile_with_the_raw_total(self, client):
        gen = client.post("/exams/generate", json={"track_code": "CCAO-F", "seed": 104}).json()
        answers = _answer_key(client, gen["questions"], 42)
        body = client.post(
            f"/attempts/{gen['attempt_id']}/submit", json={"answers": answers}
        ).json()
        assert sum(d["total"] for d in body["domain_scores"]) == body["raw_total"]
        assert sum(d["correct"] for d in body["domain_scores"]) == body["raw_correct"]

    def test_all_seven_domains_appear_in_the_breakdown(self, client):
        gen = client.post("/exams/generate", json={"track_code": "CCAO-F", "seed": 105}).json()
        answers = _answer_key(client, gen["questions"], 30)
        body = client.post(
            f"/attempts/{gen['attempt_id']}/submit", json={"answers": answers}
        ).json()
        assert len(body["domain_scores"]) == 7

    def test_every_item_returns_an_explanation(self, client):
        # Results must be useful with no ANTHROPIC_API_KEY configured.
        gen = client.post("/exams/generate", json={"track_code": "CCAO-F", "seed": 106}).json()
        answers = _answer_key(client, gen["questions"], 20)
        body = client.post(
            f"/attempts/{gen['attempt_id']}/submit", json={"answers": answers}
        ).json()
        assert all(item["explanation"].strip() for item in body["items"])

    def test_an_empty_submission_scores_the_scale_minimum(self, client):
        gen = client.post("/exams/generate", json={"track_code": "CCAO-F", "seed": 107}).json()
        body = client.post(
            f"/attempts/{gen['attempt_id']}/submit", json={"answers": []}
        ).json()
        assert body["raw_correct"] == 0 and body["scaled_score"] == 100

    def test_resubmitting_an_attempt_is_rejected(self, client):
        gen = client.post("/exams/generate", json={"track_code": "CCAO-F", "seed": 108}).json()
        client.post(f"/attempts/{gen['attempt_id']}/submit", json={"answers": []})
        again = client.post(f"/attempts/{gen['attempt_id']}/submit", json={"answers": []})
        assert again.status_code == 409

    def test_submitting_an_unknown_attempt_returns_404(self, client):
        assert client.post("/attempts/999999/submit", json={"answers": []}).status_code == 404


class TestAttemptRetrieval:
    def test_attempt_is_retrievable_after_generation(self, client):
        gen = client.post("/exams/generate", json={"track_code": "CCAO-F", "seed": 201}).json()
        body = client.get(f"/attempts/{gen['attempt_id']}").json()
        assert body["status"] == "in_progress" and body["item_count"] == 60

    def test_attempt_reflects_the_score_after_submission(self, client):
        gen = client.post("/exams/generate", json={"track_code": "CCAO-F", "seed": 202}).json()
        answers = _answer_key(client, gen["questions"], 42)
        client.post(f"/attempts/{gen['attempt_id']}/submit", json={"answers": answers})
        body = client.get(f"/attempts/{gen['attempt_id']}").json()
        assert body["status"] == "submitted"
        assert body["scaled_score"] == 720 and body["passed"] is True

    def test_the_stored_seed_reproduces_the_same_exam(self, client):
        first = client.post("/exams/generate", json={"track_code": "CCAO-F", "seed": 303}).json()
        replay = client.post("/exams/generate", json={"track_code": "CCAO-F", "seed": 303}).json()
        assert [q["id"] for q in first["questions"]] == [q["id"] for q in replay["questions"]]

    def test_unknown_attempt_returns_404(self, client):
        assert client.get("/attempts/999999").status_code == 404
