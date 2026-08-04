"""
LLM Router — ONE ROUTER PER USER, built from that user's live deployments.

WHAT CHANGED AND WHY:

The old router was a module-level SINGLETON whose keys came from os.environ. That
is single-tenant by construction: a process has one environment, so two users with
Groq keys collide in the same variable and whoever wrote last serves everyone.
There is no patch for that — the object itself had the wrong shape.

Now each user gets their own Router, built from `v_live_deployments` (their
callable deployments only), with each key DECRYPTED AND PASSED IN as api_key on
its own model_list entry. No shared namespace, so no cross-user leakage.

WHERE THE TRUTH LIVES — this is the part to keep straight:

    POSTGRES is durable truth.  Which deployments exist, and their health and
                               cooldowns, live in the DB. The probe writes them;
                               is_callable() decides what is live right now.

    THE ROUTER is a short-lived VIEW of that truth. LiteLLM keeps its own
                               in-memory cooldown/usage state, which necessarily
                               duplicates the DB's. We keep the two from drifting
                               by giving each Router a short TTL and rebuilding
                               it from the view — so a DB cooldown that expires
                               shows up within TTL_SECONDS, and a 429 seen in
                               flight is ALSO written back to the DB (see
                               record_failure) rather than living only in memory.

If those two ever seem to disagree, the DB is right and the Router is stale.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

import litellm
from litellm import Router
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import SessionLocal
from app.models.deployment import Deployment
from app.models.enums import ModelHealth
from app.models.provider import Provider
from app.models.provider_key import ProviderKey
from app.models.router_config import RouterConfig
from app.models.views import v_live_deployments
from app.services import (
    circuit_breaker, crypto, deepseek_web, health_history, nvidia_riva, presets,
)

logger = logging.getLogger("gateway.router")

# How long a built Router may be reused before it is rebuilt from the DB. This is
# the maximum staleness of the in-memory view: a cooldown that expires in Postgres
# becomes callable again within this window.
TTL_SECONDS = 30

# user_id -> (router, built_at, model_names)
_cache: Dict[int, Tuple[Router, float, List[str]]] = {}


def _router_settings(
    db: Session, user_id: int,
) -> Tuple[Dict[str, Any], Optional[int]]:
    """The user's Router kwargs, plus their pinned provider id (None = no pin)."""
    rc = db.query(RouterConfig).filter(RouterConfig.user_id == user_id).first()
    if not rc:
        return {"routing_strategy": "usage-based-routing-v2", "num_retries": 4,
                "cooldown_time": 30, "allowed_fails": 3}, None
    return {
        "routing_strategy": rc.routing_strategy,
        "num_retries": rc.num_retries,
        "cooldown_time": rc.cooldown_time,
        "allowed_fails": rc.allowed_fails,
    }, rc.pinned_provider_id


def _apply_pin(rows: List[Any], pinned_provider_id: Optional[int]) -> List[Any]:
    """
    Soft pin: PER MODEL, if the pinned provider has a callable deployment, offer
    only its deployments; models the pinned provider does not serve keep their
    full candidate set. Pinning narrows candidates — it never empties them, and
    it never overrides health or cooldowns (an unhealthy pinned deployment is
    already absent from `rows`, so its model falls back to the other providers).
    """
    if not pinned_provider_id:
        return rows
    pinned_models = {
        r["model"] for r in rows if r["provider_id"] == pinned_provider_id
    }
    return [
        r for r in rows
        if r["provider_id"] == pinned_provider_id or r["model"] not in pinned_models
    ]


def callable_deployments(db: Session, user_id: int) -> List[Dict[str, Any]]:
    """
    This user's callable deployments, each with the litellm kwargs to CALL it.

    One entry per deployment:

        {deployment_id, model, litellm_model, provider_id, provider_key_id,
         mode, params}

    where `params` is exactly what `_build` puts in a model_list entry's
    `litellm_params` — model id, decrypted key, api_base, rpm, and any provider
    quirk (Riva endpoints, forced custom_llm_provider).

    WHY THIS IS PUBLIC
        The Router load-balances a family name across every deployment behind it,
        which is right for `model: "gpt-oss-120b"` and wrong for a combo step that
        says "this model, on THIS account". Combo routing (services/combo_router.py)
        picks one entry itself and calls litellm directly with these params, so
        pinning is exact instead of hopeful. Both paths read the same view and the
        same decrypted keys, so key isolation is identical.

    Includes nothing litellm cannot call: NVCF and browser-session deployments
    are left OUT for the same reason they are left out of the model_list.

    The provider circuit breaker IS applied (an open provider is down for
    everyone). The per-user provider PIN is deliberately NOT: a pin is a
    preference over an unordered pool, and a combo step names its target
    explicitly — silently filtering out a step the user chose would make the
    combo lie about what it does.
    """
    rows = db.execute(
        select(v_live_deployments).where(v_live_deployments.c.user_id == user_id)
    ).mappings().all()

    blocked = circuit_breaker.blocked_provider_ids(db)
    if blocked:
        rows = [r for r in rows if r["provider_id"] not in blocked]

    return _entries(db, rows)


