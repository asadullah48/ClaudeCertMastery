# Claude Cert Mastery

Exam-prep platform for the four Claude certification tracks. Candidates sit
blueprint-weighted practice exams, receive a scaled score on the real 100&ndash;1000 band
with a 720 pass line, get a per-domain mastery breakdown, and drill weak areas.

**Status:** Session 1 (Foundation) complete &mdash; 261 tests passing.

| | |
|---|---|
| Frontend | Next.js 16 (App Router), TypeScript, Tailwind v4, Zustand |
| Backend | FastAPI, SQLAlchemy 2.0, Alembic, Pydantic v2 |
| Database | SQLite (dev) / PostgreSQL (prod) |
| AI | Claude API &mdash; `claude-opus-5` (Session 2) |

See [`SPEC-CERT-MASTERY.md`](SPEC-CERT-MASTERY.md) for the full specification, and
[`SESSION-1-SUMMARY.md`](SESSION-1-SUMMARY.md) for what shipped in this session.

---

## What works today

- **Track catalog** &mdash; all four tracks; CCAO-F is fully authored, the other three are
  registered and marked "content coming".
- **Blueprint-weighted exam generation** &mdash; a 60-item CCAO-F exam is composed as
  8/13/7/10/7/9/6 across the seven domains, matching the published weights exactly.
- **Scaled scoring** &mdash; 100&ndash;1000, with the pass line landing exactly on 720.
- **Per-domain breakdown** with mastery bands.
- **112-question CCAO-F bank**, 16 per domain, each with an authored explanation.

The exam runner UI, the Claude-powered explanation engine, the dashboard and flashcards
are Sessions 2&ndash;3. See "Roadmap" below.

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
| `CERTMASTERY_ANTHROPIC_API_KEY` | *(unset)* | Enables AI explanations (Session 2) |
| `CERTMASTERY_CLAUDE_MODEL` | `claude-opus-5` | Model for explanation generation |
| `CERTMASTERY_CORS_ORIGINS` | `http://localhost:3000` | Comma-separated allowed origins |

Leaving the API key unset is fully supported: every question carries an authored
explanation, so results stay useful without it. The AI layer augments rather than
replaces that.

---

## Tests

```bash
cd backend
pytest                              # 261 passed
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

---

## Project layout

```
backend/
  app/
    models/      SQLAlchemy ORM (10 tables)
    schemas/     Pydantic request/response models
    services/    scoring.py, blueprint.py, exam_generator.py  <- pure, no DB
    routers/     tracks, exams, attempts
  seed_data/ccao_f/   7 YAML files, 16 questions each
  seed.py             idempotent loader
  alembic/            migrations
  tests/
frontend/
  app/          App Router pages
  components/   TrackCard
  lib/          api client, types, Zustand store
```

The three service modules take no database dependency, which is why they can be tested
directly with no fixtures and reused across exam, practice and drill modes.

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

---

## Roadmap

| Session | Scope | Status |
|---|---|---|
| 1 &mdash; Foundation | Spec, schema, seed bank, scoring engine, track selector | **Done** |
| 2 &mdash; Integration | Exam runner UI, Claude explanation engine, prompt caching | Next |
| 3 &mdash; Advanced | Progress dashboard, SM-2 flashcards, auth | Planned |
| 4 &mdash; Validation | CCDV-F/CCAR-F/CCAR-P banks, deployment, hardening | Planned |
