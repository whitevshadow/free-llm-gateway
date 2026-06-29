"""
Key Store — bridges UI-configured API keys into the running gateway.

FLOW:
  Settings UI  ──►  ProviderKey table (persisted)
                       │
                       ▼
              os.environ[<VAR>] = value        (apply_persisted_keys / set_keys)
                       │
                       ▼
        litellm.Router rebuilt (reload_router)  ──►  deployments activate

A key saved here behaves exactly like one set in .env: the model pool references
`os.environ/<VAR>`, so once the value is in the environment and the Router is
rebuilt, every deployment using that slot joins the live pool.
"""

import os
import logging
from typing import Dict, List, Optional

from sqlalchemy.orm import Session

from app.core.database import SessionLocal
from app.core.llm_router import list_key_slots, reload_router
from app.models.provider_key import ProviderKey

logger = logging.getLogger("gateway.keystore")


def mask_value(value: Optional[str]) -> Optional[str]:
    """Return a masked preview of a secret — never the full value."""
    if not value:
        return None
    if len(value) <= 4:
        return "••••"
    return "••••" + value[-4:]


def apply_persisted_keys() -> int:
    """
    Load all persisted keys into os.environ. Called once at startup BEFORE the
    Router is built. Returns the number of keys applied.
    """
    db = SessionLocal()
    try:
        rows = db.query(ProviderKey).all()
        applied = 0
        for row in rows:
            if not row.value:
                continue
            # An explicit environment / .env value WINS over the stored copy. This
            # lets you fix or rotate a key in .env without the (possibly stale)
            # DB value clobbering it at startup.
            if os.environ.get(row.env_var):
                continue
            os.environ[row.env_var] = row.value
            applied += 1
        if applied:
            logger.info("Applied %d persisted API key(s) to the environment.", applied)
        return applied
    finally:
        db.close()


def _provider_for_env_var(env_var: str) -> Optional[str]:
    for slot in list_key_slots():
        if slot["env_var"] == env_var:
            return slot["provider"]
    return None


def set_keys(keys: Dict[str, str]) -> int:
    """
    Upsert one or more keys (env_var -> value), inject into the environment, and
    rebuild the Router so the changes take effect immediately. Empty/blank values
    are ignored (use delete_key to remove). Returns the number of keys written.
    """
    written = 0
    db: Session = SessionLocal()
    try:
        for env_var, value in keys.items():
            if not value or not value.strip():
                continue
            value = value.strip()
            row = db.query(ProviderKey).filter(ProviderKey.env_var == env_var).first()
            if row:
                row.value = value
            else:
                row = ProviderKey(
                    env_var=env_var,
                    value=value,
                    provider=_provider_for_env_var(env_var),
                )
                db.add(row)
            os.environ[env_var] = value
            written += 1
        db.commit()
    finally:
        db.close()

    if written:
        reload_router()
        logger.info("Saved %d key(s) and reloaded the Router.", written)
    return written


def delete_key(env_var: str) -> bool:
    """Remove a persisted key, unset it from the environment, and reload the Router."""
    db: Session = SessionLocal()
    try:
        row = db.query(ProviderKey).filter(ProviderKey.env_var == env_var).first()
        if not row:
            return False
        db.delete(row)
        db.commit()
    finally:
        db.close()

    os.environ.pop(env_var, None)
    reload_router()
    logger.info("Deleted key %s and reloaded the Router.", env_var)
    return True


def get_persisted_values() -> Dict[str, str]:
    """Return {env_var: value} for all persisted keys (used internally for masking/testing)."""
    db = SessionLocal()
    try:
        return {row.env_var: row.value for row in db.query(ProviderKey).all()}
    finally:
        db.close()
