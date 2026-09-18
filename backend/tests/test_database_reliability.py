"""Gate B1I: stale pooled connections are handled without masking real outages.

Production evidence: pooled PostgreSQL connections going idle long enough for Neon to
close the underlying TCP connection, surfacing as
`OperationalError: SSL connection has been closed unexpectedly` on the next query, and
self-healing on retry. That self-healing is the signature of a stale-connection-reuse
bug: SQLAlchemy's pool_pre_ping option is designed to eliminate exactly this by
liveness-checking a pooled connection before handing it to the caller.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import sessionmaker

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


class TestEnginePrePing:
    def test_application_engine_enables_pre_ping(self):
        # This is the actual engine the app serves requests with -- not a copy -- so a
        # passing test here means production really gets the behavior, not just some
        # engine built the same way.
        from app.database import engine

        assert engine.pool._pre_ping is True

    def test_pre_ping_is_set_unconditionally_not_only_for_sqlite(self):
        # Guards against a future change that only sets pool_pre_ping on one branch of
        # a database_url-conditional (e.g. only for sqlite, forgetting postgres) --
        # asserted from source rather than by constructing a real postgres engine,
        # since the postgres driver is not a dependency of the test environment.
        source = Path(ROOT, "app", "database.py").read_text(encoding="utf-8")
        create_engine_call = source[source.index("engine = create_engine(") :]
        assert "pool_pre_ping=True" in create_engine_call.split(")", 1)[0]


@pytest.fixture
def client(tmp_path_factory):
    """A TestClient wired to its own disposable SQLite database, mirroring the
    fixture in test_api.py -- not the shared module-scoped one, since these tests
    need to break and repair the DB dependency mid-test.
    """
    db_path = tmp_path_factory.mktemp("db") / "reliability.db"
    engine = create_engine(
        f"sqlite:///{db_path}", connect_args={"check_same_thread": False}
    )
    TestingSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    from app import models as _models  # noqa: F401  registers metadata
    from app.database import Base, get_db
    from app.main import app

    Base.metadata.create_all(engine)

    def override_get_db():
        db = TestingSession()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


class TestReadinessReportsRealDatabaseState:
    def test_readiness_reports_reachable_when_database_is_up(self, client):
        r = client.get("/health/ready")
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "ok"
        assert body["database_reachable"] is True

    def test_readiness_reports_unreachable_on_a_genuine_failure(self, client, monkeypatch):
        # Simulate a real outage: the session factory itself raises, the same shape of
        # error a dead connection or an unreachable host would produce. This must not
        # be swallowed into a false "healthy" -- pre-ping fixes stale reuse, it must
        # not also hide a database that is actually down.
        import app.main as main_module

        class _BrokenSession:
            def execute(self, *_args, **_kwargs):
                raise SQLAlchemyError("simulated outage")

            def close(self):
                pass

        monkeypatch.setattr(main_module, "SessionLocal", lambda: _BrokenSession())

        r = client.get("/health/ready")
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "degraded"
        assert body["database_reachable"] is False

    def test_readiness_response_never_names_the_database_url(self, client, monkeypatch):
        import app.main as main_module

        class _BrokenSession:
            def execute(self, *_args, **_kwargs):
                raise SQLAlchemyError("simulated outage: postgresql+psycopg://u:p@h/db")

            def close(self):
                pass

        monkeypatch.setattr(main_module, "SessionLocal", lambda: _BrokenSession())

        r = client.get("/health/ready")
        assert "postgresql" not in r.text
        assert "@h/db" not in r.text
