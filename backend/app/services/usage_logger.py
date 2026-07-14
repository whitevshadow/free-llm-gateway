"""
Usage logging for the /v1 endpoints.

Opens its own short-lived session and writes a best-effort RequestLog row. It
NEVER raises: analytics must never break a completion.

Per-key attribution is the point. Given the answering deployment id, we resolve
the provider and the exact KEY that served the request — which is what lets you
see per-key burn and spot an exhausted free tier.

total_tokens is NOT written: it is a GENERATED column (prompt + completion), so
the DB computes it and it cannot be logged wrong.
"""

import logging
from typing import Any, Dict, Optional

from app.core.database import SessionLocal
from app.models.deployment import Deployment
from app.models.request_log import RequestLog

logger = logging.getLogger("gateway.usage")


def log_v1_usage(
    *,
    user_id: int,
    requested_model: str,
    answered_deploy_id: Optional[int] = None,
    usage: Optional[Dict[str, Any]] = None,
    cost: float = 0.0,
    latency: float = 0.0,
    status_code: int = 200,
    error_message: Optional[str] = None,
) -> None:
    """Persist one usage row. Best-effort; swallows all errors."""
    usage = usage or {}
    try:
        db = SessionLocal()
        try:
            provider_id = provider_key_id = provider_model_id = None
            if answered_deploy_id:
                dep = (
                    db.query(Deployment)
                    .filter(Deployment.id == answered_deploy_id)
                    .first()
                )
                if dep:
                    provider_id = dep.provider_id
                    provider_key_id = dep.provider_key_id
                    provider_model_id = dep.provider_model_id

            db.add(
                RequestLog(
                    user_id=user_id,
                    requested_model=requested_model,
                    answered_deploy_id=answered_deploy_id,
                    provider_id=provider_id,
                    provider_key_id=provider_key_id,
                    provider_model_id=provider_model_id,
                    prompt_tokens=int(usage.get("prompt_tokens", 0) or 0),
                    completion_tokens=int(usage.get("completion_tokens", 0) or 0),
                    # total_tokens is GENERATED — do not write it.
                    cost=float(cost or 0.0),
                    latency_ms=int(round((latency or 0.0) * 1000)),
                    status_code=status_code,
                    error_message=error_message,
                )
            )
            db.commit()
        finally:
            db.close()
    except Exception as exc:  # pragma: no cover — logging must never break a call
        logger.debug("usage log failed: %s", exc)
