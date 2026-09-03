"""AI explanation engine -- targeted remediation for a specific wrong answer.

Spec section 6. The job is not to restate the correct answer; the candidate can already
read it on the review screen. The job is to explain *why the answer they actually chose*
was attractive and where the reasoning behind it breaks down. That is the difference
between a review screen a candidate skims and one that changes their next attempt.

Design notes
------------
**Graceful degradation is the contract, not a fallback.** Every seeded question carries a
``static_explanation``. With no ``CERTMASTERY_ANTHROPIC_API_KEY`` set, the platform still
explains every answer; this engine augments that. No route in the application may fail
because the AI layer is unavailable.

**Prompt caching.** The request is split into a stable prefix (platform role, pedagogy
rules, track blueprint, domain description) and a volatile suffix (the question and the
candidate's answer). The prefix carries ``cache_control``, so explanations within a domain
share it. Two constraints make this actually pay off, and both are easy to break silently:

1. The prefix must exceed the model's minimum cacheable length -- **512 tokens on
   claude-opus-5**. A shorter prefix is not an error; it simply never caches, and
   ``cache_read_input_tokens`` sits at 0 forever with no warning. ``test_explanations.py``
   guards the length so a well-meaning trim cannot quietly disable caching.
2. The prefix must be byte-identical between calls. Nothing here may interpolate a
   timestamp, a UUID, an attempt id, or anything with non-deterministic ordering.

``usage.cache_read_input_tokens`` is persisted per generation so the assumption is
measurable in production rather than merely asserted here.

**Model parameters.** ``thinking={"type": "adaptive"}`` -- ``budget_tokens`` is removed on
this model and returns 400. ``output_config={"effort": "medium"}`` because this is short
pedagogical prose, not hard reasoning; the default (``high``) would spend tokens on
deliberation the task does not need.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Sequence

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

# Matches the model default in app.config. Kept as a module constant so the batch path
# and the sync path cannot drift apart.
DEFAULT_MODEL = "claude-opus-5"

# Short pedagogical prose. Generous enough that a well-reasoned answer is never truncated
# mid-sentence, which would be worse than no explanation at all.
MAX_TOKENS = 2000


class ExplanationPayload(BaseModel):
    """Structured remediation for one wrong answer.

    Five separate fields rather than one prose blob, because the review screen renders
    them in distinct places and a single blob would force the frontend to parse prose.
    """

    why_correct: str = Field(
        description="Why the correct answer is correct, in two or three sentences."
    )
    why_your_answer_wrong: str = Field(
        description=(
            "Why the option the candidate actually selected is wrong. Name the specific "
            "misconception that makes it attractive, not just 'it is incorrect'."
        )
    )
    key_concept: str = Field(
        description="The single underlying concept being tested, in one sentence."
    )
    blueprint_link: str = Field(
        description="How this item connects to its blueprint domain and why it is weighted."
    )
    study_tip: str = Field(
        description="One concrete, actionable next step for this specific weakness."
    )


# The stable half of the prompt. Deliberately substantial: it has to clear 512 tokens to
# be cacheable at all on claude-opus-5, and the guidance genuinely improves output. Do not
# trim this without checking test_explanations.py::test_stable_prefix_is_long_enough.
PEDAGOGY_RULES = """\
You are the explanation engine for Claude Cert Mastery, an exam-preparation platform for \
the Anthropic Claude certification tracks. You write remediation for candidates who have \
just answered a practice question incorrectly.

Your reader has already seen the question, their own answer, and the correct answer. \
Restating which option was correct teaches them nothing. Your value is entirely in \
explaining the reasoning gap that produced their specific mistake.

How to write remediation that works:

1. Diagnose the specific error, not the general topic. A candidate who chose a \
distractor about prompt length when the item tested output validation has a different \
misconception from one who chose a distractor about model selection. Name the actual \
misconception that made their chosen option look right.

2. Treat every distractor as deliberately plausible. Certification distractors are \
authored to be attractive to a candidate holding a particular wrong mental model. \
Identify that mental model and correct it directly. Never write "this option is simply \
incorrect" -- if it were obviously incorrect it would not be on the exam.

3. Be concrete about the boundary. Most certification items turn on a distinction \
between two adjacent concepts: guidance versus enforcement, validation versus \
verification, configuration versus code, capability versus permission. State which \
boundary this item tests and which side each option falls on.

4. Respect what the candidate already knows. Assume a working practitioner, not a \
beginner. Do not define common terms. Do not pad with encouragement.

5. Ground every claim in the domain's published scope. If a claim does not follow from \
the blueprint domain described below, do not make it. Never invent Anthropic product \
behaviour, pricing, limits, or policy that is not established by the question itself.

6. Keep each field tight. Two or three sentences per field. A candidate reviewing a \
sixty-item exam will read this fifteen or twenty times; length is a tax on all of them.

