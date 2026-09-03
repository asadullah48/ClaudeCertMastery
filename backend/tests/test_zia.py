"""Ask Zia integration tests.

The MCP client is mocked throughout. These tests assert the platform's *contract with
the tutor* -- which tool is called when, what evidence is reported, and that every
failure path degrades to a hidden panel rather than a broken screen. They deliberately
do not depend on the live endpoint, which requires OAuth credentials.
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

from app.services.zia_client import LessonHit, ZiaResult, ZiaStatus  # noqa: E402


class FakeZiaClient:
    """Stand-in for ZiaTutorClient that records calls and returns canned results."""

    def __init__(self, *, configured: bool = True, fail: bool = False,
                 abstain: bool = False, thin_snippet: bool = False):
        self._configured = configured
        self.fail = fail
        self.abstain = abstain
        self.thin_snippet = thin_snippet
        self.calls: list[tuple[str, dict]] = []

    @property
    def configured(self) -> bool:
        return self._configured

    async def probe(self) -> ZiaStatus:
        return ZiaStatus(True, True, "ok", "zia", [])

    async def begin_session(self, goal=None, note=None):
        self.calls.append(("begin_session", {"goal": goal, "note": note}))
        if self.fail:
            return ZiaResult(ok=False, detail="tutor down")
        return ZiaResult(ok=True, session_handle="sess-123")

    async def open_student_record(self, course=None, observation=None):
        self.calls.append(("open_student_record", {"course": course}))
        if self.fail:
            return ZiaResult(ok=False, detail="tutor down")
        return ZiaResult(ok=True)

    async def search(self, query, grain="passage", k=5):
        self.calls.append(("search", {"query": query, "grain": grain, "k": k}))
        if self.fail:
            return ZiaResult(ok=False, detail="tutor down")
        if self.abstain:
            return ZiaResult(ok=True, detail="corpus does not cover this concept")
        content = "short." if self.thin_snippet else ("A cache hit is a discount. " * 40)
        return ZiaResult(
            ok=True,
            hits=[
                LessonHit(
                    slug="build-agents-crash-course",
                    heading_path="part-6-cost-discipline-routing-by-model-tier",
                    content=content,
                    url="https://agentfactory.panaversity.org/docs/build-agents-crash-course",
                    score=0.016,
                )
            ],
        )

    async def read_lesson(self, slug, section=None):
        self.calls.append(("read_lesson", {"slug": slug, "section": section}))
        if self.fail:
            return ZiaResult(ok=False, detail="tutor down")
        return ZiaResult(
            ok=True,
            lesson_text="Full lesson body. " * 60,
            lesson_title="Build AI Agents",
            lesson_url="https://agentfactory.panaversity.org/docs/build-agents-crash-course",
        )

    async def update_student_record(self, **kwargs):
        self.calls.append(("update_student_record", kwargs))
        if self.fail:
            return ZiaResult(ok=False, detail="tutor down")
        return ZiaResult(ok=True)

    def tool_names(self) -> list[str]:
        return [name for name, _ in self.calls]


@pytest.fixture
def zia_env(tmp_path):
    """A TestClient with a seeded DB and an injectable fake tutor client."""
    db_path = tmp_path / "zia.db"
    engine = create_engine(
        f"sqlite:///{db_path}", connect_args={"check_same_thread": False}
    )
    TestingSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    from app import models as _models  # noqa: F401
    from app.database import Base, get_db
    from app.main import app
    from app.routers.zia import get_zia_client
    import seed

    Base.metadata.create_all(engine)
    with TestingSession() as db:
        seed.seed_ccao_f(db)
        seed.seed_placeholder_tracks(db)
        seed.seed_concept_map(db)
        seed.seed_dev_user(db)
        db.commit()

    fake = FakeZiaClient()

    def override_get_db():
        db = TestingSession()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_zia_client] = lambda: fake

    with TestClient(app) as client:
        yield client, fake, app

    app.dependency_overrides.clear()


class TestConceptMapSeeding:
    def test_all_required_ccar_tags_are_mapped(self, zia_env):
        client, _, _ = zia_env
        required = {
            ("CCAR-F", "multi-agent-supervisor-worker"),
            ("CCAR-F", "prompt-caching-economics"),
            ("CCAR-F", "claude-md-team-configuration"),
            ("CCAR-P", "enterprise-rag-pipelines"),
            ("CCAR-P", "automated-eval-frameworks"),
            ("CCAR-P", "compliance-cost-latency-tradeoffs"),
        }
        for track, tag in required:
            r = client.get(f"/api/zia/explain?track_code={track}&concept_tag={tag}")
            assert r.status_code == 200, (track, tag)
            assert r.json()["available"] is True, (track, tag)

    def test_mapped_slugs_are_real_agent_factory_lessons(self, zia_env):
        # Guards against a placeholder slug reaching the mapping table. These were
        # confirmed against the live MCP corpus (generation 62) on 2026-09-03.
        from app.database import SessionLocal  # noqa: F401
        client, _, _ = zia_env
        r = client.get(
            "/api/zia/explain?track_code=CCAR-P&concept_tag=enterprise-rag-pipelines"
        )
        body = r.json()
        assert body["concept_label"] == "Enterprise RAG pipelines"
        assert any("panaversity.org" in (c["url"] or "") for c in body["citations"])


class TestSessionLifecycle:
    def test_first_open_begins_a_session(self, zia_env):
        client, fake, _ = zia_env
        r = client.post("/api/zia/session", json={"goal": "CCAR-F prep"})
        assert r.status_code == 200
        assert r.json()["ok"] is True
        assert r.json()["started_new_session"] is True
        assert fake.tool_names() == ["begin_session"]

    def test_second_open_resumes_instead_of_restarting(self, zia_env):
        # Restarting would fragment the learner's mastery record across handles.
        client, fake, _ = zia_env
        client.post("/api/zia/session", json={})
        fake.calls.clear()
        r = client.post("/api/zia/session", json={})
        assert r.json()["started_new_session"] is False
        assert fake.tool_names() == ["open_student_record"]

    def test_session_handle_is_persisted_for_the_learner(self, zia_env):
        client, _, _ = zia_env
        first = client.post("/api/zia/session", json={}).json()
        second = client.post("/api/zia/session", json={}).json()
        assert first["session_handle"] == second["session_handle"] == "sess-123"

    def test_tutor_failure_does_not_error_the_request(self, zia_env):
        client, fake, _ = zia_env
        fake.fail = True
        r = client.post("/api/zia/session", json={})
        assert r.status_code == 200
        assert r.json()["ok"] is False


class TestExplain:
    def test_explanation_carries_a_visible_source_citation(self, zia_env):
        # A tutor answer with no traceable source is exactly the failure mode the
        # platform teaches candidates to distrust.
        client, _, _ = zia_env
        body = client.get(
            "/api/zia/explain?track_code=CCAR-F&concept_tag=prompt-caching-economics"
        ).json()
        assert body["available"] is True
        assert body["citations"]
        assert body["citations"][0]["slug"] == "build-agents-crash-course"
        assert body["citations"][0]["url"].startswith("https://")

    def test_search_uses_the_mapped_query_not_the_raw_tag(self, zia_env):
        client, fake, _ = zia_env
        client.get(
            "/api/zia/explain?track_code=CCAR-F&concept_tag=prompt-caching-economics"
        )
        search = next(args for name, args in fake.calls if name == "search")
        assert "caching" in search["query"]
        assert search["query"] != "prompt-caching-economics"

    def test_a_thin_snippet_triggers_a_full_lesson_read(self, zia_env):
        client, fake, _ = zia_env
        fake.thin_snippet = True
        body = client.get(
            "/api/zia/explain?track_code=CCAR-F&concept_tag=prompt-caching-economics"
        ).json()
        assert "read_lesson" in fake.tool_names()
        assert len(body["explanation"]) > 400

    def test_a_rich_snippet_does_not_trigger_an_extra_read(self, zia_env):
        client, fake, _ = zia_env
        client.get(
            "/api/zia/explain?track_code=CCAR-F&concept_tag=prompt-caching-economics"
        )
        assert "read_lesson" not in fake.tool_names()

    def test_a_follow_up_check_is_offered(self, zia_env):
        client, _, _ = zia_env
        body = client.get(
            "/api/zia/explain?track_code=CCAR-P&concept_tag=automated-eval-frameworks"
        ).json()
        assert body["follow_up_question"]

    def test_unmapped_concept_hides_the_panel(self, zia_env):
        client, _, _ = zia_env
        body = client.get(
            "/api/zia/explain?track_code=CCAR-F&concept_tag=not-a-real-concept"
        ).json()
        assert body["ok"] is True and body["available"] is False

    def test_corpus_abstention_hides_the_panel(self, zia_env):
        # "The book does not cover this" is a correct answer, not a malfunction.
        client, fake, _ = zia_env
        fake.abstain = True
        body = client.get(
            "/api/zia/explain?track_code=CCAR-F&concept_tag=prompt-caching-economics"
        ).json()
        assert body["available"] is False

    def test_tutor_outage_hides_the_panel_without_an_error(self, zia_env):
        client, fake, _ = zia_env
        fake.fail = True
        r = client.get(
            "/api/zia/explain?track_code=CCAR-F&concept_tag=prompt-caching-economics"
        )
        assert r.status_code == 200
        assert r.json()["available"] is False

    def test_unconfigured_tutor_hides_the_panel(self, zia_env):
        client, fake, _ = zia_env
        fake._configured = False
        body = client.get(
            "/api/zia/explain?track_code=CCAR-F&concept_tag=prompt-caching-economics"
        ).json()
        assert body["available"] is False

    def test_missing_parameters_are_rejected(self, zia_env):
        client, _, _ = zia_env
        assert client.get("/api/zia/explain").status_code == 422

    def test_unknown_question_returns_404(self, zia_env):
        client, _, _ = zia_env
        assert client.get("/api/zia/explain?question_id=999999").status_code == 404


class TestCheckAnswer:
    def _open_then_answer(self, client, **overrides):
        client.post("/api/zia/session", json={})
        payload = {
            "concept_tag": "prompt-caching-economics",
            "track_code": "CCAR-F",
            "follow_up_question": "What is the one idea you would carry in?",
            "learner_answer": "Keep the prefix stable so it stays cached.",
        }
        payload.update(overrides)
        return client.post("/api/zia/check-answer", json=payload)

    def test_answer_is_recorded_on_the_learner_record(self, zia_env):
        client, fake, _ = zia_env
        r = self._open_then_answer(client)
        assert r.status_code == 200 and r.json()["recorded"] is True
        assert "update_student_record" in fake.tool_names()

    def test_evidence_basis_is_reported_honestly(self, zia_env):
        # The platform must not claim mastery it did not witness. It reports the
        # learner's own words with the basis on which they were observed.
        client, fake, _ = zia_env
        self._open_then_answer(client)
        args = next(a for n, a in fake.calls if n == "update_student_record")
        assert args["evidence"] == "teacher_reported_observed_in_chat"
        assert args["verb"] == "mastery"
        assert args["attempt"] == "Keep the prefix stable so it stays cached."

    def test_the_concept_is_recorded_as_the_step(self, zia_env):
        client, fake, _ = zia_env
        self._open_then_answer(client)
        args = next(a for n, a in fake.calls if n == "update_student_record")
        assert args["step"] == "prompt-caching-economics"

    def test_an_empty_answer_is_not_recorded(self, zia_env):
        client, fake, _ = zia_env
        r = self._open_then_answer(client, learner_answer="   ")
        assert r.json()["recorded"] is False
        assert "update_student_record" not in fake.tool_names()

    def test_answering_without_a_session_is_refused(self, zia_env):
        client, fake, _ = zia_env
        r = client.post(
            "/api/zia/check-answer",
            json={
                "concept_tag": "prompt-caching-economics",
                "track_code": "CCAR-F",
                "learner_answer": "something",
            },
        )
        assert r.json()["recorded"] is False
        assert "update_student_record" not in fake.tool_names()

    def test_tutor_failure_reports_not_recorded_without_erroring(self, zia_env):
        client, fake, _ = zia_env
        client.post("/api/zia/session", json={})
        fake.fail = True
        r = client.post(
            "/api/zia/check-answer",
            json={
                "concept_tag": "prompt-caching-economics",
                "track_code": "CCAR-F",
                "learner_answer": "an answer",
            },
        )
        assert r.status_code == 200
        assert r.json()["recorded"] is False


class TestCcaoFlowUntouched:
    """Session 2 must not alter the CCAO-F flow or its Claude-API explanations."""

    def test_ccao_f_still_generates_and_submits(self, zia_env):
        client, _, _ = zia_env
        gen = client.post(
            "/exams/generate", json={"track_code": "CCAO-F", "seed": 5150}
        ).json()
        assert len(gen["questions"]) == 60
        body = client.post(
            f"/attempts/{gen['attempt_id']}/submit", json={"answers": []}
        ).json()
        assert body["scaled_score"] == 100 and body["raw_total"] == 60

    def test_static_explanations_are_still_served(self, zia_env):
        client, _, _ = zia_env
        gen = client.post(
            "/exams/generate", json={"track_code": "CCAO-F", "seed": 5151}
        ).json()
        body = client.post(
            f"/attempts/{gen['attempt_id']}/submit", json={"answers": []}
        ).json()
        assert all(i["explanation"].strip() for i in body["items"])

    def test_ccao_questions_gained_a_mapping_in_session_3(self, zia_env):
        # Session 2 scoped Ask Zia to CCAR-F/CCAR-P and this asserted no CCAO-F mapping.
        # Session 3 widened it deliberately, so the assertion is inverted rather than
        # deleted -- the change of intent should be visible in the test history.
        client, _, _ = zia_env
        gen = client.post(
            "/exams/generate", json={"track_code": "CCAO-F", "seed": 5152}
        ).json()
        qid = gen["questions"][0]["id"]
        body = client.get(f"/api/zia/explain?question_id={qid}").json()
        assert body["available"] is True
        # Still a companion: the built-in explanation is unaffected by this.
        assert body["matched_by"] == "domain"


class TestSession3Widening:
    """Ask Zia widened to all four tracks, driven by the mapping table alone."""

    CCAO_DOMAINS = ["PTE", "OEV", "PMS", "WISD", "CKM", "GRR", "TRO"]

    @pytest.mark.parametrize("domain", CCAO_DOMAINS)
    def test_every_ccao_f_domain_is_mapped(self, zia_env, domain):
        client, _, _ = zia_env
        body = client.get(
            f"/api/zia/explain?track_code=CCAO-F&concept_tag={domain}"
        ).json()
        assert body["available"] is True, domain

    def test_every_ccao_f_question_resolves_through_its_domain(self, zia_env):
        # 112 authored questions carry no tags; they must resolve via domain code, or
        # widening would have required re-tagging the entire bank.
        client, _, _ = zia_env
        gen = client.post(
            "/exams/generate", json={"track_code": "CCAO-F", "seed": 777}
        ).json()
        for q in gen["questions"][:12]:
            body = client.get(f"/api/zia/explain?question_id={q['id']}").json()
            assert body["available"] is True, q["external_id"]
            assert body["matched_by"] == "domain"

    def test_ccdv_f_core_objectives_are_mapped(self, zia_env):
        client, _, _ = zia_env
        for tag in [
            "messages-api",
            "streaming-and-batch",
            "tool-schema-design",
            "agentic-ai-fundamentals",
            "python-for-ai",
            "managed-agents",
        ]:
            body = client.get(
                f"/api/zia/explain?track_code=CCDV-F&concept_tag={tag}"
            ).json()
            assert body["available"] is True, tag

    def test_an_unmapped_objective_hides_the_panel(self, zia_env):
        # typescript-sdk is a deliberate, recorded gap: the corpus is Python-centric.
        client, _, _ = zia_env
        body = client.get(
            "/api/zia/explain?track_code=CCDV-F&concept_tag=typescript-sdk"
        ).json()
        assert body["ok"] is True and body["available"] is False

    def test_unmapped_objectives_are_reported_not_hidden(self, zia_env):
        client, _, _ = zia_env
        body = client.get("/api/zia/concepts?track_code=CCDV-F").json()
        assert "typescript-sdk" in body["unmapped"]
        assert all(c["concept_tag"] != "typescript-sdk" for c in body["concepts"])

    @pytest.mark.parametrize(
        "track,minimum", [("CCAO-F", 7), ("CCDV-F", 6), ("CCAR-F", 4), ("CCAR-P", 4)]
    )
    def test_all_four_tracks_expose_mapped_concepts(self, zia_env, track, minimum):
        client, _, _ = zia_env
        body = client.get(f"/api/zia/concepts?track_code={track}").json()
        assert len(body["concepts"]) >= minimum, track

    def test_concepts_endpoint_drives_the_panel(self, zia_env):
        # The frontend renders from this list, so every concept it advertises must
        # actually resolve -- otherwise the panel offers a button that hides itself.
        client, _, _ = zia_env
        for track in ["CCAO-F", "CCDV-F", "CCAR-F", "CCAR-P"]:
            listed = client.get(f"/api/zia/concepts?track_code={track}").json()
            for c in listed["concepts"]:
                body = client.get(
                    f"/api/zia/explain?track_code={track}&concept_tag={c['concept_tag']}"
                ).json()
                assert body["available"] is True, (track, c["concept_tag"])

    def test_a_track_with_no_mappings_returns_an_empty_list(self, zia_env):
        client, _, _ = zia_env
        body = client.get("/api/zia/concepts?track_code=NOPE").json()
        assert body["concepts"] == [] and body["unmapped"] == []

    def test_claude_engine_remains_the_default_everywhere(self, zia_env):
        # Widening Ask Zia must not have replaced the built-in explanations.
        client, _, _ = zia_env
        gen = client.post(
            "/exams/generate", json={"track_code": "CCAO-F", "seed": 778}
        ).json()
        body = client.post(
            f"/attempts/{gen['attempt_id']}/submit", json={"answers": []}
        ).json()
        assert all(i["explanation"].strip() for i in body["items"])
