# Release 1 Gap Report

**Status:** Gate A deliverable (inspection only — no source code has been modified to produce this report).
**Date:** 2026-09-16
**Scope of this report:** `D:\ClaudeCertMastery` — FastAPI backend (`backend/`) + Next.js App Router frontend (`frontend/`).

This report is produced against `CLAUDE-CODE-MASTER-IMPLEMENTATION-PROMPT.md` (root of this repo), which defines a Release 1 "commercial readiness" scope. Per that prompt's Gate A, this document precedes any implementation and ends with an explicit stop-and-approve request (Section 9).

---

## 1. Current Architecture

**Backend** (`backend/app`, FastAPI + SQLAlchemy 2.0 + Alembic + Pydantic v2):

- `main.py` — app factory, CORS middleware (`allow_origins=settings.cors_origin_list`), `/health` endpoint that reports feature flags (`ai_explanations_enabled`, `zia_enabled`) but does **not** check DB or dependency connectivity.
- `config.py` — Pydantic-settings, all env vars `CERTMASTERY_`-prefixed. `database_url` defaults to SQLite. `claude_model` defaults to `"claude-opus-5"`.
- `database.py` — single global `engine`/`SessionLocal`, with a SQLite-specific `check_same_thread` special case and no Postgres pool tuning.
- `models/` — 10 tables: `catalog.py` (Track, Domain, Question, AnswerOption), `attempt.py` (ExamAttempt, AttemptItem, AttemptDomainScore), `user.py`, `explanation.py`, `flashcard.py`, `zia.py`.
- `services/` — `scoring.py`, `blueprint.py`, `exam_generator.py` (pure/DB-free), `explanation_engine.py` (Claude API wrapper with fallback), `zia_client.py` (an **outbound MCP client**, not a server), `concept_map.py`.
- `routers/` — `tracks`, `exams`, `attempts`, `explanations`, `zia`. **No `auth` router exists.**
- `alembic/versions/` — 2 migrations (`9ce9d2c6ddd1_initial_schema`, `861dde9802fd_zia_tutor_integration`).

**Frontend** (`frontend/app`, Next.js 16.3.1 App Router, TypeScript 5.9.3, Tailwind v4, Zustand):

- Pages: `app/page.tsx` (track list), `app/tracks/[code]/page.tsx` (blueprint + Zia panel), `app/tracks/[code]/exam/page.tsx` (length picker → runner → review).
- Components: `TrackCard`, `AskZiaPanel`, `ExamRunner`, `ReviewScreen`.
- `lib/api.ts` (fetch wrapper reading `NEXT_PUBLIC_API_URL`), `lib/store.ts` (Zustand — all in-progress exam state is client-memory only), `lib/types.ts`.

**Connection**: Frontend calls FastAPI over plain HTTP with no shared type generation. The deployed frontend (Vercel) is hardcoded to `NEXT_PUBLIC_API_URL=http://localhost:8000` — the backend has never been deployed (see Section 5).

---

## 2. Reproducible Baseline Results

Environment: Windows 11 Pro, Python 3.14.7, Node v24.19.0, npm 11.7.0. Key resolved versions: fastapi 0.141.1, pytest 9.1.1, sqlalchemy 2.0.54, alembic 1.20.0, anthropic 1.6.0, psycopg 3.3.5, next 16.3.1, typescript 5.9.3, react 19.2.8.

