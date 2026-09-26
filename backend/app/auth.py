"""Learner identity: who owns the evidence.

Every learner-private route resolves its user through `get_current_user`, never from a
request body, path or query value -- a client cannot name another learner.

Two modes (settings.auth_mode):

* ``clerk`` (the default, and what production runs): the request must carry
  ``Authorization: Bearer <Clerk session token>``. The token is an RS256 JWT verified
  against Clerk's published JWKS -- signature, expiry, issuer and authorised party
  (``azp`` must be one of our own frontend origins). The verified ``sub`` is the
  learner's stable identity and maps to ``users.auth_subject``. A first-seen subject
  is provisioned a fresh, empty learner row; it is never attached to existing
  evidence (linking the founder's historical evidence is a separately authorised,
  one-off backfill -- see scripts/link_founder_account.py).

* ``dev``: the pre-auth single-user behaviour (the seeded dev user owns everything).
  Only for local development and the existing test suite; it must be opted into
  explicitly, so a production deployment that forgets its auth settings fails closed
  (503) instead of silently sharing one identity again.

Authentication only establishes WHO owns evidence. It does not touch what the
evidence means -- scoring, first-exposure rules and readiness policy are unchanged.
"""

from __future__ import annotations

from functools import lru_cache

import jwt
from fastapi import Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.models import User

DEV_USER_EMAIL = "dev@certmastery.local"

# Placeholder email domain for provisioned learners. users.email is NOT NULL UNIQUE;
# Clerk session tokens carry no email by default, and the product never emails from
# this column, so the verified subject stands in until a real address is known.
PROVISIONED_EMAIL_DOMAIN = "learners.certmastery.invalid"


class AuthError(Exception):
    """A token that must not be trusted. Message is safe to return to the client."""


@lru_cache(maxsize=4)
def _jwks_client(jwks_url: str) -> jwt.PyJWKClient:
    # Keys are cached inside the client; a rotated kid triggers one refetch.
    return jwt.PyJWKClient(jwks_url, cache_keys=True, lifespan=3600)


def _signing_key(token: str) -> object:
    return _jwks_client(settings.clerk_jwks_endpoint).get_signing_key_from_jwt(token).key


def verify_session_token(token: str) -> dict:
    """Verify a Clerk session JWT and return its claims, or raise AuthError."""
    try:
        claims = jwt.decode(
            token,
            _signing_key(token),
            algorithms=["RS256"],
            issuer=settings.clerk_issuer,
            options={"require": ["exp", "iat", "sub", "iss"]},
            leeway=5,
        )
    except (jwt.PyJWTError, jwt.PyJWKClientError) as exc:
        raise AuthError("Invalid or expired session.") from exc

    # azp is Clerk's authorised-party claim: the origin the token was minted for.
    # Rejecting foreign origins stops a token issued to another site being replayed.
    azp = claims.get("azp")
    if azp is not None and azp not in settings.cors_origin_list:
        raise AuthError("Session was issued for a different site.")
    return claims


def _bearer_token(request: Request) -> str | None:
    header = request.headers.get("authorization", "")
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        return None
    return token.strip()


def _dev_user(db: Session) -> User:
    user = db.scalar(select(User).where(User.email == DEV_USER_EMAIL))
    if user is None:
        raise HTTPException(500, "Dev user missing. Run: python seed.py")
    return user


def _user_for_subject(db: Session, claims: dict) -> User:
    subject = claims["sub"]
    user = db.scalar(select(User).where(User.auth_subject == subject))
    if user is not None:
        return user

    email = claims.get("email") or f"{subject}@{PROVISIONED_EMAIL_DOMAIN}"
    user = User(
        auth_subject=subject,
        email=email,
        display_name=(claims.get("name") or "Learner")[:120],
    )
    db.add(user)
    try:
        db.commit()
    except IntegrityError:
        # Two first requests from the same new learner raced; the other one won.
        db.rollback()
        user = db.scalar(select(User).where(User.auth_subject == subject))
        if user is None:
            raise HTTPException(409, "Could not create learner account.") from None
    db.refresh(user)
    return user


def _resolve(request: Request, db: Session, *, required: bool) -> User | None:
    if settings.auth_mode == "dev":
        return _dev_user(db)
    if settings.auth_mode != "clerk" or not settings.clerk_issuer:
        # Fail closed: never fall back to a shared identity in a misconfigured deploy.
        raise HTTPException(503, "Sign-in is not configured on this server.")

    token = _bearer_token(request)
    if token is None:
        if required:
            raise HTTPException(401, "Sign in required.", headers={"WWW-Authenticate": "Bearer"})
        return None
    try:
        claims = verify_session_token(token)
    except AuthError as exc:
        raise HTTPException(401, str(exc), headers={"WWW-Authenticate": "Bearer"}) from exc
    return _user_for_subject(db, claims)


def get_current_user(request: Request, db: Session = Depends(get_db)) -> User:
    """The authenticated learner. 401 without a valid session."""
    return _resolve(request, db, required=True)  # type: ignore[return-value]


def get_optional_user(request: Request, db: Session = Depends(get_db)) -> User | None:
    """The learner if signed in, else None -- for public catalog routes that add
    per-learner detail when they can. An invalid token is still rejected (401)."""
    return _resolve(request, db, required=False)
