"""
Shared enum types for the common-model routing spine.

These map to the native PostgreSQL ENUM types `model_health` and `model_mode`,
which are created by schema.sql. Postgres only — SQLite has no enum type, and
this app does not support it.

The distinctions in ModelHealth carry weight: a 429 means the key WORKS and is
merely throttled (cool it down, retry soon), while an auth error means the key is
dead (no cooldown will ever fix it). Collapsing them into one boolean is exactly
what this enum exists to prevent.
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
