"""
CommonModel + CommonModelMember — the auto-derived virtual models + fallback chain.

A CommonModel is a normalized_name served by >= 2 providers: it is the "virtual"
model a client requests (e.g. 'gpt-oss-120b'). Its members form the ordered
fallback chain — priority 0 = primary, ascending = fallback order. Within a
member, the per-key deployments load-balance beneath it.
"""

from sqlalchemy import (
    Column, BigInteger, Integer, String, Text, Boolean, DateTime, ForeignKey,
    UniqueConstraint,
)
from sqlalchemy.orm import relationship
from datetime import datetime, timezone

from app.core.database import Base, BigIntPK


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class CommonModel(Base):
    __tablename__ = "common_model"

    id = Column(BigIntPK, primary_key=True, autoincrement=True)
    name = Column(String, nullable=False, unique=True, index=True)   # 'gpt-oss-120b'
    display_name = Column(String, nullable=True)
    description = Column(Text, nullable=True)
    provider_count = Column(Integer, nullable=False, default=0)      # distinct providers serving it
    is_active = Column(Boolean, nullable=False, default=True)
    auto_generated = Column(Boolean, nullable=False, default=True)

    created_at = Column(DateTime(timezone=True), default=_utcnow)
    updated_at = Column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow)
    refreshed_at = Column(DateTime(timezone=True), default=_utcnow)  # last auto-derive pass

    members = relationship(
        "CommonModelMember",
        back_populates="common_model",
        cascade="all, delete-orphan",
        order_by="CommonModelMember.priority",
    )


class CommonModelMember(Base):
    __tablename__ = "common_model_members"

    id = Column(BigIntPK, primary_key=True, autoincrement=True)
    common_model_id = Column(BigInteger, ForeignKey("common_model.id", ondelete="CASCADE"), nullable=False, index=True)
    master_model_id = Column(BigInteger, ForeignKey("master_model.id", ondelete="CASCADE"), nullable=False, index=True)
    priority = Column(Integer, nullable=False, default=0)            # 0 = primary, then 1,2,…

    created_at = Column(DateTime(timezone=True), default=_utcnow)

    common_model = relationship("CommonModel", back_populates="members")

    __table_args__ = (
        UniqueConstraint("common_model_id", "master_model_id", name="uq_member_model"),
        UniqueConstraint("common_model_id", "priority", name="uq_member_priority"),
    )
