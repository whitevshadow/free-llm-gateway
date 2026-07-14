"""
Identity, the public provider list, and usage analytics.

These are the reads the dashboard is built on. They are separate from admin.py
because none of them is an admin action — they all answer "what is true for the
caller".

EVERY QUERY IS SCOPED BY user.id. The analytics here read `request_logs`, which is
per-user; a user cannot see another user's traffic, and there is no endpoint that
would let them try.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select, desc
from sqlalchemy.orm import Session

from app.api.gateway_auth import current_user
from app.core.database import get_db
from app.models.deployment import Deployment
from app.models.provider import Provider
from app.models.provider_key import ProviderKey
from app.models.provider_model import ProviderModel
from app.models.request_log import RequestLog
from app.models.user import User

logger = logging.getLogger("gateway.me")

router = APIRouter()


# ═══════════════════════════════════════════════════════════════════════════
#  IDENTITY — who am I?
# ═══════════════════════════════════════════════════════════════════════════

@router.get("/v1/me")
async def whoami(user: User = Depends(current_user), db: Session = Depends(get_db)):
    """
    Who the presented gateway key belongs to.

    The frontend calls this to validate a pasted key and to decide whether to
    render the Admin nav. That nav decision is COSMETIC — `require_admin` enforces
    the real thing server-side with a 403. Hiding a menu item is not security.
    """
    keys = db.query(ProviderKey).filter(ProviderKey.user_id == user.id).count()
    return {
        "id": user.id,
        "public_id": str(user.public_id),
        "email": user.email,
        "is_admin": user.is_admin,
        "provider_key_count": keys,   # 0 => show the onboarding CTA, not an empty dashboard
    }


# ═══════════════════════════════════════════════════════════════════════════
#  PROVIDERS — readable by ANY user
# ═══════════════════════════════════════════════════════════════════════════

@router.get("/v1/providers")
async def list_providers(user: User = Depends(current_user), db: Session = Depends(get_db)):
    """
    The providers an admin has seeded — what a user may attach a key to.

    This has to exist and be non-admin: without it a normal user cannot populate
    the "pick a provider" dropdown, and so cannot add a key at all. It exposes no
    secrets (a provider is a destination; keys live in provider_keys).
    """
    rows = (
        db.query(Provider, func.count(ProviderModel.id))
        .outerjoin(
            ProviderModel,
            (ProviderModel.provider_id == Provider.id) & (ProviderModel.enabled.is_(True)),
        )
        .filter(Provider.enabled.is_(True))
        .group_by(Provider.id)
        .order_by(Provider.name)
        .all()
    )
    mine = {
        pk.provider_id
        for pk in db.query(ProviderKey).filter(ProviderKey.user_id == user.id)
    }

    # The preset hint says WHAT KIND of key a provider wants (e.g. GitHub needs a
    # fine-grained PAT with Models:read — a classic token 401s). Users hit that
    # question at the add-key dialog, so it ships with this response.
    from app.services import presets
    hints = {p["slug"]: p["hint"] for p in presets.PRESETS}

    return {
        "providers": [
            {
                "id": p.id,
                "slug": p.slug,
                "name": p.name,
                "base_url": p.base_url,
                "docs_url": p.docs_url,
                "key_hint": hints.get(p.slug),
                "model_count": n,
                "has_my_key": p.id in mine,
            }
            for p, n in rows
        ]
    }


# ═══════════════════════════════════════════════════════════════════════════
#  PROBE PROGRESS — powers the progress bar for Discover / Re-test
# ═══════════════════════════════════════════════════════════════════════════

@router.get("/v1/me/probe-status")
async def my_probe_status(user: User = Depends(current_user)):
    """
    How far along MY background probes are: {active, done, total}.

    Deliberately cheap (an in-memory dict lookup, no DB) because the UI polls it
    every second while a bar is showing. Progress is ephemeral by design — it
    does not survive a restart, and doesn't need to: the probes' RESULTS are in
    the deployments table; this only narrates the wait.
    """
    from app.services import prober
    return prober.get_progress(user.id)


# ═══════════════════════════════════════════════════════════════════════════
#  USAGE — the dashboard
# ═══════════════════════════════════════════════════════════════════════════

@router.get("/v1/me/usage")
async def my_usage(
    days: int = Query(30, ge=1, le=365),
    top: int = Query(5, ge=1, le=20),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    """
    Usage aggregates for MY traffic over the last `days`.

    Everything here is a GROUP BY over request_logs, which already records the
    exact deployment, provider and KEY that answered each call.

    `per_key` is the one worth reading: it shows which of your keys is carrying
    the load and which is exhausted. That is the question the whole multi-key
    design exists to answer — two Groq keys are two independent free-tier budgets,
    and this is where you see them being spent.
    """
    since = datetime.now(timezone.utc) - timedelta(days=days)
    base = (RequestLog.user_id == user.id, RequestLog.created_at >= since)

    totals = db.query(
        func.count(RequestLog.id),
        func.coalesce(func.sum(RequestLog.total_tokens), 0),
        func.coalesce(func.sum(RequestLog.prompt_tokens), 0),
        func.coalesce(func.sum(RequestLog.completion_tokens), 0),
        func.coalesce(func.sum(RequestLog.cost), 0),
        func.coalesce(func.avg(RequestLog.latency_ms), 0),
    ).filter(*base).one()

    errors = db.query(func.count(RequestLog.id)).filter(
        *base, RequestLog.status_code >= 400
    ).scalar()

    # ── top models by tokens ──
    top_models = (
        db.query(
            RequestLog.requested_model,
            func.coalesce(func.sum(RequestLog.total_tokens), 0).label("tokens"),
            func.count(RequestLog.id).label("requests"),
        )
        .filter(*base)
        .group_by(RequestLog.requested_model)
        .order_by(desc("tokens"))
        .limit(top)
        .all()
    )

    # ── top providers by tokens ──
    top_providers = (
        db.query(
            Provider.name,
            Provider.slug,
            func.coalesce(func.sum(RequestLog.total_tokens), 0).label("tokens"),
            func.count(RequestLog.id).label("requests"),
        )
        .join(Provider, Provider.id == RequestLog.provider_id)
        .filter(*base)
        .group_by(Provider.id)
        .order_by(desc("tokens"))
        .limit(top)
        .all()
    )

    # ── daily series ──
    daily = (
        db.query(
            func.date_trunc("day", RequestLog.created_at).label("day"),
            func.coalesce(func.sum(RequestLog.total_tokens), 0).label("tokens"),
            func.count(RequestLog.id).label("requests"),
        )
        .filter(*base)
        .group_by("day")
        .order_by("day")
        .all()
    )

    # ── per-KEY burn, joined to live health ──
    per_key = (
        db.query(
            ProviderKey.id,
            ProviderKey.label,
            ProviderKey.key_masked,
            Provider.name,
            func.count(RequestLog.id).label("requests"),
            func.coalesce(func.sum(RequestLog.total_tokens), 0).label("tokens"),
        )
        .join(Provider, Provider.id == ProviderKey.provider_id)
        .outerjoin(
            RequestLog,
            (RequestLog.provider_key_id == ProviderKey.id)
            & (RequestLog.created_at >= since),
        )
        .filter(ProviderKey.user_id == user.id)
        .group_by(ProviderKey.id, Provider.name)
        .order_by(desc("tokens"))
        .all()
    )

    # Live health per key: how many of its deployments are currently usable.
    health = dict(
        db.query(
            Deployment.provider_key_id,
            func.count(Deployment.id).filter(Deployment.is_working.is_(True)),
        )
        .filter(Deployment.user_id == user.id)
        .group_by(Deployment.provider_key_id)
        .all()
    )

    return {
        "window_days": days,
        "totals": {
            "requests": totals[0],
            "total_tokens": int(totals[1]),
            "prompt_tokens": int(totals[2]),
            "completion_tokens": int(totals[3]),
            "cost": float(totals[4]),
            "avg_latency_ms": int(totals[5] or 0),
            "errors": errors,
        },
        "top_models": [
            {"model": m, "tokens": int(t), "requests": r} for m, t, r in top_models
        ],
        "top_providers": [
            {"provider": n, "slug": s, "tokens": int(t), "requests": r}
            for n, s, t, r in top_providers
        ],
        "daily": [
            {"date": d.date().isoformat(), "tokens": int(t), "requests": r}
            for d, t, r in daily
        ],
        "per_key": [
            {
                "id": kid,
                "label": label,
                "masked": masked,
                "provider": pname,
                "requests": reqs,
                "tokens": int(toks),
                "working_models": health.get(kid, 0),
            }
            for kid, label, masked, pname, reqs, toks in per_key
        ],
    }


@router.get("/v1/me/logs")
async def my_logs(
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    status: Optional[str] = Query(None, description="'ok' | 'error'"),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    """
    MY recent calls, newest first — the drill-down from the dashboard.

    Each row names the KEY that answered, which is what makes a 429 legible: you
    can see one key get throttled and the next request served by its sibling.
    """
    q = (
        db.query(RequestLog, Provider, ProviderKey)
        .outerjoin(Provider, Provider.id == RequestLog.provider_id)
        .outerjoin(ProviderKey, ProviderKey.id == RequestLog.provider_key_id)
        .filter(RequestLog.user_id == user.id)
    )
    if status == "ok":
        q = q.filter(RequestLog.status_code < 400)
    elif status == "error":
        q = q.filter(RequestLog.status_code >= 400)

    total = q.count()
    rows = q.order_by(desc(RequestLog.created_at)).offset(offset).limit(limit).all()

    return {
        "total": total,
        "limit": limit,
        "offset": offset,
        "logs": [
            {
                "id": log.id,
                "created_at": log.created_at.isoformat() if log.created_at else None,
                "model": log.requested_model,
                "provider": prov.name if prov else None,
                "key_label": key.label if key else None,
                "key_masked": key.key_masked if key else None,
                "prompt_tokens": log.prompt_tokens,
                "completion_tokens": log.completion_tokens,
                "total_tokens": log.total_tokens,
                "latency_ms": log.latency_ms,
                "status_code": log.status_code,
                "error": log.error_message,
            }
            for log, prov, key in rows
        ],
    }
