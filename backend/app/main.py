"""FastAPI application entry point."""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
from slowapi.util import get_remote_address
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.config import settings
from app.database import SessionLocal
from app.routers import attempts, exams, explanations, readiness, scenarios, tracks, zia


def rate_limit_key(request: Request) -> str:
    """Resolve the caller's address for rate-limiting, correctly behind Koyeb's edge.

    Koyeb terminates TLS at its edge and reverse-proxies every request to this app,
    appending the connecting IP to X-Forwarded-For as it forwards (Koyeb's own docs:
    "the last IP of the x-forwarded-for is the only IP Koyeb can certify as valid").
    Using the raw TCP peer address (plain get_remote_address) behind that proxy would
    return Koyeb's own edge address for every request, putting every customer in one
    shared bucket -- exactly what this must avoid. Using the whole header, or its
    first entry, would trust a value the client itself can prepend and spoof.
    Only the LAST hop is used here for that reason. No IP is logged anywhere; it is
    used only as an in-memory limiter key.

    This behavior is documented, not empirically verified against a live Koyeb
    deployment -- see docs/GATE-B1-BACKEND-DEPLOYMENT-PLAN.md Section 6 for the
    mandatory post-deployment verification gate before this is trusted in production.
    """
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        last_hop = forwarded.split(",")[-1].strip()
        if last_hop:
            return last_hop
    # No proxy header present (e.g. local dev, or a direct connection) -- fall back
    # to the direct TCP peer address.
    return get_remote_address(request)


app = FastAPI(
    title=settings.app_name,
    version=settings.version,
    description=(
        "Exam-prep platform for the Claude certification tracks. "
        "Blueprint-weighted practice exams with scaled scoring on the 100-1000 band."
    ),
)

# Gate B1A: a global per-IP default limit is the smallest safe control before the
# API is reachable from the public internet. It applies to every route without
# needing a decorator on each endpoint; a stricter, endpoint-specific limit can be
# layered on top later (e.g. for the AI explanation endpoint) without touching this.
# /health/live is explicitly exempted below -- a liveness probe must never be
# rate-limited, or a host's own health checks could take the service down.
limiter = Limiter(key_func=rate_limit_key, default_limits=["60/minute"])
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
app.add_middleware(SlowAPIMiddleware)

# allow_origins is an exact list from CERTMASTERY_CORS_ORIGINS -- never a wildcard --
# so only explicitly configured origins can make credentialed requests.
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(tracks.router)
app.include_router(exams.router)
app.include_router(attempts.router)
app.include_router(explanations.router)
app.include_router(scenarios.router)
app.include_router(readiness.router)
app.include_router(zia.router)


@app.get("/health/live", tags=["meta"])
@limiter.exempt
def health_live(request: Request) -> dict:
    """Process liveness only -- no database query, never rate-limited.

    This is the path to configure as Koyeb's health-check path: it answers as long
    as the process is up, so it cannot itself become a false-negative source (a slow
    or unreachable database must never make a host restart an otherwise-healthy
    process).
    """
    return {"status": "ok"}


@app.get("/health/ready", tags=["meta"])
def health_ready() -> dict:
    """Readiness: database connectivity plus current feature flags. Rate-limited
    like any other route -- it does real work (a query) and is not a liveness probe.
    """
    db_ok = True
    try:
        db = SessionLocal()
        try:
            db.execute(text("SELECT 1"))
        finally:
            db.close()
    except SQLAlchemyError:
        db_ok = False

    return {
        "status": "ok" if db_ok else "degraded",
        "version": settings.version,
        "database_reachable": db_ok,
        # Lets the frontend show whether AI explanations are live without exposing
        # anything about the key itself.
        "ai_explanations_enabled": settings.ai_explanations_enabled,
        # Whether the Ask Zia companion panel is offered. Availability per
        # question still depends on a concept mapping existing.
        "zia_enabled": settings.zia_enabled,
    }
