"""
Settings Router — configure provider API keys and test connections from the UI.

ENDPOINTS (all under /api/v1/settings):
  GET    /providers          → key slots grouped by provider (masked values + status)
  PUT    /keys               → save one or more keys  {"keys": {"GROQ_API_KEY_1": "..."}}
  DELETE /keys/{env_var}     → remove a saved key
  POST   /test               → live-test a connection (an env-var slot OR a custom model)

The "test" endpoint does a real 1-token completion against a single provider/key so
the user gets immediate ✓/✗ feedback BEFORE relying on it. Saving a key persists it,
injects it into the environment, and rebuilds the smart Router so it takes effect now.
"""

import os
import time
import logging
from typing import Dict, List, Optional, Any

import litellm
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.api.auth import get_current_user
from app.models.user import User
from app.core.config import settings
from app.core.llm_router import list_key_slots, router_health
from app.providers import FREE_PROVIDER_INFO
from app.services import key_store
from app.utils.responses import success_response

logger = logging.getLogger("gateway.settings")

router = APIRouter()

_PROVIDER_META = {p["id"]: p for p in FREE_PROVIDER_INFO}


# ───────────────────────────────────────────────────────────────
# SCHEMAS
# ───────────────────────────────────────────────────────────────

class SaveKeysRequest(BaseModel):
    keys: Dict[str, str] = Field(..., description='Map of env-var slot -> API key value')


class TestRequest(BaseModel):
    # Test an existing slot by its env var (uses the slot's example model)...
    env_var: Optional[str] = Field(None, description="Slot to test, e.g. GROQ_API_KEY_1")
    # ...or test an arbitrary connection directly.
    model: Optional[str] = Field(None, description="LiteLLM model string, e.g. groq/llama-3.3-70b-versatile")
    api_key: Optional[str] = Field(None, description="Key to test. If omitted, the saved/env value is used.")
    api_base: Optional[str] = Field(None, description="Optional custom base URL (OpenAI-compatible APIs)")


# ───────────────────────────────────────────────────────────────
# LIST KEY SLOTS
# ───────────────────────────────────────────────────────────────

@router.get("/providers")
def list_provider_settings(current_user: User = Depends(get_current_user)):
    """
    Return the configurable key slots grouped by provider, each with a masked
    preview and live status. Drives the Settings page.
    """
    persisted = key_store.get_persisted_values()
    grouped: Dict[str, Dict[str, Any]] = {}

    for slot in list_key_slots():
        provider_id = slot["provider"]
        meta = _PROVIDER_META.get(provider_id, {"id": provider_id, "name": provider_id, "icon": "🤖", "note": ""})
        bucket = grouped.setdefault(
            provider_id,
            {
                "id": provider_id,
                "name": meta.get("name", provider_id),
                "icon": meta.get("icon", "🤖"),
                "note": meta.get("note", ""),
                "slots": [],
            },
        )
        env_var = slot["env_var"]
        current = os.environ.get(env_var)
        bucket["slots"].append(
            {
                "env_var": env_var,
                "is_set": bool(current),
                "persisted": env_var in persisted,
                "masked": key_store.mask_value(current),
                "virtual_models": slot["virtual_models"],
                "example_model": slot["example_model"],
            }
        )

    return success_response(
        data={"providers": list(grouped.values()), "router": router_health()},
        message="Provider key settings",
    )


# ───────────────────────────────────────────────────────────────
# SAVE / DELETE KEYS
# ───────────────────────────────────────────────────────────────

@router.put("/keys")
def save_keys(payload: SaveKeysRequest, current_user: User = Depends(get_current_user)):
    """Persist keys, inject into the environment, and rebuild the Router."""
    written = key_store.set_keys(payload.keys)
    return success_response(
        data={"saved": written, "router": router_health()},
        message=f"Saved {written} key(s). Router reloaded.",
    )


