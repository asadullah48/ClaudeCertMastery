"""Gate B1I: seed.py must never mutate content once real candidate data exists.

Every test here builds its own throwaway SQLite engine and monkeypatches seed.py's
module-level `engine`/`SessionLocal` to point at it, then drives seed.main() through
sys.argv -- exactly like a real invocation, but never touching the database the app
is actually configured against.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import seed  # noqa: E402
from app import models as _models  # noqa: E402,F401  registers metadata
from app.database import Base  # noqa: E402
from app.models import AnswerOption, AttemptItem, ExamAttempt, Question, Track, User  # noqa: E402


@pytest.fixture
def seed_db(tmp_path, monkeypatch):
    db_path = tmp_path / "seed_safety.db"
    test_engine = create_engine(
        f"sqlite:///{db_path}", connect_args={"check_same_thread": False}
    )
    TestSession = sessionmaker(bind=test_engine, autoflush=False, autocommit=False)
    # Tables must exist up front: some tests insert an attempt/attempt-item row before
    # ever calling seed.main() (which is what would normally create them), to prove the
    # guard fires even against a database that already has a schema but no content yet.
    Base.metadata.create_all(test_engine)

    monkeypatch.setattr(seed, "engine", test_engine)
    monkeypatch.setattr(seed, "SessionLocal", TestSession)

    yield TestSession
    test_engine.dispose()


def run_seed(monkeypatch, *args: str) -> int:
    monkeypatch.setattr(sys, "argv", ["seed.py", *args])
    return seed.main()


class TestSeedSafetyGuard:
    def test_empty_database_permits_seeding(self, seed_db, monkeypatch):
        exit_code = run_seed(monkeypatch)
        assert exit_code == 0
        with seed_db() as db:
            assert db.scalar(select(Track).where(Track.code == "CCAO-F")) is not None

    def test_existing_attempt_blocks_seeding_before_content_mutation(self, seed_db, monkeypatch):
        with seed_db() as db:
            user = User(email="candidate@example.com", display_name="Candidate")
            db.add(user)
            db.flush()
            track = Track(
                code="CCAO-F",
                name="placeholder",
                item_count=60,
                duration_minutes=120,
                pass_scaled_score=720,
                pass_raw_threshold=0.70,
                price_usd=99.00,
                validity_months=12,
                is_seeded=False,
            )
            db.add(track)
            db.flush()
            db.add(ExamAttempt(user_id=user.id, track_id=track.id, seed=1))
            db.commit()

        with pytest.raises(SystemExit) as exc_info:
            run_seed(monkeypatch)
        assert exc_info.value.code != 0

        # No content mutation happened: the track row is still the untouched placeholder,
        # never overwritten with the real CCAO-F name/description/pricing.
        with seed_db() as db:
            reloaded = db.scalar(select(Track).where(Track.code == "CCAO-F"))
            assert reloaded.name == "placeholder"
            assert db.scalar(select(Question).limit(1)) is None

    def test_existing_attempt_item_also_blocks_seeding(self, seed_db, monkeypatch):
        with seed_db() as db:
            # SQLite does not enforce foreign keys by default, and this guard must fire
            # on the mere presence of an attempt_items row regardless of what it points
            # to -- so no real attempt/question/domain needs to exist for this test.
            db.execute(
                AttemptItem.__table__.insert().values(
                    attempt_id=999,
                    question_id=999,
                    domain_id=999,
                    position=1,
                    selected_option_ids=[],
                )
            )
            db.commit()

        with pytest.raises(SystemExit) as exc_info:
            run_seed(monkeypatch)
        assert exc_info.value.code != 0

    def test_rejected_seed_leaves_content_and_option_ids_unchanged(self, seed_db, monkeypatch):
        run_seed(monkeypatch)  # initial seed: safe, database is empty

        with seed_db() as db:
            before_question_ids = sorted(db.scalars(select(Question.id)).all())
            before_option_ids = sorted(db.scalars(select(AnswerOption.id)).all())
            user = db.scalar(select(User))
            track = db.scalar(select(Track).where(Track.code == "CCAO-F"))
            db.add(ExamAttempt(user_id=user.id, track_id=track.id, seed=1))
            db.commit()

        with pytest.raises(SystemExit):
            run_seed(monkeypatch)

        with seed_db() as db:
            after_question_ids = sorted(db.scalars(select(Question.id)).all())
            after_option_ids = sorted(db.scalars(select(AnswerOption.id)).all())

        assert after_question_ids == before_question_ids
        assert after_option_ids == before_option_ids

    def test_reset_does_not_bypass_the_guard(self, seed_db, monkeypatch):
        run_seed(monkeypatch)

        with seed_db() as db:
            user = db.scalar(select(User))
            track = db.scalar(select(Track).where(Track.code == "CCAO-F"))
            db.add(ExamAttempt(user_id=user.id, track_id=track.id, seed=1))
            db.commit()
            question_count_before = len(db.scalars(select(Question)).all())

        with pytest.raises(SystemExit) as exc_info:
            run_seed(monkeypatch, "--reset")
        assert exc_info.value.code != 0

        # --reset must not have dropped anything: the guard runs before drop_all.
        with seed_db() as db:
            assert len(db.scalars(select(Question)).all()) == question_count_before
            assert db.scalar(select(ExamAttempt)) is not None

    def test_normal_initial_seed_creates_the_expected_catalogue(self, seed_db, monkeypatch):
        exit_code = run_seed(monkeypatch)
        assert exit_code == 0

        with seed_db() as db:
            tracks = {t.code for t in db.scalars(select(Track)).all()}
            assert tracks == {"CCAO-F", "CCDV-F", "CCAR-F", "CCAR-P"}
            assert len(db.scalars(select(Question)).all()) == 112
            assert db.scalar(select(User).where(User.email == seed.DEV_USER_EMAIL)) is not None
