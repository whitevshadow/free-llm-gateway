"""
Combos — CRUD, builder options, metrics, test and simulate.

Everything here is SCOPED TO THE CALLER. A combo is addressed by its database id,
and every query carries `user_id`, so guessing another user's combo id returns a
404 rather than their configuration.

WHY THE PAYLOAD IS camelCase
    These endpoints back the ported OmniRoute combo builder, which was written
    against camelCase JSON (`isActive`, `sortOrder`, `allowedConnectionIds`).
    Translating at the edge here — rather than in the dashboard's bridge — keeps
    one shape for the whole feature: what the builder sends is what a curl user
    sends, and the API documents itself through the UI that uses it.

WHAT THE SERVER VALIDATES, AND WHAT IT ONLY STORES
    Name, strategy and step SHAPE are validated: they decide routing, and a typo
    that silently changed which model is called is exactly the bug worth
    preventing. The rest of `config` is stored as sent — it is builder state
    (which panels are open, editor drafts) plus runtime knobs whose meaning lives
    in combo_router, and a strict allowlist here would mean editing this file
    every time the UI grows a checkbox.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.api.gateway_auth import current_user
from app.core.database import get_db
from app.models.combo import Combo
from app.models.deployment import Deployment
from app.models.provider import Provider
from app.models.provider_key import ProviderKey
from app.models.provider_model import ProviderModel
from app.models.request_log import RequestLog
from app.models.user import User
from app.services import combo_router

logger = logging.getLogger("gateway.combos")

router = APIRouter(tags=["Combos"])

# Mirrors the dashboard's own VALID_NAME_REGEX. A combo name IS a model name —
# clients put it in `"model": "..."` — so it must survive a URL and a JSON body
# without escaping.
NAME_RE = re.compile(r"^[a-zA-Z0-9_/.\-\[\] ]+$")
MAX_STEPS = 50


# ═══════════════════════════════════════════════════════════════════════════
#  SERIALISATION
# ═══════════════════════════════════════════════════════════════════════════

def _iso(value: Optional[datetime]) -> Optional[str]:
    return value.isoformat() if value else None


# Per-combo AGENT knobs. The builder sends these at the TOP LEVEL of the body and
# reads them back from the top level of the combo record, but none of them is a
# routing signal — they shape the request an agent client makes THROUGH the combo
# (a system prompt, a tool-name filter, a context budget). They are stored inside
# `config` under "agent" so the table keeps one column for "everything that is not
# routing", and lifted back to the top level on the way out.
AGENT_FIELDS = (
    "system_message",
    "tool_filter_regex",
    "context_cache_protection",
    "context_length",
)


def serialize(combo: Combo) -> Dict[str, Any]:
    """The shape the combo builder reads and writes."""
    config = combo.config if isinstance(combo.config, dict) else {}
    agent = config.get("agent") if isinstance(config.get("agent"), dict) else {}
    return {
        **{field: agent[field] for field in AGENT_FIELDS if field in agent},
        "id": str(combo.id),
        "name": combo.name,
        "description": combo.description,
        "strategy": combo.strategy,
        "models": combo.models or [],
        "config": combo.config or {},
        "isActive": bool(combo.is_active),
        "isHidden": bool(combo.is_hidden),
        "sortOrder": combo.sort_order,
        "createdAt": _iso(combo.created_at),
        "updatedAt": _iso(combo.updated_at),
        # The builder's list view shows a target count without expanding steps.
        "targetCount": len(combo.models or []),
    }


class ComboPayload(BaseModel):
    """
    A create or update body. Every field is optional so PUT can be a PARTIAL
    merge: the dashboard's toggle sends `{isActive: false}` alone, and treating
    that as a whole-object replace would wipe the combo's steps.
    """

    name: Optional[str] = Field(None, max_length=120)
    description: Optional[str] = None
    strategy: Optional[str] = Field(None, max_length=48)
    models: Optional[List[Any]] = None
    config: Optional[Dict[str, Any]] = None
    isActive: Optional[bool] = None
    isHidden: Optional[bool] = None
    sortOrder: Optional[int] = None

    # Agent knobs — see AGENT_FIELDS. snake_case because that is what the builder
    # sends for these four; renaming them here would only move the mismatch.
    system_message: Optional[str] = Field(None, max_length=20000)
    tool_filter_regex: Optional[str] = Field(None, max_length=2000)
    context_cache_protection: Optional[bool] = None
    context_length: Optional[int] = Field(None, ge=1000, le=2_000_000)


def _merge_agent_fields(
    config: Dict[str, Any],
    data: Dict[str, Any],
    existing_agent: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Fold the agent knobs the caller SET into `config["agent"]`.

    Only keys present in the body are touched, and an explicit null clears one —
    which is how the builder says "no system message any more" as opposed to
    "I did not mention it".

    `existing_agent` carries the stored block forward. Without it, a PUT that
    replaces `config` alone would take the agent knobs down with it — they live
    inside that column, but they are separate FIELDS, and the endpoint promises
    per-field partial updates.
    """
    merged = dict(config or {})
    agent = dict(existing_agent or merged.get("agent") or {})
    for field in AGENT_FIELDS:
        if field not in data:
            continue
        if data[field] is None:
            agent.pop(field, None)
        else:
            agent[field] = data[field]
    if agent:
        merged["agent"] = agent
    else:
        merged.pop("agent", None)
    return merged


