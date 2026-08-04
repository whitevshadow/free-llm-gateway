"""
Combo — a NAMED, ORDERED chain of routing targets, one row per user.

WHY THIS EXISTS
    `router_config` describes how the litellm Router behaves over the user's WHOLE
    pool: one flat set of deployments, one strategy. That answers "spread my
    traffic", not "call my free Kiro account first, then Qoder, and only then the
    paid one". A combo is that second thing: a user-defined candidate list with a
    named selection policy, addressable as a model name.

    So `model: "free-stack"` in an OpenAI request is legal here — the gateway
    resolves the combo, orders its targets by the combo's strategy and walks the
    list until one answers (app/services/combo_router.py).

WHY `models` AND `config` ARE JSONB, NOT CHILD TABLES
    A combo step is not a fixed tuple. The dashboard's builder writes steps of two
    kinds — a model step ({providerId, model, connectionId, allowedConnectionIds,
    weight, tags, prompt, …}) and a combo reference ({kind:"combo-ref", comboName})
    — and its Strategy stage writes an open-ended runtime config (retries, sticky
    limits, response validation, …). Normalising that into columns would mean a
    migration every time the builder grows a field, and would still have to store
    the long tail as JSON. The parts the SERVER routes on are read explicitly and
    validated in combo_router; the rest is UI state that only has to round-trip.

    What is NOT in JSON is anything the database must enforce: ownership
    (user_id FK, cascading), uniqueness of the name per user, and ordering.

NAMES ARE PER-USER UNIQUE, NOT GLOBAL
    Two users may both own a combo called "free-stack"; they resolve to different
    targets because resolution is always scoped by user_id. Uniqueness is enforced
    by the DB, not by an app-level check, so a race cannot create a duplicate that
    would make `model: "free-stack"` ambiguous.
"""

from datetime import datetime, timezone

from sqlalchemy import (
    Column, BigInteger, Integer, String, Text, Boolean, DateTime, ForeignKey,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB

from app.core.database import Base, BigIntPK


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Combo(Base):
    __tablename__ = "combos"

    id = Column(BigIntPK, primary_key=True, autoincrement=True)
    user_id = Column(
        BigInteger, ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )

    name = Column(String(120), nullable=False)
    description = Column(Text, nullable=True)

    # One of app.services.combo_router.STRATEGIES. Stored as text rather than a
    # native enum: the strategy list grows with the routing engine, and an unknown
    # value degrades to `priority` (definition order) instead of failing a call.
    strategy = Column(String(48), nullable=False, default="priority")

    # The ordered step list the builder produced. See the module docstring.
    models = Column(JSONB, nullable=False, default=list)
    # Runtime knobs + UI state (retries, sticky limits, response validation, …).
    config = Column(JSONB, nullable=False, default=dict)

    # Inactive combos stay listed and editable but refuse to serve traffic — the
    # dashboard's per-combo toggle. Hidden ones are filtered out of the list UI.
    is_active = Column(Boolean, nullable=False, default=True)
    is_hidden = Column(Boolean, nullable=False, default=False)

    # Manual ordering from the dashboard's drag-and-drop. NOT a routing signal:
    # target order inside a combo is `models`, this only orders the cards.
    sort_order = Column(Integer, nullable=False, default=0)

    created_at = Column(DateTime(timezone=True), default=_utcnow)
    updated_at = Column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow)

    __table_args__ = (
        UniqueConstraint("user_id", "name", name="uq_combos_user_name"),
    )

    def __repr__(self) -> str:
        return f"<Combo {self.name!r} user={self.user_id} {self.strategy!r}>"
