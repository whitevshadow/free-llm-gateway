"""
Key Store — manage encrypted provider API keys and bridge them into the runtime.

FLOW:
  add_key()  ──encrypt──►  provider_keys table (ciphertext + masked preview)
  apply_persisted_keys()  ──decrypt──►  os.environ[<env_slot>] = value
                                              │
                                              ▼
                             litellm.Router rebuilt  ──►  deployments activate

A key persisted here behaves exactly like one set in .env: the pool references
`os.environ/<VAR>`, so once the value is in the environment and the Router is
rebuilt, every deployment using that slot joins the live pool.
"""

import os
import logging
from typing import List, Optional

from sqlalchemy.orm import Session

from app.core.database import SessionLocal
from app.models.provider_api_key import ProviderApiKey
from app.models.provider import Provider
from app.services import crypto

logger = logging.getLogger("gateway.keystore")


def mask_value(value: Optional[str]) -> Optional[str]:
    """Return a masked preview of a secret — never the full value."""
    if not value:
        return None
    return crypto.mask(value)


def apply_persisted_keys() -> int:
    """
    Decrypt every active provider key and inject it into os.environ by its
    env_slot. Called once at startup BEFORE the Router is built. An explicit
    environment / .env value WINS over the stored copy (so you can override or
    rotate a key without the DB clobbering it). Returns the number applied.
    """
    db = SessionLocal()
    try:
        rows = db.query(ProviderApiKey).filter(ProviderApiKey.is_active.is_(True)).all()
        applied = 0
        for row in rows:
            if os.environ.get(row.env_slot):
                continue
            try:
                os.environ[row.env_slot] = crypto.decrypt(row.key_ciphertext)
                applied += 1
            except Exception as exc:
                logger.error("Could not decrypt provider key %s: %s", row.env_slot, exc)
        if applied:
            logger.info("Applied %d persisted provider key(s) to the environment.", applied)
        return applied
    finally:
        db.close()


def add_key(
    db: Session,
    *,
    provider_id: int,
    env_slot: str,
    value: str,
    label: Optional[str] = None,
    user_id: int,
) -> ProviderApiKey:
    """
    Upsert an encrypted provider key by env_slot, inject it into the environment,
    and rebuild the Router so it takes effect immediately.
    """
    value = value.strip()
    ciphertext = crypto.encrypt(value)
    masked = crypto.mask(value)

    row = db.query(ProviderApiKey).filter(ProviderApiKey.env_slot == env_slot).first()
    if row:
        row.key_ciphertext = ciphertext
        row.key_masked = masked
        row.provider_id = provider_id
        row.is_active = True
        if label:
            row.label = label
    else:
        row = ProviderApiKey(
            user_id=user_id,
            provider_id=provider_id,
            label=label or env_slot,
            env_slot=env_slot,
            key_ciphertext=ciphertext,
            key_masked=masked,
        )
        db.add(row)
    db.commit()
    db.refresh(row)

    os.environ[env_slot] = value
    _reload_router()
    logger.info("Saved provider key %s and reloaded the Router.", env_slot)
    return row


def list_keys(db: Session) -> List[dict]:
    """All persisted provider keys with masked values (never the secret)."""
    rows = (
        db.query(ProviderApiKey, Provider)
        .outerjoin(Provider, ProviderApiKey.provider_id == Provider.id)
        .order_by(ProviderApiKey.env_slot)
        .all()
    )
    return [
        {
            "id": pk.id,
            "provider": prov.slug if prov else None,
            "env_slot": pk.env_slot,
            "label": pk.label,
            "masked": pk.key_masked,
            "is_active": pk.is_active,
            "is_in_env": bool(os.environ.get(pk.env_slot)),
        }
        for pk, prov in rows
    ]


def delete_key(db: Session, key_id: int) -> bool:
    """Delete a provider key, unset it from the environment, and rebuild the Router."""
    row = db.query(ProviderApiKey).filter(ProviderApiKey.id == key_id).first()
    if not row:
        return False
    env_slot = row.env_slot
    db.delete(row)
    db.commit()
    os.environ.pop(env_slot, None)
    _reload_router()
    logger.info("Deleted provider key %s and reloaded the Router.", env_slot)
    return True


def _reload_router() -> None:
    """Rebuild the Router; tolerate failures so key ops never 500 on router issues."""
    try:
        from app.core.llm_router import reload_router
        reload_router()
    except Exception as exc:  # pragma: no cover
        logger.warning("Router reload after key change failed: %s", exc)
