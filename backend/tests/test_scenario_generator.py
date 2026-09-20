"""Deterministic recommendation: weakest-domain selection, position tie-break, and
the no-prior-attempt fallback (plan Section 7.4).

Uses a disposable SQLite DB with a minimal hand-built fixture (not the full CCAO-F
seed) -- only Track/Domain/User/ExamAttempt/AttemptDomainScore rows are needed to
exercise this deterministic logic.
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.database import Base  # noqa: E402
from app.models import (  # noqa: E402
    AttemptDomainScore,
    AttemptStatus,
    Domain,
    ExamAttempt,
    Track,
    User,
)
from app.services.scenario_recommender import (  # noqa: E402
    ScenarioRecommenderError,
    recommend_domain_code,
)


@pytest.fixture
def db_session(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'scenario_rec.db'}")
    TestingSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    Base.metadata.create_all(engine)
    session = TestingSession()
    yield session
    session.close()


@pytest.fixture
def track_with_domains(db_session):
    """PTE(pos1)/OEV(pos2)/WISD(pos3), matching the real CCAO-F ordering convention."""
    track = Track(code="CCAO-F", name="AI Operator, Foundation")
    db_session.add(track)
    db_session.flush()
    domains = {
        "PTE": Domain(track_id=track.id, code="PTE", name="Prompting", weight_bps=1400, position=1),
        "OEV": Domain(track_id=track.id, code="OEV", name="Output Eval", weight_bps=2100, position=2),
        "WISD": Domain(track_id=track.id, code="WISD", name="Workflow", weight_bps=1600, position=3),
    }
    db_session.add_all(domains.values())
    db_session.flush()
    user = User(email="dev@certmastery.local", display_name="Dev")
    db_session.add(user)
    db_session.flush()
    db_session.commit()
    return track, domains, user


def _submitted_attempt(db_session, track, user, domain_scores: dict[str, float], domains):
    attempt = ExamAttempt(
        user_id=user.id, track_id=track.id, status=AttemptStatus.SUBMITTED,
        seed=1, submitted_at=datetime.now(timezone.utc),
    )
    db_session.add(attempt)
    db_session.flush()
    for code, pct in domain_scores.items():
        db_session.add(
            AttemptDomainScore(
                attempt_id=attempt.id, domain_id=domains[code].id,
                correct=int(pct / 10), total=10, percentage=pct,
                mastery_band="developing",
            )
        )
    db_session.commit()
    return attempt


class TestNoPriorAttemptFallback:
    def test_falls_back_to_position_one_domain(self, db_session, track_with_domains):
        track, domains, user = track_with_domains
        code = recommend_domain_code(db_session, user_id=user.id, track_code="CCAO-F")
        assert code == "PTE"  # position 1, stated deterministic default

    def test_unknown_track_raises(self, db_session, track_with_domains):
        with pytest.raises(ScenarioRecommenderError):
            recommend_domain_code(db_session, user_id=1, track_code="NOPE")


class TestWeakestDomainSelection:
    def test_recommends_the_lowest_scoring_domain(self, db_session, track_with_domains):
        track, domains, user = track_with_domains
        _submitted_attempt(
            db_session, track, user,
            {"PTE": 90.0, "OEV": 40.0, "WISD": 70.0}, domains,
        )
        code = recommend_domain_code(db_session, user_id=user.id, track_code="CCAO-F")
        assert code == "OEV"

    def test_only_the_most_recent_submitted_attempt_is_used(
        self, db_session, track_with_domains
    ):
        track, domains, user = track_with_domains
        _submitted_attempt(db_session, track, user, {"PTE": 20.0}, domains)
        _submitted_attempt(db_session, track, user, {"OEV": 20.0}, domains)
        # The second (most recent) attempt only scored OEV, so OEV is the only
        # candidate even though the earlier attempt's PTE score was also low.
        code = recommend_domain_code(db_session, user_id=user.id, track_code="CCAO-F")
        assert code == "OEV"


class TestPositionTieBreak:
    def test_equal_percentage_breaks_tie_by_domain_position_ascending(
        self, db_session, track_with_domains
    ):
        track, domains, user = track_with_domains
        _submitted_attempt(
            db_session, track, user,
            {"OEV": 50.0, "WISD": 50.0, "PTE": 90.0}, domains,
        )
        # OEV (position 2) and WISD (position 3) tie at 50%; OEV wins the tie-break.
        code = recommend_domain_code(db_session, user_id=user.id, track_code="CCAO-F")
        assert code == "OEV"


class TestNoModelCallAnywhere:
    def test_recommender_module_has_no_ai_or_mcp_import(self):
        import inspect

        from app.services import scenario_recommender

        source = inspect.getsource(scenario_recommender)
        for forbidden in ("anthropic", "mcp", "zia_client"):
            assert forbidden not in source.lower()
