# Session 2 &mdash; Zia Tutor AI MCP Integration

**Date:** 2026-09-03
**Scope:** Additive. CCAO-F and CCDV-F untouched; both remain on the Claude API.
**Outcome:** Delivered. 287 tests passing (26 new).

---

## Probe result

An unauthenticated MCP Streamable HTTP `initialize` against
`https://zia-tutor-ai.panaversity.org/mcp`:

```
HTTP/1.1 401 Unauthorized
www-authenticate: Bearer error="invalid_token",
    error_description="Authentication required",
    resource_metadata=".../.well-known/oauth-protected-resource/mcp"
```

The advertised metadata names `https://auth.panaversity.org` as the authorization server,
`bearer_methods_supported: ["header"]`.

**The endpoint is an OAuth 2.0 protected resource, not a static-key API.** The brief
anticipated a simple token; that assumption does not hold. `CERTMASTERY_ZIA_MCP_TOKEN`
is therefore documented as a bearer *access token* issued by that authorization server.
Full OAuth client registration and refresh is the first item of the next session.

Curriculum slugs were confirmed separately through an already-authenticated client
against corpus generation 62, so the mapping table contains real, verified slugs rather
than guesses.

---

## Delivered

| Deliverable | Status |
|---|---|
| SPEC updated with a Zia section + probe result | Done (section 13) |
| `ZiaTutorClient` + tables + API routes | Done |
| `concept_curriculum_map` seeded with real slugs | Done (8 tags) |
| `AskZiaPanel` wired to the backend | Done |
| Mocked unit tests + manual verification script | Done (26 tests, `scripts/verify_zia_connection.py`) |
| README: token, fallback behaviour | Done |
| End-of-session summary | This file |

---

## Concept map (all slugs confirmed live)

| Track | Concept tag | Lesson |
|---|---|---|
| CCAR-F | `multi-agent-supervisor-worker` | `claude-agent-sdk-crash-course` |
| CCAR-F | `prompt-caching-economics` | `build-agents-crash-course` |
| CCAR-F | `claude-md-team-configuration` | `claude-code-teams-crash-course` |
| CCAR-F | `cli-args-config-flags` | `agentic-coding-crash-course` (0.7) |
| CCAR-P | `enterprise-rag-pipelines` | `postgres-ai-crash-course` |
| CCAR-P | `automated-eval-frameworks` | `trusting-the-checker-crash-course` |
| CCAR-P | `compliance-cost-latency-tradeoffs` | `choosing-agentic-architectures-crash-course` |
| CCAR-P | `agent-deployment-runtime` | `deploying-agents-crash-course` |

All six required tags are covered; two extras were added where the corpus clearly
supported them.

---

## Worth your attention

**1. A bug the probe caught.** The MCP SDK runs its transport inside an anyio TaskGroup,
so a plain 401 arrives wrapped in an `ExceptionGroup`. The first version of the client
read `str(exc)` on the group, lost the status code, and reported "unreachable" for what
was actually an auth failure &mdash; which would send an operator to debug DNS instead of
their token. The client now flattens nested exception groups before classifying, and the
verification script distinguishes the two cases with different exit codes.

**2. CCAR-F and CCAR-P have no question bank.** They were registered in Session 1 with no
domains and no items, so there is no review screen to hang the panel on. The panel is
therefore exercised by concept tag from the track detail page, and
`GET /api/zia/explain` accepts `track_code` + `concept_tag` alongside `question_id`. The
integration is complete and tested; what is missing is content, not machinery.

**3. Deliberate deviation.** The brief specified `ZIA_MCP_TOKEN`. It is implemented as
`CERTMASTERY_ZIA_MCP_TOKEN` to match the project-wide prefix introduced in Session 1
after an unprefixed variable resolved to an unrelated live database. Documented in the
README.

---

## Next steps

1. **OAuth client registration** against `auth.panaversity.org`, so a token can be
   obtained and refreshed rather than pasted. Until then the panel stays hidden.
2. **Session 3 (prepared, not started):** widen Ask Zia to all four tracks. Panaversity's
   PCAO-F Study Guide maps every CCAO-F domain to a crash course, and the slugs are
   already confirmed &mdash; `ai-prompting-2026`, `claude-chatgpt-101-crash-course`,
   `skills-connectors-crash-course`, `workflow-design-diagnosis-crash-course`,
   `governance-risk-responsible-use-crash-course`, `general-agents-web-crash-course`,
   `what-ai-actually-is-crash-course`, `ai-fluency-crash-course`,
   `code-you-never-write-crash-course`. CCDV-F maps well to `python-crash-course`,
   `loop-by-hand-crash-course` (Messages API), `structured-extraction-crash-course`
   (streaming/batch), `connector-native-apps` (tool schemas) and
   `claude-managed-agents-crash-course`. The track restriction in
   `frontend/app/tracks/[code]/page.tsx` becomes mapping-driven.
3. **Exam runner UI** and the Claude explanation engine, both still outstanding from the
   original Session 2 plan.
4. **CCAR-F / CCAR-P question banks**, which is what turns this panel from tested
   machinery into something a candidate actually meets.
