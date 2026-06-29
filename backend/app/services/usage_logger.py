"""
Usage logging for the /v1 compatibility endpoints.

The UI chat path logs token usage inside the request handler (it already has a DB
session + user). The /v1 endpoints are stateless and user-less, so this helper
opens its own short-lived session and writes a best-effort TokenUsageLog row. It
never raises — analytics must never break a completion.
"""

import logging
from typing import Any, Dict, Optional

from app.core.database import SessionLocal
from app.models.analytics import TokenUsageLog

logger = logging.getLogger("gateway.usage")


def log_v1_usage(
    *,
    model: str,
    usage: Optional[Dict[str, Any]] = None,
    provider: str = "router",
    cost: float = 0.0,
    latency: float = 0.0,
    status_code: int = 200,
    error_message: Optional[str] = None,
) -> None:
    """Persist one usage row for a /v1 call. Best-effort; swallows all errors."""
    usage = usage or {}
    try:
        db = SessionLocal()
        try:
            db.add(
                TokenUsageLog(
                    user_id=None,
                    provider=provider,
                    model=model,
                    prompt_tokens=int(usage.get("prompt_tokens", 0) or 0),
                    completion_tokens=int(usage.get("completion_tokens", 0) or 0),
                    total_tokens=int(usage.get("total_tokens", 0) or 0),
                    cost=float(cost or 0.0),
                    latency=float(latency or 0.0),
                    status_code=status_code,
                    error_message=error_message,
                )
            )
            db.commit()
        finally:
            db.close()
    except Exception as exc:  # pragma: no cover - logging must never break a call
        logger.debug("usage log failed: %s", exc)
