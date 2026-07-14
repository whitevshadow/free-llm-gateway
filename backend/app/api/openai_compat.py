"""
OpenAI-compatible endpoints — `/v1/chat/completions`, `/v1/embeddings`, `/v1/models`.

Drop-in surface for any OpenAI client (the OpenAI SDK, Cursor, Continue,
LangChain, curl):

    base_url = "http://localhost:8000/v1"
    api_key  = "<GATEWAY_KEY>"       # identifies WHICH USER you are
    model    = "gpt-oss-120b"        # the bare family name

EVERY REQUEST IS SCOPED TO THE CALLER. The gateway key resolves to a User, and the
Router is built from THAT USER'S live deployments with THEIR keys. Two users
calling the same model name hit different upstream accounts. There is no shared
Router and no shared key namespace.

Clients send the BARE FAMILY NAME ('gpt-oss-120b'); litellm load-balances every
deployment registered under it — across both the user's keys and their providers.
"""

from __future__ import annotations

import json
import time
import logging
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core import llm_router as router_svc
from app.api.gateway_auth import current_user
from app.models.user import User
from app.services.normalize import resolve_requested_model
from app.services.usage_logger import log_v1_usage

logger = logging.getLogger("gateway.openai")

router = APIRouter()


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


def _deployment_id(response: Any) -> Optional[int]:
    """
    Pull our deployment id back out of the litellm response.

    We stamped it into model_info when building the model_list, which is what lets
    us attribute a call — and a failure — to the exact (model, key) row.
    """
    try:
        info = getattr(response, "_hidden_params", {}) or {}
        raw = info.get("model_id")
        return int(raw) if raw is not None else None
    except (TypeError, ValueError):
        return None


@router.get("/v1/models")
async def list_models(user: User = Depends(current_user), db: Session = Depends(get_db)):
    """
    The models THIS USER can call — not a global list.

    A user who holds no keys sees an empty list, which is correct: they cannot
    call anything.
    """
    now = int(time.time())
    return {
        "object": "list",
        "data": [
            {"id": name, "object": "model", "created": now, "owned_by": "gateway"}
            for name in router_svc.list_models(user.id, db)
        ],
    }


@router.post("/v1/chat/completions")
async def chat_completions(
    request: Request,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    """Chat completions, served from THIS USER's live deployments."""
    try:
        body: Dict[str, Any] = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body.")

    if not body.get("messages"):
        raise HTTPException(status_code=400, detail="'messages' is required.")

    requested = body.get("model")
    available = router_svc.list_models(user.id, db)
    if not available:
        raise HTTPException(
            status_code=503,
            detail="You have no callable models. Add a provider key via POST /v1/me/provider-keys.",
        )
    resolved = resolve_requested_model(requested or "", set(available))
    if not resolved:
        raise HTTPException(
            status_code=404,
            detail=f"Model {requested!r} is not available to you. Yours: {available}",
        )
    body["model"] = resolved

    stream = bool(body.pop("stream", False))
    lr = router_svc.get_router(user.id, db)
    start = time.perf_counter()

    if stream:
        async def event_stream():
            try:
                response = await lr.acompletion(**body, stream=True)
                async for chunk in response:
                    yield f"data: {json.dumps(_to_dict(chunk))}\n\n"
                yield "data: [DONE]\n\n"
            except Exception as exc:
                logger.warning("stream error (user %s): %s", user.id, exc)
                err = {"error": {"message": str(exc), "type": "gateway_error"}}
                yield f"data: {json.dumps(err)}\n\n"
                yield "data: [DONE]\n\n"

        return StreamingResponse(event_stream(), media_type="text/event-stream")

    try:
        response = await lr.acompletion(**body)
    except Exception as exc:
        latency = round(time.perf_counter() - start, 3)
        log_v1_usage(
            user_id=user.id, requested_model=requested, latency=latency,
            status_code=502, error_message=str(exc),
        )
        logger.warning("completion failed (user %s): %s", user.id, exc)
        raise HTTPException(status_code=502, detail=str(exc))

    result = _to_dict(response)
    dep_id = _deployment_id(response)

    # Write the outcome back to Postgres — the durable truth. Without this a live
    # 429 would exist only in litellm's memory: invisible to the dashboard, to
    # other workers, and lost on restart.
    if dep_id:
        router_svc.record_success(dep_id)

    log_v1_usage(
        user_id=user.id,
        requested_model=requested,
        answered_deploy_id=dep_id,
        usage=result.get("usage"),
        latency=round(time.perf_counter() - start, 3),
        status_code=200,
    )
    return result


@router.post("/v1/embeddings")
async def embeddings(
    request: Request,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    """Embeddings, served from THIS USER's live embedding deployments."""
    try:
        body: Dict[str, Any] = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body.")

    if not body.get("input"):
        raise HTTPException(status_code=400, detail="'input' is required.")

    requested = body.get("model")
    available = router_svc.list_models(user.id, db)
    resolved = resolve_requested_model(requested or "", set(available))
    if not resolved:
        raise HTTPException(
            status_code=404,
            detail=f"Embedding model {requested!r} is not available to you. Yours: {available}",
        )
    body["model"] = resolved

    lr = router_svc.get_router(user.id, db)
    start = time.perf_counter()
    try:
        response = await lr.aembedding(**body)
    except Exception as exc:
        log_v1_usage(
            user_id=user.id, requested_model=requested,
            latency=round(time.perf_counter() - start, 3),
            status_code=502, error_message=str(exc),
        )
        logger.warning("embeddings failed (user %s): %s", user.id, exc)
        raise HTTPException(status_code=502, detail=str(exc))

    result = _to_dict(response)
    log_v1_usage(
        user_id=user.id,
        requested_model=requested,
        answered_deploy_id=_deployment_id(response),
        usage=result.get("usage"),
        latency=round(time.perf_counter() - start, 3),
        status_code=200,
    )
    return result
