"""
ProviderKey — API keys configured at runtime through the Settings page.

WHY A TABLE:
  Keys can come from two places: the environment / .env (set at deploy time) and
  the Settings UI (set at runtime). UI-configured keys are stored here so they
  survive restarts (the SQLite file lives on a Docker volume). At startup they are
  injected into os.environ, which is exactly where the model pool's
  `os.environ/<VAR>` references read from — so a key saved in the UI activates its
  deployments the same way an .env key would.

SECURITY NOTE:
  Keys are stored as-is (plaintext) in the local SQLite DB. This is a local, free
  gateway tool; the DB is not exposed. They are NEVER returned in full by the API —
  only a masked preview. For a hardened deployment, encrypt `value` with SECRET_KEY.
"""

from sqlalchemy import Column, Integer, String, DateTime
from datetime import datetime, timezone

from app.core.database import Base


class ProviderKey(Base):
    __tablename__ = "provider_keys"

    id = Column(Integer, primary_key=True, index=True)
    # The environment variable slot this key fills, e.g. "GROQ_API_KEY_1".
    env_var = Column(String, unique=True, nullable=False, index=True)
    # Display provider id, e.g. "groq" (derived from the deployment model string).
    provider = Column(String, nullable=True)
    # The secret value.
    value = Column(String, nullable=False)

    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )
