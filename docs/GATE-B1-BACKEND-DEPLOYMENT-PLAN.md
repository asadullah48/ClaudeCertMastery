# Gate B1: Backend Deployment Discovery Plan

**Status:** Gate B1 (discovery) is complete. **Gate B1A (local implementation)** has also been completed — see Section 13 for exact changes made. Nothing has been deployed, no external service created, no Vercel setting touched, no credential requested or searched for, and nothing committed or pushed.

**Date:** 2026-09-16

**Operating context (stated by the founder, recorded here because it shapes every recommendation in this document):** this project is being built by one primary person supporting a household of seven, recovering from two recent leg operations, with declining income from a separate textile-stitching business. The founder is prepared to take calculated commercial risk where the expected return justifies it, but capital preservation and avoiding irreversible or unnecessary expense remain hard constraints. The operating principle given: **aggressive on opportunity, disciplined on downside.**

---

## 1. Backend entry point and runtime requirements

- **Entry point:** `backend/app/main.py`, exposing `app = FastAPI(...)`.
- **Start command (now committed):** `backend/Procfile` — `web: uvicorn app.main:app --host 0.0.0.0 --port $PORT`. Koyeb's Python buildpack reads `Procfile` natively (confirmed: "The Python Buildpack comes with support for Procfile that lets users set custom start commands... consists of a `web:` prefix followed by the command to execute").
- **Python version (now pinned):** `backend/.python-version` → `3.13`. Koyeb's buildpack currently supports Python 3.9-3.13 (not 3.14). The local dev machine only had Python 3.14.7 installed; **Python 3.13.15 was installed locally this session specifically to test against the pinned version** (via `winget`, a local dev-tooling install, not an external/cloud resource) — see Section 13 for the actual test run.
- **Dependencies:** unchanged from Gate B1 discovery, plus `slowapi>=0.1.9` added for rate limiting (Section 13).

## 2. Current database/storage model

Unchanged from Gate B1 discovery: SQLite locally, Postgres already supported in code (`psycopg[binary]`, Alembic reads the DB URL dynamically from `app.config.settings`), 2 migrations, no reversibility issues. SQLite remains unsuitable for any of the hosts under consideration (ephemeral filesystem).

## 3. Required environment-variable names (unchanged, names only)

| Variable | Required in production? |
|---|---|
| `CERTMASTERY_DATABASE_URL` | Yes |
| `CERTMASTERY_CORS_ORIGINS` | Yes |
| `CERTMASTERY_ANTHROPIC_API_KEY` | No — **must stay unset for initial validation** |
| `CERTMASTERY_CLAUDE_MODEL` | No (has default) |
| `CERTMASTERY_ZIA_MCP_ENDPOINT` | No (has default) |
| `CERTMASTERY_ZIA_MCP_TOKEN` | No — **must stay unset for initial validation** |
| `CERTMASTERY_ZIA_MCP_TIMEOUT_SECONDS` | No (has default) |
| `PORT` | Injected by host |

No secret values were displayed, read, or requested.

## 4. CORS configuration

Unchanged and confirmed correct: `CERTMASTERY_CORS_ORIGINS` is an exact, comma-separated origin list (`config.py:35,48`) — never a wildcard — consumed by `CORSMiddleware`. This was verified as already satisfying "exact configurable CORS"; no code change was needed here.

## 5. Health-check endpoint

**Enhanced this session.** `/health` now performs a real `SELECT 1` against the configured database and reports `database_reachable: true/false` plus a `status: "ok"/"degraded"` field, instead of always claiming `"ok"` regardless of DB state. Verified live (Section 13).

## 6. Backend-compute and database comparison: Koyeb, Render, Neon

Per the founder's direction: Koyeb is the preferred compute platform. Koyeb Postgres is *not* assumed to be the best database choice and is compared against Render Postgres (already researched in the original discovery pass) and one durable, lower-cost external option — **Neon**, chosen as the strongest fit for a small, bursty cohort: true serverless scale-to-zero billing, a free tier that does not expire, and Postgres-standard portability.

### Koyeb (compute)

