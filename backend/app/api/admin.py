"""
Admin endpoints — operational controls for the gateway.

  POST /v1/admin/reload-router   → rebuild the Router (e.g. after a probe so the
                                   availability filter takes effect immediately).
  GET  /v1/admin/availability    → the latest model-availability snapshot.

Guarded by the same gateway token as the other /v1 endpoints (open in local dev).
"""

from __future__ import annotations

import logging
from fastapi import APIRouter, Depends

from app.api.gateway_auth import verify_gateway_key
from app.core.llm_router import reload_router, router_health, list_virtual_models

logger = logging.getLogger("gateway.admin")

router = APIRouter()


@router.post("/v1/admin/reload-router", dependencies=[Depends(verify_gateway_key)])
async def reload_router_endpoint():
    """Rebuild the smart Router from the pool + current availability data."""
    reload_router()
    logger.info("Router reloaded via admin endpoint.")
    return {
        "status": "reloaded",
        "virtual_models": list_virtual_models(),
        "router": router_health(),
    }


@router.get("/v1/admin/pool", dependencies=[Depends(verify_gateway_key)])
async def stored_pool():
    """Show the model pool stored in PostgreSQL (deployments + router settings)."""
    from app.core.config import settings
    from app.core.database import SessionLocal
    from app.models.model_pool import ModelDeployment, RouterConfig

    db = SessionLocal()
    try:
        deps = db.query(ModelDeployment).order_by(
            ModelDeployment.model_name, ModelDeployment.id
        ).all()
        rc = db.query(RouterConfig).first()
        by_model: dict = {}
        for d in deps:
            by_model.setdefault(d.model_name, []).append(
                {
                    "litellm_model": d.litellm_model,
                    "api_key_ref": d.api_key_ref,
                    "api_base_ref": d.api_base_ref,
                    "rpm": d.rpm,
                    "enabled": d.enabled,
                }
            )
        return {
            "source": settings.MODEL_POOL_SOURCE,
            "stored_in_db": bool(deps),
            "virtual_models": len(by_model),
            "deployments": len(deps),
            "router_settings": None if not rc else {
                "routing_strategy": rc.routing_strategy,
                "num_retries": rc.num_retries,
                "cooldown_time": rc.cooldown_time,
                "allowed_fails": rc.allowed_fails,
                "fallbacks": rc.fallbacks,
            },
            "models": by_model,
        }
    finally:
        db.close()


@router.get("/v1/admin/availability", dependencies=[Depends(verify_gateway_key)])
async def availability_summary():
    """Return the latest model-availability snapshot from the last probe."""
    from app.core.database import SessionLocal
    from app.models.model_availability import ModelAvailability

    db = SessionLocal()
    try:
        rows = db.query(ModelAvailability).order_by(
            ModelAvailability.available.desc(), ModelAvailability.concrete_model
        ).all()
        total = len(rows)
        available = sum(1 for r in rows if r.available)
        return {
            "total": total,
            "available": available,
            "unavailable": total - available,
            "models": [
                {
                    "model": r.concrete_model,
                    "provider": r.provider,
                    "available": r.available,
                    "status": r.status,
                    "http_code": r.http_code,
                    "latency_ms": r.latency_ms,
                    "error": r.error,
                    "checked_at": r.checked_at.isoformat() if r.checked_at else None,
                }
                for r in rows
            ],
        }
    finally:
        db.close()
