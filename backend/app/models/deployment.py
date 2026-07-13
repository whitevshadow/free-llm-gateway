"""
Deployment — the ATOMIC callable unit: one master_model × one provider API key.

This is exactly what LiteLLM registers as a model_list entry. Multiple keys for
the same provider+model = multiple rows here, each with its OWN health and
cooldown, so one exhausted free-tier key is benched independently while its
siblings keep serving.
"""

from sqlalchemy import (
    Column, BigInteger, Integer, String, Boolean, DateTime, Text, ForeignKey,
    Enum, UniqueConstraint,
)
from datetime import datetime, timezone

from app.core.database import Base, BigIntPK
from app.models.enums import ModelHealth


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Deployment(Base):
    __tablename__ = "deployments"

    id = Column(BigIntPK, primary_key=True, autoincrement=True)
    master_model_id = Column(BigInteger, ForeignKey("master_model.id", ondelete="CASCADE"), nullable=False, index=True)
    provider_key_id = Column(BigInteger, ForeignKey("provider_keys.id", ondelete="CASCADE"), nullable=False, index=True)
    litellm_model = Column(String, nullable=False)             # concrete string (denormalized)

    is_working = Column(Boolean, nullable=False, default=True, index=True)
    status = Column(Enum(ModelHealth, name="model_health"), nullable=False, default=ModelHealth.available)
    http_code = Column(Integer, nullable=True)
    latency_ms = Column(Integer, nullable=True)
    error = Column(Text, nullable=True)
    rpm = Column(Integer, nullable=True)                       # per-key requests/min hint
    cooldown_until = Column(DateTime(timezone=True), nullable=True)  # optional persisted cooldown
    last_checked_at = Column(DateTime(timezone=True), default=_utcnow)
    last_used_at = Column(DateTime(timezone=True), nullable=True)

    created_at = Column(DateTime(timezone=True), default=_utcnow)
    updated_at = Column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow)

    __table_args__ = (
        UniqueConstraint("master_model_id", "provider_key_id", name="uq_deployment_model_key"),
    )
