"""
Key store — a user's own provider keys, and the fan-out that gives them models.

THE OLD DESIGN WAS BROKEN UNDER MULTI-USER, and it is worth saying why, because
the fix is the whole point of this module:

    apply_persisted_keys()  decrypted EVERY user's key into os.environ, keyed by
    an `env_slot` name like GROQ_API_KEY_1. But a process has exactly ONE
    os.environ. Two users with Groq keys collided in the same slot, so whichever
    key was written last served EVERYONE's traffic — silently billing one user's
    requests to another user's quota.

Keys now never touch os.environ. They are decrypted on demand and passed straight
into litellm as `api_key=...` on the specific deployment being called. There is no
shared namespace to collide in. `env_slot` is gone from the schema entirely.

THE FAN-OUT — how a user acquires callable models:

    add_key(alice, groq, "sk-...")
      → 1 provider_keys row
      → N deployments rows, one per model Groq serves (from the catalog)
      → each starts 'unavailable' and is probed in the BACKGROUND

The user's model list IS those deployments. They cannot see a model they hold no
key for, because the row only exists through their key (FK-enforced).
"""

import logging
from typing import List, Optional

from sqlalchemy.orm import Session

from app.models.deployment import Deployment
from app.models.enums import ModelHealth
from app.models.provider import Provider
from app.models.provider_key import ProviderKey
from app.models.provider_model import ProviderModel
from app.services import crypto

logger = logging.getLogger("gateway.keystore")


def reveal(db: Session, provider_key_id: int) -> str:
    """
    Decrypt a key for immediate use in an outbound call.

    The ONLY place plaintext exists is the return value of this function, on the
    stack, for the duration of one request. It is never stored, never logged, and
    never returned by the API.
    """
    row = db.query(ProviderKey).filter(ProviderKey.id == provider_key_id).one()
    return crypto.decrypt(row.key_ciphertext)


def export_keys(db: Session, user_id: int) -> List[dict]:
    """
    Decrypt ALL of one user's keys for a backup download.

    Same contract as reveal(): plaintext exists only in the return value, for the
    duration of one request, and is never stored or logged. Scoped by user_id —
    you can only export what you own.
    """
    rows = (
        db.query(ProviderKey, Provider)
        .join(Provider, ProviderKey.provider_id == Provider.id)
        .filter(ProviderKey.user_id == user_id)
        .order_by(Provider.slug, ProviderKey.id)
        .all()
    )
    return [
        {
            "provider": prov.slug,
            "provider_name": prov.name,
            "label": pk.label,
            "api_key": crypto.decrypt(pk.key_ciphertext),
        }
        for pk, prov in rows
    ]


def fan_out(db: Session, key: ProviderKey) -> int:
    """
    Create one deployment per catalog model this key's provider serves.

    Deployments start as 'unavailable' — NOT 'available'. An unprobed key is an
    unknown key, and claiming otherwise would route real traffic at it. The probe
    (run in the background by the caller) is what promotes them.

    Idempotent: skips models that already have a deployment for this key, so
    re-running after the catalog grows only adds what's new.
    """
    models = (
        db.query(ProviderModel)
        .filter(
            ProviderModel.provider_id == key.provider_id,
            ProviderModel.enabled.is_(True),
        )
        .all()
    )
    existing = {
        d.provider_model_id
        for d in db.query(Deployment).filter(Deployment.provider_key_id == key.id).all()
    }

    created = 0
    for m in models:
        if m.id in existing:
            continue
        db.add(
            Deployment(
                user_id=key.user_id,
                provider_id=key.provider_id,   # composite FKs check this matches both sides
                provider_model_id=m.id,
                provider_key_id=key.id,
                status=ModelHealth.unavailable,   # until probed
            )
        )
        created += 1

    db.commit()
    logger.info(
        "Fanned out key %s (user %s): %d new deployment(s).", key.id, key.user_id, created
    )
    return created


def add_key(
    db: Session,
    *,
    user_id: int,
    provider_id: int,
    value: str,
    label: Optional[str] = None,
) -> ProviderKey:
    """
    Encrypt and store a user's provider key, then fan it out into deployments.

    Scoped by user_id throughout. The old version upserted by env_slot with NO
    user filter, so one user saving a key would OVERWRITE another user's row for
    the same slot. Here, (user_id, provider_id, label) is unique — two users can
    hold as many keys as they like without ever touching each other's.
    """
    value = value.strip()
    if not value:
        raise ValueError("An empty API key cannot be stored.")

    label = label or f"key-{crypto.mask(value)}"

    # THE KEY VALUE ITSELF IS UNIQUE per (user, provider) — the same secret
    # under two labels is not two keys, it's one quota counted twice: it doubles
    # nothing, but doubles the probe traffic and fakes 'key backup' redundancy.
    # Ciphertexts aren't comparable (random IV), so compare decrypted values —
    # a user holds a handful of keys per provider, so this is cheap.
    for other in db.query(ProviderKey).filter(
        ProviderKey.user_id == user_id,
        ProviderKey.provider_id == provider_id,
        ProviderKey.label != label,
    ):
        if crypto.decrypt(other.key_ciphertext) == value:
            raise ValueError(
                f"This exact key is already saved as {other.label!r}. "
                "The same key twice adds no quota and no redundancy."
            )

    row = (
        db.query(ProviderKey)
        .filter(
            ProviderKey.user_id == user_id,
            ProviderKey.provider_id == provider_id,
            ProviderKey.label == label,
        )
        .first()
    )
    if row:
        row.key_ciphertext = crypto.encrypt(value)
        row.key_masked = crypto.mask(value)
        row.is_active = True
    else:
        row = ProviderKey(
            user_id=user_id,
            provider_id=provider_id,
            label=label,
            key_ciphertext=crypto.encrypt(value),
            key_masked=crypto.mask(value),
        )
        db.add(row)

    db.commit()
    db.refresh(row)

    fan_out(db, row)
    return row


def list_keys(db: Session, user_id: int) -> List[dict]:
    """A user's own keys, masked. Never returns the secret."""
    rows = (
        db.query(ProviderKey, Provider)
        .join(Provider, ProviderKey.provider_id == Provider.id)
        .filter(ProviderKey.user_id == user_id)
        .order_by(ProviderKey.id)
        .all()
    )
    out = []
    for pk, prov in rows:
        live = (
            db.query(Deployment)
            .filter(Deployment.provider_key_id == pk.id, Deployment.is_working.is_(True))
            .count()
        )
        total = db.query(Deployment).filter(Deployment.provider_key_id == pk.id).count()
        out.append(
            {
                "id": pk.id,
                "provider": prov.slug,
                "label": pk.label,
                "masked": pk.key_masked,
                "is_active": pk.is_active,
                "working_models": live,
                "total_models": total,
            }
        )
    return out


def delete_key(db: Session, key_id: int, user_id: int) -> bool:
    """
    Delete one of THIS user's keys. Its deployments cascade away with it.

    user_id is not optional: without it, any authenticated user could delete
    anyone's key by guessing an id.
    """
    row = (
        db.query(ProviderKey)
        .filter(ProviderKey.id == key_id, ProviderKey.user_id == user_id)
        .first()
    )
    if not row:
        return False
    db.delete(row)   # deployments cascade (FK ON DELETE CASCADE)
    db.commit()
    logger.info("Deleted provider key %s for user %s.", key_id, user_id)
    return True
