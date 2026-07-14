"""
User — a thin identity row. THERE IS NO LOGIN.

The gateway key IS the credential: a caller proves who they are by presenting a
token from gateway_api_keys, which resolves to a user_id. Hence no password and
no name — nothing here is a secret.

`is_admin` is the entire RBAC model. Two fixed roles (admin, user) is a column,
not a roles table. Admins seed providers and mint gateway keys for users; users
add their own provider keys and call /v1.
"""

from datetime import datetime, timezone

from sqlalchemy import Column, String, Boolean, DateTime, text
from sqlalchemy.dialects.postgresql import UUID

from app.core.database import Base, BigIntPK


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class User(Base):
    __tablename__ = "users"

    id = Column(BigIntPK, primary_key=True, autoincrement=True)
    # server_default is load-bearing: without it SQLAlchemy sends an explicit
    # NULL for public_id and the NOT NULL constraint fires. The DB generates it.
    public_id = Column(
        UUID(as_uuid=True), server_default=text("gen_random_uuid()"), unique=True,
    )
    email = Column(String, unique=True, nullable=True)   # optional label, NOT a credential
    is_admin = Column(Boolean, nullable=False, default=False)
    is_active = Column(Boolean, nullable=False, default=True)

    created_at = Column(DateTime(timezone=True), default=_utcnow)
    updated_at = Column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow)

    def __repr__(self) -> str:
        role = "admin" if self.is_admin else "user"
        return f"<User id={self.id} {self.email!r} ({role})>"
