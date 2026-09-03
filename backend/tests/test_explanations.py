"""AI explanation engine: prompt construction, failure policy, dedup and the route.

No test here touches the network. The engine takes its Anthropic client by constructor
injection and the route takes the engine by FastAPI dependency, so every path -- success,
rate limit, malformed response, no key at all -- is exercised against a fake.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.services.explanation_engine import (  # noqa: E402
    DomainContext,
    ExplanationEngine,
    ExplanationPayload,
    GenerationResult,
    GenerationUsage,
    QuestionContext,
    build_system_blocks,
    build_user_prompt,
)

# claude-opus-5 will not create a cache entry for a prefix below this many tokens. It is
# not an error -- the request simply never caches and cache_read_input_tokens stays 0.
MIN_CACHEABLE_TOKENS = 512

# Chars-per-token is a proxy; count_tokens is authoritative. English prose runs ~4 chars
# per token, and 4.4 leaves headroom so this guard cannot pass a prefix that would fail
# for real.
CHARS_PER_TOKEN = 4.4


DOMAIN = DomainContext(
    track_code="CCAO-F",
    track_name="Claude Certified AI Operator - Foundation",
    domain_code="OEV",
    domain_name="Output Evaluation & Validation",
    domain_description=(
        "Judging whether Claude's output is fit for the task: spotting unsupported "
        "claims, deciding when a second pass is needed, and distinguishing verification "
        "from asking the same model to check itself."
    ),
    weight_bps=2100,
    blueprint=[
        ("PTE", "Prompting & Task Execution", 1400),
        ("OEV", "Output Evaluation & Validation", 2100),
        ("PMS", "Product & Model Selection", 1200),
        ("WISD", "Workflow Integration & Solution Design", 1600),
        ("CKM", "Configuration & Knowledge Management", 1200),
        ("GRR", "Governance, Risk & Responsible Use", 1500),
        ("TRO", "Troubleshooting & Optimization", 1000),
    ],
)

QUESTION = QuestionContext(
    question_id=42,
    external_id="CCAO-F-OEV-004",
    stem="A summary cites a statistic absent from the source. What is the correct step?",
    question_type="mcq",
    options=[
        (401, "A", "Ask Claude whether the summary is accurate."),
        (402, "B", "Check the cited statistic against the source document."),
        (403, "C", "Raise the temperature and regenerate."),
        (404, "D", "Accept it; Claude rarely fabricates figures."),
    ],
    correct_option_ids={402},
    selected_option_ids={401},
)

PAYLOAD = ExplanationPayload(
    why_correct="Verification means checking against the source.",
    why_your_answer_wrong="Asking the same model to self-check is not verification.",
    key_concept="Verification requires an independent source.",
    blueprint_link="OEV is 21% of the exam, the heaviest domain.",
    study_tip="For every claim, ask which artefact outside the model confirms it.",
)


class FakeUsage:
    def __init__(self, cache_read: int | None = 0) -> None:
        self.input_tokens = 1200
        self.output_tokens = 180
        self.cache_read_input_tokens = cache_read


class FakeResponse:
    def __init__(self, payload=PAYLOAD, cache_read: int | None = 0) -> None:
        self.parsed_output = payload
        self.usage = FakeUsage(cache_read)


class FakeMessages:
    """Records every call so tests can assert on the request that was built."""

    def __init__(self, response=None, raises: Exception | None = None) -> None:
        self._response = response if response is not None else FakeResponse()
        self._raises = raises
        self.calls: list[dict] = []

    def parse(self, **kwargs):
        self.calls.append(kwargs)
        if self._raises is not None:
            raise self._raises
        return self._response


class FakeClient:
    def __init__(self, response=None, raises: Exception | None = None) -> None:
        self.messages = FakeMessages(response, raises)


class TestPromptCaching:
    """The prefix has to be long enough and stable enough to actually cache."""

    def test_stable_prefix_is_long_enough_to_cache(self):
        blocks = build_system_blocks(DOMAIN)
        chars = sum(len(b["text"]) for b in blocks)
        estimated_tokens = chars / CHARS_PER_TOKEN
        assert estimated_tokens > MIN_CACHEABLE_TOKENS, (
            f"Stable prefix is ~{estimated_tokens:.0f} tokens, below the "
            f"{MIN_CACHEABLE_TOKENS}-token minimum for claude-opus-5. It would silently "
            "never cache. Lengthen PEDAGOGY_RULES rather than lowering this bound."
        )

    def test_prefix_is_byte_identical_across_calls(self):
        # Any per-call variation -- a timestamp, a uuid, dict iteration order -- would
        # invalidate the cache on every request while raising no error at all.
        assert build_system_blocks(DOMAIN) == build_system_blocks(DOMAIN)

    def test_cache_breakpoint_is_on_the_last_stable_block(self):
        blocks = build_system_blocks(DOMAIN)
        assert blocks[-1]["cache_control"] == {"type": "ephemeral"}
        assert "cache_control" not in blocks[0]

    def test_volatile_content_is_not_in_the_cached_prefix(self):
        # The question and the candidate's answer must sit below the breakpoint, or the
        # prefix changes per item and the cache never hits.
        prefix = " ".join(b["text"] for b in build_system_blocks(DOMAIN))
        assert QUESTION.stem not in prefix
        assert QUESTION.external_id not in prefix

    def test_two_domains_do_not_share_a_cache_key(self):
        other = DomainContext(**{**DOMAIN.__dict__, "domain_code": "PTE"})
        assert other.cache_key != DOMAIN.cache_key


class TestPromptContent:
    def test_user_prompt_names_the_candidates_actual_selection(self):
        prompt = build_user_prompt(QUESTION)
        assert "THE CANDIDATE SELECTED: A" in prompt
        assert "CORRECT ANSWER: B" in prompt

    def test_unanswered_item_is_described_not_left_blank(self):
        blank = QuestionContext(**{**QUESTION.__dict__, "selected_option_ids": set()})
        assert "(no option selected)" in build_user_prompt(blank)

    def test_multi_response_items_are_labelled_as_such(self):
        mr = QuestionContext(
            **{
                **QUESTION.__dict__,
                "question_type": "mr",
                "correct_option_ids": {402, 403},
            }
        )
        assert "multiple response" in build_user_prompt(mr)

    def test_prefix_states_the_domain_weight(self):
        prefix = " ".join(b["text"] for b in build_system_blocks(DOMAIN))
        assert "21%" in prefix


class TestModelParameters:
    def test_uses_adaptive_thinking_not_budget_tokens(self):
        # budget_tokens is removed on claude-opus-5 and returns 400.
        client = FakeClient()
        ExplanationEngine(client).generate(DOMAIN, QUESTION)
        sent = client.messages.calls[0]
        assert sent["thinking"] == {"type": "adaptive"}
        assert "budget_tokens" not in sent

    def test_requests_medium_effort(self):
        client = FakeClient()
        ExplanationEngine(client).generate(DOMAIN, QUESTION)
        assert client.messages.calls[0]["output_config"] == {"effort": "medium"}

    def test_requests_structured_output(self):
        client = FakeClient()
        ExplanationEngine(client).generate(DOMAIN, QUESTION)
        assert client.messages.calls[0]["output_format"] is ExplanationPayload


class TestFailurePolicy:
    """The AI layer may never take the platform down."""

    def test_engine_with_no_client_is_unavailable(self):
        engine = ExplanationEngine(None)
        assert engine.available is False
        result = engine.generate(DOMAIN, QUESTION)
        assert result.ok is False
        assert "ANTHROPIC_API_KEY" in result.detail

    def test_api_error_is_swallowed_into_a_failed_result(self):
        engine = ExplanationEngine(FakeClient(raises=RuntimeError("429 rate limited")))
        result = engine.generate(DOMAIN, QUESTION)
        assert result.ok is False
        assert "429" in result.detail

    def test_unparseable_response_is_a_failure_not_a_crash(self):
        class Junk:
            parsed_output = {"not": "a payload"}
            usage = FakeUsage()

        result = ExplanationEngine(FakeClient(response=Junk())).generate(DOMAIN, QUESTION)
        assert result.ok is False

    def test_output_config_rejection_retries_without_the_effort_hint(self):
        # If a future SDK treats output_format and output_config as exclusive, every
        # candidate would silently drop to static explanations. One retry avoids that.
        class RejectOnce(FakeMessages):
            def parse(self, **kwargs):
                self.calls.append(kwargs)
                if "output_config" in kwargs:
                    raise ValueError(
                        "output_config may not be combined with output_format"
                    )
                return FakeResponse()

        client = FakeClient()
        client.messages = RejectOnce()
        result = ExplanationEngine(client).generate(DOMAIN, QUESTION)
        assert result.ok is True
        assert len(client.messages.calls) == 2
        assert "output_config" not in client.messages.calls[1]

    def test_unrelated_bad_request_is_not_retried(self):
        client = FakeClient(raises=ValueError("model: unknown model id"))
        result = ExplanationEngine(client).generate(DOMAIN, QUESTION)
        assert result.ok is False
        assert len(client.messages.calls) == 1  # no pointless second call


class TestUsageAccounting:
    def test_cache_reads_are_reported(self):
        engine = ExplanationEngine(FakeClient(response=FakeResponse(cache_read=1100)))
        assert engine.generate(DOMAIN, QUESTION).usage.cache_read_tokens == 1100

    def test_missing_usage_object_does_not_crash(self):
        class NoUsage:
            parsed_output = PAYLOAD
            usage = None

        result = ExplanationEngine(FakeClient(response=NoUsage())).generate(
            DOMAIN, QUESTION
        )
        assert result.ok is True
        assert result.usage.input_tokens is None


class TestBatchRequests:
    def test_requests_are_keyed_by_custom_id(self):
        # Batch results arrive in arbitrary order, so they must be keyed, never matched
        # by position.
        engine = ExplanationEngine(FakeClient())
        requests = engine.build_batch_requests(
            [("q-42", DOMAIN, QUESTION), ("q-43", DOMAIN, QUESTION)]
        )
        assert [r["custom_id"] for r in requests] == ["q-42", "q-43"]
        assert requests[0]["params"]["output_format"] is ExplanationPayload


# ---- route ---------------------------------------------------------------------------


class StubEngine:
    """Engine double for the route tests, counting generations."""

    model = "claude-opus-5"

    def __init__(self, ok: bool = True) -> None:
        self.ok = ok
        self.generate_calls = 0

    @property
    def available(self) -> bool:
        return self.ok

    def generate(self, domain, question):
        self.generate_calls += 1
        if not self.ok:
            return GenerationResult(ok=False, detail="stubbed outage")
        return GenerationResult(
            ok=True,
            payload=PAYLOAD,
            usage=GenerationUsage(
                input_tokens=1200, output_tokens=180, cache_read_tokens=900
            ),
        )


@pytest.fixture
def env(tmp_path):
    """TestClient over a seeded disposable DB with an injectable engine."""
    db_path = tmp_path / "explanations.db"
    engine = create_engine(
        f"sqlite:///{db_path}", connect_args={"check_same_thread": False}
    )
    TestingSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    from app import models as _models  # noqa: F401  registers metadata
    from app.database import Base, get_db
    from app.main import app
    from app.routers.explanations import get_engine
    import seed

    Base.metadata.create_all(engine)
    with TestingSession() as db:
        seed.seed_ccao_f(db)
        seed.seed_placeholder_tracks(db)
        seed.seed_dev_user(db)
        db.commit()

    stub = StubEngine()

    def override_get_db():
        db = TestingSession()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_engine] = lambda: stub

    with TestClient(app) as client:
        yield client, stub, TestingSession

    app.dependency_overrides.clear()


def _submitted_attempt(client, item_count: int = 8) -> int:
    """Generate a short exam and submit it blank, so every item is wrong."""
    gen = client.post(
        "/exams/generate",
        json={"track_code": "CCAO-F", "item_count": item_count, "seed": 7},
    ).json()
    attempt_id = gen["attempt_id"]
    client.post(f"/attempts/{attempt_id}/submit", json={"answers": []})
    return attempt_id


class TestExplanationRoute:
    def test_unknown_attempt_is_404(self, env):
        client, _, _ = env
        assert client.post("/attempts/999999/explanations").status_code == 404

    def test_unsubmitted_attempt_is_refused(self, env):
        # An explanation names the correct answer. Serving one mid-exam would hand back
        # the answer key that /exams/generate deliberately withholds.
        client, _, _ = env
        gen = client.post(
            "/exams/generate", json={"track_code": "CCAO-F", "item_count": 8, "seed": 3}
        ).json()
        r = client.post(f"/attempts/{gen['attempt_id']}/explanations")
        assert r.status_code == 409
        assert "not been submitted" in r.json()["detail"]

    def test_generates_for_every_wrong_answer(self, env):
        client, _, _ = env
        attempt_id = _submitted_attempt(client)
        body = client.post(f"/attempts/{attempt_id}/explanations").json()
        assert body["generated"] == 8  # blank submission: all eight are wrong
        assert len(body["explanations"]) == 8
        assert all(e["source"] == "ai" for e in body["explanations"])

    def test_identical_mistake_is_reused_not_regenerated(self, env):
        client, stub, _ = env
        attempt_id = _submitted_attempt(client)
        client.post(f"/attempts/{attempt_id}/explanations")
        first_pass = stub.generate_calls

        second = client.post(f"/attempts/{attempt_id}/explanations").json()
        assert second["reused"] == 8
        assert second["generated"] == 0
        assert stub.generate_calls == first_pass  # no second round of billing

    def test_force_regenerate_bypasses_the_cache(self, env):
        client, stub, _ = env
        attempt_id = _submitted_attempt(client)
        client.post(f"/attempts/{attempt_id}/explanations")
        before = stub.generate_calls
        body = client.post(
            f"/attempts/{attempt_id}/explanations", json={"force_regenerate": True}
        ).json()
        assert body["generated"] == 8
        assert stub.generate_calls == before + 8

    def test_question_ids_narrows_the_fan_out(self, env):
        client, stub, _ = env
        attempt_id = _submitted_attempt(client)
        listing = client.post(f"/attempts/{attempt_id}/explanations").json()
        one = listing["explanations"][0]["question_id"]

        stub.generate_calls = 0
        body = client.post(
            f"/attempts/{attempt_id}/explanations",
            json={"question_ids": [one], "force_regenerate": True},
        ).json()
        assert len(body["explanations"]) == 1
        assert stub.generate_calls == 1

    def test_outage_falls_back_to_the_authored_explanation(self, env):
        client, stub, _ = env
        stub.ok = False
        attempt_id = _submitted_attempt(client)
        r = client.post(f"/attempts/{attempt_id}/explanations")

        assert r.status_code == 200  # never an error to the candidate
        body = r.json()
        assert body["fell_back"] == 8
        assert all(e["source"] == "static" for e in body["explanations"])
        # The whole point of the fallback: there is still something to read.
        assert all(e["static_explanation"] for e in body["explanations"])

    def test_token_accounting_is_persisted(self, env):
        client, _, TestingSession = env
        attempt_id = _submitted_attempt(client)
        client.post(f"/attempts/{attempt_id}/explanations")

        from app.models import Explanation

        with TestingSession() as db:
            rows = db.scalars(select(Explanation)).all()
            assert rows, "expected persisted explanation rows"
            # Persisted so a production cache regression is measurable, not just assumed.
            assert all(r.cache_read_tokens == 900 for r in rows)
            assert all(r.model == "claude-opus-5" for r in rows)
