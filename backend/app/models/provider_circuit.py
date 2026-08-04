"""
ProviderCircuit — the provider-level circuit breaker (SRS §14).

WHY THIS IS GLOBAL AND NOT PER-USER
    Every other health signal in this gateway is per (user, key, model), because
    every other failure is: a 401 says YOUR key is dead, a 429 says YOUR key is
    throttled. Those must never leak between users.

    A 502 is different. It says the PROVIDER is down, which is a fact about the
    provider and true for everyone at once. Tracking it per-user would make each
    user discover the same outage independently, each paying their own failures
    to learn it — and would make "is Groq up?" unanswerable.

    SRS §24 anticipated exactly this: "health is per key, keys are per user.
    Provider-wide outage detection may later be derived read-only." This is that,
    made explicit rather than derived.

WHAT MAY TRIP IT
    ONLY provider-level statuses: 408, 500, 502, 503, 504. Never 401/403/429 —
    those are account-level and belong to key cooldown and model lockout
    (services/prober.py, SRS §7.3). Mixing them would let one user's exhausted
    free tier open a circuit for every other user of that provider, which is the
    single worst failure mode this table could have.

STATES
    closed     normal. Traffic flows.
    degraded   elevated failures, still serving. A warning, not a gate.
    open       provider skipped entirely until `opened_until`.
    half_open  the cooldown lapsed; ONE probe is allowed through to test it.

RECOVERY IS LAZY
    Nothing sweeps this table. `opened_until` is compared at read time, so an
    expired OPEN becomes HALF_OPEN the moment someone looks. A background timer
    would add a moving part to buy nothing.
"""

from datetime import datetime, timezone

from sqlalchemy import (
    Column, BigInteger, Integer, String, DateTime, Text, ForeignKey, UniqueConstraint,
)

from app.core.database import Base, BigIntPK


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class ProviderCircuit(Base):
    __tablename__ = "provider_circuits"

    id = Column(BigIntPK, primary_key=True, autoincrement=True)
    provider_id = Column(
        BigInteger, ForeignKey("providers.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )

    # 'closed' | 'degraded' | 'open' | 'half_open'
    state = Column(String(16), nullable=False, default="closed")

    # Consecutive provider-level failures. Reset by a success, not by time —
    # a provider that fails, sits idle, then fails again has still failed twice.
    failure_count = Column(Integer, nullable=False, default=0)

    # When the current OPEN expires. NULL unless state == 'open'.
    opened_until = Column(DateTime(timezone=True), nullable=True)

    last_status_code = Column(Integer, nullable=True)
    last_error = Column(Text, nullable=True)
    last_failure_at = Column(DateTime(timezone=True), nullable=True)
    last_success_at = Column(DateTime(timezone=True), nullable=True)

    # How many times this circuit has opened. Never reset — a provider that
    # opens weekly looks identical to a healthy one on `state` alone.
    open_count = Column(Integer, nullable=False, default=0)

    created_at = Column(DateTime(timezone=True), nullable=False, default=_utcnow)
    updated_at = Column(
        DateTime(timezone=True), nullable=False, default=_utcnow, onupdate=_utcnow
    )

    __table_args__ = (UniqueConstraint("provider_id", name="uq_provider_circuits_provider"),)

    def __repr__(self) -> str:
        return f"<ProviderCircuit provider={self.provider_id} {self.state}>"