7. The study tip must be actionable and specific to this weakness. "Review the domain" \
is not a study tip. "Practise distinguishing a Skill from a Connector by asking which \
one supplies capability and which supplies access" is.

Write in plain, direct prose. No headings, no bullet lists, no markdown formatting -- the \
platform renders each field into its own styled section. Address the candidate as "you"."""


@dataclass(frozen=True)
class GenerationUsage:
    """Token accounting for one generation, persisted to verify caching works."""

    input_tokens: int | None = None
    output_tokens: int | None = None
    cache_read_tokens: int | None = None


@dataclass(frozen=True)
class GenerationResult:
    """Outcome of one explanation request.

    ``ok=False`` is a normal, expected outcome -- no API key, a rate limit, a network
    blip -- and the caller falls back to the question's static explanation. It is never
    an error surfaced to the candidate.
    """

    ok: bool
    payload: ExplanationPayload | None = None
    usage: GenerationUsage = field(default_factory=GenerationUsage)
    detail: str = ""


@dataclass(frozen=True)
class QuestionContext:
    """Everything the engine needs about one item, decoupled from the ORM.

    Taking a plain dataclass rather than a ``Question`` row keeps the engine testable
    without a database and keeps the prompt-building logic honest about exactly which
    fields it depends on.
    """

    question_id: int
    external_id: str
    stem: str
    question_type: str
    options: list[tuple[int, str, str]]  # (option_id, label, text)
    correct_option_ids: set[int]
    selected_option_ids: set[int]

    def _labels_for(self, ids: set[int]) -> str:
        labels = [label for oid, label, _ in self.options if oid in ids]
        return ", ".join(labels) if labels else "(no option selected)"

    @property
    def correct_labels(self) -> str:
        return self._labels_for(self.correct_option_ids)

    @property
    def selected_labels(self) -> str:
        return self._labels_for(self.selected_option_ids)


@dataclass(frozen=True)
class DomainContext:
    """The stable, per-domain half of the prompt."""

    track_code: str
    track_name: str
    domain_code: str
    domain_name: str
    domain_description: str
    weight_bps: int
    # (code, name, weight_bps) for every domain in the track, in published order.
    blueprint: list[tuple[str, str, int]] = field(default_factory=list)

    @property
    def cache_key(self) -> str:
        """Identifies the prefix. Two contexts with the same key share a cache entry."""
        return f"{self.track_code}:{self.domain_code}"


def build_system_blocks(domain: DomainContext) -> list[dict[str, Any]]:
    """Build the cacheable system prefix for a domain.

    Returns Anthropic system content blocks with ``cache_control`` on the final block, so
    everything above it is cached. Byte-stability matters more than elegance here: the
    blueprint rows are rendered from an explicitly ordered list rather than a dict, and
    nothing time-varying is interpolated.
    """
    blueprint_rows = "\n".join(
        f"  {code:<6} {name:<44} {bps / 100:>5.0f}%" for code, name, bps in domain.blueprint
    )

    context = f"""\
CERTIFICATION TRACK
  {domain.track_code} -- {domain.track_name}

PUBLISHED BLUEPRINT (domain, name, exam weight)
{blueprint_rows}

THE DOMAIN THIS ITEM BELONGS TO
  Code:   {domain.domain_code}
  Name:   {domain.domain_name}
  Weight: {domain.weight_bps / 100:.0f}% of the exam

  Scope of this domain:
  {domain.domain_description}

Every explanation you write is for an item drawn from the domain above. Anchor the \
blueprint_link field in that domain's scope and its weight, so the candidate understands \
not just what they got wrong but how much this area will cost them on the real exam."""

    return [
        {"type": "text", "text": PEDAGOGY_RULES},
        # The breakpoint sits on the last stable block: everything from the pedagogy
        # rules through the domain description is cached, and only the question and the
        # candidate's answer below it are re-read on each call.
        {"type": "text", "text": context, "cache_control": {"type": "ephemeral"}},
    ]


def build_user_prompt(question: QuestionContext) -> str:
    """Build the volatile suffix: this item and this candidate's answer.

    Everything that varies per request lives here, below the cache breakpoint.
    """
    options = "\n".join(f"  {label}. {text}" for _, label, text in question.options)
    kind = (
        "multiple response (more than one option is correct)"
        if question.question_type == "mr"
        else "multiple choice (exactly one option is correct)"
    )

    return f"""\
QUESTION ({question.external_id}, {kind})

{question.stem}

OPTIONS
{options}

CORRECT ANSWER: {question.correct_labels}
THE CANDIDATE SELECTED: {question.selected_labels}

Write remediation for this candidate's specific mistake."""