def _entries(db: Session, rows: List[Any]) -> List[Dict[str, Any]]:
    """Turn view rows into call-ready entries. Shared by _build and combo routing."""
    key_ids = {r["provider_key_id"] for r in rows}
    plaintext: Dict[int, str] = {}
    if key_ids:
        for pk in db.query(ProviderKey).filter(ProviderKey.id.in_(key_ids)):
            plaintext[pk.id] = crypto.decrypt(pk.key_ciphertext)

    providers_by_id = {p.id: p for p in db.query(Provider)}
    bases = {
        pid: presets.call_api_base(p.slug, p.base_url)
        for pid, p in providers_by_id.items()
    }
    ds_web_prefix = f"{deepseek_web.PROVIDER_SLUG}/"

    out: List[Dict[str, Any]] = []
    for r in rows:
        if r["litellm_model"].startswith("nvcf/"):
            continue
        if r["litellm_model"].startswith(ds_web_prefix):
            continue
        if r["provider_key_id"] not in plaintext:
            continue
        if nvidia_riva.is_riva_model(r["litellm_model"]):
            model_id, function_id = nvidia_riva.split_function_id(r["litellm_model"])
            params: Dict[str, Any] = {
                "model": model_id,
                "api_key": plaintext[r["provider_key_id"]],
                "api_base": nvidia_riva.GRPC_ENDPOINT,
                "nvcf_function_id": function_id,
            }
        else:
            prov = providers_by_id.get(r["provider_id"])
            slug = prov.slug if prov else None
            params = {
                "model": presets.call_model_id(slug, r["litellm_model"]),
                "api_key": plaintext[r["provider_key_id"]],
            }
            custom_llm_provider = presets.custom_llm_provider_for(slug)
            if custom_llm_provider:
                params["custom_llm_provider"] = custom_llm_provider
            base = bases.get(r["provider_id"])
            if base:
                params["api_base"] = base
        if r["rpm"]:
            params["rpm"] = r["rpm"]

        prov = providers_by_id.get(r["provider_id"])
        out.append({
            "deployment_id": r["deployment_id"],
            "model": r["model"],
            "litellm_model": r["litellm_model"],
            "provider_id": r["provider_id"],
            "provider_slug": prov.slug if prov else None,
            "provider_name": prov.name if prov else None,
            "provider_key_id": r["provider_key_id"],
            "mode": r["mode"],
            "params": params,
        })
    return out


