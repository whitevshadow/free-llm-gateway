"""
Gateway API keys — mint / verify / revoke the bearer tokens that unlock /v1.

The raw token is shown ONCE at mint time; only its SHA-256 hash is stored, so a DB
leak never exposes usable keys. Verification hashes the presented token and looks
up a live row.

An ADMIN mints a key FOR a user (`mint(db, user_id=...)`), which is how a user
gets into the system — there is no signup.
"""

import hashlib
import secrets
import logging
from datetime import datetime, timezone
from typing import List, Optional, Tuple

from sqlalchemy.orm import Session

from app.models.gateway_api_key import GatewayApiKey

logger = logging.getLogger("gateway.keys")

_TOKEN_PREFIX = "sk-gw-"


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _display_prefix(token: str) -> str:
    """Non-secret fragment for listings, e.g. 'sk-gw-A1b2…9f2a'."""
    head = token[: len(_TOKEN_PREFIX) + 4]
    return f"{head}…{token[-4:]}"


def generate_token() -> str:
    return _TOKEN_PREFIX + secrets.token_urlsafe(32)


def mint(db: Session, user_id: int, name: str = "default") -> Tuple[str, GatewayApiKey]:
    """
    Create a key for a specific user. Returns (raw_token, row); the raw token is
    not recoverable afterwards.

    Note we do NOT set is_active — it is a GENERATED column (revoked_at IS NULL).
    A fresh row has revoked_at = NULL, so it is live by construction.
    """
    token = generate_token()
    row = GatewayApiKey(
        user_id=user_id,
        name=name,
        key_hash=_hash(token),
        key_prefix=_display_prefix(token),
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return token, row


def verify(db: Session, token: str) -> Optional[GatewayApiKey]:
    """Return the live key row matching this token, or None. Touches last_used_at."""
    if not token:
        return None
    row = (
        db.query(GatewayApiKey)
        .filter(
            GatewayApiKey.key_hash == _hash(token),
            GatewayApiKey.revoked_at.is_(None),   # is_active is generated from this
        )
        .first()
    )
    if row:
        row.last_used_at = datetime.now(timezone.utc)
        db.commit()
    return row


def revoke(db: Session, key_id: int, user_id: Optional[int] = None) -> bool:
    """
    Revoke a key by setting revoked_at (is_active follows automatically — it is a
    generated column and cannot be assigned).

    Pass user_id to scope the revoke to that user's own keys, so a non-admin can
    never revoke someone else's key by guessing an id.
    """
    q = db.query(GatewayApiKey).filter(GatewayApiKey.id == key_id)
    if user_id is not None:
        q = q.filter(GatewayApiKey.user_id == user_id)
    row = q.first()
    if not row:
        return False
    row.revoked_at = datetime.now(timezone.utc)
    db.commit()
    return True


def list_keys(db: Session, user_id: Optional[int] = None) -> List[GatewayApiKey]:
    """All keys, or just one user's. Admin passes None; a user passes their id."""
    q = db.query(GatewayApiKey)
    if user_id is not None:
        q = q.filter(GatewayApiKey.user_id == user_id)
    return q.order_by(GatewayApiKey.id).all()