| Check | Command | Result |
|---|---|---|
| Backend tests | `python -m pytest -v` (from `backend/`, fresh venv, `pip install -r requirements.txt`) | **333 collected → 333 passed, 0 failed, 0 skipped, 0 errors** (91.99s). Confirms README's "333 passing tests" claim exactly. 1 third-party `DeprecationWarning` (anyio/starlette internal), no app-code warnings. |
| Frontend type-check | `npx tsc --noEmit` (no dedicated `typecheck` script exists in `package.json`) | **Pass**, zero errors. |
| Frontend lint | `npm run lint` → `eslint` | **Broken.** `'eslint' is not recognized...`. `eslint` is absent from `package.json` dependencies/devDependencies entirely, no binary in `node_modules/.bin`, no `.eslintrc*`/`eslint.config.*` file. The lint script was never wired up — this is not a transient issue. |
| Frontend build | `npm run build` → `next build` (Turbopack) | **Pass.** "Compiled successfully in 17.7s", 40.46s total. Routes: `/` (dynamic), `/_not-found` (static), `/tracks/[code]` (dynamic), `/tracks/[code]/exam` (dynamic). |
| Dependency audit | `npm audit` | **1 critical vulnerability**: `next@16.3.1` (GHSA-p293-qw3h-jr36 — unauthenticated RCE on Windows-hosted servers; GHSA-2xp9-vwfh-vxw4 — RCE in AVIF image optimization). Fix requires `next@16.3.5`; `package.json` currently pins the exact vulnerable version. Not patched by this inspection, per Gate A (inspection only). |
| Deployed connectivity | One-shot fetch to the documented Vercel URL | **Confirmed failure**, matching README exactly: page renders its shell, then errors "Could not reach the API at http://localhost:8000." Backend is not deployed anywhere. |

No test suite was weakened, skipped, or deleted to produce these numbers.

---

## 3. Working vs. Incomplete Features

| Feature | Status | Evidence |
|---|---|---|
| Track catalog (4 tracks) | Working; 1 of 4 content-complete | `catalog.py:57` `is_seeded`; only CCAO-F seeded (`seed_data/ccao_f/*.yaml`, 7 files, 112 questions) |
| Blueprint-weighted exam generation | Working | `services/exam_generator.py`, `blueprint.py`; wired in `routers/exams.py:42-143` |
| Scaled scoring (100–1000 / 720) | Working, but is a **simulation** not a verified official scale | `services/scoring.py`; SPEC-CERT-MASTERY.md:279-281 calls the 70% raw threshold "an assumption... Anthropic does not publish the mapping" |
| Exam runner (timer/flag/nav) | Working, timed-only | `components/ExamRunner.tsx`, `lib/store.ts:74-192`. `PRACTICE`/`DOMAIN_DRILL` modes exist in the `AttemptMode` enum (`models/attempt.py:24-27`) but are unreachable from the UI — the frontend always submits mode `"exam"` |
| Duplicate-submission prevention | Working | `routers/attempts.py:70-71` — 409 if already `SUBMITTED` |
| Answer autosave mid-exam | **Missing** | `useExam` state is in-memory only; no per-answer persistence to backend |
| Flag-for-review | Working | `store.ts:124-129`, persisted at submit |
| Diagnostic (short, distinct from full exam) | **Missing** | No route/schema/UI concept of a diagnostic anywhere in the codebase |
| Deterministic study-plan generator | **Missing** | No file/route/service found in backend or frontend |
| AI explanation engine with fallback | Working | `services/explanation_engine.py`, `routers/explanations.py`; never raises, falls back to `static_explanation`; **never exercised against a live Anthropic key** (self-admitted in SESSION-4-SUMMARY.md and README) |
| Zia tutor (MCP) | Working as an outbound **client** only, never authenticated | `services/zia_client.py` calls an external third-party MCP server; blocked on OAuth (never obtained, per SESSION-2/3/4) |
| KSOR / in-repo MCP server | **Does not exist** | No `KSOR`, `mcp.server`, or `FastMCP` in implementation code; no `mcp` server SDK dependency in `requirements.txt`; term appears only in the new master-implementation-prompt file |
| Auth | **Missing** | No auth router; every endpoint uses a hardcoded `DEV_USER_EMAIL = "dev@certmastery.local"` (`routers/exams.py:29,32-39`, `routers/zia.py:43,53-58`) |
| Ownership enforcement on attempts | **Missing** | `GET /attempts/{id}` and `POST /attempts/{id}/submit` (`routers/attempts.py:44-176`) look up by ID alone, no `user_id` check |
| Flashcards / SM-2 | **Stub only** | Table exists (`models/flashcard.py`) but no router/service reads or writes it |
| Email verification | **Not implemented** | README explicitly marks it "selected, not yet integrated"; no Mailboxlayer call anywhere in code |
| CI pipeline | **Missing** | No `.github/workflows/*.yml` |
| LICENSE | **Missing** | No `LICENSE` file at repo root |
| `docs/` content | **Empty** | Directory exists, 0 files |

