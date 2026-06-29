"""
Model pool definitions — the curated deployments + router settings, in Postgres.

These tables are the DB-backed equivalent of config/model_pool.yaml. When
populated (seed with app/scripts/seed_pool_from_yaml.py), the Router builds from
here instead of the file, so you can add/disable models and tune routing by
editing rows — no file edit or image rebuild.

SECURITY: deployments store the *reference* to a key (e.g. "os.environ/GROQ_API_KEY_1"),
never the secret itself — exactly like the YAML. Resolution + dropping of unset
keys happens at Router build time, unchanged.
"""

from sqlalchemy import Column, Integer, String, Boolean, JSON, DateTime
from datetime import datetime, timezone

from app.core.database import Base


class ModelDeployment(Base):
    __tablename__ = "model_deployments"

    id = Column(Integer, primary_key=True, index=True)
    # Virtual model name clients request, e.g. "gpt-oss-120b", "auto".
    model_name = Column(String, nullable=False, index=True)
    # Concrete LiteLLM model string, e.g. "groq/openai/gpt-oss-120b".
    litellm_model = Column(String, nullable=False)
    # Key reference (NOT the secret): "os.environ/GROQ_API_KEY_1", or None (keyless).
    api_key_ref = Column(String, nullable=True)
    # Optional base URL or its env reference (e.g. Ollama).
    api_base_ref = Column(String, nullable=True)
    # Optional requests-per-minute hint for the Router.
    rpm = Column(Integer, nullable=True)
    # Any other litellm_params (free-form), merged in at build time.
    extra = Column(JSON, nullable=True)
    # Disabled rows are skipped (soft delete / quick toggle).
    enabled = Column(Boolean, default=True, nullable=False, index=True)

    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )


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