| | Detail |
|---|---|
| Realistic monthly minimum | **$0** — free tier: 512MB RAM, 0.1 vCPU, 2GB SSD, one free web service, no credit card required |
| Free-tier restrictions | Limited to Frankfurt or Washington D.C. region; single small instance; no autoscaling |
| Data-expiration risk | N/A — compute is stateless |
| Backup capability | N/A for compute |
| Region availability | Free: Frankfurt or Washington D.C. Paid: adds Singapore |
| **Spending controls** | **No hard spend cap exists on Koyeb as of this research.** Billing is postpaid. Optional email billing alerts at 80%/100% of a self-set threshold exist but are **disabled by default** and must be turned on manually — an alert can still be exceeded before anyone reacts. This is a real residual risk given the capital-preservation constraint and must be mitigated operationally. |
| Migration difficulty | **Low** — a `Procfile` + `.python-version` pinned app is portable to any buildpack-compatible host with no code change |
| Paid tier | Eco instances from ~$1.61/mo (select regions); Pro plan $29/mo + compute (includes $10 compute credit) |

### Koyeb Postgres

| | Detail |
|---|---|
| Realistic monthly minimum (paid, always-on) | **~$21-30/mo** before storage (sources disagree: $20.89/mo and $29.76/mo for the Small tier) |
| Free-tier restrictions | 0.25 vCPU, 1GB RAM, 1GB storage, and **only 5 hours of active compute per month** — closer to a smoke-test allotment than a usable free database for any continuous cohort activity |
| Data-expiration risk | Not explicitly confirmed what happens once the 5 monthly hours are exhausted (throttled vs. deleted) — **unresolved by available sources; confirm directly with Koyeb before relying on the free tier beyond a short connectivity test** |
| Backup capability | **Not confirmed in available sources** — do not assume backups exist on this tier without verifying directly before storing anything important |
| Region availability | Washington D.C., Frankfurt, Singapore |
| Spending controls | Same as Koyeb compute — postpaid, no hard cap, opt-in alerts only |
| Migration difficulty | **Low for the data itself** (standard Postgres wire protocol) — Koyeb's serverless auto-sleep cost model is platform-specific, not portable |

### Render Postgres (carried over from the original comparison)

| | Detail |
|---|---|
| Realistic monthly minimum (paid) | ~$6-19/mo for the database alone, or ~$13-26/mo combined with a Starter web service |
| Free-tier restrictions | 1GB storage |
| Data-expiration risk | **High for the free tier** — expires 30 days after creation, 14-day grace period, then **deleted**. Not viable beyond initial validation, regardless of cohort size. |
| Backup capability | Paid plans include automated backups; free tier does not (exact retention window not independently re-verified this session) |
| Region availability | Oregon, Ohio, Virginia (US), Frankfurt, Singapore (general knowledge — confirm current list before relying on a specific one) |
| Spending controls | Flat/tiered pricing for the database itself (more predictable than usage-metered billing); storage/bandwidth can still add overage charges |
| Migration difficulty | Low — standard Postgres |

### Neon (recommended durable, lower-cost external option)

| | Detail |
|---|---|
| Realistic monthly minimum | **$0** to start; paid Launch tier is pure consumption at **$0.106/CU-hour** compute + **$0.35/GB-month** storage — for a small, bursty cohort this often stays in the low single digits of dollars per month |
| Free-tier restrictions | 100 CU-hours/month per project, 0.5GB storage/project, up to 100 projects, 10 branches/project, scale-to-zero after 5 minutes idle (cannot be disabled) |
| Data-expiration risk | **Low.** The free tier is explicitly permanent, not a trial, and does not expire. Scale-to-zero only suspends *compute* — data persists regardless of idle time. This is the clearest durability advantage among the three options. |
| Backup capability | Neon's copy-on-write branching storage is the basis for point-in-time recovery; exact retention windows were not independently re-verified this session — confirm in Neon's current docs before treating it as a complete backup solution. A periodic independent `pg_dump` export is recommended as a belt-and-suspenders measure regardless. |
| Region availability | AWS-backed regions spanning US, EU, and Asia-Pacific (general knowledge; confirm the current exact list before committing to one for latency reasons) |
| Spending controls | Consumption-based with **dashboard-configurable per-project usage limits** in addition to alerts — materially stronger than Koyeb's alert-only approach, though exact hard-cap mechanics should be confirmed in current docs before being relied on as an absolute ceiling |
| Migration difficulty | Low — standard Postgres wire protocol |

