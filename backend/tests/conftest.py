"""Shared test fixtures."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.main import limiter as _rate_limiter  # noqa: E402
from app.services.blueprint import DomainWeight  # noqa: E402


@pytest.fixture(autouse=True)
def _disable_rate_limiting():
    """Gate B1A added a global per-IP rate limit (main.py). Every TestClient in this
    suite resolves to the same client address, so the full run would trip a
    production-appropriate limit well before finishing. Real traffic is unaffected --
    this only toggles the limiter's enabled flag for the test process.
    """
    _rate_limiter.enabled = False
    yield


@pytest.fixture(autouse=True)
def _dev_auth_mode(monkeypatch):
    """Production defaults to Clerk auth and fails closed without it. The suites that
    predate learner auth exercise evidence semantics through the single seeded dev
    user, so they run in explicit dev mode; test_auth_isolation.py switches back to
    clerk mode to test real token verification and cross-learner isolation."""
    from app.config import settings

    monkeypatch.setattr(settings, "auth_mode", "dev")
    yield

# The published CCAO-F blueprint. Used across suites so that a change to the real
# blueprint surfaces as a single failing constant rather than seven scattered ones.
CCAO_F_WEIGHTS = [
    DomainWeight("PTE", 1400, 1),
    DomainWeight("OEV", 2100, 2),
    DomainWeight("PMS", 1200, 3),
    DomainWeight("WISD", 1600, 4),
    DomainWeight("CKM", 1200, 5),
    DomainWeight("GRR", 1500, 6),
    DomainWeight("TRO", 1000, 7),
]

# Item counts the largest-remainder allocator must produce for a 60-item CCAO-F exam.
CCAO_F_ITEMS_AT_60 = {
    "PTE": 8, "OEV": 13, "PMS": 7, "WISD": 10, "CKM": 7, "GRR": 9, "TRO": 6,
}


@pytest.fixture
def ccao_weights():
    return list(CCAO_F_WEIGHTS)


@pytest.fixture
def healthy_bank():
    """A bank with 20 questions per domain -- enough to satisfy every quota."""
    return {
        w.code: list(range(i * 1000, i * 1000 + 20))
        for i, w in enumerate(CCAO_F_WEIGHTS)
    }