class ExplanationEngine:
    """Generates explanations through the Claude API.

    Construct via :func:`build_engine`, which wires it from application settings. An
    engine with no client is a valid, fully supported state: ``available`` is False and
    every call returns ``ok=False`` so the caller serves the static explanation.
    """

    def __init__(self, client: Any | None, model: str = DEFAULT_MODEL) -> None:
        self._client = client
        self.model = model

    @property
    def available(self) -> bool:
        return self._client is not None

    def _request_kwargs(self, domain: DomainContext, question: QuestionContext) -> dict:
        return {
            "model": self.model,
            "max_tokens": MAX_TOKENS,
            "system": build_system_blocks(domain),
            "messages": [{"role": "user", "content": build_user_prompt(question)}],
            # budget_tokens is removed on this model and returns 400; adaptive thinking
            # is the current API and lets the model decide how much deliberation an item
            # actually warrants.
            "thinking": {"type": "adaptive"},
            # Short pedagogical prose, not hard reasoning. The default effort (high)
            # would spend tokens deliberating a task that does not need it.
            "output_config": {"effort": "medium"},
        }

    def generate(
        self, domain: DomainContext, question: QuestionContext
    ) -> GenerationResult:
        """Generate one explanation synchronously.

        Never raises. Every failure mode -- unconfigured, rate limited, network down,
        malformed response -- returns ``ok=False`` with a diagnostic detail, and the
        caller serves the static explanation instead.
        """
        if self._client is None:
            return GenerationResult(ok=False, detail="no ANTHROPIC_API_KEY configured")

        kwargs = self._request_kwargs(domain, question)
        try:
            response = self._parse(kwargs)
        except Exception as exc:  # noqa: BLE001 - see docstring: never raise
            detail = f"{type(exc).__name__}: {exc}"
            logger.warning(
                "Explanation generation failed for %s: %s", question.external_id, detail
            )
            return GenerationResult(ok=False, detail=detail)

        payload = getattr(response, "parsed_output", None)
        if not isinstance(payload, ExplanationPayload):
            return GenerationResult(
                ok=False, detail="response did not parse into an ExplanationPayload"
            )

        return GenerationResult(ok=True, payload=payload, usage=_usage_of(response))

    def _parse(self, kwargs: dict) -> Any:
        """Call messages.parse, retrying once without ``output_config`` if rejected.

        ``parse()`` sets ``output_config.format`` itself from ``output_format``. Passing
        our own ``output_config`` for the effort setting is the documented way to control
        effort, but if a future SDK version treats the two as mutually exclusive the
        request would 400 and every candidate would silently drop to static explanations.
        Retrying once without the effort hint costs one extra round trip in that single
        case and keeps AI explanations working; the narrow message check ensures a
        genuine bad request still fails loudly.
        """
        try:
            return self._client.messages.parse(output_format=ExplanationPayload, **kwargs)
        except Exception as exc:  # noqa: BLE001 - re-raised unless it is this one case
            message = str(exc).lower()
            if "output_config" not in message and "effort" not in message:
                raise
            logger.warning("output_config rejected; retrying without effort hint: %s", exc)
            retry = {k: v for k, v in kwargs.items() if k != "output_config"}
            return self._client.messages.parse(output_format=ExplanationPayload, **retry)

    # ---- bulk generation ------------------------------------------------------------

    def build_batch_requests(
        self, items: Sequence[tuple[str, DomainContext, QuestionContext]]
    ) -> list[dict[str, Any]]:
        """Shape a post-submission fan-out for the Batch API (50% cost, spec section 6).

        ``items`` carries an explicit ``custom_id`` per entry because batch results come
        back in arbitrary order and must be keyed, never matched by position.

        Returned as plain dicts rather than SDK ``Request`` objects so the batch payload
        can be built and asserted without importing the SDK -- which matters because the
        SDK is an optional dependency at runtime here.
        """
        requests: list[dict[str, Any]] = []
        for custom_id, domain, question in items:
            params = self._request_kwargs(domain, question)
            params["output_format"] = ExplanationPayload
            requests.append({"custom_id": custom_id, "params": params})
        return requests


def _usage_of(response: Any) -> GenerationUsage:
    """Read token accounting off a response, tolerating a partial usage object."""
    usage = getattr(response, "usage", None)
    if usage is None:
        return GenerationUsage()
    return GenerationUsage(
        input_tokens=getattr(usage, "input_tokens", None),
        output_tokens=getattr(usage, "output_tokens", None),
        cache_read_tokens=getattr(usage, "cache_read_input_tokens", None),
    )


def build_engine() -> ExplanationEngine:
    """Construct an engine from application settings.

    An absent key, or an absent ``anthropic`` package, yields an engine with
    ``available == False`` rather than an exception. Running the platform without AI
    explanations is a supported configuration, not a degraded one.
    """
    from app.config import settings

    if not settings.anthropic_api_key:
        return ExplanationEngine(None, settings.claude_model)

    try:
        import anthropic
    except ImportError as exc:  # pragma: no cover - dependency guard
        logger.warning("anthropic SDK not installed; serving static explanations: %s", exc)
        return ExplanationEngine(None, settings.claude_model)

    return ExplanationEngine(
        anthropic.Anthropic(api_key=settings.anthropic_api_key),
        settings.claude_model,
    )
