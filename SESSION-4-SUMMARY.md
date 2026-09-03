# Session 4 &mdash; Exam runner and the Claude explanation engine

**Date:** 2026-09-03
**Outcome:** Delivered. 333 tests passing (28 new).

---

## What changed

The two items left over from the Session 2 brief. Before this session the platform could
compose and grade an exam but had no way to sit one: the track page ended with a note
saying the runner shipped later. It ships now, and with it the Claude remediation engine
that gives the review screen its reason to exist.

A candidate can now go end to end &mdash; choose a length, answer under a timer, flag and
revisit, submit, read a scaled score with per-domain mastery, and expand any missed item
for remediation aimed at the answer they actually chose.

---

## Backend &mdash; explanation engine

`backend/app/services/explanation_engine.py` plus `POST /attempts/{id}/explanations`.

**Structured output.** `client.messages.parse(output_format=ExplanationPayload)` returns
five separate fields rather than one prose blob, because the review screen renders them
in distinct places and a blob would force the frontend to parse prose.

**Model parameters.** `thinking={"type": "adaptive"}` &mdash; `budget_tokens` is removed
on `claude-opus-5` and returns 400. `output_config={"effort": "medium"}`, because this is
short pedagogical prose rather than hard reasoning, and the default `high` would spend
tokens on deliberation the task does not need.

**Prompt caching.** The request splits into a stable prefix (pedagogy rules, track
blueprint, domain description) carrying `cache_control`, and a volatile suffix (the
question and the candidate's answer). Two silent failure modes are guarded by tests:

- A prefix under **512 tokens** will not cache on `claude-opus-5`. This raises no error;
  `cache_read_input_tokens` simply stays 0 forever. A test asserts the length so a
  well-meaning trim cannot quietly disable caching.
- The prefix must be byte-identical between calls. Blueprint rows render from an ordered
  list rather than a dict, and nothing time-varying is interpolated. One test asserts
  stability, another asserts the question stem never appears in the prefix.

`input_tokens`, `output_tokens` and `cache_read_tokens` are persisted per generation, so
the caching assumption is measurable in production rather than merely asserted here.

**Dedup.** Keyed on `(question_id, selected_option_signature)` &mdash; per *mistake*, not
per question. Two candidates who pick the same wrong answer share one generation.

**Graceful degradation is the contract.** Every failure mode &mdash; no API key, rate
limit, network error, malformed response &mdash; returns HTTP 200 with `source="static"`
and the authored explanation. The engine never raises.

---

## Frontend &mdash; runner and review

| File | Role |
|---|---|
| `lib/store.ts` | Answers, flags, per-question timing, deadline, submission |
| `components/ExamRunner.tsx` | Timer, options, navigation, question grid, confirm dialog |
| `components/ReviewScreen.tsx` | Scaled score, domain mastery, per-item remediation |
| `app/tracks/[code]/exam/page.tsx` | Length selection, then runner, then review |

The runner tracks time against the question actually on screen (accumulated in an effect
cleanup, so it lands on the item the candidate was looking at), enforces MCQ/MR arity at
selection time so the UI can never show a state the grader would reject, and submits
every question including unanswered ones &mdash; omitting them would make an unanswered
item indistinguishable from one the backend never saw, and the per-domain rollup depends
on knowing the true denominator.

---

## Decisions

Recorded in full as D-15 through D-19 in the spec.

- **D-15** &mdash; the timer hard-submits at zero. Chosen over a grace period, an input
  lock, or stopping the clock: each is kinder to a mis-paced sitting and each corrupts
  the readiness signal. The review screen labels an expired sitting explicitly.
- **D-16** &mdash; explanations require a submitted attempt. `/exams/generate` withholds
  the answer key by serialising options through a schema with no `is_correct`; an
  explanation names the correct answer, so serving one mid-exam would hand it back
  through the side door. 409 until graded.
- **D-17** &mdash; the countdown is an absolute deadline, not a decrementing counter,
  because background tabs throttle `setInterval` and a counter would drift.
- **D-18** &mdash; remediation generates on expand, not on submission.
- **D-19** &mdash; the cacheable prefix length is asserted by a test, because the failure
  it guards against is silent.

---

## Tests

28 new, 333 total. The engine takes its client by constructor injection and the route
takes the engine by FastAPI dependency, so every path is exercised without a network
call: adaptive-thinking parameters, the 512-token prefix floor, prefix byte-stability,
volatile content staying below the breakpoint, dedup reuse, `force_regenerate`, the 409
guard, outage fallback, and token-accounting persistence.

One test earns its place specifically. `output_config` and `output_format` are passed
together, which is documented but not something this codebase can verify without a live
key. If a future SDK treats them as exclusive, every candidate would silently drop to
static explanations. The engine retries once without the effort hint on that narrow
error, and a companion test asserts an unrelated bad request is *not* retried.

---

## Verified live

A run against uvicorn with no `ANTHROPIC_API_KEY`:

```
generate    : 201 items 10 per_domain {PTE:1 OEV:2 PMS:1 WISD:2 CKM:1 GRR:2 TRO:1}
key leaked  : False
early expl  : 409
submit      : 200 scaled 100 raw 0/10
explanations: 200 ai_enabled False generated 0 fell_back 10  -> source: static, text present
```

Blueprint weighting holds at n=10, the answer key is absent from the payload, the
pre-submission guard fires, and the fallback serves real text with no AI configured.

Frontend: `tsc --noEmit` clean, `next build` clean, `/tracks/[code]/exam` registered.

---

## Still outstanding

- **No live Claude call has been made.** Every AI path is tested against a fake. The
  `output_config` + `output_format` pairing and the real cache hit rate are unverified
  against the API. This is the first thing to check with a key in hand.
- **Batch fan-out is built but unused.** `build_batch_requests` shapes the 50%-cost
  payload; nothing submits it yet.
- **Zia OAuth** against `auth.panaversity.org`, still blocked on a credential.
- **CCAR-F / CCAR-P question banks.**
- **Session 3 scope from the original plan:** progress dashboard, SM-2 flashcards, auth.
