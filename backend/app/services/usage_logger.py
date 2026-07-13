"""
Usage logging for the /v1 compatibility endpoints.

The /v1 endpoints are stateless, so this helper opens its own short-lived session
and writes a best-effort RequestLog row. It never raises — analytics must never
break a completion.

The model/key foreign keys (common_model_id, answered_deploy_id, provider_key_id,
provider_id) are left NULL here; Phase 7 enriches them by mapping the answering
deployment back from the Router response.
"""

import logging
from typing import Any, Dict, Optional

from app.core.database import SessionLocal
from app.models.request_log import RequestLog

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
            # Best-effort: if the requested model is a known common model, link it.
            common_model_id = None
            try:
                from app.models.common_model import CommonModel
                cm = db.query(CommonModel).filter(CommonModel.name == model).first()
                common_model_id = cm.id if cm else None
            except Exception:
                pass

            db.add(
                RequestLog(
                    requested_model=model,
                    common_model_id=common_model_id,
                    prompt_tokens=int(usage.get("prompt_tokens", 0) or 0),
                    completion_tokens=int(usage.get("completion_tokens", 0) or 0),
                    total_tokens=int(usage.get("total_tokens", 0) or 0),
                    cost=float(cost or 0.0),
                    latency_ms=int(round((latency or 0.0) * 1000)),
                    status_code=status_code,
                    error_message=error_message,
                )
            )
            db.commit()
        finally:
            db.close()
    except Exception as exc:  # pragma: no cover - logging must never break a call
        logger.debug("usage log failed: %s", exc)