@router.delete("/keys/{env_var}")
def remove_key(env_var: str, current_user: User = Depends(get_current_user)):
    """Delete a saved key and rebuild the Router."""
    removed = key_store.delete_key(env_var)
    if not removed:
        raise HTTPException(status_code=404, detail=f"No saved key for '{env_var}'.")
    return success_response(
        data={"router": router_health()},
        message=f"Removed {env_var}. Router reloaded.",
    )


# ───────────────────────────────────────────────────────────────
# TEST CONNECTION
# ───────────────────────────────────────────────────────────────

def _resolve_test_target(payload: TestRequest) -> Dict[str, Optional[str]]:
    """Work out which (model, api_key, api_base) to test from the request."""
    if payload.model:
        api_key = payload.api_key or None
        return {"model": payload.model, "api_key": api_key, "api_base": payload.api_base}

    if payload.env_var:
        slot = next((s for s in list_key_slots() if s["env_var"] == payload.env_var), None)
        if not slot:
            raise HTTPException(status_code=400, detail=f"Unknown key slot '{payload.env_var}'.")
        # Use the provided key, else whatever is currently configured for that slot.
        api_key = payload.api_key or os.environ.get(payload.env_var)
        if not api_key:
            raise HTTPException(
                status_code=400,
                detail=f"No key provided or saved for '{payload.env_var}'. Enter a key to test.",
            )
        return {"model": slot["example_model"], "api_key": api_key, "api_base": payload.api_base}

    raise HTTPException(status_code=400, detail="Provide either 'env_var' or 'model' to test.")


@router.post("/test")
def test_connection(payload: TestRequest, current_user: User = Depends(get_current_user)):
    """
    Run a minimal (1-token) completion against a single provider/key and report
    whether it works, with latency. This NEVER touches the saved config — it's a
    pure dry-run so users can validate a key before saving.
    """
    target = _resolve_test_target(payload)
    model = target["model"]

    kwargs: Dict[str, Any] = {
        "model": model,
        "messages": [{"role": "user", "content": "ping"}],
        "max_tokens": 1,
        "timeout": 60,
        # A test must be fast and deterministic: do exactly ONE attempt. Without
        # this, LiteLLM + the OpenAI client retry retryable responses (429/5xx)
        # 2-3 times with backoff, so a single click can hang 30-40s and hide the
        # real status.
        "num_retries": 0,
        "max_retries": 0,
    }
    if target["api_key"]:
        kwargs["api_key"] = target["api_key"]
    if target["api_base"]:
        kwargs["api_base"] = target["api_base"]
    # DeepSeek reasoning models "think" before answering, which makes even a
    # 1-token ping slow. Disable thinking for the dry-run so the test is snappy.
    if "deepseek" in model.lower():
        kwargs["extra_body"] = {"chat_template_kwargs": {"thinking": False}}

    start = time.perf_counter()
    try:
        response = litellm.completion(**kwargs)
        latency = round(time.perf_counter() - start, 3)
        content = ""
        try:
            content = response.choices[0].message.content or ""
        except Exception:
            pass
        return success_response(
            data={
                "success": True,
                "model": model,
                "latency_seconds": latency,
                "response_preview": content[:120],
            },
            message="Connection successful",
        )
    except Exception as exc:
        latency = round(time.perf_counter() - start, 3)
        logger.info("test_connection failed for %s: %s", model, exc)
        return success_response(
            data={
                "success": False,
                "model": model,
                "latency_seconds": latency,
                "error": _clean_error(str(exc)),
            },
            message="Connection failed",
        )


def _clean_error(msg: str) -> str:
    """Trim LiteLLM's verbose error text to something readable in the UI."""
    msg = msg.strip()
    # Keep the most informative leading chunk.
    for sep in ("\nReceived Model Group", "\nModel:", " Pass "):
        idx = msg.find(sep)
        if idx > 0:
            msg = msg[:idx]
    return msg[:300]
