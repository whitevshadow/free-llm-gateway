"""
LLM Router — the smart-switching core of the gateway.

WHAT THIS IS:
  A thin wrapper that builds a single `litellm.Router` from the YAML model pool
  (config/model_pool.yaml). The Router is the engine that makes many limited free
  tiers behave like one "unlimited" provider:

    • Load balancing  — rotates across every deployment of a virtual model
                        (multiple keys + multiple providers serving the same model).
    • Cooldowns       — a deployment that returns 429 / quota errors is benched for
                        `cooldown_time` seconds; traffic shifts to the rest.
    • Retries         — `num_retries` automatic retries across deployments.
    • Fallbacks       — when a whole virtual model is exhausted, cascade to the
                        next virtual model defined in `router_settings.fallbacks`.

WHY A SINGLETON:
  The Router holds in-memory state (per-deployment usage counters, cooldown cache).
  Every request must share the SAME instance or that state is meaningless. We build
  it once, lazily, on first use.

KEY RESOLUTION & FILTERING:
  Deployments reference keys as `os.environ/<VAR>`. At load time we resolve those
  references and DROP any deployment whose key is unset — so the live pool reflects
  only the free accounts you've actually configured. Add a key in .env, restart,
  and that deployment joins the pool automatically.
"""

from __future__ import annotations

import os
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml
import litellm
from litellm import Router

from app.core.config import settings

logger = logging.getLogger("gateway.router")

BACKEND_ROOT = Path(__file__).resolve().parents[2]

_ENV_PREFIX = "os.environ/"

# Module-level singletons (populated by get_router()).
_router: Optional[Router] = None
_virtual_models: List[str] = []


# ───────────────────────────────────────────────────────────────
# YAML LOADING + KEY RESOLUTION
# ───────────────────────────────────────────────────────────────

def _pool_path() -> Path:
    """Resolve MODEL_POOL_PATH against the backend root if it's relative."""
    p = Path(settings.MODEL_POOL_PATH)
    return p if p.is_absolute() else (BACKEND_ROOT / p)


def _resolve_env_ref(value: Any) -> Optional[str]:
    """
    Resolve a possible `os.environ/<VAR>` reference.

    Returns the resolved string, or None if it referenced an unset/empty var.
    Non-reference values are returned unchanged.
    """
    if isinstance(value, str) and value.startswith(_ENV_PREFIX):
        var = value[len(_ENV_PREFIX):]
        resolved = os.environ.get(var)
        return resolved or None
    return value


