"""
ProviderApiKey — the user's upstream provider secrets, ENCRYPTED at rest.

Many keys per provider (this is what stacks free tiers / enables rotation). The
plaintext is never stored: `key_ciphertext` holds the Fernet ciphertext and
`key_masked` a safe preview (••••1234). `env_slot` maps the key to the
os.environ variable LiteLLM reads (e.g. GROQ_API_KEY_1), so a DB key activates a
deployment exactly like an .env key.
"""

from sqlalchemy import Column, BigInteger, String, Boolean, DateTime, LargeBinary, ForeignKey
from datetime import datetime, timezone

from app.core.database import Base, BigIntPK


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class ProviderApiKey(Base):
    __tablename__ = "provider_keys"

    id = Column(BigIntPK, primary_key=True, autoincrement=True)
    user_id = Column(BigInteger, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    provider_id = Column(BigInteger, ForeignKey("providers.id", ondelete="CASCADE"), nullable=False, index=True)
    label = Column(String, nullable=False)                       # 'Groq account #1'
    env_slot = Column(String, nullable=False, unique=True)       # 'GROQ_API_KEY_1'
    key_ciphertext = Column(LargeBinary, nullable=False)         # Fernet ciphertext
    key_masked = Column(String, nullable=False)                 # '••••4f2a'
    is_active = Column(Boolean, nullable=False, default=True)
    last_used_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), default=_utcnow)
    updated_at = Column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow)
