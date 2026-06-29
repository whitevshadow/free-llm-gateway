"""
LLM Service — the core engine that talks to AI providers via LiteLLM.

ARCHITECTURE:
  Route handlers NEVER call litellm directly. They call this service, which:
    1. Resolves the LiteLLM model string via the provider registry.
    2. Injects provider API keys into the environment.
    3. Calls litellm.completion() with timeout and retry logic.
    4. Returns a UNIFORM result dict regardless of which provider answered.

WHY SEPARATE THIS:
  If LiteLLM ever changes its API, or if we switch to a different abstraction
  library, we only change THIS FILE. Every route and service above it stays
  untouched. This is the Dependency Inversion Principle in action.
"""

import os
import time
import logging
from typing import Dict, Any, List, Optional, AsyncGenerator

import litellm

from app.core.config import settings
from app.core.llm_router import get_router, list_virtual_models
from app.providers import resolve_litellm_model
from app.exceptions import (
    ProviderNotFoundError,
    LLMCompletionError,
    LLMTimeoutError,
)

logger = logging.getLogger("gateway.llm")

# ───────────────────────────────────────────────────────────────
# ENVIRONMENT SETUP  (runs once at import time)
# ───────────────────────────────────────────────────────────────
# LiteLLM reads API keys from os.environ. We bridge our pydantic
# Settings into the environment so LiteLLM can find them.

_KEY_MAP = {
    "OPENAI_API_KEY":    settings.OPENAI_API_KEY,
    "ANTHROPIC_API_KEY": settings.ANTHROPIC_API_KEY,
    "GEMINI_API_KEY":    settings.GEMINI_API_KEY,
    "DEEPSEEK_API_KEY":  settings.DEEPSEEK_API_KEY,
    "XAI_API_KEY":       settings.XAI_API_KEY,
    "NVIDIA_API_KEY":    settings.NVIDIA_NIM_API_KEY,
}

for env_var, value in _KEY_MAP.items():
    if value:
        os.environ[env_var] = value

# LiteLLM global settings
# (set_verbose is deprecated; use the LITELLM_LOG env var for debug logs instead)
if settings.DEBUG:
    os.environ.setdefault("LITELLM_LOG", "INFO")
litellm.request_timeout = settings.REQUEST_TIMEOUT


# ───────────────────────────────────────────────────────────────
# RESPONSE NORMALISATION
# ───────────────────────────────────────────────────────────────

def _normalize_response(response, provider: str, model: str, latency: float) -> Dict[str, Any]:
    """Convert a LiteLLM/Router ModelResponse into our uniform result dict."""
    usage = getattr(response, "usage", None)
    prompt_tokens = getattr(usage, "prompt_tokens", 0) if usage else 0
    completion_tokens = getattr(usage, "completion_tokens", 0) if usage else 0
    total_tokens = getattr(usage, "total_tokens", 0) if usage else 0
    content = response.choices[0].message.content or ""

    try:
        cost = litellm.completion_cost(completion_response=response)
    except Exception:
        cost = 0.0

    # The Router hides which concrete deployment answered behind `response.model`.
    answered_model = getattr(response, "model", model) or model

    return {
        "success": True,
        "content": content,
        "provider": provider,
        "model": answered_model,
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": total_tokens,
        "cost": cost or 0.0,
        "latency": latency,
        "error": None,
    }


# ───────────────────────────────────────────────────────────────
# COMPLETION (synchronous, with retry)
# ───────────────────────────────────────────────────────────────

