"""
GatewayApiKey — the user's "master key": the token clients present TO us.

Many per user (rotate/revoke without downtime). The raw token is shown ONCE at
creation; only its SHA-256 hash is stored, so a DB leak never exposes usable
keys. `key_prefix` is a non-secret display fragment (e.g. 'sk-gw-…4f2a').

THIS IS WHAT IDENTIFIES THE CALLER. Resolving a token -> user_id is what scopes
every request to that user's own provider keys, and what `is_admin` is read from.

`revoked_at` is the single source of truth for liveness; `is_active` is GENERATED
from it by Postgres, so the two can never disagree. To revoke, set revoked_at —
never assign to is_active (it is read-only; SQLAlchemy will not write it).
"""

from datetime import datetime, timezone

from sqlalchemy import (
    Column, BigInteger, String, Boolean, DateTime, ForeignKey, Computed,
)

from app.core.database import Base, BigIntPK


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class GatewayApiKey(Base):
    __tablename__ = "gateway_api_keys"

    id = Column(BigIntPK, primary_key=True, autoincrement=True)
    user_id = Column(
        BigInteger, ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    name = Column(String, nullable=False, default="default")
    key_hash = Column(String, nullable=False, unique=True, index=True)  # sha256(token)
    key_prefix = Column(String, nullable=False)                         # display fragment

    revoked_at = Column(DateTime(timezone=True), nullable=True)         # NULL = live
    # DB-generated: is_active := (revoked_at IS NULL). Read-only from Python.
    is_active = Column(Boolean, Computed("revoked_at IS NULL", persisted=True))

    last_used_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), default=_utcnow)

    def __repr__(self) -> str:
        return f"<GatewayApiKey id={self.id} user={self.user_id} {self.key_prefix!r}>"
