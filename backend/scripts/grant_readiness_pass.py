"""Manually grant a 90-day Readiness Pass for one track (private beta, before checkout).

Operator tool, never an endpoint. Identify the learner by exactly ONE selector; the
match must be a single users row or the script refuses. Dry run by default -- nothing
is written without --execute.

    python scripts/grant_readiness_pass.py --subject user_XXXX --track CCAO-F --granted-by founder            # plan
    python scripts/grant_readiness_pass.py --subject user_XXXX --track CCAO-F --granted-by founder --execute  # apply

--track is required: a pass unlocks exactly that track and no other.

The only write is one new learner_entitlements row (source="manual_grant"). Evidence
tables are counted before and after inside the same transaction; any difference rolls
back. A learner with time left is extended from their current expiry, not overlapped.

Rollback: UPDATE learner_entitlements SET status='revoked' WHERE id=<printed grant id>;
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from sqlalchemy import select  # noqa: E402

from app import entitlements  # noqa: E402
from app.database import SessionLocal  # noqa: E402
from app.models import Track, User  # noqa: E402
from link_founder_account import evidence_counts  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    who = parser.add_mutually_exclusive_group(required=True)
    who.add_argument("--user-id", type=int, help="users.id")
    who.add_argument("--subject", help="Clerk user id (user_...)")
    who.add_argument("--email", help="exact users.email")
    parser.add_argument("--track", required=True, help="Track the pass is scoped to (e.g. CCAO-F)")
    parser.add_argument("--granted-by", required=True, help="Operator granting access (recorded)")
    parser.add_argument("--note", default="private beta", help="Recorded with the grant")
    parser.add_argument("--execute", action="store_true", help="Apply. Omit for a dry run.")
    args = parser.parse_args(argv)

    with SessionLocal() as db:
        track_code = args.track.strip()
        if db.scalar(select(Track.id).where(Track.code == track_code)) is None:
            print(f"REFUSE: unknown track {track_code!r}.")
            return 2
        if args.user_id is not None:
            matches = db.scalars(select(User).where(User.id == args.user_id)).all()
        elif args.subject is not None:
            matches = db.scalars(select(User).where(User.auth_subject == args.subject.strip())).all()
        else:
            matches = db.scalars(select(User).where(User.email == args.email.strip())).all()
        if len(matches) != 1:
            print(f"REFUSE: selector matched {len(matches)} learners; exactly one required.")
            return 2
        user = matches[0]
        user_id = user.id
        if user.auth_subject is None:
            print(f"REFUSE: users.id={user_id} has no sign-in (auth_subject is NULL); nobody could use this pass.")
            return 2

        access = entitlements.get_access(db, user, track_code)
        before = evidence_counts(db, user_id)
        print(f"learner users.id={user_id} auth_subject={user.auth_subject}")
        print(f"current {track_code} plan={access.plan} expires_at={access.expires_at}")
        print(f"evidence={before}")

        starts_at, expires_at = entitlements.plan_readiness_pass(db, user, track_code)
        print("plan:")
        print(
            f"  INSERT learner_entitlements(user_id={user_id}, plan=readiness_pass, track_code={track_code}, status=active, "
            f"starts_at={starts_at.isoformat()}, expires_at={expires_at.isoformat()}, "
            f"source=manual_grant, granted_by={args.granted_by!r})"
        )
        if not args.execute:
            print("DRY RUN: nothing written. Re-run with --execute to apply.")
            return 0

        grant = entitlements.activate_readiness_pass(
            db, user, track_code, source="manual_grant", granted_by=args.granted_by, note=args.note
        )
        after = evidence_counts(db, user_id)
        if after != before:
            db.rollback()
            print(f"ABORT: evidence counts moved ({before} -> {after}); rolled back.")
            return 3
        grant_id = grant.id
        db.commit()

    print(f"GRANTED: learner_entitlements.id={grant_id} ({track_code}) to users.id={user_id}. Evidence unchanged: {after}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
