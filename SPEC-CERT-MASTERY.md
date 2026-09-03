# SPEC — Claude Cert Mastery

**Version:** 1.0 (Session 1, Foundation)
**Status:** Foundation implemented; Integration/Advanced/Validation pending
**Location:** `D:\ClaudeCertMastery`

---

## 1. Problem

Candidates preparing for the Claude certification tracks have no way to sit a practice exam
that mirrors the published blueprint's domain weighting, and no way to learn whether they would
clear the 720/1000 pass line. Generic quiz tools ignore blueprint weights entirely, so a
candidate can score 85% on a bank that over-samples easy domains and still fail the real exam.

Claude Cert Mastery closes that gap: blueprint-faithful exam composition, a scaled score on the
real 100-1000 band, per-domain mastery, AI-generated remediation for wrong answers, and
spaced-repetition drilling of weak domains.

---

## 2. Certification tracks (ground truth)

| Track | Name | Items | Format | Duration | Pass | Price | Validity |
|---|---|---|---|---|---|---|---|
| `CCAO-F` | Claude Certified AI Operator - Foundation | 60 | MCQ / MR | 120 min | 720/1000 | $99 | 12 months |
| `CCDV-F` | Claude Certified Developer - Foundation | TBD | MCQ / MR | TBD | 720/1000 | TBD | 12 months |
| `CCAR-F` | Claude Certified Architect - Foundation | TBD | MCQ / MR | TBD | 720/1000 | TBD | 12 months |
| `CCAR-P` | Claude Certified Architect - Professional | TBD | MCQ / MR | TBD | 720/1000 | TBD | 12 months |

### 2.1 CCAO-F blueprint (exact weights)

| # | Domain | Code | Weight | `weight_bps` | Items @ 60 |
|---|---|---|---|---|---|
| 1 | Prompting & Task Execution | `PTE` | 14% | 1400 | 8 |
| 2 | Output Evaluation & Validation | `OEV` | 21% | 2100 | 13 |
| 3 | Product & Model Selection | `PMS` | 12% | 1200 | 7 |
| 4 | Workflow Integration & Solution Design | `WISD` | 16% | 1600 | 10 |
| 5 | Configuration & Knowledge Management | `CKM` | 12% | 1200 | 7 |
| 6 | Governance, Risk & Responsible Use | `GRR` | 15% | 1500 | 9 |
| 7 | Troubleshooting & Optimization | `TRO` | 10% | 1000 | 6 |
| | **Total** | | **100%** | **10000** | **60** |

Item counts are the output of the largest-remainder allocator (section 5.2), not hand-rounded.

### 2.2 Subject scope for the other three tracks

Recorded now so Session 2+ authoring has a target. No questions authored yet.

- **CCDV-F** - Python/TypeScript coding against the Messages API; streaming and batch;
  tool schema design; agentic AI fundamentals.
- **CCAR-F** - multi-agent supervisor/worker topologies; prompt-caching economics;
  `CLAUDE.md` configuration; CLI arguments; file-naming conventions; config flags.
- **CCAR-P** - enterprise RAG pipelines; automated eval frameworks; compliance, cost and
  latency trade-offs.

---

## 3. Product scope (v1)

| Feature | Session |
|---|---|
| Track selector (all four tracks) | 1 |
| Domain-weighted exam generator | 1 |
| Scaled scoring engine (100-1000, pass 720) | 1 |
| CCAO-F seed question bank (112 items) | 1 |
| AI explanation engine (Claude API) | 2 |
| Exam runner UI (timer, navigation, flag-for-review) | 2 |
| Progress dashboard (per-track, per-domain mastery) | 3 |
| Spaced-repetition flashcards (SM-2) | 3 |
| Auth | 3 |
| Deployment (Vercel + Render) | 4 |

---

## 4. Architecture

```
Next.js (App Router, TS, Tailwind v4, Zustand)  --HTTP-->  FastAPI
        Vercel                                              Render
                                                               |
                                                  SQLAlchemy 2.0 ORM
                                                               |
                                         PostgreSQL (prod) / SQLite (dev)
                                                               |
                                                 Claude API (Session 2)
```

