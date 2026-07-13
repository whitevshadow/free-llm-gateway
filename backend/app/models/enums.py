"""
Shared enum types for the common-model routing spine.

These map to native PostgreSQL ENUM types (`model_health`, `model_mode`) and to
CHECK-constrained VARCHARs on SQLite, so the same ORM works in prod and in tests.
"""

import enum


class ModelHealth(str, enum.Enum):
    """Result of a 1-token probe against a concrete deployment."""

    available = "available"
    rate_limited = "rate_limited"   # 429 — reachable, temporarily throttled
    unavailable = "unavailable"
    auth_error = "auth_error"
    timeout = "timeout"
    error = "error"


class ModelMode(str, enum.Enum):
    """What a model is used for (a provider may list non-chat models too)."""

    chat = "chat"
    embedding = "embedding"
    rerank = "rerank"
    image = "image"
    audio = "audio"
