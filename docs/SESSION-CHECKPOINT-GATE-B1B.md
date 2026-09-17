# Session Checkpoint — Gate B1B (in progress)

**Purpose:** durable, accurate snapshot of exactly where this work stands, so it can resume cleanly after a break without re-deriving context. Not committed as part of this checkpoint save — see Phase 1 instructions.

**Date:** 2026-09-16

---

## Product priority

**Finish ClaudeCertMastery first.** This checkpoint exists to protect that priority across a pause, not to open new scope.

## Git state

- **Local `HEAD`:** `1408494` (`feat: prepare backend for Koyeb deployment`)
- **`origin/main`:** identical — `git status -sb` reports `## main...origin/main` with no ahead/behind marker. Fully synchronized.
- **Last 3 commits:**
  ```
  1408494 feat: prepare backend for Koyeb deployment
  d3f3cf7 fix: clarify practice scoring and patch frontend security
  02d2eb7 Docs: add an Author section with portfolio link
  ```
- **Working tree:** clean except two long-standing untracked planning documents (see below). Nothing staged, nothing else modified.

## Frontend production status

- **URL:** `https://claude-cert-mastery.vercel.app/`
- **Current status:** **Healthy.** `/` and `/methodology` both return `200`. Confirmed serving the correct Gate B1A content (`Practice score`, `independent preparation platform` disclaimer) — i.e., production reflects the last **successful manual** deployment (`vercel --prod` run from `frontend/` earlier this session), not a broken state.

## Persistent Vercel Git-build Root Directory problem — UNRESOLVED

The `claude-cert-mastery` Vercel project's **Root Directory** setting has been checked **four separate times** this session (via `vercel project inspect` and the Vercel API) and has **every time** reported `.` (repository root), despite the founder reporting the dashboard visibly showing `frontend` on at least two of those occasions. The project's `updatedAt` timestamp has not advanced across these checks, indicating the dashboard save is not actually persisting server-side.

**Concrete consequence:** the one Git-triggered production build attempted this session (`dpl_Az4F6WhbiwCETe8MBHTgf4tQctzG`, triggered automatically when commit `1408494` was pushed, since the Vercel↔GitHub integration was connected in an earlier turn) **failed** with `Couldn't find any 'pages' or 'app' directory. Please create one under the project root` — direct proof the build ran from repo root, not `frontend/`.

**Production was not affected** by this failure — Vercel never promotes a failed build to production, so the site kept serving the last successful manual deployment throughout.

## Vercel support case status

**No formal Vercel support case has been opened.** Only internal diagnosis was performed (log inspection via the approved read-only `vercel inspect ... --logs` command). A suggested next step (not yet taken) was to check the browser's network tab for the actual PATCH request the dashboard sends on save, or verify account permissions on this specific project field.

## Backend deployment preparation — committed and pushed

Commit `1408494` (already on `origin/main`) contains:
- `backend/Procfile` — Koyeb-compatible start command
- `backend/.python-version` — pinned to `3.13.15` (exact tested patch, per Koyeb's own requirement that the file contain all three version components)
- `backend/requirements.txt` — added `slowapi>=0.1.9` (tested against `0.1.10`)
- `backend/app/main.py` — split `/health` into `/health/live` (liveness only, rate-limit-exempt, documented as the Koyeb health-check path) and `/health/ready` (DB check + feature flags, rate-limited); global 60/minute rate limit using a Koyeb-aware `X-Forwarded-For`-last-hop key function (documented rationale for why the naive approach would share one bucket across all customers)
- `backend/tests/conftest.py` — autouse fixture disabling the rate limiter for the test suite
- `backend/tests/test_api.py` — updated for the split health endpoints
- `backend/tests/test_rate_limiting.py` — new isolated test proving the limiter and the `/health/live` exemption both work for real
- `backend/.env.example` — documented production CORS example
- `frontend/app/page.tsx` — no longer leaks the internal API URL in production errors; footer no longer claims the exam runner is "live" while the API is unreachable
- `frontend/lib/api.ts` — `api.health()` repointed to `/health/ready`

## Backend verification

**335 tests passed** on the pinned Python **3.13.15** runtime (a fresh venv, installed specifically to test against the exact pinned version — not assumed). Also cross-checked green on the original Python 3.14 dev environment. Frontend `tsc --noEmit` and `npm run build` both clean on the same commit.

## Koyeb CLI and account

- **Installed:** `koyeb.exe` v5.10.2, official GitHub release binary, checksum-verified, added to `PATH`.
- **Authenticated as:** ASADULLAH SHAFIQUE (`asadullahshafique@hotmail.com`), organization **`agenticengineer`** (ID `dc5059fa`), **Starter** plan, org status ACTIVE.

## Existing unrelated Koyeb resources (untouched)

| App | Service | Status | Notes |
|---|---|---|---|
| `level-hazel` | `cmt-stitching-system` | UNHEALTHY | Unrelated prior project — not modified, paused, or deleted |
| `bazaar` | `backend` | HEALTHY (currently SLEEPING) | Unrelated prior project — **occupies the organization's single Free Instance**, confirmed via `instance_types: type: free` in its deployment record. This is the reason `claude-cert-mastery` cannot also use `--instance-type free`. |

## Confirmed absent

- **No Koyeb app or service named `claude-cert-mastery`** (or similar) exists.
- **No Neon database or project exists** — Neon has not been signed up for or created in any way this session.
- **No production credentials have been configured anywhere** — Vercel has zero environment variables (confirmed earlier this session); Koyeb has zero secrets created; no database connection string exists yet anywhere.
- **`CERTMASTERY_ANTHROPIC_API_KEY` and `CERTMASTERY_ZIA_MCP_TOKEN` remain unset**, keeping both AI integrations disabled by design (the code already falls back gracefully — no code change needed to keep this true).

## Exact next approval gate

**Gate B1B — corrected Koyeb deployment command, still pending approval.** Specifically blocked on:
1. Choosing between `eco-micro` (512MB, ~$2.68/mo max) and `eco-small` (1GB, ~$5.36/mo max) for the ClaudeCertMastery Koyeb service, since the org's one free instance is already used by `bazaar`.
2. Finalizing the Neon-first database plan (region pairing, secret-based connection string handoff — never pasted in chat or a command).
3. Confirming Koyeb billing-alert setup (alerts only, no hard cap — residual risk to document) before any paid resource is created.
4. Producing and getting approval for the corrected `koyeb apps init` command.

**Nothing is authorized to be created, deployed, or billed until that command is explicitly approved.**

---

*Two untracked planning documents remain exactly as they were — not modified, staged, or committed by this checkpoint:*
- `CLAUDE-CODE-MASTER-IMPLEMENTATION-PROMPT.md`
- `ClaudeCertMastery-Commercial-Offering-Blueprint.md`