### Expected cost by cohort size (estimates, not measurements)

**These are reasoned estimates based on stated assumptions about a low-traffic, bursty exam-prep workload — not measured production data, since no real users exist yet. Validate against real usage during the free-tier phase before committing to a paid tier size.**

| Active students/month | Koyeb compute | Koyeb Postgres | Render Postgres | Neon Postgres |
|---|---|---|---|---|
| 25 | Likely free ($0) | Free tier's 5 hrs/month is likely **insufficient** for continuous uptime even at this size → paid Small realistically needed from day one (~$21-30/mo) if chosen | Free tier works price-wise but time-bombs at 30+14 days regardless of cohort size | Likely stays within the free 100 CU-hour allotment given scale-to-zero → **$0**, monitor actual usage |
| 100 | Likely still free, or ~$1.61-5/mo if bumped to a paid Eco instance | Same constraint as above — paid tier needed | Same — free tier is time-limited, not size-limited | Likely still within or just over the free allotment → **~$0-5/mo estimated** |
| 500 | May need a paid Eco/Nano instance for consistent headroom → **~$5-15/mo estimated** | Paid Small, possibly larger → **~$21-40/mo estimated** | Paid Starter tier (~$13-26/mo combined) | Likely exceeds 100 CU-hours → paid Launch, but usage-based, likely **~$5-20/mo estimated** unless usage is unexpectedly heavy |

## 7. `NEXT_PUBLIC_API_URL` resolution

Unchanged: inlined at Next.js build time, currently unset on Vercel (defaults to `localhost:8000`). **Now also fixed on the frontend side this session** — the user-facing error no longer echoes this internal value (Section 13, item 6).

## 8. Security risks before exposing the API publicly

Core finding unchanged: no authentication, no ownership checks existed. **Rate limiting has now been added** (Section 13, item 4) as the smallest safe control. Authentication/ownership remains explicitly out of scope for Gate B1A and **must exist before any real student record is stored** — a hard requirement per the founder's own stated constraint, not just a recommendation.

## 9. Recommended production configuration: Koyeb (compute) + Neon (Postgres)

This is a **best-value**, not automatically cheapest, recommendation — it optimizes for durability and calculated risk management given the operating context in this document's header.

**Why cross-host instead of Koyeb for both:** Koyeb Postgres's free tier (5 compute-hours/month) is too small to be practically usable even at 25 students, its paid tier's backup capability is unconfirmed, and — like Koyeb compute — it has no hard spending cap. Neon's free tier never expires, scale-to-zero keeps cost proportional to actual bursty usage rather than fixed monthly overhead, and its per-project usage limits are a stronger spending control. Splitting compute and database across two providers is a standard, portable pattern — it does not lock the founder into either vendor, which directly serves "keep infrastructure portable."

- **Expected monthly cost:** **$0/month** during the initial validation phase (Koyeb free web service + Neon free Postgres). Realistically rising to **an estimated $5-20/month** once free-tier thresholds are exceeded (see cost table above) — genuinely low, calculated risk, not a large commitment.
- **Scaling path:** Free → Koyeb Eco paid instance (~$1.61-15/mo, incremental) + Neon Launch paid compute (pure pay-for-actual-usage, ~$0.106/CU-hour) → if the cohort grows well beyond 500, revisit instance size or Koyeb's Pro tier. No large jump is required at any step.
- **Reliability and backup plan:** Neon's scale-to-zero and copy-on-write storage underpin its point-in-time recovery; **in addition**, a periodic independent `pg_dump` export (e.g., a small scheduled job writing to cheap object storage) is recommended as a second, host-independent backup layer before any real student data is collected — not implemented this session (requires a scheduling mechanism that doesn't exist yet, and creating any external storage resource is outside Gate B1A's scope) but should be a named line item in Gate B1B.
- **Security controls:** rate limiting (implemented, Section 13), CORS locked to the exact production origin (already correct), secrets never committed (`.gitignore` confirmed), Anthropic/Zia integrations left disabled by default (implemented, Section 13) — and, as a hard gate, **authentication and ownership checks must exist before any real student record is stored**, not merely recommended.
- **Rollback path:** identical to the pattern already used successfully for the frontend — pause or delete the Koyeb service, drop the Neon project/branch, or simply stop pointing `NEXT_PUBLIC_API_URL` at it. No destructive step exists anywhere in this plan, and no data of real consequence exists anywhere until authentication is built.
- **Cost ceilings and alerts:** On Koyeb, **manually enable the 80%/100% billing alerts** (off by default) and keep the compute instance size fixed rather than autoscaling, since there is no hard spend cap to fall back on. On Neon, configure the per-project usage limit in addition to alerts. Review actual usage weekly during the free-tier validation window before committing to any paid tier size.
- **Architecture at 25 / 100 / 500 active students:** the same single small Koyeb instance + single Neon project serves all three sizes without an architectural change — only the paid-tier *size*, not the *shape*, changes as the cohort grows. This avoids premature large-scale SaaS infrastructure, matching the instruction to optimize for a small commercial cohort.

