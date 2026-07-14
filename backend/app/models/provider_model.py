"""
ProviderModel — THE model table: everything every provider lists. Global catalog.

This is NOT "the models a user can call" — that is Deployment. A user reaches a
catalog model only THROUGH one of their own keys.

`normalized_name` is the FAMILY key ('gpt-oss-120b') that clients request:
'groq/openai/gpt-oss-120b' and 'nvidia_nim/openai/gpt-oss-120b' both normalize to
it, and that shared key is what makes them interchangeable.

⚠ BOTH FLAGS BELOW ARE CROSS-USER. NEITHER IS A ROUTING SIGNAL.

    is_common     "2+ providers serve this family, somewhere in the world."
    is_reachable  "at least ONE user, somewhere, can currently call this."

is_reachable=True does NOT mean the CALLER can call it — it may be another user's
key that works. These exist for the admin/status page. To route, or to show a
user their own models, read the per-user views (app/models/views.py), which are
built from that user's own deployments.

provider_count, is_common and is_reachable are ALL maintained by Postgres
triggers (see schema.sql). Never assign to them from Python — let the DB
recompute them when the catalog or health changes.
"""

from datetime import datetime, timezone

from sqlalchemy import (
    Column, BigInteger, Integer, String, Boolean, DateTime, ForeignKey,
    UniqueConstraint, Computed,
)
from sqlalchemy.dialects.postgresql import ENUM as PGEnum

from app.core.database import Base, BigIntPK
from app.models.enums import ModelMode


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class ProviderModel(Base):
    __tablename__ = "provider_models"

    id = Column(BigIntPK, primary_key=True, autoincrement=True)
    provider_id = Column(
        BigInteger, ForeignKey("providers.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    upstream_model_id = Column(String, nullable=False)   # id as the provider names it
    litellm_model = Column(String, nullable=False)       # 'groq/openai/gpt-oss-120b'
    display_name = Column(String, nullable=True)
    normalized_name = Column(String, nullable=False, index=True)  # 'gpt-oss-120b'
    publisher = Column(String, nullable=True)   # normalized org: 'Meta', 'Mistral AI'

    # create_type=False: schema.sql owns the ENUM type; SQLAlchemy must not
    # emit CREATE TYPE for it.
    mode = Column(
        PGEnum(ModelMode, name="model_mode", create_type=False),
        nullable=False, default=ModelMode.chat,
    )
    context_window = Column(Integer, nullable=True)
    max_output_tokens = Column(Integer, nullable=True)
    is_free = Column(Boolean, nullable=False, default=True)
    supports_stream = Column(Boolean, nullable=False, default=True)
    enabled = Column(Boolean, nullable=False, default=True)

    # ── catalog-wide flags — TRIGGER-MAINTAINED, read-only from Python ──
    provider_count = Column(Integer, nullable=False, default=1)
    is_common = Column(Boolean, Computed("provider_count >= 2", persisted=True))
    is_reachable = Column(Boolean, nullable=False, default=False)

    discovered_at = Column(DateTime(timezone=True), default=_utcnow)
    updated_at = Column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow)

    __table_args__ = (
        UniqueConstraint("provider_id", "upstream_model_id"),
        # Composite-FK target: lets Deployment prove key and model share a provider.
        UniqueConstraint("id", "provider_id"),
    )

    def __repr__(self) -> str:
        return f"<ProviderModel {self.litellm_model!r} common={self.is_common}>"
