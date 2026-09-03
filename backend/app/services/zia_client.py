"""Client for the Zia Tutor AI MCP server.

Zia teaches The AI Agent Factory curriculum and maintains a per-learner mastery record.
Cert Mastery uses it as an optional companion tutor: the Claude API explanation engine
remains the default everywhere, and Zia augments it where a question's concept maps onto
a real Agent Factory lesson.

Transport
---------
MCP Streamable HTTP at ``https://zia-tutor-ai.panaversity.org/mcp``.

A probe on 2026-09-03 returned::

    HTTP/1.1 401 Unauthorized
    www-authenticate: Bearer error="invalid_token",
        resource_metadata=".../.well-known/oauth-protected-resource/mcp"

and that metadata names ``https://auth.panaversity.org`` as the authorization server
with ``bearer_methods_supported: ["header"]``. So the endpoint is an OAuth 2.0 protected
resource, not a static-key API. ``CERTMASTERY_ZIA_MCP_TOKEN`` therefore carries a bearer
*access token* issued by that authorization server; it is sent as ``Authorization:
Bearer <token>``. Full OAuth client registration and refresh is out of scope here and
noted in the spec.

Failure policy
--------------
Every public method returns a result object rather than raising. The tutor is a
companion feature: if it is unreachable, unauthorized or slow, the panel hides itself
and the candidate still gets the Claude-generated explanation. A tutor outage must never
degrade the exam flow.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

DEFAULT_ENDPOINT = "https://zia-tutor-ai.panaversity.org/mcp"
DEFAULT_TIMEOUT_SECONDS = 20.0


class ZiaUnavailable(Exception):
    """Raised internally when the tutor cannot be reached; never escapes the service."""


@dataclass(frozen=True)
class ZiaStatus:
    """Outcome of a connectivity probe."""

    reachable: bool
    authenticated: bool
    detail: str
    server_name: str | None = None
    tool_names: list[str] = field(default_factory=list)

    @property
    def usable(self) -> bool:
        return self.reachable and self.authenticated


@dataclass(frozen=True)
class LessonHit:
    """One search hit, normalised to what the panel needs to render a citation."""

    slug: str
    heading_path: str
    content: str
    url: str | None
    score: float = 0.0


@dataclass(frozen=True)
class ZiaResult:
    """Envelope for any tutor call. `ok=False` means the panel should hide itself."""

    ok: bool
    detail: str = ""
    hits: list[LessonHit] = field(default_factory=list)
    lesson_text: str | None = None
    lesson_title: str | None = None
    lesson_url: str | None = None
    session_handle: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)


def _flatten_exception(exc: BaseException) -> list[BaseException]:
    """Flatten nested ExceptionGroups into a list of leaf exceptions.

    The MCP SDK runs its transport inside an anyio TaskGroup, so a plain HTTP 401
    surfaces as ExceptionGroup("unhandled errors in a TaskGroup"). Reading str(exc) on
    the group loses the status code entirely and makes an auth failure look like a
    network failure -- which sends an operator to debug DNS instead of their token.
    """
    leaves: list[BaseException] = []
    stack: list[BaseException] = [exc]
    while stack:
        current = stack.pop()
        nested = getattr(current, "exceptions", None)
        if nested:
            stack.extend(nested)
        else:
            leaves.append(current)
            cause = current.__cause__ or current.__context__
            if cause is not None and cause not in leaves:
                stack.append(cause)
    return leaves


def _describe_failure(exc: BaseException) -> tuple[bool, str]:
    """Return (looks_unauthorized, human-readable detail) for a transport failure."""
    leaves = _flatten_exception(exc)
    messages = [f"{type(e).__name__}: {e}" for e in leaves if str(e) or e is not exc]
    blob = " | ".join(messages).lower()

    unauthorized = any(
        marker in blob
        for marker in ("401", "unauthorized", "invalid_token", "authentication required")
    )
    detail = " | ".join(messages) or f"{type(exc).__name__}: {exc}"
    return unauthorized, detail


def _text_of(call_result: Any) -> str:
    """Concatenate the text blocks of an MCP CallToolResult."""
    parts: list[str] = []
    for block in getattr(call_result, "content", []) or []:
        text = getattr(block, "text", None)
        if text:
            parts.append(text)
    return "\n".join(parts)


def _payload_of(call_result: Any) -> dict[str, Any]:
    """Best-effort structured payload from an MCP tool result.

    Prefers ``structuredContent`` when the server provides it and falls back to parsing
    the text block as JSON, since MCP servers legitimately do either.
    """
    import json

    structured = getattr(call_result, "structuredContent", None)
    if isinstance(structured, dict):
        return structured

    text = _text_of(call_result)
    if not text:
        return {}
    try:
        parsed = json.loads(text)
    except (ValueError, TypeError):
        return {"text": text}
    return parsed if isinstance(parsed, dict) else {"result": parsed}


class ZiaTutorClient:
    """Thin, failure-tolerant wrapper over the Zia Tutor AI MCP server.

    One MCP session is opened per call rather than held open. Cert Mastery traffic is
    bursty and request-scoped, and a long-lived session would have to be kept healthy
    across FastAPI workers for a feature that is optional by design. Correctness and
    simplicity win over shaving a handshake.
    """

    def __init__(
        self,
        endpoint: str = DEFAULT_ENDPOINT,
        token: str | None = None,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        self.endpoint = endpoint
        self.token = token
        self.timeout = timeout

    @property
    def configured(self) -> bool:
        """Whether a credential is present.

        The probe showed the endpoint rejects anonymous calls, so without a token there
        is no point issuing one.
        """
        return bool(self.token)

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.token}"} if self.token else {}

    async def _call(self, tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """Open a session, call one tool, return its payload.

        Raises ZiaUnavailable on any transport, auth or protocol failure. Callers
        translate that into an `ok=False` result.
        """
        try:
            from mcp import ClientSession
            from mcp.client.streamable_http import streamablehttp_client
        except ImportError as exc:  # pragma: no cover - dependency guard
            raise ZiaUnavailable(f"mcp SDK not installed: {exc}") from exc

        try:
            async with streamablehttp_client(
                self.endpoint, headers=self._headers(), timeout=self.timeout
            ) as (read, write, _):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    result = await session.call_tool(tool, arguments)
                    if getattr(result, "isError", False):
                        raise ZiaUnavailable(f"{tool} returned an error result")
                    return _payload_of(result)
        except ZiaUnavailable:
            raise
        except Exception as exc:
            # Deliberately broad: transport, TLS, auth, protocol and cancellation all
            # collapse to the same product decision -- hide the panel. The detail is
            # still unwrapped so logs name the real cause rather than "TaskGroup".
            _, detail = _describe_failure(exc)
            raise ZiaUnavailable(f"{tool} failed: {detail}") from exc

    # ---- connectivity ------------------------------------------------------------

    async def probe(self) -> ZiaStatus:
        """Initialize a session and list tools, without calling any of them.

        Distinguishes "unreachable" from "reachable but unauthorized", because the two
        need different operator responses: a network/DNS problem versus a missing or
        expired token.
        """
        try:
            from mcp import ClientSession
            from mcp.client.streamable_http import streamablehttp_client
        except ImportError as exc:  # pragma: no cover
            return ZiaStatus(False, False, f"mcp SDK not installed: {exc}")

        try:
            async with streamablehttp_client(
                self.endpoint, headers=self._headers(), timeout=self.timeout
            ) as (read, write, _):
                async with ClientSession(read, write) as session:
                    init = await session.initialize()
                    tools = await session.list_tools()
                    names = [t.name for t in getattr(tools, "tools", [])]
                    server = getattr(getattr(init, "serverInfo", None), "name", None)
                    return ZiaStatus(True, True, "ok", server, names)
        except Exception as exc:
            unauthorized, detail = _describe_failure(exc)
            return ZiaStatus(
                reachable=unauthorized,  # a 401 proves the server answered
                authenticated=False,
                detail=(
                    f"reachable but unauthorized - check CERTMASTERY_ZIA_MCP_TOKEN ({detail})"
                    if unauthorized
                    else f"unreachable: {detail}"
                ),
            )

    # ---- learner record ----------------------------------------------------------

    async def begin_session(
        self, goal: str | None = None, note: str | None = None
    ) -> ZiaResult:
        """Open a tutoring session. Called on the first panel open of a visit."""
        args = {k: v for k, v in (("goal", goal), ("note", note)) if v}
        try:
            payload = await self._call("begin_session", args)
        except ZiaUnavailable as exc:
            return ZiaResult(ok=False, detail=str(exc))
        return ZiaResult(
            ok=True,
            session_handle=str(payload.get("session_id") or payload.get("handle") or ""),
            raw=payload,
        )

    async def open_student_record(
        self, course: str | None = None, observation: str | None = None
    ) -> ZiaResult:
        """Resume an existing learner. Called on later opens within the same visit."""
        args = {
            k: v for k, v in (("course", course), ("observation", observation)) if v
        }
        try:
            payload = await self._call("open_student_record", args)
        except ZiaUnavailable as exc:
            return ZiaResult(ok=False, detail=str(exc))
        return ZiaResult(ok=True, raw=payload)

    async def update_student_record(
        self,
        verb: str,
        course: str | None = None,
        step: str | None = None,
        note: str | None = None,
        evidence: str | None = None,
        attempt: str | None = None,
        correction: str | None = None,
    ) -> ZiaResult:
        """Record an observation about the learner.

        Cert Mastery only ever reports what it actually saw -- the candidate's own answer
        to a follow-up check -- and passes it as `attempt` with the evidence basis in
        `evidence`. It never asserts mastery the platform did not observe.
        """
        args = {
            k: v
            for k, v in (
                ("verb", verb),
                ("course", course),
                ("step", step),
                ("note", note),
                ("evidence", evidence),
                ("attempt", attempt),
                ("correction", correction),
            )
            if v
        }
        try:
            payload = await self._call("update_student_record", args)
        except ZiaUnavailable as exc:
            return ZiaResult(ok=False, detail=str(exc))
        return ZiaResult(ok=True, raw=payload)

    # ---- curriculum --------------------------------------------------------------

    async def search(
        self, query: str, grain: str = "passage", k: int = 5
    ) -> ZiaResult:
        """Search the Agent Factory corpus for a concept.

        An abstention -- the corpus genuinely not covering the query -- is returned as
        `ok=True` with no hits, not as a failure. The panel then hides itself rather
        than showing an error, because "the curriculum does not cover this" is a correct
        answer and not a malfunction.
        """
        try:
            payload = await self._call(
                "search_agent_factory", {"query": query, "grain": grain, "k": k}
            )
        except ZiaUnavailable as exc:
            return ZiaResult(ok=False, detail=str(exc))

        if payload.get("abstained"):
            return ZiaResult(ok=True, detail="corpus does not cover this concept")

        hits = [
            LessonHit(
                slug=str(h.get("slug", "")),
                heading_path=str(h.get("heading_path", "")),
                content=str(h.get("content", "")),
                url=h.get("url"),
                score=float(h.get("rrf_score") or 0.0),
            )
            for h in payload.get("hits", [])
            if isinstance(h, dict)
        ]
        return ZiaResult(ok=True, hits=hits, raw=payload)

    async def read_lesson(
        self, slug: str, section: str | None = None
    ) -> ZiaResult:
        """Read one lesson (or one section of it) byte-exact."""
        args: dict[str, Any] = {"slug": slug}
        if section:
            args["section"] = section
        try:
            payload = await self._call("read_agent_factory_lesson", args)
        except ZiaUnavailable as exc:
            return ZiaResult(ok=False, detail=str(exc))

        return ZiaResult(
            ok=True,
            lesson_text=payload.get("text"),
            lesson_title=payload.get("title"),
            lesson_url=payload.get("url"),
            raw=payload,
        )

    async def outline(self, node: str | None = None) -> ZiaResult:
        """Browse the curriculum tree. Used by the seeding script, not the panel."""
        try:
            payload = await self._call(
                "outline_agent_factory", {"node": node} if node else {}
            )
        except ZiaUnavailable as exc:
            return ZiaResult(ok=False, detail=str(exc))
        return ZiaResult(ok=True, raw=payload)


def build_client() -> ZiaTutorClient:
    """Construct a client from application settings."""
    from app.config import settings

    return ZiaTutorClient(
        endpoint=settings.zia_mcp_endpoint,
        token=settings.zia_mcp_token,
        timeout=settings.zia_mcp_timeout_seconds,
    )


def run_sync(coro: Any) -> Any:
    """Run a coroutine from sync FastAPI handler code.

    The routes are sync (matching the rest of the codebase, which is sync SQLAlchemy),
    while the MCP SDK is async-only. FastAPI runs sync handlers in a worker thread with
    no running event loop, so a fresh loop here is safe.
    """
    return asyncio.run(coro)
