"""
MasterModel — the WORKING (provider, model) node.

One row per provider_model that has at least one working key. Health here is a
ROLLUP across its per-key deployments: `is_working` is True if ANY key works, and
`working_key_count` counts live deployments. Per-key detail lives in the
`deployments` table, because exhaustion is per API key.
"""

from sqlalchemy import Column, BigInteger, Integer, String, Boolean, DateTime, ForeignKey
from datetime import datetime, timezone

from app.core.database import Base, BigIntPK


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class MasterModel(Base):
    __tablename__ = "master_model"

    id = Column(BigIntPK, primary_key=True, autoincrement=True)
    provider_model_id = Column(
        BigInteger, ForeignKey("provider_models.id", ondelete="CASCADE"),
        nullable=False, unique=True,
    )
    provider_id = Column(BigInteger, ForeignKey("providers.id", ondelete="CASCADE"), nullable=False, index=True)
    litellm_model = Column(String, nullable=False)              # denormalized concrete string
    normalized_name = Column(String, nullable=False, index=True)  # denormalized family key
    is_working = Column(Boolean, nullable=False, default=False, index=True)  # any key works
    working_key_count = Column(Integer, nullable=False, default=0)
    last_checked_at = Column(DateTime(timezone=True), nullable=True)

    created_at = Column(DateTime(timezone=True), default=_utcnow)
    updated_at = Column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow)
