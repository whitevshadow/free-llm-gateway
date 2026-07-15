"""
Admin + self-service endpoints.

TWO SURFACES, TWO DEPENDENCIES — this split IS the RBAC:

  /v1/admin/*   require_admin   Seed providers, refresh the catalog, mint gateway
                                keys FOR users, inspect the system.
  /v1/me/*      current_user    A user's OWN provider keys and models. Every query
                                is scoped by user.id — a user cannot name someone
                                else's row, and could not use it if they did (the
                                composite FKs reject it).

Previously EVERY endpoint here used the same dependency as /v1/chat, so any key
holder was a full admin. The `_owner()` helper — which returned "the first user in
the table" regardless of who was calling — is gone.
"""

from __future__ import annotations

import csv
import io
import logging
from datetime import date
from typing import List, Optional

from fastapi import (
    APIRouter, BackgroundTasks, Depends, File, HTTPException, Response, UploadFile,
)
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.gateway_auth import current_user, require_admin
from app.core.database import get_db
from app.core import llm_router
from app.models.provider import Provider
from app.models.provider_key import ProviderKey
from app.models.provider_model import ProviderModel
from app.models.router_config import RouterConfig
from app.models.user import User
from app.models.views import v_my_models
from app.services import catalog, gateway_keys, key_store, presets, prober

logger = logging.getLogger("gateway.admin")

router = APIRouter()


# ═══════════════════════════════════════════════════════════════════════════
#  ADMIN — providers (the global catalog)
# ═══════════════════════════════════════════════════════════════════════════

@router.get("/v1/admin/provider-presets")
async def provider_presets(_: User = Depends(require_admin)):
    """
    The known providers offered in the "Add a provider" dropdown.

    Picking one means the admin only pastes an API key — the base_url and the
    LiteLLM prefix come from here. Anything else is added as CUSTOM, with the
    base_url supplied by hand.
    """
    return {"presets": presets.PRESETS}


class CreateProviderRequest(BaseModel):
    """
    Two shapes, one endpoint:

      PRESET  {"slug": "groq"}
              name + base_url are filled in from the preset.

      CUSTOM  {"slug": "my-llm", "name": "My LLM", "base_url": "https://…/v1"}
              base_url is REQUIRED — we have nowhere else to get it.

    DELIBERATELY NO api_key FIELD. Keys are added on the Providers & keys page,
    like everyone else's — an admin registering a destination and an admin
    spending quota are different acts. Model auto-discovery still happens: the
    FIRST key added for a provider with an empty catalog triggers discovery
    (see POST /v1/me/provider-keys), then fan-out, then the background probe.
    """

    slug: str = Field(..., description="Short id; doubles as the LiteLLM prefix, e.g. 'groq'")
    name: Optional[str] = Field(None, description="Display name (defaults to the preset's)")
    base_url: Optional[str] = Field(None, description="Required for custom providers")
    docs_url: Optional[str] = None