---

## 4. Claim Verification Table

| Claim (quoted) | Location | Status |
|---|---|---|
| "results are reported on the real 100–1000 scale with a 720 pass line" | `frontend/app/page.tsx:26` | **Unverified/misleading** — stated as fact in shipped UI copy; the project's own SPEC calls the threshold an unverified assumption |
| "the real exam" (blueprint mirrors it) | `frontend/app/tracks/[code]/exam/page.tsx:61` | **Unverified** — no cited authoritative source |
| "Multi-response items are all-or-nothing, exactly as on the real exam" | `frontend/app/tracks/[code]/exam/page.tsx:100` | **Unverified** — asserted as fact about the real certification exam, no citation |
| No "official" / "guaranteed pass" language | grep across frontend + README | **Verified absent** — those exact terms are not used |
| "333 tests passing" | README.md, SPEC-CERT-MASTERY.md | **Verified true** — reproduced exactly (333 passed, 0 failed) |
| Model `claude-opus-5` | `backend/app/config.py:33`, README, SPEC | **Unverified against a live API call** — project's own docs state no live Claude call has ever succeeded |
| "Zia Tutor AI MCP integration" | SESSION-2-SUMMARY.md title | **Real but mischaracterized** — it is a functioning MCP *client* against a third-party server, never successfully authenticated (OAuth blocked) |
| Independent-platform / non-affiliation disclaimer | Checked `frontend/app/layout.tsx`, `frontend/app/page.tsx` | **Confirmed absent from the shipped product.** The disclaimer text exists only in the new (unshipped) `ClaudeCertMastery-Commercial-Offering-Blueprint.md` |
| "CCAO-F fully authored, other three registered and marked 'content coming'" | README.md:33-34 | **Verified** — matches `is_seeded` behavior and UI gating |

---

## 5. Deployment Blockers

1. Frontend (Vercel) is hardcoded to `NEXT_PUBLIC_API_URL=http://localhost:8000` — every API call fails in production. Confirmed live.
2. Backend has never been deployed anywhere.
3. `next@16.3.1` has a critical, publicly disclosed RCE advisory with no patched version available inside the currently pinned range.
4. No CI pipeline exists to catch regressions before deploy.
5. No production database story: SQLite is the only exercised path; Postgres driver (`psycopg`) is present but completely untested end-to-end (migrations, pooling, connection string).
6. `frontend/lib/api.ts` reads a single `NEXT_PUBLIC_API_URL` with no per-environment override validation, and there is no environment-variable validation on the backend beyond Pydantic defaults.

---

## 6. Data / Auth / Security Gaps

- **No authentication at all.** Every request is attributed to a single hardcoded dev user server-side. There is nothing to authenticate against in production.
- **No object-level authorization.** Attempt read/submit endpoints trust the path-supplied ID with no ownership check — a direct IDOR once multiple real users exist.
- **No rate limiting** on any route, including the AI explanation endpoint (cost/abuse exposure) or any future auth routes.
- **CORS** is configured from settings but has not been reviewed for a production origin list.
- **`/health`** does not reflect real dependency state (DB connectivity, AI provider reachability), so it cannot be trusted as a readiness probe.
- **Secrets handling**: `.env.example` files exist and are minimal/safe; no evidence of committed secrets. Not deeply audited for provider-response logging in `explanation_engine.py` — flagged for Gate C review, not confirmed as a problem.
- **No CSRF/session-fixation surface exists yet** because there is no session/auth system — this becomes a requirement the moment auth is added, not before.

---

## 7. UI / Accessibility Gaps (not yet audited in depth)

Not systematically audited in this pass (out of scope for Gate A inspection, which focused on functional/architectural/security gaps). A full WCAG 2.2 AA pass, keyboard-completeness check, and responsive-width sweep (320–1920px) is required before Release 1 sign-off per the master prompt's Section 6 and Gate D, and should be scheduled as its own milestone rather than folded into this report speculatively.

