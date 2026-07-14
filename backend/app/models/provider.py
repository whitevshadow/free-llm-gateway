"""
Provider — an upstream destination (Groq, NVIDIA NIM, …). ADMIN-SEEDED.

Global reference data, no user scope. A user cannot create one; they only attach
their own keys to what an admin has seeded.

NO API KEY COLUMN, deliberately: a provider is a DESTINATION shared by all users,
while a key is a USER'S credential for it (see ProviderKey).

`slug` doubles as the LiteLLM prefix ('groq' -> 'groq/openai/gpt-oss-120b'). The
old `litellm_prefix` column is gone: it was always a copy of slug.

EVERY PROVIDER REQUIRES A KEY (Deployment.provider_key_id is NOT NULL), so
keyless/local providers such as Ollama are out of scope by construction. The old
`requires_key` column is gone — it promised something the schema could not honour.
"""

from datetime import datetime, timezone

from sqlalchemy import Column, String, Boolean, DateTime

from app.core.database import Base, BigIntPK


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Provider(Base):
    __tablename__ = "providers"

    id = Column(BigIntPK, primary_key=True, autoincrement=True)
    slug = Column(String, nullable=False, unique=True, index=True)  # 'groq' — also the prefix
    name = Column(String, nullable=False)                           # 'Groq'
    base_url = Column(String, nullable=True)                        # endpoint override
    enabled = Column(Boolean, nullable=False, default=True)
    docs_url = Column(String, nullable=True)

    created_at = Column(DateTime(timezone=True), default=_utcnow)
    updated_at = Column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow)

    def __repr__(self) -> str:
        return f"<Provider {self.slug!r}>"