def _build(db: Session, user_id: int) -> Tuple[Router, List[str]]:
    """
    Build a Router from this user's CALLABLE deployments.

    The view already excludes revoked keys, disabled models, and deployments still
    cooling down — and it already RE-INCLUDES ones whose cooldown expired. So the
    model_list is exactly "what this user can call right now".
    """
    rows = db.execute(
        select(v_live_deployments).where(v_live_deployments.c.user_id == user_id)
    ).mappings().all()

    # Layer 1 of the resilience model: drop providers whose circuit is OPEN.
    # The view already excludes cooled-down KEYS (layer 2); this is the
    # provider-wide signal it cannot express, because a provider being down is a
    # fact about the provider rather than about anyone's key.
    #
    # HALF_OPEN providers are deliberately left IN: that state exists to let one
    # request through and discover whether the provider recovered.
    blocked = circuit_breaker.blocked_provider_ids(db)
    if blocked:
        before = len(rows)
        rows = [r for r in rows if r["provider_id"] not in blocked]
        logger.info(
            "Circuit breaker removed %d deployment(s) across %d open provider(s).",
            before - len(rows), len(blocked),
        )

    router_kwargs, pinned_provider_id = _router_settings(db, user_id)
    rows = _apply_pin(rows, pinned_provider_id)

    # NVCF deployments (litellm_model 'nvcf/<id>') are served by the adapter in
    # services/nvcf.py, NEVER by litellm — the prefix means nothing to it and
    # could poison the whole Router build. They still count as callable models
    # (see `names` below) so /v1/models and resolution know about them.
    nvcf_rows = [r for r in rows if r["litellm_model"].startswith("nvcf/")]
    # Same exclusion, same reason, for browser-session providers: litellm has no
    # 'deepseek-web' integration, so leaving these in the model_list makes the
    # Router constructor raise "LLM Provider NOT provided" — which takes down
    # EVERY model for that user, including GET /v1/models. They stay in `names`
    # so they remain callable through the executor branch in openai_compat.
    ds_web_prefix = f"{deepseek_web.PROVIDER_SLUG}/"

    # _entries decrypts each key once, applies the provider quirks (Riva
    # endpoints, forced custom_llm_provider, api_base) and drops exactly the two
    # families above. Combo routing reads the same entries — see
    # callable_deployments.
    model_list: List[Dict[str, Any]] = [
        {
            # The client asks for the bare family name; litellm load-balances
            # every deployment registered under it — across BOTH keys and providers.
            "model_name": e["model"],
            "litellm_params": e["params"],
            # Carry our deployment id through, so a failure can be attributed back
            # to the exact (model, key) row and written to the DB.
            "model_info": {"id": str(e["deployment_id"]),
                           "deployment_id": e["deployment_id"]},
        }
        for e in _entries(db, rows)
    ]

    litellm.request_timeout = settings.REQUEST_TIMEOUT
    router = Router(
        model_list=model_list,
        timeout=settings.REQUEST_TIMEOUT,
        **router_kwargs,
    )
    # Deployments excluded from the Router above are still CALLABLE — just not by
    # litellm. Leaving them out of `names` would hide them from /v1/models and
    # make resolve_requested_model 404 a model the gateway can actually serve.
    ds_web_rows = [r for r in rows if r["litellm_model"].startswith(ds_web_prefix)]
    names = sorted(
        {d["model_name"] for d in model_list}
        | {r["model"] for r in nvcf_rows}
        | {r["model"] for r in ds_web_rows}
    )
    logger.info(
        "Router built for user %s: %d deployment(s) across %d model(s).",
        user_id, len(model_list), len(names),
    )
    return router, names


def get_router(user_id: int, db: Optional[Session] = None) -> Router:
    """The user's Router, rebuilt if older than TTL_SECONDS."""
    hit = _cache.get(user_id)
    if hit and (time.monotonic() - hit[1]) < TTL_SECONDS:
        return hit[0]

    owns = db is None
    db = db or SessionLocal()
    try:
        router, names = _build(db, user_id)
        _cache[user_id] = (router, time.monotonic(), names)
        return router
    finally:
        if owns:
            db.close()


def invalidate(user_id: Optional[int] = None) -> None:
    """
    Drop a cached Router so the next request rebuilds it.

    Call after anything that changes what a user can call: adding or deleting a
    key, a probe finishing, a catalog refresh. Without this, a new key would not
    be usable until the TTL lapsed.
    """
    if user_id is None:
        _cache.clear()
    else:
        _cache.pop(user_id, None)


def list_models(user_id: int, db: Optional[Session] = None) -> List[str]:
    """The bare family names this user can currently call — powers GET /v1/models."""
    get_router(user_id, db)          # ensure built/fresh
    hit = _cache.get(user_id)
    return list(hit[2]) if hit else []


def non_litellm_deployment(
    user_id: int, normalized_name: str, db: Session, prefix: str
) -> Optional[Tuple[str, str]]:
    """
    Resolve a family name to (litellm_model, decrypted key) for a provider that
    litellm cannot serve — currently only DeepSeek Web.

    Callers pass the NORMALIZED family name, which is what reaches the endpoint:
    `deepseek-web/deepseek-chat` normalises to `deepseek-chat`, so matching on
    the raw prefix at the endpoint would never fire.

    Returns None unless EVERY live deployment for that name belongs to `prefix`.
    That condition matters because the real DeepSeek API also serves a model
    called `deepseek-chat`: when both are configured they share one family, and
    litellm should keep handling it — falling back to the browser session would
    silently downgrade a paid, rate-limit-free API call to a scraped web session.
    The bypass is for the case where litellm has nothing to route to.

    Reads the same v_live_deployments view the Router is built from, so key
    isolation is identical — rows are already scoped to this user_id.
    """
    rows = db.execute(
        select(
            v_live_deployments.c.litellm_model,
            v_live_deployments.c.provider_key_id,
        ).where(
            v_live_deployments.c.user_id == user_id,
            # `model` IS the normalized family name in this view — the same value
            # _build registers as litellm's `model_name`.
            v_live_deployments.c.model == normalized_name,
        )
    ).mappings().all()
    if not rows:
        return None
    if not all(str(r["litellm_model"]).startswith(f"{prefix}/") for r in rows):
        return None

    row = rows[0]
    pk = db.query(ProviderKey).filter(ProviderKey.id == row["provider_key_id"]).first()
    if not pk:
        return None
    return str(row["litellm_model"]), crypto.decrypt(pk.key_ciphertext)