def generate_completion(
    provider: str,
    model: str,
    messages: List[Dict[str, str]],
    temperature: Optional[float] = None,
    max_tokens: Optional[int] = None,
) -> Dict[str, Any]:
    """
    Send a chat-completion request to a single provider.

    Returns a normalised result dict with keys:
      success, content, provider, model, prompt_tokens, completion_tokens,
      total_tokens, cost, latency, error

    RETRY LOGIC:
      We retry up to settings.MAX_RETRIES times on transient errors
      (timeouts, 5xx from provider). This is standard practice — even
      OpenAI's official SDK retries automatically.
    """
    temperature = temperature if temperature is not None else settings.DEFAULT_TEMPERATURE
    max_tokens = max_tokens or settings.DEFAULT_MAX_TOKENS

    # ── Smart Router path ───────────────────────────────
    # When `model` is one of the virtual models in the pool, route through the
    # shared litellm.Router so the UI benefits from the SAME load balancing,
    # 429 cooldowns and fallbacks as the /v1 endpoints. The Router does its own
    # retries/fallbacks, so there is no manual retry loop here.
    if model in list_virtual_models():
        start_time = time.perf_counter()
        try:
            response = get_router().completion(
                model=model,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
            )
            latency = round(time.perf_counter() - start_time, 3)
            return _normalize_response(response, provider="router", model=model, latency=latency)
        except Exception as exc:
            logger.warning("router_completion_failed", extra={"model": model, "error": str(exc)})
            return {
                "success": False, "content": "", "provider": "router", "model": model,
                "prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0,
                "cost": 0.0, "latency": round(time.perf_counter() - start_time, 3),
                "error": str(exc),
            }

    # ── Legacy direct path ──────────────────────────────
    # Resolve the LiteLLM-compatible model string
    litellm_model = resolve_litellm_model(provider, model)
    if not litellm_model:
        raise ProviderNotFoundError(provider)

    last_error: Optional[str] = None

    for attempt in range(1, settings.MAX_RETRIES + 2):  # +2 because range is exclusive and attempt 1 = first try
        start_time = time.perf_counter()
        try:
            logger.info(
                "llm_request_start",
                extra={
                    "provider": provider,
                    "model": model,
                    "litellm_model": litellm_model,
                    "attempt": attempt,
                },
            )

            response = litellm.completion(
                model=litellm_model,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
                timeout=settings.REQUEST_TIMEOUT,
            )

            latency = round(time.perf_counter() - start_time, 3)

            # Extract usage safely
            usage = getattr(response, "usage", None)
            prompt_tokens = usage.prompt_tokens if usage else 0
            completion_tokens = usage.completion_tokens if usage else 0
            total_tokens = usage.total_tokens if usage else 0
            content = response.choices[0].message.content or ""

            # Cost estimation via LiteLLM
            try:
                cost = litellm.completion_cost(completion_response=response)
            except Exception:
                cost = 0.0

            logger.info(
                "llm_request_success",
                extra={
                    "provider": provider,
                    "model": model,
                    "tokens": total_tokens,
                    "latency": latency,
                    "cost": cost,
                    "attempt": attempt,
                },
            )

            return {
                "success": True,
                "content": content,
                "provider": provider,
                "model": model,
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "total_tokens": total_tokens,
                "cost": cost or 0.0,
                "latency": latency,
                "error": None,
            }

        except litellm.exceptions.Timeout:
            latency = round(time.perf_counter() - start_time, 3)
            last_error = f"Timeout after {settings.REQUEST_TIMEOUT}s"
            logger.warning(
                "llm_request_timeout",
                extra={"provider": provider, "model": model, "attempt": attempt, "latency": latency},
            )

        except Exception as exc:
            latency = round(time.perf_counter() - start_time, 3)
            last_error = str(exc)
            logger.warning(
                "llm_request_error",
                extra={"provider": provider, "model": model, "attempt": attempt, "error": last_error, "latency": latency},
            )

        # Only retry on transient errors, break immediately on auth/validation errors
        if last_error and any(kw in last_error.lower() for kw in ["authentication", "invalid api key", "unauthorized"]):
            break

    # All attempts exhausted
    return {
        "success": False,
        "content": "",
        "provider": provider,
        "model": model,
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "total_tokens": 0,
        "cost": 0.0,
        "latency": 0.0,
        "error": last_error,
    }


# ───────────────────────────────────────────────────────────────
# STREAMING COMPLETION (async generator)
# ───────────────────────────────────────────────────────────────

async def generate_completion_stream(
    provider: str,
    model: str,
    messages: List[Dict[str, str]],
    temperature: Optional[float] = None,
    max_tokens: Optional[int] = None,
) -> AsyncGenerator[Dict[str, Any], None]:
    """
    Async streaming completion. Yields chunk dicts suitable for SSE.

    Each chunk: { "content": "...", "done": bool, "error": None }
    Final chunk has done=True.
    """
    temperature = temperature if temperature is not None else settings.DEFAULT_TEMPERATURE
    max_tokens = max_tokens or settings.DEFAULT_MAX_TOKENS

    # Virtual models stream through the shared Router; everything else uses a
    # direct LiteLLM call via the legacy MODEL_MAP.
    use_router = model in list_virtual_models()
    if not use_router:
        litellm_model = resolve_litellm_model(provider, model)
        if not litellm_model:
            yield {"content": "", "done": True, "error": f"Unknown provider/model: {provider}/{model}"}
            return

    try:
        if use_router:
            response = await get_router().acompletion(
                model=model,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
                stream=True,
            )
        else:
            response = await litellm.acompletion(
                model=litellm_model,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
                stream=True,
                timeout=settings.REQUEST_TIMEOUT,
            )

        async for chunk in response:
            delta = chunk.choices[0].delta
            content = getattr(delta, "content", "") or ""
            done = chunk.choices[0].finish_reason is not None

            yield {"content": content, "done": done, "error": None}

    except Exception as exc:
        logger.error("llm_stream_error", extra={"provider": provider, "model": model, "error": str(exc)})
        yield {"content": "", "done": True, "error": str(exc)}
