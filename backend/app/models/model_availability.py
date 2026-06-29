"""
ModelAvailability — snapshot of which concrete deployments actually work.

WHY THIS TABLE:
  The pool (and NVIDIA auto-discovery) registers far more models than a given
  account can actually call — e.g. NVIDIA lists `google/codegemma-7b` in its
  catalog but the function isn't enabled for every account, so it 404s on use.
  The manual probe (app/scripts/probe_models.py) pings every deployment once,
  in parallel, and records the result here. The Router then hides anything
  marked unavailable, so /v1/models and the Open WebUI picker only show models
  that genuinely respond.

ONE ROW PER CONCRETE MODEL:
  Keyed by the LiteLLM model string (e.g. "nvidia_nim/google/codegemma-7b").
  When several keys back the same concrete model, `available` is True if ANY
  of them answered — the Router still handles per-key cooldowns at runtime.
"""

from sqlalchemy import Column, Integer, String, Boolean, DateTime, Text
from datetime import datetime, timezone

from app.core.database import Base


class ModelAvailability(Base):
    __tablename__ = "model_availability"

    id = Column(Integer, primary_key=True, index=True)
    # The LiteLLM model string actually invoked, e.g. "groq/llama-3.3-70b-versatile".
    concrete_model = Column(String, unique=True, nullable=False, index=True)
    # Comma-joined virtual model names that route to this concrete model (info only).
    virtual_models = Column(String, nullable=True)
    # Provider prefix, e.g. "groq", "openrouter", "nvidia_nim".
    provider = Column(String, nullable=True, index=True)

    # Whether the model answered a 1-token ping (429/rate-limited counts as reachable).
    available = Column(Boolean, default=False, nullable=False, index=True)
    # Human-readable status: available | rate_limited | unavailable | auth_error | timeout | error
    status = Column(String, nullable=True)
    http_code = Column(Integer, nullable=True)
    latency_ms = Column(Integer, nullable=True)
    error = Column(Text, nullable=True)

    checked_at = Column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        index=True,
    )
