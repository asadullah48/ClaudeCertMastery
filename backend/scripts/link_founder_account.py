"""One-off: bind the founder's pre-auth evidence to the founder's Clerk sign-in.

Before learner auth, every request resolved to the seeded dev user
(dev@certmastery.local), so all founder evidence is owned by that users row. This script
sets that row's `auth_subject` to the founder's Clerk user id. That is the whole
mutation: one column on one users row. No evidence row is read for writing, moved,
rescored or deleted -- attempts keep their ids, answers, first-exposure flags, scores
and timestamps, and learner_domain_states stays exactly as projected.

If the founder signed in BEFORE this runs, the backend already provisioned a fresh,
empty users row for that subject. The script then detaches it (auth_subject -> NULL,
row kept, never deleted) -- but only after proving it owns zero evidence. If it owns
any, the script refuses: someone has been using that account and a human must decide.

Dry run by default. Nothing is written without --execute.

    python scripts/link_founder_account.py --subject user_XXXXXXXX            # plan only
    python scripts/link_founder_account.py --subject user_XXXXXXXX --execute  # apply

Rollback (restores the exact pre-link state; evidence was never touched):

    UPDATE users SET auth_subject = NULL WHERE id = <dev_user_id>;
    -- only if a provisioned row was detached:
    UPDATE users SET auth_subject = '<subject>' WHERE id = <provisioned_id>;
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import func, select, update  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app.auth import DEV_USER_EMAIL  # noqa: E402
from app.database import SessionLocal  # noqa: E402
from app.models import (  # noqa: E402
    ExamAttempt,
    Flashcard,
    LearnerDomainState,
    ScenarioAttempt,
    ScenarioEvent,
    User,
    ZiaLearnerLink,
)

# Every table whose rows are owned by a users row. Child tables (attempt_items,
# attempt_domain_scores, scenario_step_attempts) hang off these by attempt id and
# follow their parent automatically.
OWNED = {
    "exam_attempts": ExamAttempt,
    "scenario_attempts": ScenarioAttempt,
    "scenario_events": ScenarioEvent,
    "learner_domain_states": LearnerDomainState,
    "flashcards": Flashcard,
    "zia_learner_links": ZiaLearnerLink,
}


def evidence_counts(db: Session, user_id: int) -> dict[str, int]:
    return {
        name: db.scalar(select(func.count()).select_from(model).where(model.user_id == user_id))
        for name, model in OWNED.items()
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--subject", required=True, help="Founder's Clerk user id (user_...)")
    parser.add_argument("--execute", action="store_true", help="Apply. Omit for a dry run.")
    args = parser.parse_args()
    subject = args.subject.strip()
    if not subject.startswith("user_"):
        print(f"REFUSE: {subject!r} does not look like a Clerk user id (user_...).")
        return 2

    with SessionLocal() as db:
        dev = db.scalar(select(User).where(User.email == DEV_USER_EMAIL))
        if dev is None:
            print("REFUSE: founder/dev user not found.")
            return 2
        if dev.auth_subject == subject:
            print(f"NO-OP: users.id={dev.id} is already linked to {subject}.")
            return 0
        if dev.auth_subject is not None:
            print(f"REFUSE: users.id={dev.id} is already linked to a DIFFERENT subject.")
            return 2

        dev_id = dev.id  # commit() expires ORM state; keep the plain id
        before = evidence_counts(db, dev_id)
        provisioned = db.scalar(select(User).where(User.auth_subject == subject))
        if provisioned is not None:
            owned = evidence_counts(db, provisioned.id)
            if any(owned.values()):
                print(f"REFUSE: users.id={provisioned.id} already holds evidence for {subject}: {owned}")
                return 2

        print(f"founder users.id={dev.id}  evidence={before}")
        print("plan:")
        if provisioned is not None:
            print(f"  UPDATE users SET auth_subject=NULL WHERE id={provisioned.id}  -- empty auto-provisioned row")
        print(f"  UPDATE users SET auth_subject='{subject}' WHERE id={dev.id}")
        if not args.execute:
            print("DRY RUN: nothing written. Re-run with --execute to apply.")
            return 0

        # One transaction: detach first so the unique index never sees two holders.
        if provisioned is not None:
            db.execute(
                update(User)
                .where(User.id == provisioned.id, User.auth_subject == subject)
                .values(auth_subject=None)
            )
        result = db.execute(
            update(User)
            .where(User.id == dev.id, User.auth_subject.is_(None))
            .values(auth_subject=subject)
        )
        if result.rowcount != 1:
            db.rollback()
            print("ABORT: founder row changed underneath us; rolled back.")
            return 3
        after = evidence_counts(db, dev.id)
        if after != before:
            db.rollback()
            print(f"ABORT: evidence counts moved ({before} -> {after}); rolled back.")
            return 3
        db.commit()

    print(f"LINKED: users.id={dev_id} -> {subject}. Evidence unchanged: {after}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