Frontend and backend are independently deployable and share no code. The contract is the
OpenAPI schema FastAPI emits at `/docs`.

---

## 5. Core algorithms

### 5.1 Scaled scoring

Three anchors define a piecewise-linear map from raw proportion to the 100-1000 scale:

| Raw | Scaled |
|---|---|
| 0% | 100 |
| `pass_raw_threshold` (default 70%) | **720** |
| 100% | 1000 |

```
raw <= pass_raw :  scaled = 100 + (raw / pass_raw) * (720 - 100)
raw >  pass_raw :  scaled = 720 + ((raw - pass_raw) / (1 - pass_raw)) * (1000 - 720)
```

Rounded half-up to an integer, then clamped to [100, 1000].

Properties this guarantees, all covered by tests:

- Monotonic non-decreasing in `raw`.
- `scaled >= 720` **iff** `raw >= pass_raw` - the pass line is exact by construction, never an
  artefact of rounding.
- Endpoints are exact: 0 -> 100, 100% -> 1000.

Worked values on a 60-item CCAO-F exam:

| Correct | Raw | Scaled | Result |
|---|---|---|---|
| 0 | 0.0% | 100 | fail |
| 30 | 50.0% | 543 | fail |
| 41 | 68.3% | 705 | fail |
| **42** | **70.0%** | **720** | **pass** |
| 45 | 75.0% | 767 | pass |
| 54 | 90.0% | 907 | pass |
| 60 | 100.0% | 1000 | pass |

Because 42/60 is exactly 70%, the CCAO-F pass boundary falls on a whole item count.

### 5.2 Blueprint allocation (largest remainder)

Converting percentage weights into whole item counts is an apportionment problem: 14% of 60 is
8.4 items. Rounding each domain independently yields 58 or 61 items, not 60.

The largest-remainder (Hamilton) method:

1. Exact quota per domain: `q_i = weight_bps_i * n / 10000`.
2. Floor each to get a base allocation.
3. Distribute the `n - sum(floors)` remaining items to the domains with the largest fractional
   remainders, breaking ties by `position` so the result is deterministic.

Verified totals: n = 60 gives `8/13/7/10/7/9/6`; n = 30, 100 and 7 all total exactly n. At
n = 100 the allocation reproduces the published percentages exactly.

### 5.3 Item grading

- **MCQ** - exactly one selected option, and it is the correct one.
- **MR** - set equality between selected and correct option IDs (see D-4).
- `partial_credit` in [0, 1] is recorded for every item:
  `max(0, (|selected AND correct| - |selected MINUS correct|) / |correct|)`.
  Over-selection is penalised, so selecting every option scores 0, not 1.

### 5.4 Exam generation

Seeded `random.Random(seed)`, so an exam is reproducible from its stored seed. Each domain's
quota is drawn without replacement from that domain's active question bank.

**Shortfall policy.** If a domain holds fewer active questions than its quota, the deficit is
redistributed to domains with surplus, proportional to weight, and the attempt records a
`composition_warning`. The exam is never silently short, and the deviation is always visible.

### 5.5 Spaced repetition (Session 3)

SM-2. Each missed question becomes a flashcard with `ease_factor` (initial 2.5),
`interval_days`, `repetitions`, `lapses` and `due_at`. Grade 0-5 from the review; grades below 3
reset `repetitions` to 0 and increment `lapses`. `partial_credit` from 5.3 seeds the initial
grade so a near-miss on an MR item is not treated the same as a blank.

---

## 6. AI explanation engine (Session 2)

Generates targeted remediation for the specific mistake a candidate made, not a generic
restatement of the correct answer.

- **Model** `claude-opus-5` - $5.00 / $25.00 per MTok, 1M context.
- **Thinking** `{"type": "adaptive"}`. `budget_tokens` is not used; it returns 400 on this model.
- **Effort** `output_config={"effort": "medium"}` - short pedagogical output, not hard reasoning.
- **Structured output** via `client.messages.parse(...)` against a Pydantic `ExplanationPayload`:
  `why_correct`, `why_your_answer_wrong`, `key_concept`, `blueprint_link`, `study_tip`.
