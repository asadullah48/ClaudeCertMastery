"""Gate B1A hardening: proves the rate limiter that ships to production actually
works, and that /health/live stays exempt from it.

Isolated on purpose: every other test file relies on the suite-wide autouse
fixture in conftest.py that disables rate limiting (so 333 tests sharing one
TestClient address don't trip a 60/minute limit). This is the one place that
fixture is deliberately overridden, for exactly the length of this test, so the
limiter itself is exercised for real rather than only existing in code no test
ever runs.
"""

from __future__ import annotations

import sys
from pathlib import Path

from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.main import app, limiter  # noqa: E402

DEFAULT_LIMIT_PER_MINUTE = 60


def test_ready_endpoint_enforces_limit_and_live_stays_exempt():
    limiter.enabled = True  # override the suite-wide autouse fixture, for this test only
    try:
        with TestClient(app) as client:
            # Every request up to the configured threshold must succeed.
            for i in range(DEFAULT_LIMIT_PER_MINUTE):
                res = client.get("/health/ready")
                assert res.status_code == 200, f"request {i + 1} unexpectedly limited"

            # The next request, over the threshold, must be rejected.
            over_limit = client.get("/health/ready")
            assert over_limit.status_code == 429

            # /health/live is explicitly exempt from the limiter (main.py). It must
            # keep answering even though /health/ready, on the same client key, has
            # just been rate-limited -- proving the exemption is real, not merely
            # that the two paths happen not to share load yet.
            for i in range(DEFAULT_LIMIT_PER_MINUTE + 5):
                res = client.get("/health/live")
                assert res.status_code == 200, f"live request {i + 1} was limited"
    finally:
        limiter.enabled = False
