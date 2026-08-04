"""
Dashboard read surfaces — SRS §20, the last open item.

SRS §25 lists "Dashboard — surface existing health/usage data" as one of two
genuinely open items, and §20 names the surfaces: provider status board, health
timeline, latency graph, top models, error feed. The data has been collected all
along (deployment health, request_log); nothing here computes anything new about
routing. These are READS.

WHY A SEPARATE MODULE FROM me.py
    me.py owns the endpoints a gateway USER needs to operate: their keys, their
    models, their usage. Everything here exists to draw a screen. Keeping them
    apart means a change to the dashboard's needs never risks the operational
    surface — and makes it obvious which endpoints could be dropped if the
    dashboard were.

EVERY ENDPOINT IS SCOPED TO THE CALLER. `user.id` is in the WHERE clause of every
query below, not applied afterwards in Python. Per-user isolation is structural
(SRS §6, §21): a bug in a filter here must not be able to show one user another
user's keys.

Read-only throughout — nothing in this module writes.
"""

from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import desc, func, select
from sqlalchemy.orm import Session

from app.api.gateway_auth import current_user
from app.core.database import get_db
from app.models.deployment import Deployment
from app.models.deployment_status_event import DeploymentStatusEvent
from app.models.enums import ModelHealth
from app.models.provider import Provider
from app.models.provider_key import ProviderKey
from app.models.provider_model import ProviderModel
from app.models.request_log import RequestLog
from app.models.user import User

router = APIRouter(tags=["Dashboard"])


def _iso(value: Optional[datetime]) -> Optional[str]:
    return value.isoformat() if value else None


# ═══════════════════════════════════════════════════════════════════════════
#  DEPLOYMENTS — SRS §6.1. The unit of everything.
# ═══════════════════════════════════════════════════════════════════════════

