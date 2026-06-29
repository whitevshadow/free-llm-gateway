"""
Unified response builders.

WHY STANDARDIZE RESPONSES:
  Every production API returns a predictable JSON envelope. Clients should never
  have to guess the shape of your response. Whether the call succeeds or fails,
  the structure is the same — only the content differs.

  This is exactly how the OpenAI, Stripe, and GitHub APIs work:
    • success → { "status": "success", "data": { ... } }
    • error   → { "status": "error",   "error": { "code": "...", "message": "..." } }
"""

from typing import Any, Dict, Optional


def success_response(
    data: Any,
    message: str = "Request completed successfully",
    meta: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Build a standardised success envelope."""
    response = {
        "status": "success",
        "message": message,
        "data": data,
    }
    if meta:
        response["meta"] = meta
    return response


def error_response(
    message: str,
    error_code: str = "INTERNAL_ERROR",
    status_code: int = 500,
    details: Optional[Any] = None,
) -> Dict[str, Any]:
    """Build a standardised error envelope."""
    response = {
        "status": "error",
        "error": {
            "code": error_code,
            "message": message,
        },
    }
    if details:
        response["error"]["details"] = details
    return response


def llm_response(
    provider: str,
    model: str,
    content: str,
    prompt_tokens: int = 0,
    completion_tokens: int = 0,
    total_tokens: int = 0,
    cost: float = 0.0,
    latency: float = 0.0,
    fallback_used: bool = False,
    original_provider: Optional[str] = None,
    original_model: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Build the unified LLM completion response.

    This is THE standard format every provider returns, no matter if it was
    OpenAI, DeepSeek, Gemini, or a fallback. The frontend can always rely on
    these exact keys being present.
    """
    result = {
        "provider": provider,
        "model": model,
        "response": content,
        "tokens_used": {
            "prompt": prompt_tokens,
            "completion": completion_tokens,
            "total": total_tokens,
        },
        "cost_usd": round(cost, 6),
        "latency_seconds": round(latency, 3),
    }
    if fallback_used:
        result["fallback"] = {
            "used": True,
            "original_provider": original_provider,
            "original_model": original_model,
        }
    return result
