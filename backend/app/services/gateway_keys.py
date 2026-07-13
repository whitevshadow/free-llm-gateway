"""
Gateway API keys — mint / verify / revoke the bearer tokens that unlock /v1.

The raw token is shown ONCE at mint time; only its SHA-256 hash is stored, so a DB
leak never exposes usable keys. Verification hashes the presented token and looks
up an active, non-revoked row.
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
    """Create a key. Returns (raw_token, row). The raw token is not recoverable later."""
    token = generate_token()
    row = GatewayApiKey(
        user_id=user_id,
        name=name,
        key_hash=_hash(token),
        key_prefix=_display_prefix(token),
        is_active=True,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return token, row


def verify(db: Session, token: str) -> Optional[GatewayApiKey]:
    """Return the active key row matching this token, or None. Touches last_used_at."""
    if not token:
        return None
    row = (
        db.query(GatewayApiKey)
        .filter(GatewayApiKey.key_hash == _hash(token), GatewayApiKey.is_active.is_(True))
        .first()
    )
    if row:
        row.last_used_at = datetime.now(timezone.utc)
        db.commit()
    return row


def revoke(db: Session, key_id: int) -> bool:
    """Deactivate a key by id. Returns False if not found."""
    row = db.query(GatewayApiKey).filter(GatewayApiKey.id == key_id).first()
    if not row:
        return False
    row.is_active = False
    row.revoked_at = datetime.now(timezone.utc)
    db.commit()
    return True


def list_keys(db: Session) -> List[GatewayApiKey]:
    return db.query(GatewayApiKey).order_by(GatewayApiKey.id).all()