def _load_pool_from_yaml() -> Dict[str, Any]:
    """Read and parse the YAML pool file. Returns {} if it doesn't exist."""
    path = _pool_path()
    if not path.exists():
        logger.error("Model pool file not found at %s", path)
        return {}
    with open(path, "r", encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def _load_pool_from_db() -> Optional[Dict[str, Any]]:
    """
    Build the pool dict from the Postgres tables (model_deployments + router_config).

    Returns None if there are no deployments (so callers can fall back to YAML) or
    if the DB is unreachable. The returned shape is identical to the YAML's:
    {"model_list": [...], "router_settings": {...}}.
    """
    try:
        from app.core.database import SessionLocal
        from app.models.model_pool import ModelDeployment, RouterConfig

        db = SessionLocal()
        try:
            deps = (
                db.query(ModelDeployment)
                .filter(ModelDeployment.enabled.is_(True))
                .order_by(ModelDeployment.id)
                .all()
            )
            if not deps:
                return None

            model_list: List[Dict[str, Any]] = []
            for d in deps:
                params: Dict[str, Any] = {"model": d.litellm_model}
                if d.api_key_ref:
                    params["api_key"] = d.api_key_ref
                if d.api_base_ref:
                    params["api_base"] = d.api_base_ref
                if d.rpm is not None:
                    params["rpm"] = d.rpm
                if d.extra:
                    params.update(d.extra)
                model_list.append({"model_name": d.model_name, "litellm_params": params})

            router_settings: Dict[str, Any] = {}
            rc = db.query(RouterConfig).first()
            if rc:
                for key, val in (
                    ("routing_strategy", rc.routing_strategy),
                    ("num_retries", rc.num_retries),
                    ("cooldown_time", rc.cooldown_time),
                    ("allowed_fails", rc.allowed_fails),
                    ("fallbacks", rc.fallbacks),
                ):
                    if val is not None:
                        router_settings[key] = val

            return {"model_list": model_list, "router_settings": router_settings}
        finally:
            db.close()
    except Exception as exc:  # table missing, DB down, etc.
        logger.warning("Could not load pool from DB (%s); falling back to YAML.", exc)
        return None


def _load_pool() -> Dict[str, Any]:
    """
    Load the curated pool from the configured source (see settings.MODEL_POOL_SOURCE):
      • auto → Postgres if it has deployments, else the YAML file
      • db   → Postgres only
      • yaml → the YAML file only
    """
    source = (settings.MODEL_POOL_SOURCE or "auto").lower()

    if source in ("auto", "db"):
        db_pool = _load_pool_from_db()
        if db_pool and db_pool.get("model_list"):
            return db_pool
        if source == "db":
            logger.warning("MODEL_POOL_SOURCE=db but no deployments found in the DB.")
            return db_pool or {}

    return _load_pool_from_yaml()


def _build_model_list(raw_list: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Resolve env references and keep only usable deployments.

    A deployment is kept when:
      • it has an `api_key` that resolves to a non-empty value, OR
      • it has no `api_key` at all (keyless local providers like Ollama).
    Deployments referencing an unset key are silently dropped.
    """
    usable: List[Dict[str, Any]] = []
    dropped = 0

    for entry in raw_list:
        params = dict(entry.get("litellm_params", {}))
        name = entry.get("model_name", "?")

        # api_base may reference env too (e.g. Ollama) — resolve but don't require.
        if "api_base" in params:
            resolved_base = _resolve_env_ref(params["api_base"])
            if resolved_base:
                params["api_base"] = resolved_base
            else:
                params.pop("api_base", None)

        if "api_key" in params:
            resolved_key = _resolve_env_ref(params["api_key"])
            if not resolved_key:
                dropped += 1
                continue
            params["api_key"] = resolved_key

        usable.append({**entry, "litellm_params": params})

    if dropped:
        logger.info("Skipped %d deployment(s) with missing API keys.", dropped)
    return usable


# ───────────────────────────────────────────────────────────────
# NVIDIA AUTO-DISCOVERY
# ───────────────────────────────────────────────────────────────
# When a NVIDIA key is set, pull NVIDIA's full model catalog and register every
# chat model as a callable deployment (model_name == the NVIDIA id, e.g.
# "meta/llama-3.3-70b-instruct"). Non-chat models (embeddings, rerankers, OCR)
# are skipped since they can't serve chat completions.

_NVIDIA_MODELS_URL = "https://integrate.api.nvidia.com/v1/models"
# Substrings that mark a model as non-chat — excluded from the pool.
_NVIDIA_NON_CHAT = ("embed", "rerank", "-ocr", "ocrnet", "retrieval", "/bge-")
_nvidia_cache: Dict[str, Any] = {"sig": None, "models": []}


def _nvidia_keys() -> List[str]:
    """All NVIDIA key values currently in the environment (supports rotation)."""
    keys: List[str] = []
    for name, value in os.environ.items():
        if not value:
            continue
        if (
            name == "NVIDIA_API_KEY" or name.startswith("NVIDIA_API_KEY_")
            or name == "NVIDIA_NIM_API_KEY" or name.startswith("NVIDIA_NIM_API_KEY_")
        ):
            keys.append(value)
    return keys


def _fetch_nvidia_model_ids(keys: List[str]) -> List[str]:
    """Fetch + cache the list of NVIDIA chat model ids. Returns [] on any failure."""
    sig = tuple(sorted({k[-6:] for k in keys}))
    if _nvidia_cache["sig"] == sig:
        return _nvidia_cache["models"]

    ids: List[str] = []
    try:
        import httpx
        headers = {"Authorization": f"Bearer {keys[0]}"} if keys else {}
        resp = httpx.get(_NVIDIA_MODELS_URL, headers=headers, timeout=15)
        resp.raise_for_status()
        for item in resp.json().get("data", []):
            mid = item.get("id", "")
            if mid and not any(tok in mid.lower() for tok in _NVIDIA_NON_CHAT):
                ids.append(mid)
    except Exception as exc:  # network/parse failure — degrade gracefully
        logger.warning("NVIDIA model discovery failed: %s", exc)

    _nvidia_cache.update({"sig": sig, "models": ids})
    return ids


def _nvidia_discovered_deployments(existing_names: set) -> List[Dict[str, Any]]:
    """Build a deployment per (discovered NVIDIA model, key), skipping duplicates."""
    if not settings.NVIDIA_AUTO_DISCOVER:
        return []
    keys = _nvidia_keys()
    if not keys:
        return []

    deployments: List[Dict[str, Any]] = []
    model_ids = [m for m in _fetch_nvidia_model_ids(keys) if m not in existing_names]
    for mid in model_ids:
        for key in keys:
            deployments.append(
                {
                    "model_name": mid,
                    "litellm_params": {"model": f"nvidia_nim/{mid}", "api_key": key},
                }
            )
    if model_ids:
        logger.info("NVIDIA auto-discovery: registered %d chat model(s).", len(model_ids))
    return deployments


# ───────────────────────────────────────────────────────────────
# OPENROUTER AUTO-DISCOVERY
# ───────────────────────────────────────────────────────────────
# When an OpenRouter key is set, pull OpenRouter's catalog and register every
# model as a callable deployment (model_name == the OpenRouter id, e.g.
# "deepseek/deepseek-r1:free"). By default only free models are kept.

_OPENROUTER_MODELS_URL = "https://openrouter.ai/api/v1/models"
_openrouter_cache: Dict[str, Any] = {"sig": None, "models": []}


def _openrouter_keys() -> List[str]:
    """All OpenRouter key values currently in the environment (supports rotation)."""
    keys: List[str] = []
    for name, value in os.environ.items():
        if not value:
            continue
        if name == "OPENROUTER_API_KEY" or name.startswith("OPENROUTER_API_KEY_"):
            keys.append(value)
    return keys


def _is_free_openrouter(item: Dict[str, Any]) -> bool:
    """True if an OpenRouter catalog entry is free (":free" id or zero-priced)."""
    mid = item.get("id", "")
    if mid.endswith(":free"):
        return True
    pricing = item.get("pricing", {}) or {}
    def _zero(v: Any) -> bool:
        try:
            return float(v) == 0.0
        except (TypeError, ValueError):
            return False
    return _zero(pricing.get("prompt", "0")) and _zero(pricing.get("completion", "0"))


def _fetch_openrouter_model_ids(keys: List[str]) -> List[str]:
    """Fetch + cache the list of OpenRouter model ids. Returns [] on any failure."""
    sig = tuple(sorted({k[-6:] for k in keys}))
    if _openrouter_cache["sig"] == sig:
        return _openrouter_cache["models"]

    ids: List[str] = []
    try:
        import httpx
        headers = {"Authorization": f"Bearer {keys[0]}"} if keys else {}
        resp = httpx.get(_OPENROUTER_MODELS_URL, headers=headers, timeout=15)
        resp.raise_for_status()
        for item in resp.json().get("data", []):
            mid = item.get("id", "")
            if not mid:
                continue
            if settings.OPENROUTER_FREE_ONLY and not _is_free_openrouter(item):
                continue
            ids.append(mid)
    except Exception as exc:  # network/parse failure — degrade gracefully
        logger.warning("OpenRouter model discovery failed: %s", exc)

    _openrouter_cache.update({"sig": sig, "models": ids})
    return ids


def _openrouter_discovered_deployments(existing_names: set) -> List[Dict[str, Any]]:
    """Build a deployment per (discovered OpenRouter model, key), skipping duplicates."""
    if not settings.OPENROUTER_AUTO_DISCOVER:
        return []
    keys = _openrouter_keys()
    if not keys:
        return []

    deployments: List[Dict[str, Any]] = []
    model_ids = [m for m in _fetch_openrouter_model_ids(keys) if m not in existing_names]
    for mid in model_ids:
        for key in keys:
            deployments.append(
                {
                    "model_name": mid,
                    "litellm_params": {"model": f"openrouter/{mid}", "api_key": key},
                }
            )
    if model_ids:
        logger.info("OpenRouter auto-discovery: registered %d model(s).", len(model_ids))
    return deployments


# ───────────────────────────────────────────────────────────────
# ROUTER CONSTRUCTION
# ───────────────────────────────────────────────────────────────

def _build_router() -> Router:
    global _virtual_models

    pool = _load_pool()
    raw_list = pool.get("model_list", []) or []
    router_settings = dict(pool.get("router_settings", {}) or {})

    model_list = _build_model_list(raw_list)

    # Auto-discover every NVIDIA chat model and register it by its full id.
    model_list.extend(_nvidia_discovered_deployments({d["model_name"] for d in model_list}))
    # Auto-discover every OpenRouter model and register it by its full id.
    model_list.extend(_openrouter_discovered_deployments({d["model_name"] for d in model_list}))

    # Hide deployments a manual probe marked unavailable. No effect until a probe
    # has populated the model_availability table (see app/scripts/probe_models.py).
    if settings.FILTER_BY_AVAILABILITY:
        unavailable = get_unavailable_concrete_models()
        if unavailable:
            before = len(model_list)
            model_list = [
                d for d in model_list
                if (d.get("litellm_params", {}) or {}).get("model") not in unavailable
            ]
            dropped = before - len(model_list)
            if dropped:
                logger.info(
                    "Availability filter: hid %d deployment(s) marked unavailable.", dropped
                )

    if not model_list:
        logger.warning(
            "No usable deployments in the pool — set at least one free API key "
            "(e.g. GROQ_API_KEY_1) in your .env. The /v1 endpoints will error "
            "until a key is configured."
        )

    # Drop fallback entries that reference virtual models with no live deployment,
    # otherwise the Router would try to fall back to an empty model.
    live_names = {d["model_name"] for d in model_list}
    raw_fallbacks = router_settings.pop("fallbacks", []) or []
    fallbacks = []
    for fb in raw_fallbacks:
        for primary, targets in fb.items():
            if primary not in live_names:
                continue
            kept = [t for t in targets if t in live_names]
            if kept:
                fallbacks.append({primary: kept})

    # Honour the global request timeout from settings.
    litellm.request_timeout = settings.REQUEST_TIMEOUT

    router = Router(
        model_list=model_list,
        fallbacks=fallbacks,
        timeout=settings.REQUEST_TIMEOUT,
        **router_settings,
    )

    _virtual_models = sorted(live_names)
    logger.info(
        "Router built: %d deployment(s) across %d virtual model(s): %s",
        len(model_list),
        len(_virtual_models),
        ", ".join(_virtual_models) or "(none)",
    )
    return router


def get_router() -> Router:
    """Return the lazily-built singleton Router."""
    global _router
    if _router is None:
        _router = _build_router()
    return _router


def reload_router() -> Router:
    """Force a rebuild (e.g. after keys change). Mainly for tests/admin."""
    global _router
    _router = None
    return get_router()


# ───────────────────────────────────────────────────────────────
# INTROSPECTION HELPERS  (used by /v1/models, catalog, health)
# ───────────────────────────────────────────────────────────────

def list_virtual_models() -> List[str]:
    """Distinct virtual model names that currently have a live deployment."""
    get_router()  # ensure built
    return list(_virtual_models)


def get_unavailable_concrete_models() -> set:
    """
    Concrete model strings a manual probe marked unavailable.

    Returns an empty set if no probe has run yet (table empty/missing) or the DB
    is unreachable — so filtering is a no-op until there's real data to act on.
    """
    try:
        from app.core.database import SessionLocal
        from app.models.model_availability import ModelAvailability

        db = SessionLocal()
        try:
            rows = db.query(
                ModelAvailability.concrete_model, ModelAvailability.available
            ).all()
            return {row[0] for row in rows if not row[1]}
        finally:
            db.close()
    except Exception as exc:  # table not created yet, DB down, etc.
        logger.debug("Availability table not queryable (%s) — skipping filter.", exc)
        return set()


def all_probe_targets() -> List[Dict[str, Any]]:
    """
    Enumerate EVERY deployment (pool + NVIDIA auto-discovered) for the manual
    availability probe — unfiltered, with resolved keys and the env-var name.

    Each dict: {virtual_model, model, api_key, api_base, api_key_var, provider}.
    Deployments whose key env-var is unset are skipped (nothing to test).
    """
    pool = _load_pool()
    raw_list = pool.get("model_list", []) or []
    targets: List[Dict[str, Any]] = []

    def _add(virtual: str, params: Dict[str, Any], api_key_var: Optional[str]) -> None:
        model = params.get("model", "")
        if not model:
            return
        targets.append(
            {
                "virtual_model": virtual,
                "model": model,
                "api_key": params.get("api_key"),
                "api_base": params.get("api_base"),
                "api_key_var": api_key_var,
                "provider": model.split("/", 1)[0] if model else "unknown",
            }
        )

    for entry in raw_list:
        params = dict(entry.get("litellm_params", {}) or {})
        virtual = entry.get("model_name", "")

        base = params.get("api_base")
        if isinstance(base, str) and base.startswith(_ENV_PREFIX):
            params["api_base"] = os.environ.get(base[len(_ENV_PREFIX):]) or None

        api_key_var: Optional[str] = None
        ref = params.get("api_key")
        if isinstance(ref, str) and ref.startswith(_ENV_PREFIX):
            api_key_var = ref[len(_ENV_PREFIX):]
            resolved = os.environ.get(api_key_var) or None
            if not resolved:
                continue  # key not configured — nothing to probe
            params["api_key"] = resolved

        _add(virtual, params, api_key_var)

    # NVIDIA + OpenRouter auto-discovered models (each maps to one deployment).
    existing = {e.get("model_name") for e in raw_list}
    discovered = _nvidia_discovered_deployments(existing)
    discovered += _openrouter_discovered_deployments(
        existing | {d["model_name"] for d in discovered}
    )
    for dep in discovered:
        _add(dep.get("model_name", ""), dict(dep.get("litellm_params", {}) or {}), None)

    return targets


def list_key_slots() -> List[Dict[str, Any]]:
    """
    Discover the API-key "slots" the pool references, for the Settings UI.

    Reads the raw YAML (not the filtered live pool) so it lists every configurable
    key — including ones not yet set — and which provider / models each unlocks.

    Returns a list of dicts:
      { "env_var", "provider", "litellm_models", "virtual_models", "example_model" }
    """
    pool = _load_pool()
    raw_list = pool.get("model_list", []) or []

    slots: Dict[str, Dict[str, Any]] = {}
    for entry in raw_list:
        params = entry.get("litellm_params", {}) or {}
        api_key = params.get("api_key")
        if not (isinstance(api_key, str) and api_key.startswith(_ENV_PREFIX)):
            continue  # keyless deployment (e.g. Ollama) — nothing to configure
        env_var = api_key[len(_ENV_PREFIX):]
        model = params.get("model", "")
        provider = model.split("/", 1)[0] if model else "unknown"
        virtual = entry.get("model_name", "")

        slot = slots.setdefault(
            env_var,
            {
                "env_var": env_var,
                "provider": provider,
                "litellm_models": [],
                "virtual_models": [],
                "example_model": model,
            },
        )
        if model and model not in slot["litellm_models"]:
            slot["litellm_models"].append(model)
        if virtual and virtual not in slot["virtual_models"]:
            slot["virtual_models"].append(virtual)

    return list(slots.values())


def router_health() -> Dict[str, Any]:
    """
    Summarise the pool for /health and analytics: how many deployments back each
    virtual model, and which (if any) are currently cooling down after failures.

    The cooldown API differs across litellm versions, so we read it defensively.
    """
    router = get_router()

    per_model: Dict[str, int] = {}
    for dep in getattr(router, "model_list", []) or []:
        name = dep.get("model_name", "?")
        per_model[name] = per_model.get(name, 0) + 1

    cooling: List[str] = []
    try:
        # Map deployment id -> "virtual/concrete" label for readable output.
        id_to_label: Dict[str, str] = {}
        for dep in getattr(router, "model_list", []) or []:
            dep_id = (dep.get("model_info") or {}).get("id")
            if dep_id:
                label = f"{dep.get('model_name')} ({dep.get('litellm_params', {}).get('model')})"
                id_to_label[dep_id] = label

        cooldown_cache = getattr(router, "cooldown_cache", None)
        get_active = getattr(cooldown_cache, "get_active_cooldowns", None)
        if callable(get_active) and id_to_label:
            active = get_active(list(id_to_label.keys()), None)  # [(id, value), ...]
            for entry in active or []:
                dep_id = entry[0] if isinstance(entry, (list, tuple)) else entry
                cooling.append(id_to_label.get(dep_id, dep_id))
    except Exception as exc:  # pragma: no cover - version-dependent
        logger.debug("Could not read cooldown state: %s", exc)

    return {
        "virtual_models": per_model,
        "total_deployments": sum(per_model.values()),
        "cooling_down": cooling,
    }