- **Prompt caching** - the stable prefix (track blueprint, domain description, pedagogy rules)
  carries `cache_control={"type": "ephemeral"}`; the volatile suffix is the question and the
  candidate's answer. Explanations within a domain share the prefix, so cache hits are the norm.
  `usage.cache_read_input_tokens` is persisted per call to verify this in production.
- **Deduplication** - `explanations` is keyed by `(question_id, selected_option_signature)`.
  The same wrong answer is generated once and reused for every candidate who makes it.
- **Bulk generation** - post-submission fan-out over all wrong answers goes through the Batch
  API at 50% cost.
- **Graceful degradation** - every seeded question carries a `static_explanation`. With no
  `ANTHROPIC_API_KEY` set, the platform still explains every answer; the AI layer augments it.

---

## 7. Data model

Ten tables.

| Table | Purpose | Key columns |
|---|---|---|
| `users` | Candidate identity | `email`, `display_name` |
| `tracks` | Certification tracks | `code`, `item_count`, `duration_minutes`, `pass_scaled_score`, `pass_raw_threshold`, `price_usd`, `validity_months`, `is_seeded` |
| `domains` | Blueprint domains | `track_id`, `code`, `weight_bps`, `position` |
| `questions` | Item bank | `domain_id`, `external_id`, `stem`, `question_type`, `difficulty`, `static_explanation`, `is_active` |
| `answer_options` | Options per item | `question_id`, `label`, `text`, `is_correct`, `position` |
| `exam_attempts` | One sitting | `user_id`, `track_id`, `mode`, `status`, `seed`, `scaled_score`, `passed`, `composition_warning` |
| `attempt_items` | Item-level result | `attempt_id`, `question_id`, `domain_id`, `selected_option_ids`, `is_correct`, `partial_credit`, `time_spent_seconds` |
| `attempt_domain_scores` | Per-domain rollup | `attempt_id`, `domain_id`, `correct`, `total`, `percentage`, `mastery_band` |
| `explanations` | AI remediation cache | `question_id`, `selected_option_signature`, `model`, payload columns, token counts |
| `flashcards` | SM-2 state | `user_id`, `question_id`, `ease_factor`, `interval_days`, `repetitions`, `due_at`, `lapses` |

`attempt_domain_scores` is a deliberate denormalisation: the dashboard reads per-domain mastery
on every page load, and recomputing it from `attempt_items` on each read would not scale.

---

## 8. API surface

| Method | Path | Session |
|---|---|---|
| `GET` | `/health` | 1 |
| `GET` | `/tracks` | 1 |
| `GET` | `/tracks/{code}` | 1 |
| `GET` | `/tracks/{code}/blueprint` | 1 |
| `POST` | `/exams/generate` | 1 |
| `GET` | `/attempts/{id}` | 1 |
| `POST` | `/attempts/{id}/submit` | 1 |
| `POST` | `/attempts/{id}/explanations` | 2 |
| `GET` | `/dashboard/{track_code}` | 3 |
| `GET` `POST` | `/flashcards/due`, `/flashcards/{id}/review` | 3 |

---

## 9. Testing strategy

Pure functions (`scoring.py`, `blueprint.py`) hold the logic worth testing and take no database
dependency, so they are tested directly with no fixtures.

| Suite | Covers |
|---|---|
| `test_scoring.py` | anchors, monotonicity, clamping, rounding, the 719/720 boundary, threshold config |
| `test_blueprint.py` | totals at n = 60/30/100/7, published-weight fidelity, determinism, validation |
| `test_grading.py` | MCQ, MR set equality, partial credit, over-selection, unanswered |
| `test_exam_generator.py` | quotas, no duplicates, seed reproducibility, shortfall redistribution |
| `test_seed_integrity.py` | one correct option per MCQ, 2+ per MR, 15+ per domain, unique `external_id` |
| `test_api.py` | health, track listing, generate/submit round trip |

Session 1 target ~45 tests. Project target 80+ by Session 4.

---

## 10. Stack

