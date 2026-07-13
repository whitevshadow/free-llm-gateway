"""
Router configuration — behaviour knobs for the litellm.Router, in Postgres.

A single-row table (id=1) the Router builder reads to configure routing strategy,
retries, cooldowns and cross-model fallbacks. Kept from the legacy pool design;
the model list itself now comes from the common-model spine (provider_models →
master_model → deployments → common_model), not from this module.
"""

from sqlalchemy import Column, Integer, String, JSON, DateTime
from datetime import datetime, timezone

from app.core.database import Base


class RouterConfig(Base):
    """Single-row table (id=1) holding litellm.Router behaviour."""

    __tablename__ = "router_config"

    id = Column(Integer, primary_key=True)
    routing_strategy = Column(String, default="usage-based-routing-v2")
    num_retries = Column(Integer, default=4)
    cooldown_time = Column(Integer, default=30)
    allowed_fails = Column(Integer, default=3)
    # List of {primary_model: [fallback_model, ...]} dicts.
    fallbacks = Column(JSON, nullable=True)

    updated_at = Column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )
