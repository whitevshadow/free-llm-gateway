"""
Fallback Service — automatic failover when a provider goes down.

HOW IT WORKS:
  1. Try the primary provider/model.
  2. If it fails, look up the FALLBACK_CHAINS in the provider registry.
  3. Try each fallback in order until one succeeds.
  4. If ALL fail, raise AllProvidersFailedError.

WHY THIS MATTERS:
  In production, providers go down. OpenAI hits rate limits at peak hours,
  Anthropic has maintenance windows, DeepSeek may throttle heavy users.
  A gateway without fallback is a single point of failure. With fallback,
  your users never see a 5xx — they just get a response from a different
  model, and the response envelope tells them which one answered.
"""

import logging
from typing import Dict, Any, List, Optional

from app.providers import get_fallbacks
from app.services.llm_service import generate_completion
from app.exceptions import AllProvidersFailedError

logger = logging.getLogger("gateway.fallback")


def completion_with_fallback(
    provider: str,
    model: str,
    messages: List[Dict[str, str]],
    temperature: Optional[float] = None,
    max_tokens: Optional[int] = None,
) -> Dict[str, Any]:
    """
    Attempt completion on the primary provider; on failure, cascade through
    the fallback chain defined in the provider registry.

    Returns the same normalised result dict as generate_completion(), with
    extra keys when a fallback was used:
      fallback_used: True
      original_provider: "..."
      original_model: "..."
    """
    # ── 1. Try primary ──────────────────────────────────
    logger.info("fallback_primary_attempt", extra={"provider": provider, "model": model})

    result = generate_completion(
        provider=provider,
        model=model,
        messages=messages,
        temperature=temperature,
        max_tokens=max_tokens,
    )

    if result["success"]:
        return result

    logger.warning(
        "fallback_primary_failed",
        extra={"provider": provider, "model": model, "error": result["error"]},
    )

    # ── 2. Try fallbacks ────────────────────────────────
    fallbacks = get_fallbacks(provider)
    if not fallbacks:
        logger.error("fallback_no_chain", extra={"provider": provider})
        raise AllProvidersFailedError(
            f"Primary '{provider}/{model}' failed ({result['error']}) and no fallback chain is configured."
        )

    errors_collected = [f"{provider}/{model}: {result['error']}"]

    for fb in fallbacks:
        fb_provider = fb["provider"]
        fb_model = fb["model"]
        logger.info("fallback_attempt", extra={"provider": fb_provider, "model": fb_model})

        fb_result = generate_completion(
            provider=fb_provider,
            model=fb_model,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
        )

        if fb_result["success"]:
            logger.info(
                "fallback_success",
                extra={"provider": fb_provider, "model": fb_model},
            )
            fb_result["fallback_used"] = True
            fb_result["original_provider"] = provider
            fb_result["original_model"] = model
            return fb_result

        errors_collected.append(f"{fb_provider}/{fb_model}: {fb_result['error']}")
        logger.warning(
            "fallback_attempt_failed",
            extra={"provider": fb_provider, "model": fb_model, "error": fb_result["error"]},
        )

    # ── 3. All failed ───────────────────────────────────
    all_errors = " | ".join(errors_collected)
    raise AllProvidersFailedError(all_errors)
