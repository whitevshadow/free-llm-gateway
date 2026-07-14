"""
Deployment — the ATOMIC callable unit, AND the user's model list.

(user × catalog model × that user's key) = exactly one entry in litellm's
model_list. Two keys for the same provider+model = two rows, each with its OWN
health and cooldown, so one exhausted free-tier key is benched while its siblings
keep serving. That per-key independence is the reason this gateway exists.

This table IS "the models a user can call". A user cannot see a model they hold
no key for, because the row can only EXIST through their key — enforced by
foreign key, not by a WHERE clause someone could forget.

TWO INVARIANTS, ENFORCED BY POSTGRES (composite FKs in schema.sql), not here:

  (a) SAME OWNER    — a deployment can only use a key belonging to the same user.
      Alice physically cannot spend Bob's Groq quota, even given a bug in the app.
  (b) SAME PROVIDER — the key and the model must belong to the same provider.
      A Groq key cannot be bound to an NVIDIA model.

SQLAlchemy cannot express composite FKs against a *pair* of columns as cleanly as
raw DDL, so they live in schema.sql (which is the source of truth). The
ForeignKeyConstraints below mirror them so the ORM knows the join paths.

HEALTH: `status` is the source of truth; `is_working` is GENERATED from it by the
DB, so a probe can never write is_working=True alongside status='auth_error'.

⚠ is_working IS NOT "callable". A rate-limited deployment whose cooldown has
EXPIRED is not `available`, but IS callable again — that is what makes a 429'd
free-tier key self-heal. Use the is_callable() SQL function (or the
v_live_deployments view); never filter the router on is_working alone.
"""

from datetime import datetime, timezone

from sqlalchemy import (
    Column, BigInteger, Integer, String, Boolean, DateTime, Text, ForeignKey,
    ForeignKeyConstraint, UniqueConstraint, Computed,
)
from sqlalchemy.dialects.postgresql import ENUM as PGEnum

from app.core.database import Base, BigIntPK
from app.models.enums import ModelHealth


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Deployment(Base):
    __tablename__ = "deployments"

    id = Column(BigIntPK, primary_key=True, autoincrement=True)
    user_id = Column(
        BigInteger, ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    provider_id = Column(
        BigInteger, ForeignKey("providers.id", ondelete="CASCADE"),
        nullable=False,
    )
    provider_model_id = Column(BigInteger, nullable=False, index=True)
    provider_key_id = Column(BigInteger, nullable=False, index=True)

    # ── per-key health: the probe writes here ──
    status = Column(
        PGEnum(ModelHealth, name="model_health", create_type=False),
        nullable=False, default=ModelHealth.available,
    )
    # DB-generated from status. Read-only from Python.
    is_working = Column(Boolean, Computed("status = 'available'", persisted=True))

    http_code = Column(Integer, nullable=True)
    latency_ms = Column(Integer, nullable=True)
    error = Column(Text, nullable=True)
    rpm = Column(Integer, nullable=True)                       # per-key requests/min hint
    cooldown_until = Column(DateTime(timezone=True), nullable=True)  # benched after a 429

    last_checked_at = Column(DateTime(timezone=True), nullable=False, default=_utcnow)
    last_used_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), default=_utcnow)
    updated_at = Column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow)

    __table_args__ = (
        UniqueConstraint("provider_model_id", "provider_key_id"),
        # (a) same owner
        ForeignKeyConstraint(
            ["provider_key_id", "user_id"],
            ["provider_keys.id", "provider_keys.user_id"],
            ondelete="CASCADE",
        ),
        # (b) same provider, on both sides
        ForeignKeyConstraint(
            ["provider_key_id", "provider_id"],
            ["provider_keys.id", "provider_keys.provider_id"],
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["provider_model_id", "provider_id"],
            ["provider_models.id", "provider_models.provider_id"],
            ondelete="CASCADE",
        ),
    )

    def __repr__(self) -> str:
        return (
            f"<Deployment id={self.id} user={self.user_id} "
            f"model={self.provider_model_id} key={self.provider_key_id} "
            f"status={self.status}>"
        )
