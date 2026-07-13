"""
ProviderModel — the RAW model catalog, one unified table keyed by provider.

Everything a provider lists / auto-discovery finds, before any health check.
`normalized_name` is the family key used to group models across providers into
common models (e.g. 'groq/openai/gpt-oss-120b' → 'gpt-oss-120b').
"""

from sqlalchemy import (
    Column, BigInteger, Integer, String, Boolean, DateTime, ForeignKey,
    Enum, UniqueConstraint,
)
from datetime import datetime, timezone

from app.core.database import Base, BigIntPK
from app.models.enums import ModelMode


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class ProviderModel(Base):
    __tablename__ = "provider_models"

    id = Column(BigIntPK, primary_key=True, autoincrement=True)
    provider_id = Column(BigInteger, ForeignKey("providers.id", ondelete="CASCADE"), nullable=False, index=True)
    upstream_model_id = Column(String, nullable=False)          # id as the provider names it
    litellm_model = Column(String, nullable=False)             # full 'groq/openai/gpt-oss-120b'
    display_name = Column(String, nullable=True)
    normalized_name = Column(String, nullable=False, index=True)  # family key for grouping
    mode = Column(Enum(ModelMode, name="model_mode"), nullable=False, default=ModelMode.chat)
    context_window = Column(Integer, nullable=True)
    max_output_tokens = Column(Integer, nullable=True)
    is_free = Column(Boolean, nullable=False, default=True)
    supports_stream = Column(Boolean, nullable=False, default=True)
    enabled = Column(Boolean, nullable=False, default=True)

    discovered_at = Column(DateTime(timezone=True), default=_utcnow)
    updated_at = Column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow)

    __table_args__ = (
        UniqueConstraint("provider_id", "upstream_model_id", name="uq_provider_model"),
    )
