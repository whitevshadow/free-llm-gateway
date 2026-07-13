"""
Discovery (Phase 3) — populate `providers` and `provider_models` from the pool.

Enumerates every deployment the Router knows about (YAML pool + NVIDIA/OpenRouter
auto-discovery) via llm_router.all_probe_targets(), then:
  • ensures a `providers` row per provider slug,
  • imports each referenced env key into the encrypted `provider_keys` table
    (so .env keys become first-class DB rows the deployments can reference),
  • upserts a `provider_models` row per concrete model.

Idempotent: re-running reconciles rows in place.
"""

import logging
from typing import Dict

from sqlalchemy.orm import Session

from app.core.database import SessionLocal
from app.models.user import User
from app.models.provider import Provider
from app.models.provider_api_key import ProviderApiKey
from app.models.provider_model import ProviderModel
from app.models.enums import ModelMode
from app.services import crypto
from app.services.normalize import provider_slug, upstream_id, normalize_model_name

logger = logging.getLogger("gateway.catalog")


def _owner_id(db: Session) -> int:
    user = db.query(User).first()
    if not user:
        user = User(email="owner@localhost", hashed_password="disabled", full_name="Owner")
        db.add(user)
        db.commit()
        db.refresh(user)
    return user.id


def ensure_provider(db: Session, slug: str, cache: Dict[str, Provider]) -> Provider:
    if slug in cache:
        return cache[slug]
    prov = db.query(Provider).filter(Provider.slug == slug).first()
    if not prov:
        prov = Provider(slug=slug, name=slug, litellm_prefix=slug,
                        requires_key=(slug != "ollama"))
        db.add(prov)
        db.commit()
        db.refresh(prov)
    cache[slug] = prov
    return prov


def _import_key(db: Session, provider_id: int, owner_id: int, env_slot: str, value: str) -> None:
    """Upsert an encrypted provider_keys row for an env-sourced key."""
    row = db.query(ProviderApiKey).filter(ProviderApiKey.env_slot == env_slot).first()
    if row:
        return  # already managed (admin-added or previously imported)
    db.add(ProviderApiKey(
        user_id=owner_id, provider_id=provider_id, label=env_slot, env_slot=env_slot,
        key_ciphertext=crypto.encrypt(value), key_masked=crypto.mask(value),
    ))
    db.commit()


def _upsert_model(db: Session, provider_id: int, litellm_model: str, mode: str, seen: set) -> None:
    up = upstream_id(litellm_model)
    keypair = (provider_id, up)
    if keypair in seen:
        return  # same (provider, model) already handled this run (multiple keys / aliases)
    seen.add(keypair)

    row = (
        db.query(ProviderModel)
        .filter(ProviderModel.provider_id == provider_id, ProviderModel.upstream_model_id == up)
        .first()
    )
    try:
        mode_enum = ModelMode(mode)
    except ValueError:
        mode_enum = ModelMode.chat
    if row:
        row.litellm_model = litellm_model
        row.normalized_name = normalize_model_name(litellm_model)
        row.mode = mode_enum
        row.enabled = True
    else:
        db.add(ProviderModel(
            provider_id=provider_id,
            upstream_model_id=up,
            litellm_model=litellm_model,
            normalized_name=normalize_model_name(litellm_model),
            mode=mode_enum,
        ))
    db.flush()  # make visible to later queries (session has autoflush disabled)


def discover(targets=None) -> dict:
    """
    Run discovery. `targets` defaults to llm_router.all_probe_targets(); pass a
    list to test without touching the network.
    """
    if targets is None:
        from app.core.llm_router import all_probe_targets
        targets = all_probe_targets()

    db = SessionLocal()
    try:
        owner_id = _owner_id(db)
        pcache: Dict[str, Provider] = {}
        seen_models: set = set()
        keys_imported = 0
        for t in targets:
            model = t.get("model", "")
            if not model:
                continue
            slug = t.get("provider") or provider_slug(model)
            prov = ensure_provider(db, slug, pcache)

            env_slot = t.get("api_key_var")
            value = t.get("api_key")
            if env_slot and value:
                before = db.query(ProviderApiKey).filter(ProviderApiKey.env_slot == env_slot).count()
                _import_key(db, prov.id, owner_id, env_slot, value)
                if not before:
                    keys_imported += 1

            _upsert_model(db, prov.id, model, t.get("mode", "chat"), seen_models)
        db.commit()

        n_prov = db.query(Provider).count()
        n_models = db.query(ProviderModel).count()
        logger.info("Discovery: %d providers, %d provider_models, %d key(s) imported.",
                    n_prov, n_models, keys_imported)
        return {"providers": n_prov, "provider_models": n_models, "keys_imported": keys_imported}
    finally:
        db.close()