def _validate_name(db: Session, user_id: int, name: str, exclude_id: Optional[int]) -> str:
    cleaned = (name or "").strip()
    if not cleaned:
        raise HTTPException(status_code=400, detail="A combo needs a name.")
    if not NAME_RE.fullmatch(cleaned):
        raise HTTPException(
            status_code=400,
            detail="Combo names may contain letters, digits, spaces and _ / . - [ ] only.",
        )
    clash = (
        db.query(Combo)
        .filter(Combo.user_id == user_id, func.lower(Combo.name) == cleaned.lower())
        .first()
    )
    if clash and clash.id != exclude_id:
        raise HTTPException(
            status_code=409, detail=f"You already have a combo named {cleaned!r}.",
        )
    return cleaned


def _validate_steps(steps: List[Any]) -> List[Any]:
    """
    Check the SHAPE of each step. Rejects what would silently misroute.

    A step is either a model step (needs a model) or a combo reference (needs a
    combo name). Anything else is dropped rather than stored: a step the router
    cannot read is a step that will look configured in the UI and never fire.
    """
    if len(steps) > MAX_STEPS:
        raise HTTPException(
            status_code=400, detail=f"A combo may hold at most {MAX_STEPS} steps.",
        )
    cleaned: List[Any] = []
    for index, step in enumerate(steps):
        if isinstance(step, str):
            if step.strip():
                cleaned.append(step.strip())
            continue
        if not isinstance(step, dict):
            raise HTTPException(
                status_code=400, detail=f"Step {index + 1} is not a model or combo reference.",
            )
        if step.get("kind") == "combo-ref":
            if not str(step.get("comboName") or "").strip():
                raise HTTPException(
                    status_code=400, detail=f"Step {index + 1} references a combo with no name.",
                )
        elif not str(step.get("model") or "").strip():
            raise HTTPException(
                status_code=400, detail=f"Step {index + 1} has no model.",
            )
        cleaned.append(step)
    return cleaned


def _validate_strategy(strategy: str) -> str:
    cleaned = (strategy or "priority").strip().lower()
    if cleaned not in combo_router.STRATEGIES:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown strategy {strategy!r}. Known: {', '.join(combo_router.STRATEGIES)}.",
        )
    return cleaned


def _mine_or_404(db: Session, user_id: int, combo_id: str) -> Combo:
    try:
        numeric = int(combo_id)
    except (TypeError, ValueError):
        raise HTTPException(status_code=404, detail=f"No combo {combo_id!r}.")
    combo = (
        db.query(Combo)
        .filter(Combo.id == numeric, Combo.user_id == user_id)
        .first()
    )
    if not combo:
        # 404, not 403: confirming that someone ELSE'S combo exists is itself a leak.
        raise HTTPException(status_code=404, detail=f"No combo {combo_id!r}.")
    return combo


# ═══════════════════════════════════════════════════════════════════════════
#  CRUD
# ═══════════════════════════════════════════════════════════════════════════

