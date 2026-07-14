"""
ProviderKey — the user's OWN upstream secret, ENCRYPTED at rest.

The user picks a provider (from the admin-seeded catalog) and adds their key for
it. `key_ciphertext` holds the Fernet ciphertext; the plaintext is never stored
and never returned by the API. `key_masked` ('••••4f2a') is the UI-safe preview.

MANY KEYS PER (user, provider) is the load-bearing property of this app: free
tiers exhaust PER KEY, so two Groq keys = two independent budgets. That is why
this is a table and not a JSONB column on User.

Adding a row here FANS OUT into Deployment — one per model that provider serves.
That fan-out is how a user acquires callable models.

NO `env_slot` COLUMN. It used to name an os.environ variable that LiteLLM read.
That is fundamentally single-tenant: there is exactly one GROQ_API_KEY_1 per
PROCESS, so under multi-user whichever key landed there last would serve
everyone's traffic. Keys are now decrypted and passed per-deployment into
LiteLLM instead, so no two users ever share a namespace.
"""

from datetime import datetime, timezone

from sqlalchemy import (
    Column, BigInteger, String, Boolean, DateTime, LargeBinary, ForeignKey,
    UniqueConstraint,
)

from app.core.database import Base, BigIntPK


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class ProviderKey(Base):
    __tablename__ = "provider_keys"

    id = Column(BigIntPK, primary_key=True, autoincrement=True)
    user_id = Column(
        BigInteger, ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    provider_id = Column(
        BigInteger, ForeignKey("providers.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    label = Column(String, nullable=False)              # 'Groq account #1'
    key_ciphertext = Column(LargeBinary, nullable=False)  # Fernet ciphertext
    key_masked = Column(String, nullable=False)         # '••••4f2a'
    is_active = Column(Boolean, nullable=False, default=True)

    created_at = Column(DateTime(timezone=True), default=_utcnow)
    updated_at = Column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow)

    __table_args__ = (
        UniqueConstraint("user_id", "provider_id", "label"),
        # Composite-FK targets. These are what let Deployment prove, IN THE
        # DATABASE, that a key belongs to the same user (and the same provider)
        # as the deployment using it.
        UniqueConstraint("id", "user_id"),
        UniqueConstraint("id", "provider_id"),
    )

    def __repr__(self) -> str:
        return f"<ProviderKey id={self.id} user={self.user_id} {self.label!r}>"
