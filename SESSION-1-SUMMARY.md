# Session 1 &mdash; Foundation

**Date:** 2026-09-03
**Outcome:** All Session 1 deliverables complete. 261 tests passing.

---

## Delivered

| Deliverable | Status | Where |
|---|---|---|
| `SPEC-CERT-MASTERY.md` | Done (321 lines, spec written before any code) | root |
| Repo scaffold, clean frontend/backend split | Done | `backend/`, `frontend/` |
| Database schema + migration | Done (10 tables, Alembic `9ce9d2c6ddd1`) | `backend/app/models/`, `backend/alembic/` |
| Seed script + CCAO-F question bank | Done (112 questions, idempotent loader) | `backend/seed_data/`, `backend/seed.py` |
| Core scoring algorithm + unit tests | Done | `backend/app/services/scoring.py` |
| README with setup/run instructions | Done | `README.md` |
| End-of-session summary | This file | |

Beyond the required scope, because the deliverables were not verifiable without them:
the blueprint allocator, the exam generator, three API routers, and a working track
selector and blueprint viewer in the frontend.

---

## Verification performed

Everything below was actually run, not assumed.

- `pytest` &mdash; **261 passed** in 6s.
- `alembic upgrade head` from an empty database, then `seed.py` on top &mdash; clean.
- `seed.py` run three times &mdash; still 4 tracks, 7 domains, 112 questions, 448 options.
  Idempotency confirmed by row count, not by inspection.
- `npx next build` &mdash; compiled successfully, TypeScript clean.
- Live stack on ports 8010/3010: generated a seeded 60-item exam, submitted an answer key
  of exactly 42 correct, and confirmed the API returned `scaled_score: 720`,
  `passed: true`, with domain totals reconciling to 42/60.
- Frontend rendered against the live backend: all four tracks listed, three marked
  "content coming", and the CCAO-F blueprint table showing 14/21/12/16/12/15/10% with
  items 8/13/7/10/7/9/6 totalling 60.

---

## Numbers

| | |
|---|---|
| CCAO-F questions authored | 112 (16 per domain &times; 7) |
| Answer options | 448 |
| Question types | 91 MCQ, 21 multi-response |
| Tests | 261 passing (~116 hand-written cases plus a 112-item parametrized sweep) |
| Database tables | 10 |
| API endpoints | 7 |

---

## Decisions made without asking

Each was a real fork with a live alternative. Full rationale in SPEC section 11.

1. **Blueprint weights govern composition, not scoring** (D-1). Every item counts
   equally; the weight decides how many items are drawn. Weighting the score too would
   double-count it.
2. **Piecewise-linear scaling on three anchors** (D-2). A single line from 0&ndash;100%
   onto 100&ndash;1000 would put the pass line at a raw 68.9%. The 70% raw threshold is an
   assumption &mdash; Anthropic does not publish the mapping &mdash; so it is stored per
   track in `pass_raw_threshold` and correctable in data.
3. **Weights as integer basis points** (D-3), so seven domains sum to exactly 10000 and
   tests assert equality with no epsilon.
4. **MR items are all-or-nothing for the score** (D-4). Partial credit is still computed
   and stored for the dashboard. Isolated behind one flag; see "Your call" below.
5. **psycopg 3, not psycopg2** (D-5). Local Python is 3.14.7 and psycopg2 wheels lag new
   interpreters. Deviates from the sibling `tradeflow` project.
6. **Seed content in YAML** (D-6), because 112 prose-heavy questions embedded in `.py`
   would be unreviewable and merge-hostile.
7. **No auth** (D-7), with `user_id` already on every table that will need it, so
   Session 3 adds auth without a schema migration.
8. **Frontend is scaffold + track selector only** (D-8). A half-built exam runner would
   misrepresent progress.
9. **Unseeded tracks are visible, not hidden** (D-9), so the catalog reflects the real
   certification landscape.
10. **No invented blueprints for the other three tracks.** The plan said to seed domain
    rows for them, but their published weights are not available, and inventing weights
    would fabricate the exact ground truth this platform exists to mirror. They are
    registered as tracks with their subject scope recorded and no domains.
11. **`CERTMASTERY_` prefix on every setting.** See below &mdash; this one was forced.

---

## Worth your attention

**A machine-wide `DATABASE_URL` points at a live Neon PostgreSQL database.**
`pydantic-settings` gives environment variables priority over `.env`, so the first
`seed.py --reset` resolved to that remote database and attempted to drop its tables.
It failed only because the URL specifies an async driver incompatible with the sync
engine &mdash; that was luck, not a safeguard.

Fixed structurally: every setting now reads from a `CERTMASTERY_`-prefixed variable, so
this project cannot pick up unrelated global configuration. No data was written to or
removed from that database.

Two things you may want to do independently of this project: that connection string
carries a live password and is readable by every process on the machine, and it is worth
checking whether other local projects resolve it the same way.

---

## Your call (nothing is blocked)

`scoring.py` has one deliberate policy seam: `MR_PARTIAL_CREDIT_COUNTS_TOWARD_SCORE`,
currently `False`.

The trade-off: all-or-nothing mirrors the real exam and will not inflate a candidate's
sense of readiness &mdash; the failure mode that matters most in exam prep. But it gives a
coarser signal to the flashcard scheduler, which would benefit from knowing that someone
got 2 of 3 options right rather than simply "wrong".

Flipping it to `True` is the entire change; `score_attempt()` reads the flag and nothing
else does.

---

## Next session (Integration)

1. Exam runner UI &mdash; timer, question navigation, flag-for-review, submission.
2. Claude explanation engine: `claude-opus-5`, adaptive thinking, `effort: medium`,
   structured output via `client.messages.parse()`.
3. Prompt caching &mdash; stable track/domain prefix marked `ephemeral`, volatile question
   suffix after it. Persist `cache_read_input_tokens` per call so cache effectiveness is
   measurable rather than assumed.
4. Explanation dedupe by `(question_id, selected_option_signature)` &mdash; the same wrong
   answer is generated once and reused for every candidate who makes it.
5. Batch API for post-submission bulk generation at 50% cost.

The `explanations` table, the signature helper and the config flag for all of this are
already in place.
