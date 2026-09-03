"""FastAPI application entry point."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.routers import attempts, exams, explanations, tracks, zia

app = FastAPI(
    title=settings.app_name,
    version=settings.version,
    description=(
        "Exam-prep platform for the Claude certification tracks. "
        "Blueprint-weighted practice exams with scaled scoring on the 100-1000 band."
    ),
)

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
app.include_router(zia.router)


@app.get("/health", tags=["meta"])
def health() -> dict:
    return {
        "status": "ok",
        "version": settings.version,
        # Lets the frontend show whether AI explanations are live without exposing
        # anything about the key itself.
        "ai_explanations_enabled": settings.ai_explanations_enabled,
        # Whether the Ask Zia companion panel is offered. Availability per
        # question still depends on a concept mapping existing.
        "zia_enabled": settings.zia_enabled,
    }
