"""
Admin endpoints — operational controls for the gateway.

  POST /v1/admin/reload-router   → rebuild the Router (e.g. after a probe so the
                                   availability filter takes effect immediately).
  GET  /v1/admin/availability    → the latest model-availability snapshot.

Guarded by the same gateway token as the other /v1 endpoints (open in local dev).
"""

from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.gateway_auth import verify_gateway_key
from app.core.database import get_db
from app.core.llm_router import reload_router, router_health, list_virtual_models
from app.models.user import User
from app.models.provider import Provider
from app.services import gateway_keys, key_store

logger = logging.getLogger("gateway.admin")

router = APIRouter()


def _owner(db: Session) -> User:
    user = db.query(User).first()
    if not user:
        raise HTTPException(status_code=500, detail="No owner user; startup bootstrap did not run.")
    return user


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


@router.post("/v1/admin/refresh", dependencies=[Depends(verify_gateway_key)])
async def refresh_pipeline():
    """
    Run the full spine refresh: discovery → per-key probe → derive common models
    → reload the Router. This is what populates the DB-driven routing tables.
    """
    from app.services.pipeline import refresh
    result = refresh()
    return {"status": "ok", **result, "router": router_health()}


@router.get("/v1/admin/common-models", dependencies=[Depends(verify_gateway_key)])
async def list_common_models(db: Session = Depends(get_db)):
    """List derived common models with their ordered fallback members."""
    from app.models.common_model import CommonModel, CommonModelMember
    from app.models.master_model import MasterModel

    out = []
    for cm in db.query(CommonModel).filter(CommonModel.is_active.is_(True)).order_by(CommonModel.name).all():
        members = (
            db.query(CommonModelMember, MasterModel)
            .join(MasterModel, CommonModelMember.master_model_id == MasterModel.id)
            .filter(CommonModelMember.common_model_id == cm.id)
            .order_by(CommonModelMember.priority)
            .all()
        )
        out.append({
            "name": cm.name,
            "provider_count": cm.provider_count,
            "members": [
                {"priority": mem.priority, "litellm_model": mm.litellm_model,
                 "working_keys": mm.working_key_count}
                for mem, mm in members
            ],
        })
    return {"common_models": out}


@router.get("/v1/admin/pool", dependencies=[Depends(verify_gateway_key)])
async def stored_pool():
    """
    Show the live routing pool. In the single-model design the model list comes
    from the common-model spine (Phase 6); until that builder lands this reflects
    the YAML bootstrap via router_health().
    """
    from app.core.config import settings

    health = router_health()
    return {
        "source": settings.MODEL_POOL_SOURCE,
        "router_source": getattr(settings, "ROUTER_SOURCE", "yaml"),
        "virtual_models": len(health.get("virtual_models", {})),
        "deployments": health.get("total_deployments", 0),
        "models": health.get("virtual_models", {}),
    }


# ───────────────────────────────────────────────────────────────
# GATEWAY KEY MANAGEMENT  (the tokens clients present to /v1)
# ───────────────────────────────────────────────────────────────

class MintKeyRequest(BaseModel):
    name: str = Field(default="default", description="Human label for the key")


@router.post("/v1/admin/gateway-keys", dependencies=[Depends(verify_gateway_key)])
async def mint_gateway_key(payload: MintKeyRequest, db: Session = Depends(get_db)):
    """Mint a new gateway key. The raw token is returned ONCE — store it now."""
    token, row = gateway_keys.mint(db, _owner(db).id, name=payload.name)
    return {
        "id": row.id,
        "name": row.name,
        "token": token,  # shown once
        "key_prefix": row.key_prefix,
        "warning": "Store this token now — it cannot be retrieved again.",
    }


@router.get("/v1/admin/gateway-keys", dependencies=[Depends(verify_gateway_key)])
async def list_gateway_keys(db: Session = Depends(get_db)):
    """List gateway keys (prefixes only — never the raw token)."""
    return {
        "keys": [
            {
                "id": k.id,
                "name": k.name,
                "key_prefix": k.key_prefix,
                "is_active": k.is_active,
                "last_used_at": k.last_used_at.isoformat() if k.last_used_at else None,
                "created_at": k.created_at.isoformat() if k.created_at else None,
            }
            for k in gateway_keys.list_keys(db)
        ]
    }