| Layer | Choice |
|---|---|
| Frontend | Next.js 16 (App Router), TypeScript, Tailwind v4, Zustand |
| Backend | FastAPI, SQLAlchemy 2.0, Alembic, Pydantic v2 |
| Database | PostgreSQL (prod) / SQLite (dev) |
| AI | Claude API - `claude-opus-5` via the `anthropic` Python SDK |
| Deploy | Vercel (frontend), Render (backend) |

---

## 11. Design decisions

Recorded because each was a judgement call with a live alternative.

**D-1 - Blueprint weights govern composition, not scoring.**
Every item contributes equally to the raw score. The weight decides how many items are *drawn*
from a domain, and that weighting is therefore already present in the item mix. Applying the
weights again at scoring time would count them twice. This matches how real certification exams
score: per-domain feedback is diagnostic, not a scoring multiplier.

**D-2 - Piecewise-linear scaling on three anchors.**
A single linear map from 0-100% onto 100-1000 would put the pass line at a raw 68.9%, not 70%.
Anchoring at the pass point guarantees `scaled >= 720` exactly when `raw >= pass_raw`. The 70%
raw threshold is an assumption - Anthropic does not publish the raw-to-scaled mapping - and is
stored per track in `tracks.pass_raw_threshold` so it can be corrected without touching code.

**D-3 - Weights as integer basis points.**
`weight_bps = 1400` rather than `0.14`. Seven floats summing to 1.0 is not exactly representable;
seven integers summing to 10000 is. Tests assert equality with no epsilon.

**D-4 - MR items are all-or-nothing for the scaled score.**
Real exams award no partial credit on multi-response items, and awarding it here would inflate a
candidate's sense of readiness - the failure mode that matters most in exam prep. `partial_credit`
is still computed and stored, feeding the mastery dashboard and the SM-2 initial grade, where a
finer signal is genuinely useful. The policy is isolated behind
`MR_PARTIAL_CREDIT_COUNTS_TOWARD_SCORE` in `scoring.py`; flipping it is a one-line change.

**D-5 - psycopg 3 rather than psycopg2.**
Local Python is 3.14.7; `psycopg2-binary` wheels lag new interpreter releases. `psycopg[binary]`
is fully supported by SQLAlchemy 2.0. This deviates from the sibling `tradeflow` project.

**D-6 - Seed content in YAML, not Python.**
112 prose-heavy questions embedded in `.py` files would be unreviewable and merge-hostile. YAML
keeps them diffable and lets a non-engineer author items. `PyYAML` is the only dependency added
for this.

**D-7 - No auth in Session 1.**
A single seeded dev user. Every table that will eventually need an owner already carries
`user_id`, so adding auth in Session 3 requires no schema migration.

**D-8 - Session 1 frontend is scaffold + track selector only.**
The exam runner needs the timer, navigation, flag-for-review and submission flow to be worth
anything. A half-built runner would misrepresent progress; an honest scaffold does not.

**D-9 - Unseeded tracks are visible, not hidden.**
CCDV-F, CCAR-F and CCAR-P are seeded as track and domain rows with `is_seeded = false`. The
selector shows all four with the three marked "content coming", rather than implying the product
covers only one track.

---

## 12. Out of scope for v1

Proctoring; payment; certificate issuance; question authoring UI; multi-tenant orgs;
adaptive/IRT item selection (the generator is blueprint-weighted random, not ability-adaptive).

---

## 13. Zia Tutor AI Integration (Session 2)

An optional companion tutor for the architect tracks, backed by the Zia Tutor AI MCP
server. The Claude API explanation engine (section 6) remains the default for every
track; Zia augments it where a concept maps onto a real Agent Factory lesson.

### 13.1 Probe result (2026-09-03)

An unauthenticated MCP Streamable HTTP `initialize` against
`https://zia-tutor-ai.panaversity.org/mcp` returned:

```
HTTP/1.1 401 Unauthorized
www-authenticate: Bearer error="invalid_token",
    error_description="Authentication required",
    resource_metadata=".../.well-known/oauth-protected-resource/mcp"
```