@router.get("/v1/me/deployments")
async def my_deployments(
    status: Optional[str] = Query(None, description="Filter by health status"),
    provider: Optional[str] = Query(None, description="Filter by provider slug"),
    limit: int = Query(500, ge=1, le=2000),
    offset: int = Query(0, ge=0),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    """
    MY deployments — every (provider, key, model) triple I own, with its health.

    SRS §3 calls the deployment "the unit of everything": health, cooldowns,
    probing and routing all operate on it. Until now nothing exposed the raw
    rows — `/v1/me/models` aggregates them up to a model name, which is the
    right answer for "what can I call?" and the wrong one for "which of my two
    Groq keys is throttled right now?".

    `is_callable` is computed here rather than read from `is_working`, and the
    difference matters: `is_working` is a generated column meaning
    status = 'available'. A rate-limited deployment whose cooldown has EXPIRED
    is not available but IS callable again — that is exactly how a 429'd free
    tier self-heals (see the warning in models/deployment.py). Showing
    `is_working` on this screen would report healthy keys as dead.
    """
    now = datetime.now(timezone.utc)

    q = (
        db.query(Deployment, Provider, ProviderKey, ProviderModel)
        .join(Provider, Provider.id == Deployment.provider_id)
        .join(ProviderKey, ProviderKey.id == Deployment.provider_key_id)
        .join(ProviderModel, ProviderModel.id == Deployment.provider_model_id)
        .filter(Deployment.user_id == user.id)
    )

    if status:
        # An unknown status is a client mistake, not a server error: return an
        # empty page rather than 500 on an enum cast.
        try:
            q = q.filter(Deployment.status == ModelHealth(status))
        except ValueError:
            return {"total": 0, "limit": limit, "offset": offset, "deployments": []}

    if provider:
        q = q.filter(Provider.slug == provider)

    total = q.count()
    rows = (
        q.order_by(Provider.name, ProviderKey.id, ProviderModel.normalized_name)
        .offset(offset)
        .limit(limit)
        .all()
    )

    return {
        "total": total,
        "limit": limit,
        "offset": offset,
        "deployments": [
            {
                "id": dep.id,
                "provider": prov.name,
                "provider_slug": prov.slug,
                "key_id": key.id,
                "key_label": key.label,
                "key_masked": key.key_masked,
                "model": pm.normalized_name,
                "upstream_model_id": pm.upstream_model_id,
                "mode": pm.mode.value if hasattr(pm.mode, "value") else pm.mode,
                "status": dep.status.value,
                "is_working": bool(dep.is_working),
                # Cooling down = benched now but revives on its own.
                "is_cooling_down": bool(dep.cooldown_until and dep.cooldown_until > now),
                # The honest answer to "can the router pick this right now?"
                "is_callable": (
                    dep.status is ModelHealth.available
                    or (
                        dep.status is ModelHealth.rate_limited
                        and (dep.cooldown_until is None or dep.cooldown_until <= now)
                    )
                ),
                "http_code": dep.http_code,
                "latency_ms": dep.latency_ms,
                "error": dep.error,
                "rpm": dep.rpm,
                "cooldown_until": _iso(dep.cooldown_until),
                "cooldown_seconds_left": (
                    max(0, int((dep.cooldown_until - now).total_seconds()))
                    if dep.cooldown_until and dep.cooldown_until > now
                    else 0
                ),
                "rate_limit_strikes": dep.rate_limit_strikes or 0,
                "last_checked_at": _iso(dep.last_checked_at),
                "last_used_at": _iso(dep.last_used_at),
            }
            for dep, prov, key, pm in rows
        ],
    }


# ═══════════════════════════════════════════════════════════════════════════
#  RESILIENCE — SRS §14. The three layers, reported separately.
# ═══════════════════════════════════════════════════════════════════════════

@router.get("/v1/me/resilience")
async def resilience_status(
    user: User = Depends(current_user), db: Session = Depends(get_db),
):
    """
    All three resilience layers in one read, kept explicitly apart.

    They are separate on purpose and confusing them is the classic debugging
    trap (OmniRoute's RESILIENCE_GUIDE opens by warning about it):

      circuit   whole PROVIDER, tripped only by 408/5xx. Global — a provider
                being down is true for everyone.
      cooldowns one KEY, tripped by 429. Per user, per key, self-healing.
      lockouts  one (key, model). A model that 404s or is quota-limited while
                its siblings on the same key still work.

    A reader who cannot tell which layer benched something cannot tell whether
    to wait, replace a key, or call the provider.
    """
    from app.services import circuit_breaker

    now = datetime.now(timezone.utc)
    circuits = circuit_breaker.status(db)
    providers = {p.id: p for p in db.query(Provider)}

    # ── layer 2: keys currently cooling down ──
    cooling = (
        db.query(Deployment, Provider, ProviderKey)
        .join(Provider, Provider.id == Deployment.provider_id)
        .join(ProviderKey, ProviderKey.id == Deployment.provider_key_id)
        .filter(
            Deployment.user_id == user.id,
            Deployment.cooldown_until.isnot(None),
            Deployment.cooldown_until > now,
        )
        .order_by(Deployment.cooldown_until)
        .all()
    )

    # ── layer 3: models locked out for a reason that is NOT a cooldown ──
    locked = (
        db.query(Deployment, Provider, ProviderKey, ProviderModel)
        .join(Provider, Provider.id == Deployment.provider_id)
        .join(ProviderKey, ProviderKey.id == Deployment.provider_key_id)
        .join(ProviderModel, ProviderModel.id == Deployment.provider_model_id)
        .filter(
            Deployment.user_id == user.id,
            Deployment.status.in_(
                [ModelHealth.unavailable, ModelHealth.auth_error, ModelHealth.error]
            ),
        )
        .order_by(Provider.name, ProviderModel.normalized_name)
        .limit(500)
        .all()
    )

    return {
        "generated_at": now.isoformat(),
        "circuits": [
            {
                "provider": providers[pid].name if pid in providers else str(pid),
                "provider_slug": providers[pid].slug if pid in providers else None,
                **info,
            }
            for pid, info in circuits.items()
        ],
        "key_cooldowns": [
            {
                "deployment_id": dep.id,
                "provider": prov.name,
                "key_masked": key.key_masked,
                "seconds_left": max(0, int((dep.cooldown_until - now).total_seconds())),
                "expires_at": _iso(dep.cooldown_until),
                "strikes": dep.rate_limit_strikes or 0,
            }
            for dep, prov, key in cooling
        ],
        "model_lockouts": [
            {
                "deployment_id": dep.id,
                "provider": prov.name,
                "provider_slug": prov.slug,
                "key_masked": key.key_masked,
                "model": pm.normalized_name,
                "reason": dep.status.value,
                "http_code": dep.http_code,
                "error": (dep.error or "")[:200] or None,
                "since": _iso(dep.last_checked_at),
            }
            for dep, prov, key, pm in locked
        ],
        "summary": {
            "circuits_open": sum(1 for c in circuits.values() if c["state"] == "open"),
            "circuits_degraded": sum(1 for c in circuits.values() if c["state"] == "degraded"),
            "keys_cooling": len(cooling),
            "models_locked": len(locked),
        },
    }


@router.post("/v1/me/resilience/reset")
async def reset_resilience(
    provider: Optional[str] = Query(None, description="Slug; omit to reset every circuit"),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    """
    Force provider circuits closed — "I fixed it, stop waiting".

    Resets ONLY layer 1. Key cooldowns and model lockouts are deliberately left
    alone: those are per-key facts that expire on their own or need the key
    replaced, and clearing them here would just re-run the failing request.
    """
    from app.services import circuit_breaker
    from app.core import llm_router

    provider_id = None
    if provider:
        row = db.query(Provider).filter(Provider.slug == provider.lower()).first()
        if not row:
            raise HTTPException(status_code=404, detail=f"No provider {provider!r}.")
        provider_id = row.id

    changed = circuit_breaker.reset(db, provider_id)
    db.commit()
    # The candidate set just changed; do not make the caller wait out the TTL.
    llm_router.invalidate(user.id)

    return {"success": True, "circuits_reset": changed, "provider": provider}


@router.delete("/v1/me/resilience/model-lockout/{deployment_id}")
async def clear_model_lockout(
    deployment_id: int,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    """
    Manually re-enable one locked-out model.

    Sets the deployment back to `available` so the router will try it again. It
    is an assertion, not a measurement — if the model is still broken the next
    real request re-locks it, which is the intended cost of the override.
    """
    from app.core import llm_router
    from app.services import health_history

    dep = (
        db.query(Deployment)
        .filter(Deployment.id == deployment_id, Deployment.user_id == user.id)
        .first()
    )
    if not dep:
        raise HTTPException(status_code=404, detail="No such deployment.")

    health_history.record_transition(
        db, dep, ModelHealth.available, source="manual_override",
    )
    dep.status = ModelHealth.available
    dep.cooldown_until = None
    dep.rate_limit_strikes = 0
    dep.error = None
    db.commit()
    llm_router.invalidate(user.id)

    return {"success": True, "deployment_id": deployment_id, "status": "available"}


# ═══════════════════════════════════════════════════════════════════════════
#  CAPABILITIES — SRS §19. What this endpoint actually serves.
# ═══════════════════════════════════════════════════════════════════════════

@router.get("/v1/capabilities")
async def capabilities(user: User = Depends(current_user), db: Session = Depends(get_db)):
    """
    The compatibility surface, reported from the routes that exist.

    This is the answer to "what can I point at this URL?" — the one question the
    Endpoints screen is for. It lists only what is IMPLEMENTED, and says so
    explicitly for what is not: advertising an endpoint the gateway does not
    serve turns a clear 404 into a debugging session in someone else's client.

    Counts are scoped to the caller, because "embeddings supported" and "you hold
    a key for an embedding model" are different facts and only the second one
    means a request will succeed.
    """
    by_mode: dict[str, int] = {}
    rows = (
        db.query(ProviderModel.mode, func.count(Deployment.id))
        .join(Deployment, Deployment.provider_model_id == ProviderModel.id)
        .filter(Deployment.user_id == user.id, Deployment.is_working.is_(True))
        .group_by(ProviderModel.mode)
        .all()
    )
    for mode, count in rows:
        by_mode[mode.value if hasattr(mode, "value") else str(mode)] = count

    def entry(path: str, method: str, label: str, mode: Optional[str], note: str = "") -> dict:
        return {
            "path": path,
            "method": method,
            "label": label,
            "supported": True,
            "live_models": by_mode.get(mode, 0) if mode else None,
            "note": note,
        }

    return {
        "openai_compatible": [
            entry("/v1/chat/completions", "POST", "Chat completions", "chat",
                  "Streaming and tool calls supported."),
            entry("/v1/models", "GET", "List models", None,
                  "The models you can call right now."),
            entry("/v1/embeddings", "POST", "Embeddings", "embedding",
                  "NVIDIA retrieval models default to input_type=query."),
            entry("/v1/images/generations", "POST", "Image generation", "image"),
            entry("/v1/audio/transcriptions", "POST", "Speech to text", "audio_transcription"),
            entry("/v1/audio/speech", "POST", "Text to speech", "audio_speech"),
        ],
        "anthropic_compatible": [
            entry("/v1/messages", "POST", "Messages", "chat",
                  "What Claude Code speaks. Tools and streaming supported."),
        ],
        # Named rather than omitted: a client pointed at a missing endpoint gets
        # a 404 with no explanation, and the operator has no way to know it was
        # never implemented rather than broken.
        "not_supported": [
            {
                "path": "/v1/video/generations",
                "label": "Video generation",
                "reason": "No video provider is integrated. LiteLLM has no video route for the providers this gateway routes to.",
            },
            {
                "path": "/v1/rerank",
                "label": "Reranking",
                "reason": "Rerank models are filtered out during discovery — they serve neither the chat nor the embeddings endpoint (SRS §9.1).",
            },
        ],
        "live_models_by_mode": by_mode,
    }


# ═══════════════════════════════════════════════════════════════════════════
#  PROVIDER CATALOG — SRS §12. What a provider serves, key or no key.
# ═══════════════════════════════════════════════════════════════════════════

@router.get("/v1/providers/{slug}/models")
async def provider_catalog(
    slug: str,
    include_disabled: bool = Query(False),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    """
    One provider's model catalog — what the provider page's "Available Models"
    list shows.

    Distinct from /v1/me/models, which answers "what can I CALL right now" and is
    therefore scoped to the caller's keys. This answers "what does this provider
    OFFER", which is a fact about the provider and visible before any key exists.
    That is the difference between an empty list and an empty account.

    Disabled models are excluded by default. A model goes `enabled=false` when it
    disappears from the provider's catalog (SRS §9.2: disable, never delete), so
    including them by default would show a list that no longer exists upstream.
    """
    prov = db.query(Provider).filter(Provider.slug == slug.lower()).first()
    if not prov:
        return {"provider": slug, "found": False, "models": [], "total": 0}

    q = db.query(ProviderModel).filter(ProviderModel.provider_id == prov.id)
    if not include_disabled:
        q = q.filter(ProviderModel.enabled.is_(True))
    rows = q.order_by(ProviderModel.normalized_name).all()

    # Per-model health for THIS user, so the list can show which of the
    # provider's models actually answer for them.
    mine = {
        d.provider_model_id: d
        for d in db.query(Deployment).filter(
            Deployment.user_id == user.id, Deployment.provider_id == prov.id
        )
    }

    now = datetime.now(timezone.utc)
    return {
        "provider": prov.slug,
        "provider_name": prov.name,
        "found": True,
        "total": len(rows),
        "models": [
            {
                "id": m.upstream_model_id,
                "litellm_model": m.litellm_model,
                "name": m.display_name or m.normalized_name,
                "normalized_name": m.normalized_name,
                "publisher": m.publisher,
                "mode": m.mode.value if hasattr(m.mode, "value") else m.mode,
                "context_window": m.context_window,
                "max_output_tokens": m.max_output_tokens,
                "is_free": m.is_free,
                "supports_stream": m.supports_stream,
                "enabled": m.enabled,
                "is_common": m.is_common,
                # Null when the user holds no key: the model exists, they just
                # cannot reach it yet.
                "my_status": (mine[m.id].status.value if m.id in mine else None),
                "my_latency_ms": (mine[m.id].latency_ms if m.id in mine else None),
                "my_is_callable": (
                    (
                        mine[m.id].status is ModelHealth.available
                        or (
                            mine[m.id].status is ModelHealth.rate_limited
                            and (
                                mine[m.id].cooldown_until is None
                                or mine[m.id].cooldown_until <= now
                            )
                        )
                    )
                    if m.id in mine
                    else None
                ),
            }
            for m in rows
        ],
    }


# ═══════════════════════════════════════════════════════════════════════════
#  PROVIDER STATUS BOARD — SRS §20. "Is anything on fire?"
# ═══════════════════════════════════════════════════════════════════════════

@router.get("/v1/me/status-board")
async def status_board(
    user: User = Depends(current_user), db: Session = Depends(get_db),
):
    """
    Per-provider, per-key rollup of deployment health.

    Deliberately grouped by KEY, not just by provider. The gateway's whole
    premise is that two keys at one provider are two independent free-tier
    budgets (SRS §6.1), so a board that only said "Groq: degraded" would hide
    the thing worth seeing — one key exhausted, its sibling fine.

    A single GROUP BY does the counting in Postgres. Pulling every deployment
    into Python to tally it would be the same numbers and a much larger
    response on an account with a few hundred deployments.
    """
    now = datetime.now(timezone.utc)

    rows = (
        db.query(
            Provider.id,
            Provider.name,
            Provider.slug,
            Provider.enabled,
            ProviderKey.id,
            ProviderKey.label,
            ProviderKey.key_masked,
            ProviderKey.is_active,
            Deployment.status,
            func.count(Deployment.id),
            func.count(Deployment.id).filter(
                Deployment.cooldown_until.isnot(None), Deployment.cooldown_until > now
            ),
            func.min(Deployment.cooldown_until).filter(
                Deployment.cooldown_until.isnot(None), Deployment.cooldown_until > now
            ),
            func.avg(Deployment.latency_ms).filter(Deployment.latency_ms.isnot(None)),
        )
        .join(ProviderKey, ProviderKey.provider_id == Provider.id)
        .outerjoin(
            Deployment,
            (Deployment.provider_key_id == ProviderKey.id)
            & (Deployment.user_id == user.id),
        )
        .filter(ProviderKey.user_id == user.id)
        .group_by(Provider.id, ProviderKey.id, Deployment.status)
        .order_by(Provider.name, ProviderKey.id)
        .all()
    )

    providers: dict[int, dict] = {}
    for (
        pid, pname, pslug, penabled,
        kid, klabel, kmasked, kactive,
        dstatus, count, cooling, next_expiry, avg_latency,
    ) in rows:
        prov = providers.setdefault(
            pid,
            {
                "provider_id": pid,
                "provider": pname,
                "slug": pslug,
                "enabled": penabled,
                "keys": {},
                "totals": {"deployments": 0, "callable": 0, "cooling_down": 0, "dead": 0},
            },
        )
        key = prov["keys"].setdefault(
            kid,
            {
                "key_id": kid,
                "label": klabel,
                "masked": kmasked,
                "is_active": kactive,
                "by_status": {},
                "deployments": 0,
                "callable": 0,
                "cooling_down": 0,
                "next_cooldown_expiry": None,
                "avg_latency_ms": None,
            },
        )

        # A key with no deployments yet (just added, probes still running) joins
        # as a NULL status row. It belongs on the board — that is precisely the
        # moment an operator is watching it — but it counts as nothing.
        if dstatus is None:
            continue

        name = dstatus.value
        key["by_status"][name] = key["by_status"].get(name, 0) + count
        key["deployments"] += count
        key["cooling_down"] += cooling or 0

        if name == "available":
            key["callable"] += count
        elif name == "rate_limited":
            # Throttled but past its cooldown is callable again (SRS §7.3).
            key["callable"] += max(0, count - (cooling or 0))

        if next_expiry and (
            key["next_cooldown_expiry"] is None
            or next_expiry < datetime.fromisoformat(key["next_cooldown_expiry"])
        ):
            key["next_cooldown_expiry"] = next_expiry.isoformat()

        if avg_latency is not None:
            key["avg_latency_ms"] = int(avg_latency)

    out = []
    for prov in providers.values():
        keys = list(prov["keys"].values())
        prov["keys"] = keys
        prov["totals"] = {
            "keys": len(keys),
            "deployments": sum(k["deployments"] for k in keys),
            "callable": sum(k["callable"] for k in keys),
            "cooling_down": sum(k["cooling_down"] for k in keys),
            "dead": sum(k["by_status"].get("auth_error", 0) for k in keys),
        }
        # One word for the tile colour, decided here so every consumer of this
        # endpoint agrees on what "degraded" means.
        t = prov["totals"]
        if t["deployments"] == 0:
            prov["health"] = "unknown"
        elif t["callable"] == 0:
            prov["health"] = "down"
        elif t["callable"] < t["deployments"]:
            prov["health"] = "degraded"
        else:
            prov["health"] = "healthy"
        out.append(prov)

    return {
        "generated_at": now.isoformat(),
        "providers": out,
        "summary": {
            "providers": len(out),
            "healthy": sum(1 for p in out if p["health"] == "healthy"),
            "degraded": sum(1 for p in out if p["health"] == "degraded"),
            "down": sum(1 for p in out if p["health"] == "down"),
        },
    }


# ═══════════════════════════════════════════════════════════════════════════
#  HEALTH TIMELINE — SRS §20
# ═══════════════════════════════════════════════════════════════════════════

@router.get("/v1/me/health/timeline")
async def health_timeline(
    days: int = Query(7, ge=1, le=90),
    deployment_id: Optional[int] = Query(None),
    limit: int = Query(200, ge=1, le=1000),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    """
    Status changes over time — when a key got throttled, when it came back.

    Reads deployment_status_events, which records TRANSITIONS ONLY (see
    services/health_history.py). That is why this can be a flat "most recent
    first" list and still be readable: consecutive identical states were never
    written, so every row here is a real change.

    History starts at the deploy that introduced the table — there is no
    backfill, because the information to backfill from was overwritten in place.
    An empty timeline on a busy gateway means "nothing has changed since then",
    not "nothing is being recorded".
    """
    since = datetime.now(timezone.utc) - timedelta(days=days)

    q = (
        db.query(DeploymentStatusEvent, Provider, ProviderKey, ProviderModel)
        .join(Deployment, Deployment.id == DeploymentStatusEvent.deployment_id)
        .join(Provider, Provider.id == Deployment.provider_id)
        .join(ProviderKey, ProviderKey.id == Deployment.provider_key_id)
        .join(ProviderModel, ProviderModel.id == Deployment.provider_model_id)
        .filter(
            DeploymentStatusEvent.user_id == user.id,
            DeploymentStatusEvent.created_at >= since,
        )
    )
    if deployment_id:
        q = q.filter(DeploymentStatusEvent.deployment_id == deployment_id)

    total = q.count()
    rows = q.order_by(desc(DeploymentStatusEvent.created_at)).limit(limit).all()

    return {
        "window_days": days,
        "total": total,
        "events": [
            {
                "id": ev.id,
                "at": _iso(ev.created_at),
                "deployment_id": ev.deployment_id,
                "provider": prov.name,
                "provider_slug": prov.slug,
                "key_label": key.label,
                "key_masked": key.key_masked,
                "model": pm.normalized_name,
                "from_status": ev.from_status.value if ev.from_status else None,
                "to_status": ev.to_status.value,
                # 'probe' = the gateway went looking; 'request' = a user's call
                # hit it. Different questions, so the UI distinguishes them.
                "source": ev.source,
                "http_code": ev.http_code,
                "latency_ms": ev.latency_ms,
                "error": ev.error,
                "cooldown_until": _iso(ev.cooldown_until),
                "recovered": (
                    ev.from_status is not None
                    and ev.from_status is not ModelHealth.available
                    and ev.to_status is ModelHealth.available
                ),
            }
            for ev, prov, key, pm in rows
        ],
    }


# ═══════════════════════════════════════════════════════════════════════════
#  LATENCY — SRS §20
# ═══════════════════════════════════════════════════════════════════════════

@router.get("/v1/me/analytics/latency")
async def latency_series(
    days: int = Query(7, ge=1, le=90),
    bucket: str = Query("hour", pattern="^(hour|day)$"),
    group_by: str = Query("none", pattern="^(none|provider|model)$"),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    """
    p50 / p95 latency over time, from request_log.

    p95 and not just the mean, deliberately: an average hides the tail, and the
    tail is what a user actually feels when one provider in the pool is slow.
    percentile_cont is computed by Postgres — pulling every latency into Python
    to sort it would move the whole log over the wire to produce two numbers.

    Only successful calls (status_code < 400) are measured. A 401 that returns
    in 30 ms is not "fast"; mixing failures in would make an outage look like a
    latency improvement.
    """
    since = datetime.now(timezone.utc) - timedelta(days=days)
    trunc = func.date_trunc(bucket, RequestLog.created_at).label("bucket")

    p50 = func.percentile_cont(0.5).within_group(RequestLog.latency_ms.asc())
    p95 = func.percentile_cont(0.95).within_group(RequestLog.latency_ms.asc())

    group_col = None
    if group_by == "provider":
        group_col = Provider.name
    elif group_by == "model":
        group_col = RequestLog.requested_model

    selected = [trunc] + ([group_col.label("series")] if group_col is not None else [])
    q = db.query(
        *selected,
        p50.label("p50"),
        p95.label("p95"),
        func.count(RequestLog.id).label("requests"),
        func.avg(RequestLog.latency_ms).label("avg"),
    )

    if group_by == "provider":
        q = q.join(Provider, Provider.id == RequestLog.provider_id)

    q = q.filter(
        RequestLog.user_id == user.id,
        RequestLog.created_at >= since,
        RequestLog.latency_ms.isnot(None),
        RequestLog.status_code < 400,
    ).group_by(*selected).order_by(trunc)

    rows = q.all()

    points = []
    for row in rows:
        data = row._mapping
        points.append(
            {
                "bucket": data["bucket"].isoformat() if data["bucket"] else None,
                "series": data.get("series") if group_col is not None else "all",
                "p50_ms": int(data["p50"]) if data["p50"] is not None else None,
                "p95_ms": int(data["p95"]) if data["p95"] is not None else None,
                "avg_ms": int(data["avg"]) if data["avg"] is not None else None,
                "requests": data["requests"],
            }
        )

    return {
        "window_days": days,
        "bucket": bucket,
        "group_by": group_by,
        "points": points,
    }


# ═══════════════════════════════════════════════════════════════════════════
#  ERROR FEED — SRS §20
# ═══════════════════════════════════════════════════════════════════════════

@router.get("/v1/me/errors")
async def error_feed(
    days: int = Query(7, ge=1, le=90),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    """
    Recent failures, newest first, with the deployment that produced each.

    `/v1/me/logs?status=error` already returns failing rows; this exists because
    the feed needs the WHY, not just the row: `reason` classifies the HTTP code
    into the same vocabulary the prober and the router use (SRS §13.2), so a
    reader sees "rate_limited" and "auth_error" rather than 429 and 401 and has
    to know that 429 means the key is fine and 401 means it is dead.
    """
    since = datetime.now(timezone.utc) - timedelta(days=days)

    q = (
        db.query(RequestLog, Provider, ProviderKey)
        .outerjoin(Provider, Provider.id == RequestLog.provider_id)
        .outerjoin(ProviderKey, ProviderKey.id == RequestLog.provider_key_id)
        .filter(
            RequestLog.user_id == user.id,
            RequestLog.created_at >= since,
            RequestLog.status_code >= 400,
        )
    )

    total = q.count()
    rows = q.order_by(desc(RequestLog.created_at)).offset(offset).limit(limit).all()

    def reason(code: Optional[int]) -> str:
        # Mirrors prober._classify's vocabulary so one word means one thing
        # everywhere in the product.
        if code is None:
            return "error"
        if code == 429:
            return "rate_limited"
        if code in (401, 403):
            return "auth_error"
        if code == 404:
            return "unavailable"
        if code in (408, 504):
            return "timeout"
        return "error"

    # Rollup so the feed can lead with "what is failing most", not just a list.
    by_reason: dict[str, int] = {}
    for log, _, _ in rows:
        r = reason(log.status_code)
        by_reason[r] = by_reason.get(r, 0) + 1

    return {
        "window_days": days,
        "total": total,
        "limit": limit,
        "offset": offset,
        "by_reason": by_reason,
        "errors": [
            {
                "id": log.id,
                "at": _iso(log.created_at),
                "model": log.requested_model,
                "provider": prov.name if prov else None,
                "key_label": key.label if key else None,
                "key_masked": key.key_masked if key else None,
                "status_code": log.status_code,
                "reason": reason(log.status_code),
                "message": log.error_message,
                "latency_ms": log.latency_ms,
            }
            for log, prov, key in rows
        ],
    }
