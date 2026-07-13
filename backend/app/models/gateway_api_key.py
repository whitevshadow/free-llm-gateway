"""
GatewayApiKey — a bearer token that unlocks the /v1 compatibility port.

Many per user (rotate/revoke without downtime). The raw token is shown ONCE at
creation; only its SHA-256 hash is stored, so a DB leak never exposes usable
keys. `key_prefix` is a non-secret display fragment (e.g. 'sk-gw-…4f2a').
"""

from sqlalchemy import Column, BigInteger, String, Boolean, DateTime, ForeignKey
from datetime import datetime, timezone

from app.core.database import Base, BigIntPK


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class GatewayApiKey(Base):
    __tablename__ = "gateway_api_keys"

    id = Column(BigIntPK, primary_key=True, autoincrement=True)
    user_id = Column(BigInteger, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    name = Column(String, nullable=False, default="default")
    key_hash = Column(String, nullable=False, unique=True, index=True)  # sha256(token)
    key_prefix = Column(String, nullable=False)                         # display fragment
    is_active = Column(Boolean, nullable=False, default=True, index=True)
    last_used_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), default=_utcnow)
    revoked_at = Column(DateTime(timezone=True), nullable=True)