## 10. Exact proposed file changes still outstanding (not yet applied)

These remain proposals, not implemented, and are **not required to reach a working Koyeb deployment** — they are refinements for Gate B1B or later:

| File | Change | Required now? |
|---|---|---|
| A scheduled backup script (path TBD) | Independent `pg_dump` export as a second backup layer | No — Gate B1B, once a real database exists |
| `backend/app/routers/explanations.py` | A stricter, endpoint-specific rate limit on top of the new global default, given this is the AI-cost-sensitive endpoint | No — global default (implemented) is sufficient for the "before public exposure" bar; tighten once `CERTMASTERY_ANTHROPIC_API_KEY` is actually set |
| Auth router (new) | Required before any real student record is stored | No — explicitly out of scope for Gate B1A, hard-gated per the founder's own instruction |

## 11. Verification and rollback plan

Unchanged in substance: validate `/health` and `/tracks` against the new host, run `alembic upgrade head` against a clean Neon database, wire the frontend through a Vercel **Preview** environment variable first, verify the full flow, only then flip Production. Rollback is reverting the Vercel env var and pausing/deleting the Koyeb service and Neon project — no irreversible step, no real data at risk since auth doesn't exist yet.

## 12. Explicit non-goals reaffirmed

Per this session's instructions: no deployment, no paid service created, no Vercel setting touched, no credential requested or searched for, no MCP/advanced KSOR/payments/AI-remediation work begun, nothing committed or pushed.

---

## 13. Gate B1A implementation summary — exact changes made this session

All changes below are **local, uncommitted, and unpushed**. Full verification results are in the accompanying turn report.

| File | Change | Item |
|---|---|---|
| `backend/Procfile` (new) | `web: uvicorn app.main:app --host 0.0.0.0 --port $PORT` | 1 — deterministic Koyeb-compatible startup |
| `backend/.python-version` (new) | `3.13` — the latest version Koyeb's buildpack currently supports | 2 — pinned, actually tested (see below) |
| `backend/requirements.txt` | Added `slowapi>=0.1.9` | 4 — rate limiting |
| `backend/app/main.py` | Added a global 60/minute per-IP rate limit via `slowapi` (`SlowAPIMiddleware` + `default_limits`, applies to every route with no per-endpoint decorator needed); enhanced `/health` to run a real `SELECT 1` and report `database_reachable`/`status: degraded` on failure; added a comment confirming CORS is already an exact list, never a wildcard | 3, 4, 5 |
| `backend/tests/conftest.py` | Added one `autouse=True` fixture that disables the rate limiter for the test process only (all `TestClient` instances in the suite share one client address and would otherwise trip a real limit well before 333 tests finish) | Kept the existing test baseline green while adding item 4 |
| `backend/.env.example` | Added a commented example line showing the production CORS origin | 5 — documentation |
| `frontend/app/page.tsx` | User-facing error message no longer echoes the internal API URL (was: `Could not reach the API at http://localhost:8000.`); dev-only "start the backend" instructions now gated behind `NODE_ENV === "development"`; the "exam runner... are live" footer claim moved so it only renders when tracks actually loaded, never alongside the unreachable-API error | 6, 7 |

**Items 9 and 10 required no code change** — already true by construction: `ai_explanations_enabled`/`zia_enabled` are computed from whether their respective env vars are set (`config.py`), so leaving `CERTMASTERY_ANTHROPIC_API_KEY`/`CERTMASTERY_ZIA_MCP_TOKEN` unset keeps both disabled automatically, and `explanation_engine.py`'s fallback to `static_explanation` was untouched. Verified live below.