@router.delete("/v1/admin/gateway-keys/{key_id}", dependencies=[Depends(verify_gateway_key)])
async def revoke_gateway_key(key_id: int, db: Session = Depends(get_db)):
    """Revoke (deactivate) a gateway key."""
    if not gateway_keys.revoke(db, key_id):
        raise HTTPException(status_code=404, detail=f"No gateway key with id {key_id}.")
    return {"status": "revoked", "id": key_id}


# ───────────────────────────────────────────────────────────────
# PROVIDER KEY MANAGEMENT  (the upstream secrets, encrypted at rest)
# ───────────────────────────────────────────────────────────────

class SaveProviderKeyRequest(BaseModel):
    provider: str = Field(..., description="Provider slug, e.g. 'groq'")
    env_slot: str = Field(..., description="Env var LiteLLM reads, e.g. 'GROQ_API_KEY_1'")
    value: str = Field(..., description="The API key (encrypted at rest; never returned)")
    label: Optional[str] = Field(None, description="Optional human label")


@router.put("/v1/admin/provider-keys", dependencies=[Depends(verify_gateway_key)])
async def save_provider_key(payload: SaveProviderKeyRequest, db: Session = Depends(get_db)):
    """Encrypt + persist a provider key, inject it, and rebuild the Router."""
    slug = payload.provider.strip().lower()
    provider = db.query(Provider).filter(Provider.slug == slug).first()
    if not provider:
        provider = Provider(slug=slug, name=slug, litellm_prefix=slug)
        db.add(provider)
        db.commit()
        db.refresh(provider)
    row = key_store.add_key(
        db,
        provider_id=provider.id,
        env_slot=payload.env_slot.strip(),
        value=payload.value,
        label=payload.label,
        user_id=_owner(db).id,
    )
    return {"id": row.id, "env_slot": row.env_slot, "masked": row.key_masked, "provider": slug}


@router.get("/v1/admin/provider-keys", dependencies=[Depends(verify_gateway_key)])
async def list_provider_keys(db: Session = Depends(get_db)):
    """List persisted provider keys (masked — never the secret)."""
    return {"keys": key_store.list_keys(db)}


@router.delete("/v1/admin/provider-keys/{key_id}", dependencies=[Depends(verify_gateway_key)])
async def delete_provider_key(key_id: int, db: Session = Depends(get_db)):
    """Delete a provider key, unset it from the environment, and rebuild the Router."""
    if not key_store.delete_key(db, key_id):
        raise HTTPException(status_code=404, detail=f"No provider key with id {key_id}.")
    return {"status": "deleted", "id": key_id}


@router.get("/v1/admin/availability", dependencies=[Depends(verify_gateway_key)])
async def availability_summary():
    """
    Per-key availability snapshot from the `deployments` table.

    Populated by the Phase 4 probe; empty until a probe has run.
    """
    from app.core.database import SessionLocal
    from app.models.deployment import Deployment
    from app.models.master_model import MasterModel

    db = SessionLocal()
    try:
        rows = (
            db.query(Deployment, MasterModel)
            .join(MasterModel, Deployment.master_model_id == MasterModel.id)
            .order_by(Deployment.is_working.desc(), Deployment.litellm_model)
            .all()
        )
        total = len(rows)
        available = sum(1 for d, _ in rows if d.is_working)
        return {
            "total": total,
            "available": available,
            "unavailable": total - available,
            "models": [
                {
                    "model": d.litellm_model,
                    "normalized_name": mm.normalized_name,
                    "available": d.is_working,
                    "status": d.status.value if d.status else None,
                    "http_code": d.http_code,
                    "latency_ms": d.latency_ms,
                    "error": d.error,
                    "checked_at": d.last_checked_at.isoformat() if d.last_checked_at else None,
                }
                for d, mm in rows
            ],
        }
    finally:
        db.close()