@router.get("/v1/me/combos")
async def list_combos(
    user: User = Depends(current_user), db: Session = Depends(get_db),
):
    """MY combos, in dashboard order."""
    rows = (
        db.query(Combo)
        .filter(Combo.user_id == user.id)
        .order_by(Combo.sort_order, func.lower(Combo.name))
        .all()
    )
    return {"combos": [serialize(c) for c in rows], "total": len(rows)}


@router.post("/v1/me/combos", status_code=201)
async def create_combo(
    payload: ComboPayload,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    """Create a combo. Its name becomes callable as a model immediately."""
    name = _validate_name(db, user.id, payload.name or "", exclude_id=None)
    highest = (
        db.query(func.coalesce(func.max(Combo.sort_order), 0))
        .filter(Combo.user_id == user.id)
        .scalar()
    )

    combo = Combo(
        user_id=user.id,
        name=name,
        description=payload.description,
        strategy=_validate_strategy(payload.strategy or "priority"),
        models=_validate_steps(payload.models or []),
        config=_merge_agent_fields(
            payload.config or {}, payload.model_dump(exclude_unset=True),
        ),
        is_active=True if payload.isActive is None else bool(payload.isActive),
        is_hidden=bool(payload.isHidden),
        sort_order=(
            payload.sortOrder if payload.sortOrder is not None else int(highest or 0) + 1
        ),
    )
    db.add(combo)
    db.commit()
    db.refresh(combo)
    logger.info("User %s created combo %r (%s).", user.id, combo.name, combo.strategy)
    return serialize(combo)


@router.get("/v1/me/combos/builder-options")
async def builder_options(
    user: User = Depends(current_user), db: Session = Depends(get_db),
):
    """
    Everything the combo builder's three dropdowns need: MY providers, the models
    each one serves me, and MY accounts on each.

    Built from deployments rather than the global catalog, because the builder
    must only offer what the caller can actually call. A provider the user holds
    no key for has nothing to select and does not appear.

    `providerId` is the provider SLUG, and a model option's id is the bare family
    name — so a step's `model` is "<slug>/<family>", which is exactly what
    combo_router splits it back into. The identifiers the UI writes are the
    identifiers the router reads; nothing is translated in between.
    """
    keys = (
        db.query(ProviderKey, Provider)
        .join(Provider, ProviderKey.provider_id == Provider.id)
        .filter(ProviderKey.user_id == user.id)
        .order_by(ProviderKey.id)
        .all()
    )

    # Working / total deployment counts per key — the same signal the providers
    # page uses to call a connection healthy.
    counts = dict(
        db.query(
            Deployment.provider_key_id,
            func.count(Deployment.id).filter(Deployment.is_working.is_(True)),
        )
        .filter(Deployment.user_id == user.id)
        .group_by(Deployment.provider_key_id)
        .all()
    )
    totals = dict(
        db.query(Deployment.provider_key_id, func.count(Deployment.id))
        .filter(Deployment.user_id == user.id)
        .group_by(Deployment.provider_key_id)
        .all()
    )

    # Models per provider, from THIS user's deployments.
    model_rows = (
        db.query(
            Provider.slug,
            ProviderModel.normalized_name,
            func.min(ProviderModel.display_name),
            func.max(ProviderModel.context_window),
            func.max(ProviderModel.max_output_tokens),
            func.min(ProviderModel.mode),
            func.count(Deployment.id).filter(Deployment.is_working.is_(True)),
            # What the "Free Stack" / "Paid Premium" templates select on. The
            # catalog records free-vs-paid, not a price, so this flag is the only
            # cost signal that is a measurement rather than a guess.
            func.bool_or(ProviderModel.is_free),
        )
        .join(Deployment, Deployment.provider_model_id == ProviderModel.id)
        .join(Provider, Provider.id == ProviderModel.provider_id)
        .filter(Deployment.user_id == user.id, ProviderModel.enabled.is_(True))
        .group_by(Provider.slug, ProviderModel.normalized_name)
        .order_by(Provider.slug, ProviderModel.normalized_name)
        .all()
    )

    models_by_slug: Dict[str, List[Dict[str, Any]]] = {}
    for slug, name, display, ctx, out_tokens, mode, live, is_free in model_rows:
        mode_value = mode.value if hasattr(mode, "value") else mode
        models_by_slug.setdefault(slug, []).append({
            "id": name,
            "qualifiedModel": f"{slug}/{name}",
            "name": display or name,
            # "imported" is the builder's word for "came from a connected
            # provider", as opposed to a hand-typed custom model.
            "source": "imported",
            "sources": ["imported"],
            "contextLength": int(ctx or 0) or None,
            "outputTokenLimit": int(out_tokens or 0) or None,
            "mode": mode_value,
            "liveDeployments": int(live or 0),
            "isFree": bool(is_free),
        })

    providers: Dict[str, Dict[str, Any]] = {}
    for key, provider in keys:
        entry = providers.setdefault(provider.slug, {
            "providerId": provider.slug,
            "providerType": provider.slug,
            "displayName": provider.name,
            "alias": provider.slug,
            "prefix": provider.slug,
            "icon": "hub",
            "color": "#6366f1",
            "source": "system",
            # The gateway only calls models it discovered from the provider, so a
            # hand-typed model id has nothing to bind to.
            "acceptsArbitraryModel": False,
            "connections": [],
            "models": models_by_slug.get(provider.slug, []),
        })

        working = int(counts.get(key.id, 0) or 0)
        total = int(totals.get(key.id, 0) or 0)
        if not key.is_active:
            status = "inactive"
        elif total > 0 and working == 0:
            status = "error"
        else:
            status = "active"

        entry["connections"].append({
            # The connection id is the PROVIDER KEY id — the same identity the
            # providers page uses, and what a pinned step stores.
            "id": str(key.id),
            "label": key.label or f"{provider.name} #{key.id}",
            "type": "apikey",
            "status": status,
            "priority": 0,
            "isActive": bool(key.is_active),
            "defaultModel": None,
            "rateLimitedUntil": None,
            "lastError": None,
            "lastTested": None,
            "workingModels": working,
            "totalModels": total,
        })

    options = []
    for entry in providers.values():
        entry["connectionCount"] = len(entry["connections"])
        entry["activeConnectionCount"] = sum(
            1 for c in entry["connections"] if c["status"] == "active"
        )
        entry["modelCount"] = len(entry["models"])
        options.append(entry)
    options.sort(key=lambda e: e["displayName"].lower())

    combo_refs = [
        {"id": str(c.id), "name": c.name, "strategy": c.strategy,
         "targetCount": len(c.models or [])}
        for c in db.query(Combo)
        .filter(Combo.user_id == user.id, Combo.is_active.is_(True))
        .order_by(Combo.sort_order, Combo.name)
    ]

    return {"providers": options, "comboRefs": combo_refs}


@router.get("/v1/me/combos/metrics")
async def combo_metrics(
    combo: Optional[str] = Query(None, description="Combo name; omit for all of mine."),
    days: int = Query(30, ge=1, le=365),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    """
    Measured per-combo traffic, from `request_logs`.

    With `?combo=<name>` the response is that combo's metrics object; without it,
    a map keyed by combo name (what the list page renders per card).

    Every number here is counted, never estimated. A combo with no traffic is
    ABSENT from the map rather than present with zeros — "not used yet" and
    "used, and everything failed" must not look the same.
    """
    since = datetime.now(timezone.utc) - timedelta(days=days)
    query = (
        db.query(
            RequestLog.combo_name,
            func.count(RequestLog.id),
            func.count(RequestLog.id).filter(RequestLog.status_code < 400),
            func.count(RequestLog.id).filter(RequestLog.combo_attempt > 1),
            func.coalesce(func.avg(RequestLog.latency_ms), 0),
            func.max(RequestLog.created_at),
        )
        .filter(
            RequestLog.user_id == user.id,
            RequestLog.combo_name.isnot(None),
            RequestLog.created_at >= since,
        )
        .group_by(RequestLog.combo_name)
    )
    if combo:
        query = query.filter(func.lower(RequestLog.combo_name) == combo.strip().lower())

    metrics: Dict[str, Dict[str, Any]] = {}
    for name, total, ok, fallbacks, avg_latency, last in query.all():
        total = int(total or 0)
        metrics[name] = {
            "totalRequests": total,
            "totalSuccesses": int(ok or 0),
            "totalFailures": total - int(ok or 0),
            "totalFallbacks": int(fallbacks or 0),
            "avgLatencyMs": int(round(float(avg_latency or 0))),
            "successRate": round(100.0 * float(ok or 0) / total, 1) if total else 0.0,
            "fallbackRate": round(100.0 * float(fallbacks or 0) / total, 1) if total else 0.0,
            "lastUsedAt": _iso(last),
        }

    if combo:
        # Single-combo form: the control centre reads `metrics` as one object.
        for value in metrics.values():
            return {"metrics": value}
        return {"metrics": None, "message": "This combo has served no traffic yet."}

    return {"metrics": metrics}


@router.post("/v1/me/combos/test")
async def test_combo(
    payload: Dict[str, Any] = Body(...),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    """
    Resolve a combo and report what WOULD be called — no upstream call, no tokens.

    Answers the question a config screen can actually answer ("does every step
    bind to something I can reach right now?"), which is why it does not send a
    probe completion: a live probe would spend the user's free-tier quota every
    time they clicked Test, and would report the provider's mood rather than the
    combo's correctness.
    """
    name = str(payload.get("comboName") or payload.get("name") or "").strip()
    combo_id = payload.get("comboId") or payload.get("id")

    combo = None
    if name:
        combo = combo_router.get_combo(db, user.id, name)
    elif combo_id:
        combo = _mine_or_404(db, user.id, str(combo_id))
    if not combo:
        raise HTTPException(status_code=404, detail=f"No combo named {name!r}.")

    try:
        return combo_router.dry_run(db, user.id, combo)
    except combo_router.ComboError as exc:
        return {"error": exc.message, "comboName": combo.name}


@router.post("/v1/me/combos/simulate")
async def simulate_combo(
    payload: Dict[str, Any] = Body(...),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    """
    The routing playground: the ordered target list this combo would produce,
    with per-target status and a token-count-based cost estimate.

    Cost is reported as 0 for free deployments and left `null`-ish (0) for paid
    ones, because the catalog stores no per-token price — see
    combo_router._order_by_catalog. Showing a made-up dollar figure would be
    worse than showing none.
    """
    combo_id = payload.get("comboId") or payload.get("id")
    name = str(payload.get("comboName") or "").strip()

    combo = None
    if combo_id:
        combo = _mine_or_404(db, user.id, str(combo_id))
    elif name:
        combo = combo_router.get_combo(db, user.id, name)
    if not combo:
        raise HTTPException(status_code=404, detail="Pick a combo to simulate.")

    try:
        steps = combo_router.plan(db, user.id, combo)
    except combo_router.ComboError as exc:
        return {
            "comboId": str(combo.id), "comboName": combo.name,
            "strategy": combo.strategy, "targets": [],
            "totalEstimatedCost": 0, "totalEstimatedLatencyMs": 0,
            "warnings": [], "errors": [exc.message],
        }

    # Median-ish latency per model from this user's own history — a measurement,
    # not a guess. Models with no history report 0 and the UI shows no estimate.
    since = datetime.now(timezone.utc) - timedelta(days=7)
    latencies = dict(
        db.query(
            RequestLog.requested_model,
            func.coalesce(func.avg(RequestLog.latency_ms), 0),
        )
        .filter(
            RequestLog.user_id == user.id,
            RequestLog.created_at >= since,
            RequestLog.status_code < 400,
        )
        .group_by(RequestLog.requested_model)
        .all()
    )

    targets = []
    warnings: List[str] = []
    total_latency = 0
    for rank, (target, deployments) in enumerate(steps, start=1):
        latency = int(round(float(latencies.get(target.model, 0) or 0)))
        if deployments:
            status = "available"
            total_latency += latency
        else:
            status = "no_quota"
            warnings.append(f"{target.describe()} has no reachable deployment right now.")
        targets.append({
            "provider": target.provider_slug or "any",
            "model": target.model,
            "strategy": combo.strategy,
            "rank": rank,
            "estimatedCost": 0,
            "estimatedLatencyMs": latency,
            "status": status,
        })

    return {
        "comboId": str(combo.id),
        "comboName": combo.name,
        "strategy": combo.strategy,
        "targets": targets,
        "totalEstimatedCost": 0,
        "totalEstimatedLatencyMs": total_latency,
        "warnings": warnings,
        "errors": [] if targets else ["This combo has no steps yet."],
    }


@router.post("/v1/me/combos/reorder")
async def reorder_combos(
    payload: Dict[str, Any] = Body(...),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    """
    Persist the dashboard's drag-and-drop order.

    Ids the caller does not own are ignored rather than rejected, and combos the
    payload omits keep their relative order at the end — so a reorder sent from a
    stale page cannot delete, reveal or reshuffle anything it did not know about.
    """
    ids = payload.get("comboIds") or payload.get("ids") or []
    if not isinstance(ids, list):
        raise HTTPException(status_code=400, detail="'comboIds' must be a list.")

    rows = (
        db.query(Combo)
        .filter(Combo.user_id == user.id)
        .order_by(Combo.sort_order, func.lower(Combo.name))
        .all()
    )
    by_id = {str(c.id): c for c in rows}

    ordered: List[Combo] = []
    seen: set = set()
    for raw in ids:
        combo = by_id.get(str(raw))
        if combo and combo.id not in seen:
            ordered.append(combo)
            seen.add(combo.id)
    ordered.extend(c for c in rows if c.id not in seen)

    for index, combo in enumerate(ordered, start=1):
        combo.sort_order = index
    db.commit()

    return {"combos": [serialize(c) for c in ordered]}


@router.get("/v1/me/combos/{combo_id}")
async def get_combo(
    combo_id: str,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    """One of MY combos."""
    return serialize(_mine_or_404(db, user.id, combo_id))


@router.put("/v1/me/combos/{combo_id}")
async def update_combo(
    combo_id: str,
    payload: ComboPayload,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    """
    PARTIAL update. Only the fields present in the body change.

    Partial, not replace, because the dashboard sends `{isActive:false}` for a
    toggle and `{models:[…]}` for a step edit; treating either as the whole
    object would silently clear everything the sending screen did not mention.
    """
    combo = _mine_or_404(db, user.id, combo_id)
    data = payload.model_dump(exclude_unset=True)

    if "name" in data:
        combo.name = _validate_name(db, user.id, data["name"] or "", exclude_id=combo.id)
    if "description" in data:
        combo.description = data["description"]
    if "strategy" in data:
        combo.strategy = _validate_strategy(data["strategy"] or "priority")
    if "models" in data:
        combo.models = _validate_steps(data["models"] or [])
    # The agent knobs live inside `config`, so they must be folded in AFTER a
    # replacement config — otherwise a body carrying both would drop them.
    stored_config = combo.config if isinstance(combo.config, dict) else {}
    stored_agent = stored_config.get("agent") if isinstance(stored_config.get("agent"), dict) else {}
    base_config = data["config"] if "config" in data else stored_config
    if "config" in data or any(field in data for field in AGENT_FIELDS):
        combo.config = _merge_agent_fields(base_config or {}, data, stored_agent)
    if "isActive" in data:
        combo.is_active = bool(data["isActive"])
    if "isHidden" in data:
        combo.is_hidden = bool(data["isHidden"])
    if "sortOrder" in data and data["sortOrder"] is not None:
        combo.sort_order = int(data["sortOrder"])

    db.commit()
    db.refresh(combo)
    return serialize(combo)


@router.delete("/v1/me/combos/{combo_id}")
async def delete_combo(
    combo_id: str,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    """
    Delete one of MY combos.

    Other combos may still REFERENCE this one by name. Those references are not
    rewritten: combo_router skips a dangling reference and logs it, so the rest
    of a chain keeps serving instead of the whole thing failing because one
    helper combo went away.
    """
    combo = _mine_or_404(db, user.id, combo_id)
    name = combo.name
    db.delete(combo)
    db.commit()
    logger.info("User %s deleted combo %r.", user.id, name)
    return {"success": True, "deleted": name}