### Verification performed

1. **Actually tested against the pinned Python version**, not just declared it: installed Python 3.13.15 locally via `winget` (local dev tooling, not an external/cloud resource), created a fresh venv, installed `requirements.txt` including the new `slowapi` dependency, and ran the full suite:
   ```
   platform win32 -- Python 3.13.15, pytest-9.1.1
   collected 333 items
   333 passed, 1 warning in 50.03s
   ```
   Identical pass count to the Python 3.14 baseline — no regression from any of this session's changes.

2. **Live-verified the enhanced `/health` endpoint** by running the server on Python 3.13:
   ```
   {"status":"ok","version":"1.0.0","database_reachable":true,"ai_explanations_enabled":false,"zia_enabled":false}
   ```
   Confirms items 3, 9, and 10 simultaneously: DB check works, and both AI integrations report disabled with no env vars set.

3. **Live-verified rate limiting actually triggers**, not just that the code compiles: fired 65 rapid requests at `/health` — the first 60 returned `200`, requests 61-65 returned `429`, exactly at the configured boundary.

4. **Re-ran frontend type-check and build** after the `page.tsx` changes: `npx tsc --noEmit` → 0 errors; `npm run build` → success, same route list as before (`/`, `/methodology`, `/tracks/[code]`, `/tracks/[code]/exam`).

5. Verification server and its process were stopped and confirmed unreachable afterward; no process left running.

### What was not done (explicitly out of scope this session)

Deployment to Koyeb, creation of a Koyeb or Neon account/project, setting `CERTMASTERY_CORS_ORIGINS`/`CERTMASTERY_DATABASE_URL` anywhere live, any Vercel change, authentication/ownership work, KSOR/MCP work, payments, or live AI remediation. No commit, no push.

---

## 14. Koyeb CLI operational procedure (documented only — nothing in this section has been run)

Per the founder's Koyeb operational policy: the Koyeb CLI is the primary interface for deployment, the same way the Vercel CLI was used for the frontend. This section documents the exact commands for a *later, separately approved* deployment step. **No command in this section was executed this pass.** No install, no auth, no app/service/domain/secret/database/deployment was created.

### 14.1 Installation (read-only research; not yet installed)

On this Windows dev machine, the two viable options (no Windows-native installer is documented) are:

