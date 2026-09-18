"""Database engine, session factory and declarative base."""

from __future__ import annotations

from collections.abc import Iterator

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import settings

# check_same_thread is a SQLite-only concern: FastAPI serves requests from a thread
# pool, and SQLite otherwise refuses a connection created on another thread.
connect_args = (
    {"check_same_thread": False} if settings.database_url.startswith("sqlite") else {}
)

# pool_pre_ping=True: production has shown pooled PostgreSQL connections going stale
# (Neon closing the underlying TCP connection while it sits idle in the pool), which
# surfaced as `OperationalError: SSL connection has been closed unexpectedly` on the
# first query after the idle period -- and self-healed on retry, which is exactly the
# symptom pre-ping eliminates: it runs a cheap liveness check before handing a pooled
# connection to the caller and transparently reconnects if that check fails, instead of
# handing back a connection that dies on first use. Harmless for SQLite too.
engine = create_engine(
    settings.database_url, connect_args=connect_args, pool_pre_ping=True, future=True
)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


class Base(DeclarativeBase):
    pass


def get_db() -> Iterator[Session]:
    """FastAPI dependency yielding a request-scoped session."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
