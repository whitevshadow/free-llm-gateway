"""
Recording health transitions for the timeline (SRS §20).

One function, called from the three places that can change a deployment's
status: the prober (services/prober.py) and the two live-traffic write-backs
(core/llm_router.py record_success / record_failure).

The whole design is in `record_transition`'s first branch: if the status did not
change, nothing is written. That single guard is what keeps this table a
timeline rather than a log — the re-probe loop sweeps every unhealthy deployment
every 20 minutes (SRS §10), and without it a deployment that has been dead for a
week produces ~500 identical rows saying so.

Follows the same rule as the rest of the bookkeeping path (SRS §22): a failure
here is logged and swallowed. Never let recording history break a completion.
"""

import logging
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy.orm import Session

from app.models.deployment import Deployment
from app.models.deployment_status_event import DeploymentStatusEvent
from app.models.enums import ModelHealth

logger = logging.getLogger(__name__)


def record_transition(
    db: Session,
    deployment: Deployment,
    new_status: ModelHealth,
    *,
    source: str,
    http_code: Optional[int] = None,
    latency_ms: Optional[int] = None,
    error: Optional[str] = None,
    cooldown_until: Optional[datetime] = None,
) -> bool:
    """
    Append a status change for `deployment`, if it IS a change.

    Must be called BEFORE `deployment.status` is reassigned — it reads the old
    value off the instance to fill `from_status`.

    Returns True if an event was written. The caller flushes/commits; this never
    commits on its own, so the transition and the status write land in the same
    transaction and cannot disagree.
    """
    try:
        old_status = deployment.status

        # The guard that makes this a timeline. See the module docstring.
        if old_status is new_status:
            return False

        db.add(
            DeploymentStatusEvent(
                deployment_id=deployment.id,
                user_id=deployment.user_id,
                from_status=old_status,
                to_status=new_status,
                source=source,
                http_code=http_code,
                latency_ms=latency_ms,
                error=(error or None) if error is None else error[:500],
                cooldown_until=cooldown_until,
                created_at=datetime.now(timezone.utc),
            )
        )
        return True
    except Exception as exc:
        # Never let bookkeeping break a completion (SRS §22).
        logger.warning(
            "Could not record status transition for deployment %s: %s",
            getattr(deployment, "id", "?"), exc,
        )
        return False
