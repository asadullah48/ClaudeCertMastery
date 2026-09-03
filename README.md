# Claude Cert Mastery

Exam-prep platform for the four Claude certification tracks. Candidates sit
blueprint-weighted practice exams, receive a scaled score on the real 100&ndash;1000 band
with a 720 pass line, get a per-domain mastery breakdown, and drill weak areas.

**Live:** <https://claude-cert-mastery.vercel.app>

**Status:** Session 4 complete &mdash; 333 tests passing.

> **The live link is the frontend only.** The backend is not deployed yet, so the
> deployed site renders its shell and then reports that it cannot reach the API. Nobody
> can sit an exam on it. Follow [Setup](#setup) to run both halves locally, which is the
> only way to use the product today. Wiring the two together is tracked under
> [Deployment](#deployment).

| | |
|---|---|
| Frontend | Next.js 16 (App Router), TypeScript, Tailwind v4, Zustand |
| Backend | FastAPI, SQLAlchemy 2.0, Alembic, Pydantic v2 |
| Database | SQLite (dev) / PostgreSQL (prod) |
| AI | Claude API &mdash; `claude-opus-5` |
| Companion tutor | Zia Tutor AI over MCP (optional) |
| Email verification | Mailboxlayer via APILayer (selected, not yet integrated) |

See [`SPEC-CERT-MASTERY.md`](SPEC-CERT-MASTERY.md) for the full specification, and the
`SESSION-N-SUMMARY.md` files for what shipped in each session.

---

## What works today

- **Track catalog** &mdash; all four tracks; CCAO-F is fully authored, the other three are
  registered and marked "content coming".
- **Blueprint-weighted exam generation** &mdash; a 60-item CCAO-F exam is composed as
  8/13/7/10/7/9/6 across the seven domains, matching the published weights exactly.
- **Exam runner** &mdash; wall-clock timer, flag-for-review, question grid, keyboard
  navigation, per-question timing, and a confirm step that names how many items are
  still unanswered.
- **Scaled scoring** &mdash; 100&ndash;1000, with the pass line landing exactly on 720.
- **Review screen** &mdash; score against the pass line, per-domain mastery bands, and
  per-item remediation.
- **AI explanation engine** &mdash; Claude-generated remediation aimed at the answer the
  candidate actually chose, cached per mistake rather than per question.
- **Ask Zia** &mdash; optional companion tutor across all four tracks.
- **112-question CCAO-F bank**, 16 per domain, each with an authored explanation.

The progress dashboard, spaced-repetition flashcards and auth are next. See "Roadmap".

---

## Setup

Requires Python 3.11+ and Node 20+.

### Backend

```bash
cd backend
python -m venv venv
source venv/Scripts/activate        # Windows (Git Bash)
# .\venv\Scripts\Activate.ps1       # Windows (PowerShell)
# source venv/bin/activate          # macOS / Linux

pip install -r requirements.txt
cp .env.example .env

alembic upgrade head                # create the schema
python seed.py                      # load the CCAO-F question bank

uvicorn app.main:app --reload       # http://localhost:8000
```

Interactive API docs at <http://localhost:8000/docs>.

### Frontend

```bash
cd frontend
npm install
cp .env.example .env.local
npm run dev                         # http://localhost:3000
```

Then open <http://localhost:3000>, pick CCAO-F, and start a practice exam.

---

## Configuration

Every backend setting is read from a **`CERTMASTERY_`-prefixed** environment variable.

This prefix is deliberate. Unprefixed names like `DATABASE_URL` are commonly set
machine-wide by other projects, and `pydantic-settings` gives environment variables
priority over `.env` &mdash; so an unprefixed setting would let a stray global variable
silently point this application at somebody else's database.

| Variable | Default | Purpose |
|---|---|---|
| `CERTMASTERY_DATABASE_URL` | `sqlite:///./certmastery.db` | Database connection |
| `CERTMASTERY_ANTHROPIC_API_KEY` | *(unset)* | Enables AI explanations |
| `CERTMASTERY_CLAUDE_MODEL` | `claude-opus-5` | Model for explanation generation |
| `CERTMASTERY_CORS_ORIGINS` | `http://localhost:3000` | Comma-separated allowed origins |
| `CERTMASTERY_ZIA_MCP_ENDPOINT` | `https://zia-tutor-ai.panaversity.org/mcp` | Zia Tutor AI MCP server |
| `CERTMASTERY_ZIA_MCP_TOKEN` | *(unset)* | Bearer access token for Ask Zia |

Every credential above is optional. Leaving one unset disables its feature and nothing
else &mdash; the platform is built so that no third party can take it down.

> **Never commit a real key.** `.env` is git-ignored; `.env.example` carries names only.

---

## AI explanations

When `CERTMASTERY_ANTHROPIC_API_KEY` is set, a missed item can be expanded on the review
screen for remediation targeted at the specific wrong answer chosen &mdash; not a
restatement of the correct one, which the candidate can already see.

- **Model** `claude-opus-5`, adaptive thinking, medium effort.
- **Cached per mistake.** Keyed on `(question_id, selected_option_signature)`, so two
  candidates who pick the same wrong answer share one generation.
- **Prompt caching.** A stable per-domain prefix (pedagogy rules, blueprint, domain
  scope) is cached; only the question and the candidate's answer vary per call. Token
  counts are persisted so cache effectiveness is measurable rather than assumed.
- **Fallback.** No key, a rate limit or a network error all return HTTP 200 carrying the
  question's authored explanation. Every answer is explained with or without AI.

Explanations are served only for a **submitted** attempt. An explanation names the
correct answer, so returning one mid-exam would hand back the answer key that
`/exams/generate` deliberately withholds; the route returns `409` until the attempt is
graded.

---

## Email verification (selected, not yet integrated)

**Chosen API: [Mailboxlayer](https://apilayer.com/marketplace/email_verification-api)**,
reached with a single [APILayer](https://app.apilayer.com/dashboard) account key.

Of the APIs offered on the APILayer dashboard &mdash; Aviationstack, Countrylayer,
IPstack, Mailboxlayer, Marketstack, Mediastack, Positionstack, Scrapestack &mdash;
Mailboxlayer is the only one that serves work this project has already committed to. The
`users` table carries `email` (spec section 7), auth is on the roadmap, and D-7 records
that every table already carries `user_id`, so auth arrives without a migration.

Why it earns a place at signup:

- A mistyped address silently breaks score and certificate delivery. The candidate
  believes they registered, nothing ever arrives, and no part of the system reports a
  fault.
- Disposable-domain detection stops a free tier being farmed by throwaway accounts.
- The check is a syntax, MX-record and disposable-domain lookup. It never sends mail, so
  verification costs the candidate no round trip.

**Planned wiring**, consistent with every other integration here:

| Variable | Default | Purpose |
|---|---|---|
| `CERTMASTERY_MAILBOXLAYER_KEY` | *(unset)* | Enables signup email verification |

Unset means the check is skipped and registration proceeds &mdash; a verification
provider being down must never stop a candidate signing up. Only a definitive negative
(bad syntax, no MX record, known disposable domain) blocks; an inconclusive result is
treated as a pass, because turning away a real candidate is a worse failure than
admitting a questionable address.

**Not yet implemented.** The API is chosen and the account exists, but nothing is
subscribed and no code calls it. This section records the decision, not a shipped
feature.

---

## Ask Zia (optional companion tutor)

`CERTMASTERY_ZIA_MCP_TOKEN` enables the Ask Zia panel, which teaches the same concepts
from The AI Agent Factory curriculum with a source link on every answer. It is available
on all four tracks, driven by the `concept_curriculum_map` table: 22 concept tags, 21
mapped, 1 recorded as an explicit gap.

The endpoint is an **OAuth 2.0 protected resource**, not a static-key API. An
unauthenticated probe returns `401` with an authorization server of
`https://auth.panaversity.org`. This variable therefore holds a bearer **access token**
issued by that server, not an API key you can mint locally.

Check your setup at any time:

```bash
cd backend
python scripts/verify_zia_connection.py
# exit 0 usable | 1 reachable but unauthorized | 2 unreachable
```

**Fallback behaviour.** Unset, expired, unreachable, or a concept the curriculum does not
cover all produce the same result: the panel renders nothing and the Claude-generated
explanation continues to serve the question. A tutor outage never surfaces to a candidate
as a broken screen, and never blocks an exam.

---

## Tests

```bash
cd backend
pytest                              # 333 passed
pytest -q tests/test_scoring.py     # the scaled-scoring engine alone
```

| Suite | What it covers |
|---|---|
| `test_scoring.py` | Scale anchors, monotonicity, rounding, the 719/720 boundary |
| `test_blueprint.py` | Apportionment totals, published-weight fidelity, determinism |
| `test_grading.py` | MCQ, MR set equality, partial credit, mastery bands |
| `test_exam_generator.py` | Quotas, seed reproducibility, shortfall redistribution |
| `test_seed_integrity.py` | Per-question validation across all 112 authored items |
| `test_api.py` | Endpoint behaviour and the generate&rarr;submit round trip |
| `test_zia.py` | Ask Zia session lifecycle, citations, evidence honesty, every fallback |
| `test_explanations.py` | Prompt-caching guards, dedup, the 409 gate, outage fallback |

Two tests in `test_explanations.py` guard failures that raise no error: `claude-opus-5`
silently declines to cache a prefix under 512 tokens, and a prefix that varies byte-wise
between calls never produces a cache hit. Either would cost money indefinitely without
ever surfacing as a bug.

---

## Project layout

```
backend/
  app/
    models/      SQLAlchemy ORM (10 tables)
    schemas/     Pydantic request/response models
    services/    scoring.py, blueprint.py, exam_generator.py  <- pure, no DB
                 explanation_engine.py, zia_client.py, concept_map.py
    routers/     tracks, exams, attempts, explanations, zia
  seed_data/ccao_f/   7 YAML files, 16 questions each
  seed.py             idempotent loader
  alembic/            migrations
  tests/
frontend/
  app/          App Router pages, incl. tracks/[code]/exam
  components/   TrackCard, AskZiaPanel, ExamRunner, ReviewScreen
  lib/          api client, types, Zustand store
```

`scoring.py`, `blueprint.py` and `exam_generator.py` take no database dependency, which
is why they can be tested directly with no fixtures and reused across exam, practice and
drill modes.

---

## How scoring works

Blueprint weights decide **how many items each domain contributes**, not how the score is
weighted. Every item counts equally toward the raw score &mdash; the weighting is already
present in the item mix, so applying it again at scoring time would count it twice.

The raw proportion maps onto the reporting scale through three anchors:

| Raw | Scaled |
|---|---|
| 0% | 100 |
| 70% | **720** (pass) |
| 100% | 1000 |

Anchoring the midpoint is what makes the threshold exact. A single straight line from
0&ndash;100% onto 100&ndash;1000 would put the pass line at a raw 68.9%.

On a 60-item exam: 41 correct &rarr; 705 (fail), 42 correct &rarr; 720 (pass),
54 correct &rarr; 907.

When the timer reaches zero the exam is submitted as it stands and unanswered items are
graded incorrect. Locking input, a grace period or stopping the clock would each be
kinder to a mis-paced sitting, and each would corrupt the one signal the product exists
to give. The review screen labels an expired sitting, so it is never mistaken for one
finished in time.

---

## Deployment

| Half | Target | State |
|---|---|---|
| Frontend | Vercel &mdash; <https://claude-cert-mastery.vercel.app> | **Live** |
| Backend | Render (spec section 10) | Not deployed |

The frontend is live and the backend is not, which is why the deployed site cannot load
a track list. The frontend reads its API base URL from `NEXT_PUBLIC_API_URL`, which still
points at `http://localhost:8000` &mdash; and from Vercel's servers that resolves to
Vercel itself, not to your machine.

The backend cannot go on Vercel as it stands: it keeps state in a SQLite file and runs
Alembic migrations at deploy time, neither of which survives an ephemeral serverless
filesystem. It needs a host with a persistent disk or a managed Postgres.

Once the backend has a public URL:

```bash
cd frontend
npx vercel env add NEXT_PUBLIC_API_URL production   # the deployed backend URL
npx vercel --prod                                    # rebuild with it baked in
```

Both steps are required. `NEXT_PUBLIC_*` values are inlined into the bundle at build
time, so setting the variable without redeploying leaves the live site still calling
`localhost`.

---

## Roadmap

| Session | Scope | Status |
|---|---|---|
| 1 &mdash; Foundation | Spec, schema, seed bank, scoring engine, track selector | **Done** |
| 2 &mdash; Integration | Zia Tutor AI MCP companion for CCAR-F/CCAR-P | **Done** |
| 3 &mdash; Advanced | Ask Zia widened to all four tracks (mapping-driven) | **Done** |
| 4 &mdash; Advanced | Exam runner UI, Claude explanation engine | **Done** |
| 5 &mdash; Deployment | Frontend on Vercel | **Done** |
| &nbsp; | Backend on a persistent host, Postgres, CORS | **In progress** |
| 6 &mdash; Advanced | Progress dashboard, SM-2 flashcards, auth + email verification | Planned |
| 7 &mdash; Validation | CCDV-F / CCAR-F / CCAR-P question banks, hardening | Planned |

Deployment is split across two rows because it is genuinely half done, and a single
"Planned" would have contradicted the [Deployment](#deployment) section above. The
frontend is live; the backend is not, which is the one thing standing between the
deployed site and a usable product.

### Known gaps

- **The deployed site cannot load data.** Frontend only &mdash; see
  [Deployment](#deployment). Run both halves locally to use the product.
- **No live Claude call has been made.** Every AI path is tested against a fake, so the
  `output_config` + `output_format` pairing and the real cache hit rate are unverified
  against the API.
- **Batch fan-out is built but unused.** `build_batch_requests` shapes the 50%-cost
  payload; nothing submits it yet.
- **Zia OAuth is blocked** on a credential from `auth.panaversity.org`.
- **Three of four tracks have no questions.** CCDV-F, CCAR-F and CCAR-P publish their
  blueprint and resolve their Zia concepts, but no exam can be sat on them.
