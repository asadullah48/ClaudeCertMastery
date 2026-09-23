"""KSOR Slice 7: the best-effort integration boundary between an authoritative
evidence-completion transaction and the disposable readiness projection.

Gate C3, Slice 7. This module exists so attempts.py/scenarios.py don't each
duplicate the same try/except/rollback/log logic (gate Section 10). It calls
Slice 3's existing `recompute_learner_domain_state` unchanged -- no redesign of
that service's self-commit contract -- and contains any failure it raises so a
learner's already-successful, already-committed submission is never turned into
an error response because derived projection refresh (not evidence) failed.

Central invariant (gate Section 1/4/7): evidence persistence must never depend on
projection maintenance succeeding. `LearnerDomainState` is derived, disposable,
and rebuildable (Slice 3); a failed refresh here is degraded freshness, not lost
evidence -- the next successful recompute (or a future backfill) repairs it.
"""

from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from app.services.learner_readiness import recompute_learner_domain_state

logger = logging.getLogger(__name__)


def best_effort_recompute_learner_domain_state(
    db: Session, *, user_id: int, track_id: int, domain_id: int
) -> bool:
    """Refresh one learner-domain readiness projection. MUST be called only after
    the caller's own authoritative evidence transaction has already committed
    successfully (never before, never in the same transaction) -- this function
    commits its own, separate transaction via recompute_learner_domain_state's
    existing Slice 3 contract, which is exactly what keeps a projection failure
    from ever being able to roll back evidence that was already durably written.

    Never raises. On failure: the exception is logged (with identifiers, no
    payload/answer content) and the session is rolled back to a clean, reusable
    state -- required so that a sibling domain's own recompute attempt (a single
    exam can affect several domains; gate Section 13) is never poisoned by an
    earlier domain's failed transaction. The broad `except Exception` here mirrors
    this codebase's existing best-effort pattern for non-evidence, degradable
    operations (explanation_engine.py's own "never raise" AI-fallback path) --
    never used around code that writes evidence.

    Returns True on a successful refresh, False on a contained failure. Callers
    must never let this return value affect the evidence-submission response's
    success -- it exists for logging/observability only, never for the learner-
    facing contract (gate Section 30).
    """
    try:
        recompute_learner_domain_state(
            db, user_id=user_id, track_id=track_id, domain_id=domain_id
        )
        return True
    except Exception as exc:  # noqa: BLE001 - see docstring: never raise
        logger.warning(
            "Readiness projection refresh failed for user_id=%s track_id=%s "
            "domain_id=%s: %s: %s",
            user_id, track_id, domain_id, type(exc).__name__, exc,
        )
        db.rollback()
        return False
