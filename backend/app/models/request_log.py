"""
RequestLog — append-only usage / analytics ledger for the /v1 spine.

One row per call, with per-key attribution: which gateway key, the requested
model, resolved common_model, and the exact deployment + provider key that
answered. FKs use ON DELETE SET NULL so history survives key/model cleanups.

NOTE: kept distinct from the legacy `token_usage_logs` table — this one carries
the new spine's model/key foreign keys.
"""

from sqlalchemy import Column, BigInteger, Integer, String, Text, Boolean, DateTime, Numeric, ForeignKey
from datetime import datetime, timezone

from app.core.database import Base, BigIntPK


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class RequestLog(Base):
    __tablename__ = "request_logs"

    id = Column(BigIntPK, primary_key=True, autoincrement=True)
    gateway_api_key_id = Column(BigInteger, ForeignKey("gateway_api_keys.id", ondelete="SET NULL"), nullable=True, index=True)
    requested_model = Column(String, nullable=False)                    # what the client asked for
    common_model_id = Column(BigInteger, ForeignKey("common_model.id", ondelete="SET NULL"), nullable=True, index=True)
    answered_model_id = Column(BigInteger, ForeignKey("master_model.id", ondelete="SET NULL"), nullable=True)
    answered_deploy_id = Column(BigInteger, ForeignKey("deployments.id", ondelete="SET NULL"), nullable=True)
    provider_key_id = Column(BigInteger, ForeignKey("provider_keys.id", ondelete="SET NULL"), nullable=True, index=True)
    provider_id = Column(BigInteger, ForeignKey("providers.id", ondelete="SET NULL"), nullable=True, index=True)

    prompt_tokens = Column(Integer, nullable=False, default=0)
    completion_tokens = Column(Integer, nullable=False, default=0)
    total_tokens = Column(Integer, nullable=False, default=0)
    cost = Column(Numeric(12, 6), nullable=False, default=0)
    latency_ms = Column(Integer, nullable=True)
    status_code = Column(Integer, nullable=False, default=200)
    is_fallback = Column(Boolean, nullable=False, default=False)         # did it leave the primary?
    error_message = Column(Text, nullable=True)

    created_at = Column(DateTime(timezone=True), default=_utcnow, index=True)
