"""
Catalog discovery — populate provider_models from a provider's /v1/models API.

ADMIN-DRIVEN. An admin seeds a provider, then runs discovery, which asks the
provider what it actually serves and upserts the result. New models appear on
their own; nobody hand-maintains a YAML list.

Discovery needs a key to call /v1/models, so it BORROWS one active key for that
provider (one read-only call), ROTATING DAILY across the provider's keys so no
single key holder pays for every catalog refresh. That is a deliberate, narrow
exception to per-user key isolation — the only place a user's key is used on
behalf of the system rather than on behalf of that user.

WHAT IT MUST NOT DO: set is_common or provider_count. Those are maintained by a
Postgres trigger; writing them here would create a second, drifting source of
truth. Insert the rows and the flags follow.
"""

import logging
from datetime import date, datetime, timezone
from typing import Dict, List, Optional

import httpx
from sqlalchemy.orm import Session

from app.models.enums import ModelMode
from app.models.provider import Provider
from app.models.provider_key import ProviderKey
from app.models.provider_model import ProviderModel
from app.services import crypto, presets
from app.services.normalize import normalize_model_name, publisher_for

logger = logging.getLogger("gateway.catalog")

# Junk we never ingest even in chat mode: OCR pipelines and safety/guard models
# are listed as chat-shaped but can't serve a real conversation. Embeddings are
# NOT junk — they're detected by _infer_mode and kept with mode='embedding', so
# they get probed with an embedding call and shown in their own card.
_CHAT_JUNK = ("-ocr", "ocrnet", "guard")


def _infer_mode(model_id: str) -> ModelMode:
    lowered = model_id.lower()
    if any(tok in lowered for tok in ("embed", "/bge-", "retrieval")):
        return ModelMode.embedding
    if "rerank" in lowered:
        return ModelMode.rerank
    return ModelMode.chat


def _rotation_index(count: int, when: Optional[date] = None) -> int:
    """
    Which of `count` keys today's discovery borrows.

    Deterministic by DATE, not stored state: the same key serves every discovery
    for one calendar day (a manual refresh does not burn a second key), and the
    next day rotates to the next one. Stateless, so it survives restarts and
    needs no schema — and over a month, no single key pays for more than its
    share of catalog calls.
    """
    when = when or datetime.now(timezone.utc).date()
    return when.toordinal() % count


def _borrow_key(db: Session, provider_id: int) -> Optional[str]:
    """
    One active key for this provider, decrypted. Used ONLY to call /v1/models.

    Rotates daily across the provider's active keys (see _rotation_index) so the
    borrow cost is spread over every key holder instead of always billing the
    oldest key.
    """
    rows = (
        db.query(ProviderKey)
        .filter(ProviderKey.provider_id == provider_id, ProviderKey.is_active.is_(True))
        .order_by(ProviderKey.id)
        .all()
    )
    if not rows:
        return None
    row = rows[_rotation_index(len(rows))]
    return crypto.decrypt(row.key_ciphertext)


def _base_url(provider: Provider) -> Optional[str]:
    """The provider's own base_url, or the preset default for a known slug."""
    return provider.base_url or presets.base_url_for(provider.slug)


def fetch_model_ids(models_url: str, api_key: str) -> List[str]:
    """
    GET the model catalog. Returns [] on any failure — discovery degrades,
    never crashes.

    Tolerates both response shapes in the wild: OpenAI's {"data": [{id}, …]}
    and GitHub's bare [{id}, …] array.
    """
    try:
        resp = httpx.get(
            models_url,
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=20,
        )
        resp.raise_for_status()
        body = resp.json()
        items = body if isinstance(body, list) else body.get("data", [])
        return [m["id"] for m in items if isinstance(m, dict) and m.get("id")]
    except Exception as exc:
        logger.warning("Model discovery failed for %s: %s", models_url, exc)
        return []


def discover_provider(
    db: Session, provider: Provider, *,
    api_key: Optional[str] = None,
) -> Dict:
    """
    Refresh one provider's catalog. Upserts by (provider_id, upstream_model_id).

    `api_key` lets the caller supply the key to list models with — the user
    discovery endpoint passes the CALLER's own key, so a user only ever spends
    their own quota. Without it, we borrow any active key (the admin Sync path).

    Models the provider has DROPPED are disabled, not deleted: deployments still
    reference them, and disabling is enough for the trigger to stop counting them
    towards is_common. Deleting would cascade away a user's deployment rows.
    """
    base = _base_url(provider)
    if not base:
        return {"provider": provider.slug, "error": "no base_url, and no default known"}

    api_key = api_key or _borrow_key(db, provider.id)
    if not api_key:
        return {"provider": provider.slug, "error": "no active key available for discovery"}

    # Presets may override where the catalog lives (GitHub's is not {base}/models).
    preset = presets.get(provider.slug)
    models_url = (preset or {}).get("models_url") or f"{base.rstrip('/')}/models"

    ids = fetch_model_ids(models_url, api_key)
    if not ids:
        return {"provider": provider.slug, "error": "provider returned no models"}

    existing = {
        m.upstream_model_id: m
        for m in db.query(ProviderModel).filter(ProviderModel.provider_id == provider.id)
    }
    seen: set = set()
    added = updated = 0

    for mid in ids:
        mode = _infer_mode(mid)
        # Chat and embedding models are both first-class; rerankers and the
        # rest can serve neither /chat/completions nor /embeddings, so they
        # never enter the catalog.
        if mode not in (ModelMode.chat, ModelMode.embedding):
            continue
        if mode is ModelMode.chat and any(tok in mid.lower() for tok in _CHAT_JUNK):
            continue

        seen.add(mid)
        litellm_model = f"{provider.slug}/{mid}"   # slug doubles as the litellm prefix
        normalized = normalize_model_name(litellm_model)

        row = existing.get(mid)
        if row:
            row.litellm_model = litellm_model
            row.normalized_name = normalized
            row.publisher = publisher_for(mid)
            row.mode = mode
            row.enabled = True
            updated += 1
        else:
            db.add(
                ProviderModel(
                    provider_id=provider.id,
                    upstream_model_id=mid,
                    litellm_model=litellm_model,
                    normalized_name=normalized,
                    publisher=publisher_for(mid),
                    mode=mode,
                )
            )
            added += 1

    disabled = 0
    for mid, row in existing.items():
        if mid not in seen and row.enabled:
            row.enabled = False
            disabled += 1

    db.commit()
    result = {"provider": provider.slug, "added": added, "updated": updated, "disabled": disabled}
    logger.info("Discovery: %s", result)
    return result


def discover_all(db: Session) -> List[Dict]:
    """Refresh the catalog for every enabled provider."""
    providers = db.query(Provider).filter(Provider.enabled.is_(True)).all()
    return [discover_provider(db, p) for p in providers]
