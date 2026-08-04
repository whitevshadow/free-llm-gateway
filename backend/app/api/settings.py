"""
Dashboard settings, provider config, and the actions the providers page performs.

Everything here backs the ported providers screen. Three groups:

  SETTINGS     a per-user key/value store for UI preferences (models/user_setting.py).
               Routing settings are NOT here — they stay in `router_config`, the
               typed table the Router reads (SRS §7.1). The merged read below
               includes them so the dashboard sees one object, but a write routes
               each key back to whichever store owns it.

  PROVIDER     per-provider request shaping the gateway can honour: parameter
  CONFIG       denylists/allowlists. Stored as settings keys, applied at call time.

  ACTIONS      connection test, bulk enable/disable, disconnect.

WHY NOT ONE BIG SETTINGS BLOB
    Because `routing_strategy` and friends decide how traffic is routed. Those
    must stay typed and validated in `router_config`; an untyped blob that the
    Router read would make a typo silently change routing. The split is the
    point, not an inconvenience.
"""

import logging
from typing import Any, Dict, Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.gateway_auth import current_user, require_admin
from app.core.database import get_db
from app.models.provider import Provider
from app.models.provider_key import ProviderKey
from app.models.provider_model import ProviderModel
from app.models.user import User
from app.models.user_setting import UserSetting

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Settings"])

# Keys that belong to router_config, not the KV store. A write naming one of
# these is forwarded there so the Router keeps seeing validated values.
ROUTER_CONFIG_KEYS = {
    "routing_strategy",
    "num_retries",
    "cooldown_time",
    "allowed_fails",
    "pinned_provider_id",
}


# ═══════════════════════════════════════════════════════════════════════════
#  SETTINGS
# ═══════════════════════════════════════════════════════════════════════════

def read_settings(db: Session, user_id: int) -> Dict[str, Any]:
    """Every stored preference for one user, as a flat dict."""
    return {
        row.key: row.value
        for row in db.query(UserSetting).filter(UserSetting.user_id == user_id)
    }


def write_setting(db: Session, user_id: int, key: str, value: Any) -> None:
    """Upsert one preference. Caller commits."""
    row = (
        db.query(UserSetting)
        .filter(UserSetting.user_id == user_id, UserSetting.key == key)
        .first()
    )
    if row:
        row.value = value
    else:
        db.add(UserSetting(user_id=user_id, key=key, value=value))


@router.get("/v1/me/settings")
async def get_settings(user: User = Depends(current_user), db: Session = Depends(get_db)):
    """
    All of MY preferences, with the routing settings merged in.

    One object, because the dashboard reads one object. The merge is read-only —
    see the PUT for how writes are split back to the right store.
    """
    from app.models.router_config import RouterConfig

    settings = read_settings(db, user.id)

    rc = db.query(RouterConfig).filter(RouterConfig.user_id == user.id).first()
    if rc:
        settings.update(
            {
                "routing_strategy": rc.routing_strategy,
                "num_retries": rc.num_retries,
                "cooldown_time": rc.cooldown_time,
                "allowed_fails": rc.allowed_fails,
                "pinned_provider_id": rc.pinned_provider_id,
            }
        )
    return settings