def record_failure(deployment_id: int, exc: Exception) -> None:
    """
    Write an in-flight failure back to Postgres.

    Without this, a 429 hit during a real request would live ONLY in litellm's
    in-memory cooldown cache — invisible to the DB, to other workers, and to the
    dashboard, and lost on restart. The DB is the durable truth; this is what
    keeps it that way.
    """
    from app.services.prober import _classify, cooldown_seconds, retry_after_seconds

    status, code, msg = _classify(exc)
    now = datetime.now(timezone.utc)

    db = SessionLocal()
    try:
        dep = db.query(Deployment).filter(Deployment.id == deployment_id).first()
        if not dep:
            return

        # Health timeline (SRS §20), recorded before the status is overwritten.
        # source='request' marks this as observed from real traffic rather than
        # a probe — the two answer different questions.
        health_history.record_transition(
            db, dep, status, source="request", http_code=code, error=msg,
        )

        dep.status = status
        dep.http_code = code
        dep.error = msg[:500]
        dep.last_checked_at = now

        # Layer 1: only 408/5xx count as a PROVIDER fault. A 401 or 429 is
        # account-level and is handled above, per key — feeding it to the
        # circuit would let one user's exhausted quota bench the provider for
        # everyone (services/circuit_breaker.py).
        circuit_breaker.record_failure(db, dep.provider_id, code, msg)
        # 429s escalate: honour the provider's Retry-After when present, else
        # walk the per-deployment ladder (60s → 2m → 5m) on consecutive strikes.
        if status is ModelHealth.rate_limited:
            dep.rate_limit_strikes = (dep.rate_limit_strikes or 0) + 1
            dep.cooldown_until = now + timedelta(
                seconds=cooldown_seconds(dep.rate_limit_strikes, retry_after_seconds(exc))
            )
        else:
            dep.cooldown_until = None
        db.commit()
        invalidate(dep.user_id)
        logger.info("Deployment %s marked %s from a live request.", deployment_id, status.value)
    except Exception as exc2:  # never let bookkeeping break a completion
        logger.warning("Could not record failure for deployment %s: %s", deployment_id, exc2)
    finally:
        db.close()


def record_success(deployment_id: int) -> None:
    """Mark a deployment used (and healthy) after a successful call."""
    db = SessionLocal()
    try:
        dep = db.query(Deployment).filter(Deployment.id == deployment_id).first()
        if not dep:
            return
        now = datetime.now(timezone.utc)
        dep.last_used_at = now
        # The provider just answered, so it is up.
        circuit_breaker.record_success(db, dep.provider_id)
        # A success ends any 429 streak — the next throttle starts the ladder over.
        dep.rate_limit_strikes = 0
        if dep.status is not ModelHealth.available:
            # A recovery is the most interesting event on the timeline: it is
            # where "throttled" becomes "serving again". Recorded before the
            # status is overwritten (SRS §20).
            health_history.record_transition(
                db, dep, ModelHealth.available, source="request",
            )
            # It worked — whatever we thought was wrong isn't any more.
            dep.status = ModelHealth.available
            dep.cooldown_until = None
            dep.error = None
            dep.last_checked_at = now
        db.commit()
    except Exception as exc:
        logger.warning("Could not record success for deployment %s: %s", deployment_id, exc)
    finally:
        db.close()


def router_health(user_id: Optional[int] = None) -> Dict[str, Any]:
    """Summary for /health. With no user_id, reports cache size only."""
    if user_id is None:
        return {"cached_routers": len(_cache)}
    router = get_router(user_id)
    per_model: Dict[str, int] = {}
    for dep in getattr(router, "model_list", []) or []:
        name = dep.get("model_name", "?")
        per_model[name] = per_model.get(name, 0) + 1
    return {
        "models": per_model,
        "total_deployments": sum(per_model.values()),
    }
