"""
OpenAI-compatible endpoints — `/v1/chat/completions`, `/v1/embeddings`,
`/v1/images/generations`, `/v1/audio/transcriptions`, `/v1/audio/speech`,
`/v1/models`.

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

import io
import json
import time
import logging
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import Response, StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core import llm_router as router_svc
from app.api.gateway_auth import current_user
from app.models.provider_key import ProviderKey
from app.models.user import User
from app.models.views import v_live_deployments
from app.services import crypto
from app.services import nvcf as nvcf_svc
from app.services import deepseek_web
from app.services import combo_router
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
    names = router_svc.list_models(user.id, db)

    # Modality per public model name, so a caller can tell a chat model from an
    # embedding or TTS one without calling each to find out. `type`/`subtype` are
    # NON-STANDARD additions: OpenAI's schema has no modality field, and clients
    # ignore unknown keys, so this stays compatible while making the list useful.
    #
    # A name can be served by several deployments (that is the point of the
    # gateway), so the mode is taken from the models behind the name — they share
    # a normalized name only when they are interchangeable, which means they
    # share a mode too.
    from app.models.provider_model import ProviderModel

    modes = dict(
        db.query(ProviderModel.normalized_name, ProviderModel.mode)
        .filter(ProviderModel.normalized_name.in_(names))
        .distinct()
        .all()
    )

    # Mapped to the vocabulary OpenAI-compatible tooling expects, not our enum's.
    TYPE_BY_MODE = {
        "embedding": ("embedding", None),
        "image": ("image", None),
        "audio_transcription": ("audio", "transcription"),
        "audio_speech": ("audio", "speech"),
    }

    data = []
    for name in names:
        mode = modes.get(name)
        mode_value = mode.value if hasattr(mode, "value") else mode
        # Chat is the default and carries NO type field — that absence is what
        # marks a model as a plain chat model to OpenAI-compatible clients.
        type_, subtype = TYPE_BY_MODE.get(mode_value or "", (None, None))
        entry = {"id": name, "object": "model", "created": now, "owned_by": "gateway"}
        if type_:
            entry["type"] = type_
        if subtype:
            entry["subtype"] = subtype
        data.append(entry)

    # Combos are callable model names too (see _serve_combo), so a client that
    # populates its model picker from this list can select one. Only combos with
    # a live target are listed — same rule as everything else here. Marked by
    # owned_by so tooling can tell a routing chain from a real model.
    for combo_name in combo_router.callable_combo_names(db, user.id):
        data.append({
            "id": combo_name, "object": "model", "created": now,
            "owned_by": "gateway-combo",
        })

    return {"object": "list", "data": data}


async def _serve_combo(
    user: User,
    db: Session,
    combo: Any,
    body: Dict[str, Any],
    stream: bool,
):
    """
    Serve a request addressed to a COMBO name.

    The combo engine (services/combo_router.py) orders the combo's targets by its
    strategy and walks them until one answers, so everything below is about the
    envelope: OpenAI-shaped streaming, and a usage row that records WHICH combo
    served the call and at which attempt.

    Streaming is deliberately non-resumable: once bytes are on the wire the
    fallback chain is over, because a client that already received half an answer
    cannot be handed a different model's continuation. Fallback therefore happens
    entirely BEFORE the first chunk — combo_router only returns once a target has
    accepted the request.
    """
    start = time.perf_counter()
    requested = body.get("model")

    try:
        response, attempt, attempt_number = await combo_router.acompletion(
            db, user.id, combo, body, stream=stream,
        )
    except combo_router.ComboError as exc:
        log_v1_usage(
            user_id=user.id, requested_model=requested or combo.name,
            latency=round(time.perf_counter() - start, 3),
            status_code=exc.status, error_message=exc.message,
            combo_name=combo.name,
        )
        raise HTTPException(status_code=exc.status, detail=exc.message)

    if stream:
        async def combo_stream():
            try:
                async for chunk in response:
                    yield f"data: {json.dumps(_to_dict(chunk))}\n\n"
                yield "data: [DONE]\n\n"
            except Exception as exc:
                logger.warning(
                    "combo %r stream error (user %s): %s", combo.name, user.id, exc,
                )
                err = {"error": {"message": str(exc), "type": "gateway_error"}}
                yield f"data: {json.dumps(err)}\n\n"
                yield "data: [DONE]\n\n"

        # Usage is logged up front for streams: token counts are not known until
        # the stream ends, and a row written only on completion would lose every
        # request whose client disconnected mid-answer.
        log_v1_usage(
            user_id=user.id, requested_model=requested or combo.name,
            answered_deploy_id=attempt.deployment_id,
            latency=round(time.perf_counter() - start, 3), status_code=200,
            combo_name=combo.name, combo_attempt=attempt_number,
        )
        return StreamingResponse(combo_stream(), media_type="text/event-stream")

    result = _to_dict(response)
    log_v1_usage(
        user_id=user.id,
        requested_model=requested or combo.name,
        answered_deploy_id=attempt.deployment_id,
        usage=result.get("usage"),
        latency=round(time.perf_counter() - start, 3),
        status_code=200,
        combo_name=combo.name,
        combo_attempt=attempt_number,
    )
    # Non-standard, and useful: which target actually answered, and whether the
    # chain had to fall back to reach it. Clients ignore unknown keys.
    result["gateway_combo"] = {
        "name": combo.name,
        "strategy": combo.strategy,
        "target": attempt.target.describe(),
        "attempt": attempt_number,
    }
    return result


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

    # ── combos come FIRST ────────────────────────────────────────────────────
    # A combo name IS a model name to the client, and it must win over model
    # resolution: `resolve_requested_model` fuzzy-matches, so a combo called
    # "gpt-oss" could otherwise be silently served by the model gpt-oss-120b —
    # ignoring the ordering and account pinning that is the whole point of
    # having made the combo. Looked up exactly, by name, before anything else.
    combo = combo_router.get_combo(db, user.id, requested or "")
    if combo:
        return await _serve_combo(user, db, combo, body, stream=bool(body.get("stream", False)))

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
    start = time.perf_counter()

    # ── browser-session providers bypass litellm entirely ────────────────────
    # DeepSeek Web is not an OpenAI-compatible endpoint: it needs a per-request
    # proof-of-work and speaks a private SSE dialect, so litellm cannot carry it.
    # Branching here (rather than inside the router) keeps the surrounding
    # contract intact — the executor returns OpenAI-shaped objects, so logging,
    # streaming and error handling below are unchanged.
    ds_web = router_svc.non_litellm_deployment(
        user.id, resolved, db, deepseek_web.PROVIDER_SLUG
    )
    if ds_web:
        litellm_model, token = ds_web
        client = deepseek_web.DeepSeekWebClient(token)
        model_id = deepseek_web.strip_prefix(litellm_model)
        call_body = {k: v for k, v in body.items() if k not in ("model", "messages")}

        if stream:
            async def ds_stream():
                try:
                    async for chunk in client.stream_chat(body["messages"], model_id, call_body):
                        yield f"data: {json.dumps(chunk)}\n\n"
                    yield "data: [DONE]\n\n"
                except Exception as exc:
                    logger.warning("deepseek-web stream error (user %s): %s", user.id, exc)
                    err = {"error": {"message": str(exc), "type": "gateway_error"}}
                    yield f"data: {json.dumps(err)}\n\n"
                    yield "data: [DONE]\n\n"

            return StreamingResponse(ds_stream(), media_type="text/event-stream")

        try:
            result = await client.complete(body["messages"], model_id, call_body)
        except deepseek_web.DeepSeekWebError as exc:
            log_v1_usage(
                user_id=user.id, requested_model=requested,
                latency=round(time.perf_counter() - start, 3),
                status_code=exc.status, error_message=str(exc),
            )
            raise HTTPException(status_code=exc.status, detail=str(exc))
        log_v1_usage(
            user_id=user.id, requested_model=requested,
            latency=round(time.perf_counter() - start, 3), status_code=200,
        )
        return result

    lr = router_svc.get_router(user.id, db)

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


def _resolve_or_404(user: User, db: Session, requested: Optional[str]) -> str:
    """Map the client's model spelling onto one of the caller's live families."""
    available = router_svc.list_models(user.id, db)
    resolved = resolve_requested_model(requested or "", set(available))
    if not resolved:
        raise HTTPException(
            status_code=404,
            detail=f"Model {requested!r} is not available to you. Yours: {available}",
        )
    return resolved


@router.post("/v1/images/generations")
async def image_generations(
    request: Request,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    """Image generation, served from THIS USER's live image deployments."""
    try:
        body: Dict[str, Any] = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body.")

    if not body.get("prompt"):
        raise HTTPException(status_code=400, detail="'prompt' is required.")

    requested = body.get("model")
    body["model"] = _resolve_or_404(user, db, requested)

    start = time.perf_counter()

    # NVIDIA's image models are NVCF cloud functions — a surface litellm cannot
    # speak (see services/nvcf.py). Their deployments carry litellm_model
    # 'nvcf/<id>', so route them through the adapter with the caller's own key.
    nvcf_dep = db.execute(
        select(v_live_deployments).where(
            v_live_deployments.c.user_id == user.id,
            v_live_deployments.c.model == body["model"],
            v_live_deployments.c.litellm_model.like("nvcf/%"),
        )
    ).mappings().first()
    if nvcf_dep:
        pk = db.query(ProviderKey).filter(
            ProviderKey.id == nvcf_dep["provider_key_id"]
        ).first()
        try:
            result = await nvcf_svc.generate_image(
                nvcf_dep["litellm_model"], crypto.decrypt(pk.key_ciphertext),
                body["prompt"], size=body.get("size"), steps=body.get("steps"),
            )
        except Exception as exc:
            router_svc.record_failure(nvcf_dep["deployment_id"], exc)
            log_v1_usage(
                user_id=user.id, requested_model=requested,
                latency=round(time.perf_counter() - start, 3),
                status_code=502, error_message=str(exc),
            )
            logger.warning("nvcf image generation failed (user %s): %s", user.id, exc)
            raise HTTPException(status_code=502, detail=str(exc))
        router_svc.record_success(nvcf_dep["deployment_id"])
        log_v1_usage(
            user_id=user.id, requested_model=requested,
            answered_deploy_id=nvcf_dep["deployment_id"],
            latency=round(time.perf_counter() - start, 3), status_code=200,
        )
        return {"created": int(time.time()), **result}

    lr = router_svc.get_router(user.id, db)
    try:
        response = await lr.aimage_generation(**body)
    except Exception as exc:
        log_v1_usage(
            user_id=user.id, requested_model=requested,
            latency=round(time.perf_counter() - start, 3),
            status_code=502, error_message=str(exc),
        )
        logger.warning("image generation failed (user %s): %s", user.id, exc)
        raise HTTPException(status_code=502, detail=str(exc))

    result = _to_dict(response)
    dep_id = _deployment_id(response)
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


