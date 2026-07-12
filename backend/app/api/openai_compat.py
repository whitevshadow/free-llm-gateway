"""
OpenAI-compatible endpoints — `/v1/chat/completions`, `/v1/embeddings` and
`/v1/models`.

This is the drop-in surface for *any* OpenAI client (the OpenAI SDK, Cursor,
Continue, LangChain, plain curl, ...). Point the client at this gateway:

    base_url = "http://localhost:8000/v1"
    api_key  = "<GATEWAY_API_KEY>"        # or anything if auth is disabled
    model    = "auto"                      # or gpt-oss-120b, deepseek, qwen, ...

Requests are handed straight to the smart Router, which load-balances across free
keys/providers and cools down anything that hits a 429. Because LiteLLM already
speaks OpenAI's schema natively, the response is passed through unchanged.
"""

from __future__ import annotations

import json
import time
import logging
from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse

from app.core.config import settings
from app.core.llm_router import get_router, list_virtual_models
from app.api.gateway_auth import verify_gateway_key
from app.services.usage_logger import log_v1_usage

logger = logging.getLogger("gateway.openai")

router = APIRouter()

# Virtual model /v1/embeddings falls back to when the requested model is
# unknown (e.g. a client default like "text-embedding-3-small").
DEFAULT_EMBEDDING_MODEL = "embed"


def _resolve_model(requested: str | None) -> str:
    """Map an incoming model id to a live virtual model, else the default."""
    virtual = list_virtual_models()
    if requested and requested in virtual:
        return requested
    return settings.DEFAULT_VIRTUAL_MODEL


def _to_dict(obj: Any) -> Dict[str, Any]:
    """Normalise a LiteLLM response/chunk to a plain dict across versions."""
    if isinstance(obj, dict):
        return obj
    for attr in ("model_dump", "dict"):
        fn = getattr(obj, attr, None)
        if callable(fn):
            try:
                return fn()
            except Exception:
                pass
    return json.loads(getattr(obj, "json", lambda: "{}")())


@router.get("/v1/models", dependencies=[Depends(verify_gateway_key)])
async def list_models():
    """List the virtual models the gateway exposes (OpenAI `/v1/models` shape)."""
    now = int(time.time())
    data = [
        {"id": name, "object": "model", "created": now, "owned_by": "free-gateway"}
        for name in list_virtual_models()
    ]
    return {"object": "list", "data": data}


@router.post("/v1/chat/completions", dependencies=[Depends(verify_gateway_key)])
async def chat_completions(request: Request):
    """OpenAI chat-completions, served by the smart free-provider Router."""
    try:
        body: Dict[str, Any] = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body.")

    if not body.get("messages"):
        raise HTTPException(status_code=400, detail="'messages' is required.")

    requested_model = body.get("model")
    body["model"] = _resolve_model(requested_model)
    stream = bool(body.pop("stream", False))
    llm_router = get_router()
    start = time.perf_counter()

    # ── Streaming ───────────────────────────────────────
    if stream:
        async def event_stream():
            try:
                response = await llm_router.acompletion(**body, stream=True)
                async for chunk in response:
                    yield f"data: {json.dumps(_to_dict(chunk))}\n\n"
                yield "data: [DONE]\n\n"
            except Exception as exc:
                logger.warning("v1 stream error: %s", exc)
                err = {"error": {"message": str(exc), "type": "gateway_error"}}
                yield f"data: {json.dumps(err)}\n\n"
                yield "data: [DONE]\n\n"

        return StreamingResponse(event_stream(), media_type="text/event-stream")

    # ── Standard ────────────────────────────────────────
    try:
        response = await llm_router.acompletion(**body)
    except Exception as exc:
        latency = round(time.perf_counter() - start, 3)
        log_v1_usage(
            model=body["model"], latency=latency, status_code=502,
            error_message=str(exc),
        )
        logger.warning("v1 completion failed: %s", exc)
        raise HTTPException(status_code=502, detail=str(exc))

    result = _to_dict(response)
    log_v1_usage(
        model=result.get("model", body["model"]),
        usage=result.get("usage"),
        latency=round(time.perf_counter() - start, 3),
        status_code=200,
    )
    return result


@router.post("/v1/embeddings", dependencies=[Depends(verify_gateway_key)])
async def embeddings(request: Request):
    """
    OpenAI embeddings, served by the free NVIDIA NIM embedding pool.

    NVIDIA's retrieval models are asymmetric: the pool defaults to
    `input_type: query`; pass `"input_type": "passage"` in the body when
    embedding documents for an index.
    """
    try:
        body: Dict[str, Any] = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body.")

    if not body.get("input"):
        raise HTTPException(status_code=400, detail="'input' is required.")

    virtual = list_virtual_models()
    requested = body.get("model")
    if requested in virtual:
        body["model"] = requested
    elif DEFAULT_EMBEDDING_MODEL in virtual:
        body["model"] = DEFAULT_EMBEDDING_MODEL
    else:
        raise HTTPException(
            status_code=503,
            detail=(
                "No embedding deployment is configured — set NVIDIA_API_KEY_1 "
                "in .env to activate the free NVIDIA embedding models."
            ),
        )

    llm_router = get_router()
    start = time.perf_counter()
    try:
        response = await llm_router.aembedding(**body)
    except Exception as exc:
        latency = round(time.perf_counter() - start, 3)
        log_v1_usage(
            model=body["model"], latency=latency, status_code=502,
            error_message=str(exc),
        )
        logger.warning("v1 embeddings failed: %s", exc)
        raise HTTPException(status_code=502, detail=str(exc))

    result = _to_dict(response)
    log_v1_usage(
        model=result.get("model") or body["model"],
        usage=result.get("usage"),
        latency=round(time.perf_counter() - start, 3),
        status_code=200,
    )
    return result
