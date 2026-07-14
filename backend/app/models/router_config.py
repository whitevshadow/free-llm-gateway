"""
RouterConfig — litellm.Router behaviour knobs, ONE ROW PER USER.

The model list is NOT here; it comes from Deployment (via the v_live_deployments
view). This only holds how the router BEHAVES: strategy, retries, cooldowns.

Replaces the old single-row (id=1) `router_config` from the single-user design —
under multi-user, each user tunes their own. The PK is user_id.
"""

from datetime import datetime, timezone

from sqlalchemy import Column, BigInteger, Integer, String, DateTime, ForeignKey

from app.core.database import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class RouterConfig(Base):
    __tablename__ = "router_config"

    user_id = Column(
        BigInteger, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True,
    )
    routing_strategy = Column(String, nullable=False, default="usage-based-routing-v2")
    num_retries = Column(Integer, nullable=False, default=4)
    cooldown_time = Column(Integer, nullable=False, default=30)   # seconds benched after 429
    allowed_fails = Column(Integer, nullable=False, default=3)

    updated_at = Column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow)

    def __repr__(self) -> str:
        return f"<RouterConfig user={self.user_id} {self.routing_strategy!r}>"