@router.post("/v1/audio/transcriptions")
async def audio_transcriptions(
    file: UploadFile = File(...),
    model: Optional[str] = Form(None),
    language: Optional[str] = Form(None),
    prompt: Optional[str] = Form(None),
    response_format: Optional[str] = Form(None),
    temperature: Optional[float] = Form(None),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    """Speech-to-text (multipart, OpenAI-shaped), from the user's audio deployments."""
    resolved = _resolve_or_404(user, db, model)

    # litellm hands the upload to the provider SDK, which reads the filename
    # (and thus the format) off the stream — so it must carry a name.
    raw = await file.read()
    buf = io.BytesIO(raw)
    buf.name = file.filename or "audio.wav"

    kwargs: Dict[str, Any] = {"model": resolved, "file": buf}
    if language:
        kwargs["language"] = language
    if prompt:
        kwargs["prompt"] = prompt
    if response_format:
        kwargs["response_format"] = response_format
    if temperature is not None:
        kwargs["temperature"] = temperature

    lr = router_svc.get_router(user.id, db)
    start = time.perf_counter()
    try:
        response = await lr.atranscription(**kwargs)
    except Exception as exc:
        log_v1_usage(
            user_id=user.id, requested_model=model,
            latency=round(time.perf_counter() - start, 3),
            status_code=502, error_message=str(exc),
        )
        logger.warning("transcription failed (user %s): %s", user.id, exc)
        raise HTTPException(status_code=502, detail=str(exc))

    result = _to_dict(response)
    dep_id = _deployment_id(response)
    if dep_id:
        router_svc.record_success(dep_id)
    log_v1_usage(
        user_id=user.id,
        requested_model=model,
        answered_deploy_id=dep_id,
        usage=result.get("usage"),
        latency=round(time.perf_counter() - start, 3),
        status_code=200,
    )
    return result


# What /v1/audio/speech hands back per response_format. Anything unlisted
# falls through to audio/mpeg — OpenAI's own default is mp3.
_SPEECH_MEDIA_TYPES = {
    "mp3": "audio/mpeg", "opus": "audio/opus", "aac": "audio/aac",
    "flac": "audio/flac", "wav": "audio/wav", "pcm": "audio/pcm",
}


@router.post("/v1/audio/speech")
async def audio_speech(
    request: Request,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    """Text-to-speech. Returns raw audio bytes, like OpenAI's endpoint."""
    try:
        body: Dict[str, Any] = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body.")

    if not body.get("input"):
        raise HTTPException(status_code=400, detail="'input' is required.")
    if not body.get("voice"):
        # Voices are provider-specific; guessing one would 400 upstream anyway.
        raise HTTPException(status_code=400, detail="'voice' is required.")

    requested = body.get("model")
    body["model"] = _resolve_or_404(user, db, requested)

    lr = router_svc.get_router(user.id, db)
    start = time.perf_counter()
    try:
        response = await lr.aspeech(**body)
        audio = response.read() if hasattr(response, "read") else bytes(response.content)
    except Exception as exc:
        log_v1_usage(
            user_id=user.id, requested_model=requested,
            latency=round(time.perf_counter() - start, 3),
            status_code=502, error_message=str(exc),
        )
        logger.warning("speech failed (user %s): %s", user.id, exc)
        raise HTTPException(status_code=502, detail=str(exc))

    dep_id = _deployment_id(response)
    if dep_id:
        router_svc.record_success(dep_id)
    log_v1_usage(
        user_id=user.id,
        requested_model=requested,
        answered_deploy_id=dep_id,
        latency=round(time.perf_counter() - start, 3),
        status_code=200,
    )
    media = _SPEECH_MEDIA_TYPES.get(
        str(body.get("response_format", "mp3")).lower(), "audio/mpeg"
    )
    return Response(content=audio, media_type=media)