- **Docker (no local binary needed):** `docker pull koyeb/koyeb-cli:latest`
- **Direct binary:** download from the [koyeb/koyeb-cli GitHub releases page](https://github.com/koyeb/koyeb-cli/releases) and add it to `PATH`

On macOS/Linux, Koyeb also documents `brew install koyeb/tap/koyeb` and a shell installer (`curl -fsSL https://raw.githubusercontent.com/koyeb/koyeb-cli/master/install.sh | sh`), included here for completeness even though this session is on Windows.

**Installation itself will not happen until explicitly approved**, per the founder's instruction ("Do not install the CLI yet unless explicitly approved").

### 14.2 Authentication handoff (manual, interactive — never automated by Claude)

```
koyeb login
```

This opens an interactive browser-based or token-prompt flow. Per the founder's explicit policy: Claude will **never ask for, search for, read, print, or extract a stored Koyeb token**, and will never paste a token into chat, source code, command history, or documentation. The founder runs `koyeb login` themselves; Claude only issues commands *after* confirming the login succeeded via a read-only identity check.

### 14.3 Read-only preflight (run first, before any mutation is proposed)

| Command | Purpose |
|---|---|
| `koyeb whoami` | Confirm the active account before touching anything |
| `koyeb organizations list` | Confirm the active organization |
| `koyeb apps list` | Confirm no app named `claude-cert-mastery` (or similar) already exists |
| `koyeb services list` | Confirm no same-name service already exists |
| `koyeb apps describe claude-cert-mastery` (only if the prior list shows one exists) | Inspect an existing app before deciding whether to reuse or rename |

All five are **read-only**.

### 14.4 Proposed service-creation command (to be shown for approval before ever running)

```
koyeb apps init claude-cert-mastery \
  --git github.com/asadullah48/ClaudeCertMastery \
  --git-branch main \
  --env CERTMASTERY_DATABASE_URL=<PLACEHOLDER> \
  --env CERTMASTERY_CORS_ORIGINS=https://claude-cert-mastery.vercel.app \
  --checks 8000:http:/health/live
```

Notes on this exact command:
- `--git` points at this repository directly (Koyeb builds from Git, matching the Vercel pattern already used for the frontend) — the app root is `backend/`, which Koyeb's buildpack detection should find via the presence of `requirements.txt`/`Procfile`/`.python-version` there; if Koyeb requires an explicit subdirectory flag for a non-root app, that flag will need to be added and re-shown before running (not guessed here).
- `--env` values are shown as `<PLACEHOLDER>` — real values are supplied interactively or via the founder's own secure input at execution time, never typed into chat or committed.
- `--checks 8000:http:/health/live` wires Koyeb's own health check to the liveness-only endpoint (Section 5/13) — this is deliberate: a slow database must never make Koyeb kill an otherwise-healthy process. `CERTMASTERY_ANTHROPIC_API_KEY` and `CERTMASTERY_ZIA_MCP_TOKEN` are **deliberately absent** from this command, per the requirement to keep both integrations disabled for initial validation.
- This command **creates a resource** and will not be run without the founder explicitly approving this exact command text first, per policy item 7 ("Show the exact proposed create/update command before executing it").

### 14.5 Environment-variable setup procedure (names only — see Section 3)

Either inline via `--env KEY=VALUE` at creation time (above) or afterward via the Koyeb dashboard/CLI env-var update commands (exact update-command syntax to be confirmed against the live CLI help output at execution time, not guessed here). Only variable **names** are ever written into any command shown in chat or committed to this repo; values are supplied at the point of execution, outside of any document or transcript.

### 14.6 Deployment / status / log inspection (read-only)

| Command | Purpose |
|---|---|
| `koyeb services describe claude-cert-mastery` | Current service state, region, instance type |
| `koyeb deployments logs <deployment-id>` | Build/runtime logs for a specific deployment |
| `koyeb services logs claude-cert-mastery` | Live service logs |

All **read-only**.

### 14.7 Health-check verification (after any deployment)

1. `koyeb services describe claude-cert-mastery` → confirm `READY`/healthy state and note the assigned public URL.
2. `curl https://<assigned-koyeb-url>/health/live` → expect `{"status":"ok"}` — this is exactly what Koyeb's own configured health check (14.4) is already probing continuously.
3. `curl https://<assigned-koyeb-url>/health/ready` → expect `database_reachable: true` once `CERTMASTERY_DATABASE_URL` is set.
4. **Mandatory verification gate (per Section 6's SlowAPI/Koyeb finding):** confirm the `X-Forwarded-For`-based rate-limit key actually resolves to distinct values for two different real client IPs hitting the live deployment — this cannot be verified from a local session and must happen once real traffic (or two independent test clients) can reach the live URL.

### 14.8 Rollback procedure

- `koyeb services pause claude-cert-mastery` — immediately stops serving traffic without deleting the service or its configuration (fastest, fully reversible).
- To fully undo: `koyeb services delete claude-cert-mastery` then `koyeb apps delete claude-cert-mastery` — only with explicit approval, per policy item 14 ("Do not delete or replace resources without explicit approval").
- Frontend side: revert/remove the `NEXT_PUBLIC_API_URL` Vercel env var and redeploy — the same reversible pattern already used successfully for the frontend-only deployments.

### 14.9 Cleanup procedure (for a validation deployment that's no longer needed)

`koyeb services delete claude-cert-mastery` → `koyeb apps delete claude-cert-mastery` — both are **mutating and destructive**, require explicit approval each time, and are only relevant after a validation deployment has served its purpose.

### 14.10 Read-only vs. mutating, at a glance

| Read-only (safe to run anytime for inspection) | Mutating (requires explicit per-command approval) |
|---|---|
| `whoami`, `organizations list`, `apps list`, `apps describe`, `services list`, `services describe`, `deployments logs`, `services logs` | `login` (session-establishing, not data-mutating, but still requires the founder's own interactive action), `apps init`, env-var set/update, `services pause`, `services delete`, `apps delete` |

---

**Stopping here for approval, as instructed.**
