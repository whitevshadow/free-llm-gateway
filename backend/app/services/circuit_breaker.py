"""
Provider circuit breaker — layer 1 of the three-layer resilience model.

Ported from OmniRoute's docs/architecture/RESILIENCE_GUIDE.md onto this
gateway's data model. The three layers and their scopes:

  1. PROVIDER CIRCUIT   this module. Whole provider. Trips on upstream faults.
  2. KEY COOLDOWN       services/prober.py + core/llm_router.py. One key.
                        Trips on 429, with the escalating ladder (SRS §7.3).
  3. MODEL LOCKOUT      the same per-deployment state, scoped to one
                        (provider, key, model) triple (SRS §6.1).

Keeping them separate is the entire point. A 429 must bench ONE KEY, never a
provider — otherwise one user exhausting a free tier takes the provider away
from everyone. A 502 must bench the PROVIDER, because retrying its other keys
just spends more failures learning the same thing.

TRIP CODES ARE A WHITELIST, NOT A BLACKLIST
    Only 408/500/502/503/504 count. Anything else — including every auth and
    rate-limit status — is explicitly not a provider fault. Inverting this (trip
    on "anything not 2xx") is the mistake the guide warns about, and it silently
    turns a billing problem into a provider outage.

STATE IS DURABLE, RECOVERY IS LAZY
    The state lives in Postgres so it survives a restart and is shared across
    processes (unlike LiteLLM's in-memory cooldowns). Nothing sweeps it: an
    expired OPEN is reclassified at read time by `_refresh`.
"""

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Dict, Iterable, Optional, Set

from sqlalchemy.orm import Session

from app.models.provider import Provider
from app.models.provider_circuit import ProviderCircuit

logger = logging.getLogger(__name__)

# Provider-level faults only. See the module docstring before adding to this.
TRIP_CODES: Set[int] = {408, 500, 502, 503, 504}


@dataclass(frozen=True)
class Thresholds:
    """
    Per-provider-class tuning, from the guide's table.

    Local providers open fastest (2 failures): a local process that is down is
    down now, and waiting out seven failures only delays the fallback. Hosted
    API-key providers get the most slack (12) because transient 5xx at that
    scale is normal and opening on a blip costs every user of that provider.
    """

    degraded_at: int
    opens_at: int
    reset_seconds: int


API_KEY = Thresholds(degraded_at=7, opens_at=12, reset_seconds=30)
OAUTH = Thresholds(degraded_at=5, opens_at=8, reset_seconds=60)
LOCAL = Thresholds(degraded_at=1, opens_at=2, reset_seconds=15)


def thresholds_for(provider: Provider) -> Thresholds:
    """
    Which profile applies. Every provider this gateway routes to authenticates
    with an API key, so that is the default; a local base URL is the one case
    that is detectable and behaves differently enough to matter.
    """
    base = (provider.base_url or "").lower()
    if "localhost" in base or "127.0.0.1" in base or "://host.docker.internal" in base:
        return LOCAL
    return API_KEY


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _get_or_create(db: Session, provider_id: int) -> ProviderCircuit:
    row = (
        db.query(ProviderCircuit)
        .filter(ProviderCircuit.provider_id == provider_id)
        .first()
    )
    if not row:
        row = ProviderCircuit(provider_id=provider_id, state="closed", failure_count=0)
        db.add(row)
        db.flush()
    return row


def _refresh(row: ProviderCircuit) -> ProviderCircuit:
    """
    Lazy recovery: an OPEN whose cooldown has passed becomes HALF_OPEN.

    Mutates the instance but does not commit — every caller is already inside a
    transaction, and committing here would turn a read into a write.
    """
    if row.state == "open" and row.opened_until and row.opened_until <= _now():
        row.state = "half_open"
        row.opened_until = None
    return row


# ── reads ───────────────────────────────────────────────────────────────────