@router.put("/v1/me/settings")
async def update_settings(
    payload: Dict[str, Any] = Body(...),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    """
    Merge a partial settings object.

    PARTIAL, not replace: the dashboard sends only what changed, and treating a
    partial body as the whole object would silently clear every preference the
    sending screen happened not to know about.

    Keys owned by router_config are forwarded there rather than shadowed here.
    """
    from app.models.router_config import RouterConfig

    routing_updates = {k: v for k, v in payload.items() if k in ROUTER_CONFIG_KEYS}
    plain = {k: v for k, v in payload.items() if k not in ROUTER_CONFIG_KEYS}

    for key, value in plain.items():
        write_setting(db, user.id, key, value)

    if routing_updates:
        rc = db.query(RouterConfig).filter(RouterConfig.user_id == user.id).first()
        if not rc:
            rc = RouterConfig(user_id=user.id)
            db.add(rc)
            db.flush()
        for key, value in routing_updates.items():
            # pinned_provider_id = 0 is the documented "clear the pin" signal
            # (SRS §8.3), which is not the same as leaving it unset.
            if key == "pinned_provider_id" and value in (0, "0"):
                value = None
            setattr(rc, key, value)
        # A changed routing setting must not wait out the 30s Router TTL.
        from app.core import llm_router

        llm_router.invalidate(user.id)

    db.commit()
    return await get_settings(user=user, db=db)


# ═══════════════════════════════════════════════════════════════════════════
#  PER-PROVIDER PARAMETER FILTERS
# ═══════════════════════════════════════════════════════════════════════════

class ParamFilters(BaseModel):
    """
    Request-parameter shaping for one provider.

    Providers reject parameters they do not implement — NVIDIA NIM 400s on
    `thinking` — and a 400 is not retryable, so one unsupported field fails a
    request that any sibling deployment could have served.

    blocked    stripped from outgoing requests (denylist)
    allowed    re-added after stripping, when the client actually sent them.
               Order matters: allow is applied AFTER block, so a param in both
               survives. That is what makes a broad denylist plus a narrow
               exception expressible.
    """

    blocked: list[str] = Field(default_factory=list)
    allowed: list[str] = Field(default_factory=list)
    auto_learn: bool = Field(
        default=False,
        description="Add a param to `blocked` automatically on an 'Unsupported parameter' 400.",
    )


def _param_key(slug: str) -> str:
    return f"paramFilters.{slug.lower()}"


@router.get("/v1/me/providers/{slug}/param-filters")
async def get_param_filters(
    slug: str, user: User = Depends(current_user), db: Session = Depends(get_db)
):
    stored = read_settings(db, user.id).get(_param_key(slug))
    if not stored:
        return ParamFilters().model_dump()
    return stored


@router.put("/v1/me/providers/{slug}/param-filters")
async def set_param_filters(
    slug: str,
    payload: ParamFilters,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    """Replace this provider's parameter filters. Whole-object by design — the
    editor sends both lists together, so a merge would make removal impossible."""
    write_setting(db, user.id, _param_key(slug), payload.model_dump())
    db.commit()
    return payload.model_dump()


# ═══════════════════════════════════════════════════════════════════════════
#  CONNECTION ACTIONS
# ═══════════════════════════════════════════════════════════════════════════

@router.get("/v1/me/providers/{slug}")
async def provider_detail(
    slug: str, user: User = Depends(current_user), db: Session = Depends(get_db)
):
    """
    One provider, with MY keys and catalog size — what the provider detail page
    loads on open.
    """
    prov = db.query(Provider).filter(Provider.slug == slug.lower()).first()
    if not prov:
        raise HTTPException(status_code=404, detail=f"No provider {slug!r}.")

    from app.services import key_store, presets

    keys = [k for k in key_store.list_keys(db, user_id=user.id) if k["provider"] == prov.slug]
    model_count = (
        db.query(ProviderModel)
        .filter(ProviderModel.provider_id == prov.id, ProviderModel.enabled.is_(True))
        .count()
    )
    hints = {p["slug"]: p["hint"] for p in presets.PRESETS}

    return {
        "id": prov.slug,
        "slug": prov.slug,
        "name": prov.name,
        "base_url": prov.base_url,
        "docs_url": prov.docs_url,
        "enabled": prov.enabled,
        "key_hint": hints.get(prov.slug),
        "model_count": model_count,
        "keys": keys,
        "param_filters": read_settings(db, user.id).get(_param_key(prov.slug))
        or ParamFilters().model_dump(),
    }


@router.delete("/v1/me/providers/{slug}/connection")
async def disconnect_provider(
    slug: str, user: User = Depends(current_user), db: Session = Depends(get_db),
):
    """
    Remove MY keys for a provider — the dashboard's "disconnect".

    Deletes only the CALLER's keys. The provider row is left registered: it is a
    destination an admin seeded, shared with every other user, and disconnecting
    yourself must not remove it from under them.

    Deployments cascade with the keys (models/deployment.py), so this is
    destructive — the caller is expected to have confirmed.
    """
    from app.services import key_store

    prov = db.query(Provider).filter(Provider.slug == slug.lower()).first()
    if not prov:
        raise HTTPException(status_code=404, detail=f"No provider {slug!r}.")

    mine = (
        db.query(ProviderKey)
        .filter(ProviderKey.user_id == user.id, ProviderKey.provider_id == prov.id)
        .all()
    )
    removed = 0
    for key in mine:
        if key_store.delete_key(db, key.id, user.id):
            removed += 1

    from app.core import llm_router

    llm_router.invalidate(user.id)
    return {"success": True, "provider": prov.slug, "keys_removed": removed}


class BulkProviderRequest(BaseModel):
    slugs: list[str] = Field(default_factory=list)
    enabled: Optional[bool] = None


@router.patch("/v1/admin/providers-bulk")
async def bulk_update_providers(
    payload: BulkProviderRequest,
    _: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """
    Enable or disable several providers at once — the page's bulk actions.

    Admin-only, matching single-provider updates: a disabled provider drops out
    of every user's candidate set, so it is not a personal preference.
    """
    if payload.enabled is None:
        raise HTTPException(status_code=400, detail="`enabled` is required.")

    rows = db.query(Provider).filter(Provider.slug.in_([s.lower() for s in payload.slugs])).all()
    for row in rows:
        row.enabled = payload.enabled
    db.commit()

    from app.core import llm_router

    # Every user's candidate set just changed.
    llm_router.invalidate_all() if hasattr(llm_router, "invalidate_all") else None

    return {
        "success": True,
        "updated": [r.slug for r in rows],
        "enabled": payload.enabled,
        "not_found": sorted(set(s.lower() for s in payload.slugs) - {r.slug for r in rows}),
    }