---

## 8. Proposed Minimal Changes (maps to master prompt Section 4)

These are the smallest changes that close each Release-1 requirement without a rewrite:

1. **Truthful language** — replace the three unqualified "real"/"exact exam" strings in `page.tsx` and `exam/page.tsx` with "Practice score" / "Estimated readiness" language; add a footer disclaimer component; add a short methodology page. Small, isolated, no schema change.
2. **Lint** — install and configure `eslint` with Next.js's own recommended config (`eslint-config-next` is normally bundled by `create-next-app`; its absence here looks like an incomplete initial setup rather than a deliberate removal). Small, isolated.
3. **Next.js CVE** — bump to `16.3.5` inside the same major version; re-run type-check/build to confirm no breakage. Small, isolated.
4. **Postgres readiness** — add pool configuration to `database.py` behind the existing `database_url` setting, add a docs page, and add one CI job that runs Alembic upgrade against a throwaway Postgres service container. No destructive migration; SQLite remains for local/dev.
5. **Auth + ownership** — this is the largest item in-scope. Requires a new auth router, a maintained library (e.g. an existing, well-supported FastAPI auth solution), a `user_id` foreign key already present on relevant tables (verify in Gate B), and an ownership check added to the two attempt endpoints. Not a rewrite, but touches every attempt/progress route.
6. **Diagnostic + study plan** — new, small, additive: a new `diagnostic` concept reusing `exam_generator.py` with a smaller deterministic item count and domain coverage, plus a new deterministic study-plan service consuming `AttemptDomainScore`. No AI dependency required for a valid plan.
7. **KSOR + MCP** — new, additive schema (learner/evidence/decision tables) and a new read-oriented MCP server module. This is the second-largest item and should be sequenced last, after the deterministic progression policy has real data to serve.

None of these require deleting or rewriting existing working code (scoring, blueprint, exam generation, and the AI explanation engine all stay as-is).

---

## 9. Risks, Rollback, and — per Gate A — Stop for Approval

**Rollback approach:** every milestone below is additive or isolated (new router, new service, new migration, new doc, dependency bump within the same major version). Each Alembic migration will be written with a working `downgrade()`. No milestone requires dropping or destructively altering existing tables that already hold CCAO-F content or attempt data.

**Per the master prompt's own Gate A instruction, I am stopping here for approval before Gate B/C, because:**

- **Auth (4.4)** requires choosing and integrating an external, maintained auth solution — this is a real architectural addition, not a rewrite, but it is a decision with long-term lock-in (library choice, session model) that should be confirmed before implementation.
- **Production PostgreSQL (4.1)** implies a managed-Postgres hosting choice, which is very likely a **paid external service** — the master prompt explicitly says to stop for "a major rewrite, destructive migration, paid external service, unverified certification claim, or change outside Release 1."
- **Email ownership verification (4.4)** likely also implies a paid provider (the README already notes Mailboxlayer was selected but not integrated, and Mailboxlayer only validates syntax/MX/disposable status — not ownership — so a different, ownership-capable flow, e.g. verification-link email, needs to be chosen, which typically means a transactional email provider).
- **KSOR + MCP (4.8)** is the largest new subsystem in the whole scope (new schema, new deterministic policy engine, new MCP server, new docs) and is explicitly called out in the master prompt as foundational but non-trivial; it deserves its own confirmed milestone rather than silent inclusion in a broader pass.
- The **100–1000 scale / 720 threshold** cannot currently be verified against an authoritative source (Section 3/4.2 of the master prompt requires either verifying this or replacing it with a transparent, clearly-labeled simulation) — this is a product decision, not just an engineering one.

**Recommended next step:** review Section 8's proposed minimal changes and confirm scope, then approve moving to Gate B (an ordered implementation plan with named milestones and expected file changes), starting with the low-risk, no-external-dependency items (truthful language, lint fix, Next.js patch bump) before the auth/Postgres/KSOR milestones that carry cost and lock-in decisions.