def blocked_provider_ids(db: Session) -> Set[int]:
    """
    Providers whose circuit is OPEN right now.

    The router removes these from the candidate set. HALF_OPEN is deliberately
    NOT included: that state exists precisely to let one request through and
    find out whether the provider recovered.
    """
    blocked: Set[int] = set()
    for row in db.query(ProviderCircuit).filter(ProviderCircuit.state == "open"):
        _refresh(row)
        if row.state == "open":
            blocked.add(row.provider_id)
    return blocked


def status(db: Session) -> Dict[int, dict]:
    """Every known circuit, keyed by provider id, with lazy recovery applied."""
    out: Dict[int, dict] = {}
    for row in db.query(ProviderCircuit):
        _refresh(row)
        out[row.provider_id] = {
            "state": row.state,
            "failure_count": row.failure_count,
            "opened_until": row.opened_until.isoformat() if row.opened_until else None,
            "open_count": row.open_count,
            "last_status_code": row.last_status_code,
            "last_error": row.last_error,
            "last_failure_at": row.last_failure_at.isoformat() if row.last_failure_at else None,
            "last_success_at": row.last_success_at.isoformat() if row.last_success_at else None,
        }
    return out


# ── writes ──────────────────────────────────────────────────────────────────

def record_failure(
    db: Session,
    provider_id: int,
    status_code: Optional[int],
    error: Optional[str] = None,
) -> bool:
    """
    Count one failure against a provider. Returns True if the circuit is now open.

    A status code outside TRIP_CODES is NOT a provider fault and is ignored
    entirely — it does not even reset anything. That is what keeps a user's 429
    or a dead key from ever affecting the provider's circuit.
    """
    if status_code not in TRIP_CODES:
        return False

    try:
        provider = db.query(Provider).filter(Provider.id == provider_id).first()
        if not provider:
            return False

        limits = thresholds_for(provider)
        row = _refresh(_get_or_create(db, provider_id))

        row.failure_count = (row.failure_count or 0) + 1
        row.last_status_code = status_code
        row.last_error = (error or "")[:500] or None
        row.last_failure_at = _now()

        # A failure while probing in HALF_OPEN means the provider is still down.
        # Re-open immediately rather than waiting to re-cross the threshold —
        # the probe already answered the question.
        if row.state == "half_open" or row.failure_count >= limits.opens_at:
            row.state = "open"
            row.opened_until = _now() + timedelta(seconds=limits.reset_seconds)
            row.open_count = (row.open_count or 0) + 1
            logger.warning(
                "Circuit OPEN for provider %s (%s failures, HTTP %s) — skipping for %ss.",
                provider.slug, row.failure_count, status_code, limits.reset_seconds,
            )
            return True

        if row.failure_count >= limits.degraded_at:
            row.state = "degraded"

        return False
    except Exception as exc:
        # Never let bookkeeping break a completion (SRS §22).
        logger.warning("Circuit breaker failed to record a failure: %s", exc)
        return False


def record_success(db: Session, provider_id: int) -> None:
    """
    A success closes the circuit outright.

    Not a decay: this is a PROVIDER-level signal, and a provider that just
    answered is up. Decaying gradually would keep a recovered provider in
    DEGRADED — and therefore keep alarming — long after it was fine. Per-key
    429 strikes decay separately (SRS §7.3); this does not.
    """
    try:
        row = (
            db.query(ProviderCircuit)
            .filter(ProviderCircuit.provider_id == provider_id)
            .first()
        )
        if not row:
            return
        row.last_success_at = _now()
        if row.state != "closed" or row.failure_count:
            row.state = "closed"
            row.failure_count = 0
            row.opened_until = None
    except Exception as exc:
        logger.warning("Circuit breaker failed to record a success: %s", exc)


def reset(db: Session, provider_id: Optional[int] = None) -> int:
    """
    Force circuits closed — the operator's "I fixed it, stop waiting" button.

    With no provider_id, resets every circuit. Returns how many were changed.
    """
    q = db.query(ProviderCircuit)
    if provider_id is not None:
        q = q.filter(ProviderCircuit.provider_id == provider_id)

    changed = 0
    for row in q:
        if row.state != "closed" or row.failure_count:
            row.state = "closed"
            row.failure_count = 0
            row.opened_until = None
            changed += 1
    return changed
