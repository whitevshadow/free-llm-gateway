"""
Provider — an upstream LLM provider (Groq, NVIDIA NIM, OpenRouter, Ollama, …).

Global reference data (no user scope). Seeded once; referenced by provider keys
and by the model catalog.
"""

from sqlalchemy import Column, String, Boolean, DateTime
from datetime import datetime, timezone

from app.core.database import Base, BigIntPK


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Provider(Base):
    __tablename__ = "providers"

    id = Column(BigIntPK, primary_key=True, autoincrement=True)
    slug = Column(String, nullable=False, unique=True, index=True)   # 'groq'
    name = Column(String, nullable=False)                           # 'Groq'
    litellm_prefix = Column(String, nullable=False)                 # prefix in litellm str
    base_url = Column(String, nullable=True)                        # override / local
    requires_key = Column(Boolean, nullable=False, default=True)    # Ollama = False
    enabled = Column(Boolean, nullable=False, default=True)
    docs_url = Column(String, nullable=True)

    created_at = Column(DateTime(timezone=True), default=_utcnow)
    updated_at = Column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow)
