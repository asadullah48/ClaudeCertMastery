"""Merchant-of-record boundary (Lemon Squeezy or Paddle -- not yet chosen).

The application owns entitlement semantics; a payment provider only ever supplies a
*verified* fact: "this learner bought this product, reference X". Everything
provider-specific (signature scheme, payload shape, product ids) lives inside one
`PaymentProvider` adapter and stops there -- only a `VerifiedPurchase` crosses into the
app, and only `apply_verified_purchase` turns it into access.

Deliberately NOT here yet: any HTTP endpoint. A webhook route is added together with
its first adapter, and it must call `verify_and_parse` (signature check) before
anything else -- unsigned or unverifiable events are never accepted.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Mapping, Protocol

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import entitlements
from app.models import LearnerEntitlement, User

PRODUCT_READINESS_PASS = "readiness_pass"


@dataclass(frozen=True)
class VerifiedPurchase:
    """A purchase whose provider signature has already been verified."""

    provider: str             # "lemonsqueezy" | "paddle"
    external_reference: str   # the provider's order/transaction id -- idempotency key
    learner_subject: str      # Clerk subject passed through checkout custom data
    product: str              # internal product code, mapped by the adapter
    occurred_at: datetime


class PaymentProvider(Protocol):
    name: str

    def verify_and_parse(self, headers: Mapping[str, str], raw_body: bytes) -> VerifiedPurchase | None:
        """Verify the provider signature over raw_body; raise on a bad signature.
        Return None for event types that do not grant access (refund handling is a
        later decision, not an implicit side effect)."""
        ...


# Adapters register here when a merchant of record is connected. Empty today, which is
# what keeps every "Unlock" button in its honest "checkout coming soon" state.
PROVIDERS: dict[str, PaymentProvider] = {}


def checkout_available() -> bool:
    return bool(PROVIDERS)


class PurchaseRejected(Exception):
    pass


def apply_verified_purchase(db: Session, purchase: VerifiedPurchase) -> LearnerEntitlement:
    """Translate a verified purchase into access. Idempotent: a re-delivered event
    returns the grant it already created. The caller commits."""
    if purchase.product != PRODUCT_READINESS_PASS:
        raise PurchaseRejected(f"unknown product {purchase.product!r}")
    user = db.scalar(select(User).where(User.auth_subject == purchase.learner_subject))
    if user is None:
        raise PurchaseRejected("purchase does not match a known learner")
    return entitlements.activate_readiness_pass(
        db,
        user,
        source="purchase",
        provider=purchase.provider,
        external_reference=purchase.external_reference,
        now=purchase.occurred_at,
    )
