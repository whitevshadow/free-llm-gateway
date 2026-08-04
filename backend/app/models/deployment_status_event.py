"""
DeploymentStatusEvent — the health timeline (SRS §20).

WHY A TABLE AND NOT A DERIVED VIEW
    `deployments` stores only the CURRENT status. It is overwritten in place by
    every probe and every live request, so the moment a throttled key recovers,
    the evidence that it was ever throttled is gone. SRS §20 asks for a health
    timeline — "when a key got throttled, when its cooldown expired, when a probe
    revived it" — and that is not recoverable from a column that keeps no history.

WHAT IS RECORDED
    TRANSITIONS ONLY. A row is written when a deployment's status actually
    CHANGES, never on every probe. The re-probe loop runs every 20 minutes over
    every unhealthy deployment (SRS §10); recording each result would write
    thousands of identical `unavailable → unavailable` rows a day and bury the
    handful of events that mean something.

    `source` distinguishes a probe from real traffic, because they answer
    different questions: a probe transition says the gateway went looking, a
    traffic transition says a user's request hit it.

RETENTION
    Nothing prunes this table yet. At transition-only volume that is fine for a
    long time, but it grows without bound — see the note in §20 of
    OMNIROUTE_INTEGRATION.md before running this anywhere long-lived.

This is append-only. Rows are never updated, so a timeline can be read without
worrying that history has been rewritten under it.
"""

from datetime import datetime, timezone

from sqlalchemy import (
    Column, BigInteger, Integer, String, DateTime, Text, ForeignKey, Index,
)
from sqlalchemy.dialects.postgresql import ENUM as PGEnum

from app.core.database import Base, BigIntPK
from app.models.enums import ModelHealth


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class DeploymentStatusEvent(Base):
    __tablename__ = "deployment_status_events"

    id = Column(BigIntPK, primary_key=True, autoincrement=True)

    deployment_id = Column(
        BigInteger, ForeignKey("deployments.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    # Denormalised so the timeline can be scoped to a user without joining
    # through deployments — and so an event survives being read after the
    # deployment it describes is gone.
    user_id = Column(
        BigInteger, ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )

    # NULL for the very first observation of a deployment: there is no prior
    # state to have come from, and inventing one would show a fake transition.
    from_status = Column(
        PGEnum(ModelHealth, name="model_health", create_type=False), nullable=True,
    )
    to_status = Column(
        PGEnum(ModelHealth, name="model_health", create_type=False), nullable=False,
    )

    # 'probe' | 'request' | 'cooldown_expiry'
    source = Column(String(24), nullable=False, default="probe")

    http_code = Column(Integer, nullable=True)
    latency_ms = Column(Integer, nullable=True)
    error = Column(Text, nullable=True)
    # Set when the transition benched the deployment, so the timeline can draw
    # the width of a cooldown rather than just its start.
    cooldown_until = Column(DateTime(timezone=True), nullable=True)

    created_at = Column(DateTime(timezone=True), nullable=False, default=_utcnow, index=True)

    __table_args__ = (
        # The timeline query is always "this user, newest first", optionally
        # narrowed to one deployment.
        Index("ix_dse_user_created", "user_id", "created_at"),
        Index("ix_dse_deployment_created", "deployment_id", "created_at"),
    )

    def __repr__(self) -> str:
        frm = self.from_status.value if self.from_status else "—"
        return f"<StatusEvent dep={self.deployment_id} {frm}→{self.to_status.value}>"
