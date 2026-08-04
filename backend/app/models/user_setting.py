"""
UserSetting — per-user dashboard preferences.

WHAT BELONGS HERE
    Choices that shape what a user SEES or how their requests are shaped on the
    way out, but that the router does not decide with: hidden models, paid-model
    filtering, per-provider parameter denylists, web-tool interception toggles.

WHAT DOES NOT
    Routing behaviour. `routing_strategy`, `num_retries`, `cooldown_time`,
    `allowed_fails` and `pinned_provider_id` live in `router_config`, which is a
    typed table the Router reads directly (SRS §7.1). Duplicating them into an
    untyped blob would create a second source of truth for the one thing the
    gateway must not be ambiguous about.

WHY KEY/VALUE AND NOT COLUMNS
    The ported dashboard writes a wide, open-ended and still-growing set of UI
    preferences. Giving each a column would mean a migration per checkbox, and a
    schema that documents someone else's UI rather than this gateway's domain.
    A JSONB value keyed by name keeps that churn out of the schema entirely.

    The trade is real: nothing validates these. Anything the ROUTER reads must
    therefore be a typed column, not a key here — that boundary is the whole
    reason this table is safe.

Values are per-user. Two users hiding different models do not collide.
"""

from datetime import datetime, timezone

from sqlalchemy import Column, BigInteger, String, DateTime, ForeignKey, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB

from app.core.database import Base, BigIntPK


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class UserSetting(Base):
    __tablename__ = "user_settings"

    id = Column(BigIntPK, primary_key=True, autoincrement=True)
    user_id = Column(
        BigInteger, ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    # Dotted namespacing keeps per-provider config addressable without another
    # table, e.g. "paramFilters.nvidia_nim", "interception.groq".
    key = Column(String(160), nullable=False)
    value = Column(JSONB, nullable=False)

    created_at = Column(DateTime(timezone=True), nullable=False, default=_utcnow)
    updated_at = Column(
        DateTime(timezone=True), nullable=False, default=_utcnow, onupdate=_utcnow
    )

    __table_args__ = (UniqueConstraint("user_id", "key", name="uq_user_settings_user_key"),)

    def __repr__(self) -> str:
        return f"<UserSetting user={self.user_id} {self.key!r}>"
