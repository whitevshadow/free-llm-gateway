"""
Anthropic-compatible endpoint — `/v1/messages`.

This is the surface **Claude Code** talks to. Point Claude Code at the gateway:

    ANTHROPIC_BASE_URL=http://localhost:8000
    ANTHROPIC_AUTH_TOKEN=<GATEWAY_API_KEY>     # or anything if auth is disabled

Claude Code then sends standard Anthropic Messages requests here, and the gateway
serves them from the pool of FREE providers — so you get Claude Code's workflow on
top of free models, with automatic switching when a free tier is exhausted.

HOW THE TRANSLATION WORKS:
  We reuse LiteLLM's battle-tested Anthropic adapter via `Router.aadapter_completion`.
  It translates the Anthropic request → OpenAI format (including tools, system
  prompts, images), routes it through the smart Router (load balancing + 429
  cooldowns across free keys/providers), then translates the response — and the
  full SSE event stream — back into Anthropic's format. This is the same mechanism
  LiteLLM's own proxy uses for `/v1/messages`, so tool-calling works end to end.
"""

from __future__ import annotations

import logging
from typing import Any, Dict

import litellm
from litellm.llms.anthropic.experimental_pass_through.adapters.handler import (
    AnthropicAdapter,
)
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse

from app.core.config import settings
from app.core.llm_router import get_router, list_virtual_models
from app.api.gateway_auth import verify_gateway_key

logger = logging.getLogger("gateway.anthropic")

router = APIRouter()

ADAPTER_ID = "anthropic"


def _ensure_adapter_registered() -> None:
    """Register the Anthropic adapter once (idempotent)."""
    if not isinstance(getattr(litellm, "adapters", None), list):
        litellm.adapters = []
    if not any(item.get("id") == ADAPTER_ID for item in litellm.adapters):
        litellm.adapters.append({"id": ADAPTER_ID, "adapter": AnthropicAdapter()})


def _resolve_model(requested: str | None) -> str:
    """Map an incoming model id (e.g. 'claude-3-5-sonnet') to a live virtual model."""
    virtual = list_virtual_models()
    if requested and requested in virtual:
        return requested
    return settings.DEFAULT_VIRTUAL_MODEL


def _to_dict(obj: Any) -> Dict[str, Any]:
    if isinstance(obj, dict):
        return obj
    for attr in ("model_dump", "dict"):
        fn = getattr(obj, attr, None)
        if callable(fn):
            try:
                return fn()
            except Exception:
                pass
    return obj  # last resort; FastAPI will try to serialise it


@router.post("/v1/messages", dependencies=[Depends(verify_gateway_key)])
async def anthropic_messages(request: Request):
    """Anthropic Messages API, served by the smart free-provider Router."""
    _ensure_adapter_registered()

    try:
        body: Dict[str, Any] = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body.")

    if not body.get("messages"):
        raise HTTPException(status_code=400, detail="'messages' is required.")
    if not body.get("max_tokens"):
        # Anthropic requires max_tokens; supply a sane default if a client omits it.
        body["max_tokens"] = settings.DEFAULT_MAX_TOKENS

    # `model` is consumed by the Router for deployment selection; the adapter reads
    # the rest of the Anthropic params from kwargs.
    requested_model = body.pop("model", None)
    model = _resolve_model(request.headers.get("x-model") or requested_model)
    stream = bool(body.pop("stream", False))
    llm_router = get_router()

    # ── Streaming ───────────────────────────────────────
    if stream:
        try:
            response = await llm_router.aadapter_completion(
                adapter_id=ADAPTER_ID, model=model, stream=True, **body
            )
        except Exception as exc:
            logger.warning("anthropic stream error: %s", exc)
            raise HTTPException(status_code=502, detail=str(exc))

        # The adapter returns a wrapper that knows how to emit Anthropic SSE events.
        sse = getattr(response, "async_anthropic_sse_wrapper", None)
        if callable(sse):
            return StreamingResponse(sse(), media_type="text/event-stream")

        # Fallback: forward whatever the wrapper yields.
        async def passthrough():
            async for chunk in response:  # type: ignore[func-returns-value]
                yield chunk

        return StreamingResponse(passthrough(), media_type="text/event-stream")

    # ── Standard ────────────────────────────────────────
    try:
        response = await llm_router.aadapter_completion(
            adapter_id=ADAPTER_ID, model=model, **body
        )
    except Exception as exc:
        logger.warning("anthropic completion failed: %s", exc)
        raise HTTPException(status_code=502, detail=str(exc))

    return _to_dict(response)