@router.post("/v1/admin/providers", status_code=201)
async def create_provider(
    payload: CreateProviderRequest,
    _: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """
    Register a provider — a DESTINATION only; it carries no key and no models yet.

    The catalog fills in when the first key for it is added under Providers &
    keys: that key is what discovery calls /v1/models with. Until then the
    provider shows 0 models, which is accurate, not broken.
    """
    slug = payload.slug.strip().lower()
    if db.query(Provider).filter(Provider.slug == slug).first():
        raise HTTPException(status_code=409, detail=f"Provider {slug!r} already exists.")

    preset = presets.get(slug)
    name = (payload.name or (preset["name"] if preset else "")).strip()
    base_url = payload.base_url or (preset["base_url"] if preset else None)
    docs_url = payload.docs_url or (preset["docs_url"] if preset else None)

    if not name:
        raise HTTPException(status_code=400, detail="A display name is required.")
    if not base_url:
        # A custom provider with no base_url is unusable: we would have nowhere
        # to send the request. Fail here, not at the first chat completion.
        raise HTTPException(
            status_code=400,
            detail=f"{slug!r} is not a known provider, so base_url is required.",
        )

    prov = Provider(slug=slug, name=name, base_url=base_url, docs_url=docs_url)
    db.add(prov)
    db.commit()
    db.refresh(prov)

    return {
        "id": prov.id,
        "slug": prov.slug,
        "name": prov.name,
        "base_url": prov.base_url,
        "is_preset": preset is not None,
        "status": "created",
        "detail": (
            f"{name} registered. Add your API key under Providers & keys — the "
            "first key added will auto-discover its models."
        ),
    }


class ToggleProviderRequest(BaseModel):
    enabled: bool


@router.patch("/v1/admin/providers/{slug}")
async def toggle_provider(
    slug: str,
    payload: ToggleProviderRequest,
    _: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """
    Enable / disable a provider WITHOUT deleting anything.

    Disabling is the reversible sibling of delete: keys, models and deployments
    all stay in the database, but the provider

      • disappears from GET /v1/providers (users can't attach new keys to it), and
      • drops out of v_live_deployments (nothing routes to it — enforced in the
        VIEW, not in app code, so no query can forget the filter).

    Re-enabling brings everything back exactly as it was. Use this for a provider
    outage or a policy pause; use DELETE only when it's gone for good.
    """
    prov = db.query(Provider).filter(Provider.slug == slug.lower()).first()
    if not prov:
        raise HTTPException(status_code=404, detail=f"No provider {slug!r}.")

    prov.enabled = payload.enabled
    db.commit()
    llm_router.invalidate()   # every user's callable set may have changed

    return {"slug": prov.slug, "enabled": prov.enabled}


@router.get("/v1/admin/providers")
async def list_providers(_: User = Depends(require_admin), db: Session = Depends(get_db)):
    """Every provider, with its catalog size — the admin providers table."""
    rows = (
        db.query(Provider, func.count(ProviderModel.id))
        .outerjoin(ProviderModel, ProviderModel.provider_id == Provider.id)
        .group_by(Provider.id)
        .order_by(Provider.slug)
        .all()
    )
    return {
        "providers": [
            {"id": p.id, "slug": p.slug, "name": p.name,
             "base_url": p.base_url, "enabled": p.enabled, "model_count": n}
            for p, n in rows
        ]
    }


@router.delete("/v1/admin/providers/{slug}")
async def delete_provider(
    slug: str,
    _: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """
    Remove a provider — a DESTRUCTIVE, CROSS-USER action.

    The schema cascades from providers.id, so deleting one also deletes:
      • its whole model catalog (provider_models),
      • EVERY user's keys for it (provider_keys), and
      • their deployments.
    Request-log history survives (its provider FK is ON DELETE SET NULL), so past
    usage analytics stay intact.

    Because it wipes other people's keys, the response reports the counts, and the
    UI must confirm before calling this. There is no per-user scoping here — an
    admin deleting a provider deletes it for everyone, by definition.
    """
    prov = db.query(Provider).filter(Provider.slug == slug.lower()).first()
    if not prov:
        raise HTTPException(status_code=404, detail=f"No provider {slug!r}.")

    # Count what will cascade away, so the caller knows the blast radius.
    models = db.query(ProviderModel).filter(ProviderModel.provider_id == prov.id).count()
    keys = db.query(ProviderKey).filter(ProviderKey.provider_id == prov.id).count()

    db.delete(prov)   # provider_models, provider_keys, deployments cascade
    db.commit()
    llm_router.invalidate()   # everyone's callable set may have shrunk

    return {
        "status": "deleted",
        "slug": slug,
        "models_removed": models,
        "provider_keys_removed": keys,
    }


@router.post("/v1/admin/providers/{slug}/discover")
async def discover_provider(
    slug: str,
    _: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """
    Ask the provider what models it serves and upsert them into the catalog.

    Needs at least one active key for that provider to exist (it borrows one to
    call /v1/models). is_common is NOT set here — the DB trigger does it.
    """
    prov = db.query(Provider).filter(Provider.slug == slug.lower()).first()
    if not prov:
        raise HTTPException(status_code=404, detail=f"No provider {slug!r}.")
    result = catalog.discover_provider(db, prov)
    llm_router.invalidate()          # the catalog changed for everyone
    return result


@router.get("/v1/admin/providers/{slug}/models")
async def list_provider_models(
    slug: str,
    _: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """
    The full catalog for ONE provider — every model, enabled or not.

    This is the admin's curation view, so unlike the user-facing lists it hides
    nothing: disabled rows are exactly the ones an admin may want to re-enable.
    """
    prov = db.query(Provider).filter(Provider.slug == slug.lower()).first()
    if not prov:
        raise HTTPException(status_code=404, detail=f"No provider {slug!r}.")
    rows = (
        db.query(ProviderModel)
        .filter(ProviderModel.provider_id == prov.id)
        .order_by(ProviderModel.upstream_model_id)
        .all()
    )
    return {
        "provider": prov.slug,
        "models": [
            {
                "id": m.id,
                "model": m.upstream_model_id,
                "mode": m.mode.value if hasattr(m.mode, "value") else m.mode,
                "publisher": m.publisher,
                "enabled": m.enabled,
            }
            for m in rows
        ],
    }


@router.patch("/v1/admin/models/{model_id}")
async def toggle_model(
    model_id: int,
    payload: ToggleProviderRequest,
    _: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """
    Enable / disable ONE catalog model, for everyone.

    Same semantics as disabling a provider, one row narrower: nothing is
    deleted, deployments stay, but v_live_deployments filters on pm.enabled so
    a disabled model drops out of every user's routing set immediately.
    Re-enabling restores it as it was.
    """
    row = db.query(ProviderModel).filter(ProviderModel.id == model_id).first()
    if not row:
        raise HTTPException(status_code=404, detail=f"No catalog model #{model_id}.")

    row.enabled = payload.enabled
    db.commit()
    llm_router.invalidate()   # every user's callable set may have changed

    return {"id": row.id, "model": row.upstream_model_id, "enabled": row.enabled}


@router.post("/v1/admin/discover")
async def discover_all(_: User = Depends(require_admin), db: Session = Depends(get_db)):
    """Refresh the catalog for every enabled provider."""
    results = catalog.discover_all(db)
    llm_router.invalidate()
    return {"results": results}


# ═══════════════════════════════════════════════════════════════════════════
#  ADMIN — users + gateway keys (how a user gets into the system)
# ═══════════════════════════════════════════════════════════════════════════

class CreateUserRequest(BaseModel):
    email: Optional[str] = Field(None, description="Optional label, not a credential")
    is_admin: bool = False


@router.post("/v1/admin/users")
async def create_user(
    payload: CreateUserRequest,
    _: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Create a user. There is no signup — an admin creates them, then mints a key."""
    user = User(email=payload.email, is_admin=payload.is_admin)
    db.add(user)
    db.commit()
    db.refresh(user)
    return {"id": user.id, "public_id": str(user.public_id),
            "email": user.email, "is_admin": user.is_admin}


@router.get("/v1/admin/users")
async def list_users(_: User = Depends(require_admin), db: Session = Depends(get_db)):
    return {
        "users": [
            {"id": u.id, "email": u.email, "is_admin": u.is_admin, "is_active": u.is_active}
            for u in db.query(User).order_by(User.id)
        ]
    }


class MintKeyRequest(BaseModel):
    user_id: int = Field(..., description="Who the key is FOR")
    name: str = Field(default="default", description="Human label")


@router.post("/v1/admin/gateway-keys")
async def mint_gateway_key(
    payload: MintKeyRequest,
    _: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """
    Mint a gateway key FOR a user, and hand it to them. The raw token is returned
    ONCE. (The old version ignored who it was for and always used the first user.)
    """
    if not db.query(User).filter(User.id == payload.user_id).first():
        raise HTTPException(status_code=404, detail=f"No user with id {payload.user_id}.")

    token, row = gateway_keys.mint(db, payload.user_id, name=payload.name)
    return {
        "id": row.id,
        "user_id": row.user_id,
        "name": row.name,
        "token": token,  # shown once
        "key_prefix": row.key_prefix,
        "warning": "Store this token now — it cannot be retrieved again.",
    }


class RotateKeyRequest(BaseModel):
    """Nothing required — sensible defaults rotate the caller's own admin key."""

    name: str = Field(default="admin", description="Label for the new key")
    revoke_existing: bool = Field(
        default=True,
        description="Revoke the caller's other gateway keys (true = a real rotation)",
    )


@router.post("/v1/admin/gateway-keys/rotate")
async def rotate_my_admin_key(
    payload: RotateKeyRequest = RotateKeyRequest(),
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """
    Issue a fresh gateway key for the CALLING admin, and (by default) revoke their
    old ones — a self-service rotation. The raw token is returned ONCE.

    Mint-then-revoke order matters: the new key is created first and spared from
    the revoke, so there is never a window where the admin has no working key.

    This touches only DB-issued keys. A configured MASTER_ADMIN_KEY lives in
    config, not the database, so it is NEVER revoked here — it remains the
    unloseable way back in even if a rotation goes wrong. (If you authenticated
    WITH the master key, this simply mints a DB key for the owner admin.)
    """
    token, row = gateway_keys.mint(db, admin.id, name=payload.name)
    revoked = (
        gateway_keys.revoke_user_keys(db, admin.id, except_id=row.id)
        if payload.revoke_existing else 0
    )
    return {
        "id": row.id,
        "user_id": row.user_id,
        "name": row.name,
        "token": token,                # shown once
        "key_prefix": row.key_prefix,
        "revoked_previous": revoked,
        "warning": "Store this token now — it cannot be retrieved again.",
    }


@router.get("/v1/admin/gateway-keys")
async def list_all_gateway_keys(
    _: User = Depends(require_admin), db: Session = Depends(get_db),
):
    """Every key in the system (prefixes only — never the raw token)."""
    return {
        "keys": [
            {"id": k.id, "user_id": k.user_id, "name": k.name, "key_prefix": k.key_prefix,
             "is_active": k.is_active,
             "last_used_at": k.last_used_at.isoformat() if k.last_used_at else None}
            for k in gateway_keys.list_keys(db)
        ]
    }


@router.delete("/v1/admin/gateway-keys/{key_id}")
async def revoke_any_gateway_key(
    key_id: int, _: User = Depends(require_admin), db: Session = Depends(get_db),
):
    if not gateway_keys.revoke(db, key_id):
        raise HTTPException(status_code=404, detail=f"No gateway key with id {key_id}.")
    return {"status": "revoked", "id": key_id}


@router.get("/v1/admin/users/{user_id}/keys")
async def user_keys(
    user_id: int, _: User = Depends(require_admin), db: Session = Depends(get_db),
):
    """
    One user's gateway keys AND provider keys — for the admin user table.

    Provider keys are returned MASKED, exactly as they are to their owner. An
    admin can see that a user has a Groq key and whether it works; they cannot
    read its value. Nothing in this system returns a decrypted secret over HTTP.
    """
    if not db.query(User).filter(User.id == user_id).first():
        raise HTTPException(status_code=404, detail=f"No user with id {user_id}.")

    return {
        "user_id": user_id,
        "gateway_keys": [
            {"id": k.id, "name": k.name, "key_prefix": k.key_prefix,
             "is_active": k.is_active,
             "last_used_at": k.last_used_at.isoformat() if k.last_used_at else None}
            for k in gateway_keys.list_keys(db, user_id=user_id)
        ],
        "provider_keys": key_store.list_keys(db, user_id=user_id),
    }


# ═══════════════════════════════════════════════════════════════════════════
#  USER — my provider keys  (this is "user adds his own API key")
# ═══════════════════════════════════════════════════════════════════════════

class AddProviderKeyRequest(BaseModel):
    provider: str = Field(..., description="Provider slug, e.g. 'groq'")
    value: str = Field(..., description="The API key. Encrypted at rest; never returned.")
    label: Optional[str] = Field(None, description="e.g. 'Groq account #1'")


@router.post("/v1/me/provider-keys", status_code=201)
async def add_my_provider_key(
    payload: AddProviderKeyRequest,
    background: BackgroundTasks,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    """
    Add one of MY keys for an existing provider.

    Returns immediately. The key is fanned out into one deployment per model that
    provider serves, all marked 'unavailable', and a BACKGROUND probe promotes the
    ones that work. Probing ~30 models inline would both hang the request and
    rate-limit the key we're validating.
    """
    prov = db.query(Provider).filter(Provider.slug == payload.provider.strip().lower()).first()
    if not prov:
        raise HTTPException(
            status_code=404,
            detail=f"No provider {payload.provider!r}. An admin must add it first.",
        )

    try:
        row = key_store.add_key(
            db, user_id=user.id, provider_id=prov.id,
            value=payload.value, label=payload.label,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    # If the catalog for this provider is empty (an admin added it without a key,
    # so nothing could be discovered yet), this key is the first one that CAN read
    # it. Discover now, then fan out — otherwise the user would get a key with
    # zero models and no way to understand why.
    has_models = (
        db.query(ProviderModel).filter(ProviderModel.provider_id == prov.id).count() > 0
    )
    discovered = 0
    if not has_models:
        result = catalog.discover_provider(db, prov)
        discovered = result.get("added", 0)
        if discovered:
            key_store.fan_out(db, row)   # idempotent; the catalog now exists

    background.add_task(_probe_and_refresh, row.id, user.id)

    return {
        "id": row.id,
        "provider": prov.slug,
        "label": row.label,
        "masked": row.key_masked,
        "models_discovered": discovered,
        "status": "probing",
        "detail": "Models are being tested in the background; poll GET /v1/me/models.",
    }


async def _probe_and_refresh(provider_key_id: int, user_id: int) -> None:
    """Background: probe the new key's deployments, then rebuild that user's Router."""
    await prober.probe_key(provider_key_id)
    llm_router.invalidate(user_id)


@router.get("/v1/me/provider-keys")
async def list_my_provider_keys(
    user: User = Depends(current_user), db: Session = Depends(get_db),
):
    """MY keys, masked. Scoped to me — I cannot see anyone else's."""
    return {"keys": key_store.list_keys(db, user_id=user.id)}


@router.get("/v1/me/provider-keys/export")
async def export_my_provider_keys(
    user: User = Depends(current_user), db: Session = Depends(get_db),
):
    """
    Download MY provider keys — DECRYPTED — as a CSV backup.

    This is the one deliberate exception to "the API never returns the secret":
    a user restoring after a lost database needs the original keys back, and they
    already own them. Scoped to the caller's own keys, served with no-store so
    no proxy or browser cache ever holds the plaintext.
    """
    rows = key_store.export_keys(db, user_id=user.id)
    if not rows:
        raise HTTPException(status_code=404, detail="You have no provider keys to export.")

    buf = io.StringIO()
    writer = csv.DictWriter(
        buf, fieldnames=["provider", "provider_name", "label", "api_key"]
    )
    writer.writeheader()
    writer.writerows(rows)

    filename = f"provider-keys-backup-{date.today().isoformat()}.csv"
    return Response(
        content=buf.getvalue(),
        media_type="text/csv",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Cache-Control": "no-store",
        },
    )


@router.post("/v1/me/provider-keys/import")
async def import_my_provider_keys(
    background: BackgroundTasks,
    file: UploadFile = File(...),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    """
    Restore MY provider keys from a CSV — the exact file /export produces.

    Columns: provider, provider_name, label, api_key (provider_name is ignored;
    the slug is the identity). Rows upsert by (me, provider, label) — re-importing
    a backup is idempotent, and an updated key value replaces the stored one.
    Each imported key fans out into deployments and is probed in the background,
    exactly as if it had been added by hand.

    Rows for providers an admin has not registered are SKIPPED and reported,
    not failed: a user restore must not be able to create catalog entries.
    """
    raw = await file.read()
    # Our export is UTF-8, but backups round-trip through Windows tooling that
    # re-saves CSVs as UTF-16 (PowerShell, Excel). Extra columns are fine —
    # DictReader ignores them — so tolerate both encodings rather than reject.
    try:
        try:
            text = raw.decode("utf-8-sig")
        except UnicodeDecodeError:
            text = raw.decode("utf-16")
        if "\x00" in text:               # utf-16 decoded as utf-8 garbage
            text = raw.decode("utf-16")
        reader = csv.DictReader(io.StringIO(text))
        rows = list(reader)
    except Exception:
        raise HTTPException(status_code=400, detail="Not a readable CSV file.")

    if not rows or not {"provider", "api_key"} <= set(reader.fieldnames or []):
        raise HTTPException(
            status_code=400,
            detail="Expected the backup format: provider, provider_name, label, api_key.",
        )

    providers = {p.slug: p for p in db.query(Provider).all()}
    imported: List[str] = []
    skipped: List[str] = []

    for i, r in enumerate(rows, start=2):   # header = line 1
        slug = (r.get("provider") or "").strip().lower()
        value = (r.get("api_key") or "").strip()
        label = (r.get("label") or "").strip() or None

        prov = providers.get(slug)
        if not prov:
            skipped.append(f"line {i}: provider {slug or '?'!r} is not registered")
            continue
        if not prov.enabled:
            skipped.append(f"line {i}: provider {slug!r} is disabled")
            continue
        try:
            row = key_store.add_key(
                db, user_id=user.id, provider_id=prov.id, value=value, label=label,
            )
        except ValueError as exc:
            skipped.append(f"line {i}: {exc}")
            continue

        # First key for an empty catalog is the first that CAN read it —
        # same rescue as the manual add path.
        if not db.query(ProviderModel).filter(
            ProviderModel.provider_id == prov.id
        ).count():
            if catalog.discover_provider(db, prov).get("added", 0):
                key_store.fan_out(db, row)

        background.add_task(_probe_and_refresh, row.id, user.id)
        imported.append(f"{slug} · {row.label}")

    llm_router.invalidate(user.id)
    return {
        "status": "probing" if imported else "nothing_imported",
        "imported": len(imported),
        "keys": imported,
        "skipped": skipped,
        "detail": (
            f"Imported {len(imported)} key(s)"
            + (f", skipped {len(skipped)}" if skipped else "")
            + ". Models are being tested in the background."
        ),
    }


@router.delete("/v1/me/provider-keys/{key_id}")
async def delete_my_provider_key(
    key_id: int, user: User = Depends(current_user), db: Session = Depends(get_db),
):
    """Delete one of MY keys. Its deployments cascade away."""
    if not key_store.delete_key(db, key_id, user_id=user.id):
        raise HTTPException(status_code=404, detail=f"You have no provider key with id {key_id}.")
    llm_router.invalidate(user.id)
    return {"status": "deleted", "id": key_id}


@router.post("/v1/me/provider-keys/{key_id}/probe")
async def reprobe_my_key(
    key_id: int,
    background: BackgroundTasks,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    """
    Re-test ONE key — the narrowest probe there is.

    The surgical sibling of /providers/{slug}/probe: one provider, one key,
    nobody else's deployments touched. Use it when a single key looks stale
    ("did topping up my second Groq key revive it?") without burning probe
    quota on keys that were fine. No catalog refresh — that's /discover.
    """
    key = (
        db.query(ProviderKey)
        .filter(ProviderKey.id == key_id, ProviderKey.user_id == user.id)
        .first()
    )
    if not key:
        raise HTTPException(
            status_code=404, detail=f"You have no provider key with id {key_id}."
        )
    if not key.is_active:
        raise HTTPException(status_code=400, detail="That key is inactive — nothing to test.")

    background.add_task(_probe_and_refresh, key.id, user.id)
    return {
        "status": "probing",
        "key_id": key.id,
        "detail": "Re-testing this key's models in the background. Poll GET /v1/me/models for results.",
    }


# ═══════════════════════════════════════════════════════════════════════════
#  USER — my models
# ═══════════════════════════════════════════════════════════════════════════

@router.get("/v1/me/models")
async def my_models(user: User = Depends(current_user), db: Session = Depends(get_db)):
    """
    The models I can actually call, from v_my_models.

    is_common           catalog badge: 2+ providers serve it SOMEWHERE. Not routable.
    has_backup_key      I have 2+ live deployments — if one fails there IS another.
                        Two keys at the SAME provider counts; that is real redundancy.
    has_backup_provider Stronger: 2+ live PROVIDERS — survives a provider outage.
    """
    rows = db.execute(
        select(v_my_models).where(v_my_models.c.user_id == user.id)
        .order_by(v_my_models.c.model)
    ).mappings().all()

    return {
        "models": [
            {
                "model": r["model"],
                "mode": r["mode"],
                "publisher": r["publisher"],
                "providers": r["providers"],
                "is_common": r["is_common"],
                "is_usable": r["is_usable"],
                "has_backup_key": r["has_backup_key"],
                "has_backup_provider": r["has_backup_provider"],
                "live_keys": r["live_keys"],
                "total_keys": r["total_keys"],
                # Why the dead ones are dead: distinct deployment probe outcomes
                # ('rate_limited', 'auth_error', …). The UI turns these into a
                # reason badge instead of a flat "Unavailable".
                "statuses": r["statuses"],
                "last_checked_at": (
                    r["last_checked_at"].isoformat() if r["last_checked_at"] else None
                ),
            }
            for r in rows
        ]
    }


@router.post("/v1/me/providers/{slug}/discover")
async def discover_provider_as_user(
    slug: str,
    background: BackgroundTasks,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    """
    Refresh one provider's model catalog, USING MY OWN KEY, then re-fan-out and
    re-probe all MY keys for it.

    This is the user-facing sibling of the admin Sync. Three things distinguish it:

      • it spends the CALLER's quota — discovery calls /models with their key,
        never someone else's (the admin path borrows whichever key is around);
      • it requires the caller to actually hold an active key for the provider
        (without one there is nothing to discover with, and nothing of theirs
        to update);
      • it finishes by re-probing all of the caller's keys for the provider —
        so it doubles as "something looks stale for me, re-check everything",
        e.g. a key showing 0 working models after a flaky probe run.

    The catalog upsert itself is global — model lists are facts about the
    provider, identical no matter whose key read them.
    """
    prov = db.query(Provider).filter(Provider.slug == slug.lower()).first()
    if not prov:
        raise HTTPException(status_code=404, detail=f"No provider {slug!r}.")
    if not prov.enabled:
        raise HTTPException(status_code=400, detail=f"{prov.name} is currently disabled.")

    my_keys = (
        db.query(ProviderKey)
        .filter(
            ProviderKey.user_id == user.id,
            ProviderKey.provider_id == prov.id,
            ProviderKey.is_active.is_(True),
        )
        .all()
    )
    if not my_keys:
        raise HTTPException(
            status_code=400,
            detail=f"You have no active {prov.name} key — add one first; discovery needs it.",
        )

    result = catalog.discover_provider(
        db, prov, api_key=key_store.reveal(db, my_keys[0].id),
    )
    if result.get("error"):
        return {
            "status": "discovery_failed",
            "detail": f"Listing models failed: {result['error']}. Check that your key is valid.",
            **result,
        }

    # New catalog rows → new deployments for EVERY one of my keys (idempotent),
    # then re-probe them all in the background.
    fanned = sum(key_store.fan_out(db, k) for k in my_keys)
    for k in my_keys:
        background.add_task(_probe_and_refresh, k.id, user.id)

    return {
        "status": "probing",
        "provider": prov.slug,
        "added": result.get("added", 0),
        "updated": result.get("updated", 0),
        "disabled": result.get("disabled", 0),
        "new_deployments": fanned,
        "detail": (
            f"Catalog refreshed ({result.get('added', 0)} new). Re-testing your "
            f"{len(my_keys)} key(s) in the background."
        ),
    }


@router.post("/v1/me/probe")
async def reprobe_my_models(
    background: BackgroundTasks, user: User = Depends(current_user),
):
    """Re-test all MY deployments in the background."""
    background.add_task(_probe_user_and_refresh, user.id)
    return {"status": "probing", "detail": "Poll GET /v1/me/models for results."}


@router.post("/v1/me/providers/{slug}/probe")
async def reprobe_my_provider(
    slug: str,
    background: BackgroundTasks,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    """
    Re-test all MY keys at ONE provider — probe only, no catalog refresh.

    The lighter sibling of /discover: it spends no quota on /models and touches
    no other provider. Use it after fixing a key's permissions ("did that revive
    HuggingFace?") without re-firing hundreds of probes at providers that were
    fine.
    """
    prov = db.query(Provider).filter(Provider.slug == slug.lower()).first()
    if not prov:
        raise HTTPException(status_code=404, detail=f"No provider {slug!r}.")

    my_keys = (
        db.query(ProviderKey)
        .filter(
            ProviderKey.user_id == user.id,
            ProviderKey.provider_id == prov.id,
            ProviderKey.is_active.is_(True),
        )
        .all()
    )
    if not my_keys:
        raise HTTPException(
            status_code=400,
            detail=f"You have no active {prov.name} key — nothing to test.",
        )

    for k in my_keys:
        background.add_task(_probe_and_refresh, k.id, user.id)

    return {
        "status": "probing",
        "provider": prov.slug,
        "keys": len(my_keys),
        "detail": (
            f"Re-testing your {len(my_keys)} {prov.name} key(s) in the "
            "background. Poll GET /v1/me/models for results."
        ),
    }


async def _probe_user_and_refresh(user_id: int) -> None:
    await prober.probe_user(user_id)
    llm_router.invalidate(user_id)


# ═══════════════════════════════════════════════════════════════════════════
#  USER — my LiteLLM router configuration
# ═══════════════════════════════════════════════════════════════════════════
#  These are the knobs on the litellm.Router built for THIS user. They control
#  how it BEHAVES, not what it can call — the model list comes from deployments
#  and is not editable here (add or remove a provider key to change it).
#
#  A change takes effect on the next request: the PUT invalidates this user's
#  cached Router, which is then rebuilt with the new settings.

_STRATEGIES = {
    "simple-shuffle",
    "least-busy",
    "usage-based-routing",
    "usage-based-routing-v2",
    "latency-based-routing",
}

_DEFAULTS = {
    "routing_strategy": "usage-based-routing-v2",
    "num_retries": 4,
    "cooldown_time": 30,
    "allowed_fails": 3,
    "pinned_provider_id": None,
}


class RouterConfigRequest(BaseModel):
    """All fields optional — send only what you want to change."""

    routing_strategy: Optional[str] = Field(
        None, description=f"One of: {', '.join(sorted(_STRATEGIES))}"
    )
    num_retries: Optional[int] = Field(
        None, ge=0, le=10, description="Retries across deployments before giving up"
    )
    cooldown_time: Optional[int] = Field(
        None, ge=0, le=3600, description="Seconds a deployment is benched after failing"
    )
    allowed_fails: Optional[int] = Field(
        None, ge=1, le=100, description="Failures before a deployment is benched"
    )
    # Partial updates use exclude_none, so None cannot mean "clear the pin";
    # send 0 to unpin. (A provider id can never be 0 — BIGINT IDENTITY starts at 1.)
    pinned_provider_id: Optional[int] = Field(
        None, ge=0,
        description="Prefer this provider where it serves a model. 0 clears the pin.",
    )


def _config_payload(rc: Optional[RouterConfig]) -> dict:
    if not rc:
        return {**_DEFAULTS, "is_default": True}
    return {
        "routing_strategy": rc.routing_strategy,
        "num_retries": rc.num_retries,
        "cooldown_time": rc.cooldown_time,
        "allowed_fails": rc.allowed_fails,
        "pinned_provider_id": rc.pinned_provider_id,
        "is_default": False,
    }


@router.get("/v1/me/router-config")
async def get_my_router_config(
    user: User = Depends(current_user), db: Session = Depends(get_db),
):
    """
    MY router settings. If I've never set any, this returns the defaults with
    is_default=true — the row is created lazily on first PUT.
    """
    rc = db.query(RouterConfig).filter(RouterConfig.user_id == user.id).first()
    return _config_payload(rc)


@router.put("/v1/me/router-config")
async def update_my_router_config(
    payload: RouterConfigRequest,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    """
    Update MY router settings. Partial: omitted fields keep their current value.

    routing_strategy is validated against the strategies litellm actually
    implements — an unknown one is rejected here rather than blowing up inside
    Router() on the user's next chat request, which would be a 502 with a
    baffling message.
    """
    if payload.routing_strategy and payload.routing_strategy not in _STRATEGIES:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown routing_strategy. Valid: {sorted(_STRATEGIES)}",
        )

    # Validate the pin HERE, not inside Router construction on the next chat
    # request. 0 means "clear the pin" (None can't — partial update semantics).
    if payload.pinned_provider_id:
        pinned = (
            db.query(Provider)
            .filter(Provider.id == payload.pinned_provider_id, Provider.enabled.is_(True))
            .first()
        )
        if not pinned:
            raise HTTPException(
                status_code=400,
                detail="pinned_provider_id does not match an enabled provider.",
            )

    rc = db.query(RouterConfig).filter(RouterConfig.user_id == user.id).first()
    if not rc:
        rc = RouterConfig(user_id=user.id, **_DEFAULTS)
        db.add(rc)

    for field, value in payload.model_dump(exclude_none=True).items():
        if field == "pinned_provider_id" and value == 0:
            value = None
        setattr(rc, field, value)

    db.commit()
    db.refresh(rc)

    # Rebuild on next request, so the change is live immediately rather than
    # after the Router's TTL happens to lapse.
    llm_router.invalidate(user.id)

    return {**_config_payload(rc), "status": "updated"}


@router.delete("/v1/me/router-config")
async def reset_my_router_config(
    user: User = Depends(current_user), db: Session = Depends(get_db),
):
    """Reset MY router settings back to the defaults."""
    rc = db.query(RouterConfig).filter(RouterConfig.user_id == user.id).first()
    if rc:
        db.delete(rc)
        db.commit()
        llm_router.invalidate(user.id)
    return {**_DEFAULTS, "is_default": True, "status": "reset"}