That metadata names `https://auth.panaversity.org` as the authorization server with
`bearer_methods_supported: ["header"]`.

**Conclusion:** the endpoint is an OAuth 2.0 protected resource, not a static-key API.
`CERTMASTERY_ZIA_MCP_TOKEN` therefore carries a bearer *access token* issued by that
authorization server, sent as `Authorization: Bearer <token>`. Full OAuth client
registration and refresh is out of scope for this session and is the first item of the
next one.

The curriculum slugs in section 13.3 were confirmed through an already-authenticated
client against corpus generation 62.

### 13.2 Architecture

| Piece | Location |
|---|---|
| `ZiaTutorClient` | `backend/app/services/zia_client.py` |
| Concept resolution | `backend/app/services/concept_map.py` |
| Tables | `zia_learner_links`, `concept_curriculum_map` |
| Routes | `POST /api/zia/session`, `GET /api/zia/explain`, `POST /api/zia/check-answer` |
| Panel | `frontend/components/AskZiaPanel.tsx` |
| Probe script | `backend/scripts/verify_zia_connection.py` |

**Failure policy.** Every route returns HTTP 200 with `available: false` when the tutor
is unconfigured, unreachable, unauthorized, or the corpus abstains. The panel then
renders nothing. A tutor outage must never surface to a candidate as a broken review
screen, and "the curriculum does not cover this" is a correct answer rather than a
malfunction.

**Error classification.** The MCP SDK runs its transport inside an anyio TaskGroup, so a
plain 401 arrives wrapped in an `ExceptionGroup`. The client flattens nested groups
before classifying, because reading `str(exc)` on the group loses the status code and
reports an auth failure as a network failure -- sending an operator to debug DNS instead
of their token.

**Identity.** `zia_learner_links` maps a Cert Mastery user to a stable Zia learner
handle. First panel open in a 4-hour visit window calls `begin_session`; later opens call
`open_student_record`, so the learner is resumed rather than restarted and their mastery
record stays continuous.

**Evidence honesty.** `check-answer` reports only what the platform observed: the
candidate's own words as `attempt`, with `evidence="teacher_reported_observed_in_chat"`.
Cert Mastery never asserts mastery it did not witness, because an unverified claim would
corrupt the tutor's record of what the learner can actually do.

### 13.3 Concept map (confirmed slugs)

| Track | Concept tag | Agent Factory lesson |
|---|---|---|
| CCAR-F | `multi-agent-supervisor-worker` | `claude-agent-sdk-crash-course` |
| CCAR-F | `prompt-caching-economics` | `build-agents-crash-course` |
| CCAR-F | `claude-md-team-configuration` | `claude-code-teams-crash-course` |
| CCAR-F | `cli-args-config-flags` | `agentic-coding-crash-course` (0.7 confidence) |
| CCAR-P | `enterprise-rag-pipelines` | `postgres-ai-crash-course` |
| CCAR-P | `automated-eval-frameworks` | `trusting-the-checker-crash-course` |
| CCAR-P | `compliance-cost-latency-tradeoffs` | `choosing-agentic-architectures-crash-course` |
| CCAR-P | `agent-deployment-runtime` | `deploying-agents-crash-course` |

Resolution order for a question: explicit `questions.tags` entry, then the question's
domain code, then nothing (panel hidden). Explicit tags win so one question can point at
a more specific lesson than its whole domain would.

### 13.4 Decisions

**D-10 - Zia tables are separate from the scoring schema.** Identity mapping stays out
of `users` and curriculum mapping out of `questions`, so the integration can be removed
or re-pointed without touching a row scoring depends on.

**D-11 - A concept with no mapping hides the panel, with no generic fallback.** Sending
a candidate to an unrelated lesson is worse than sending them nowhere.

**D-12 - One MCP session per call rather than a pooled long-lived session.** Traffic is
bursty and request-scoped, and keeping a session healthy across FastAPI workers is real
complexity for an optional feature. Correctness over a saved handshake.

**D-13 - `is_mapped=false` rows are stored explicitly.** A recorded coverage gap is
useful; an omitted row is indistinguishable from an unseeded table.
