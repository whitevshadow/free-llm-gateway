"""
Pipeline — run discovery → probe → derive → router reload as one step.

This is the refresh cycle that keeps the common-model spine current: it imports
providers/keys/models (Phase 3), tests every key (Phase 4), regroups working
models into common models (Phase 5), and rebuilds the Router (Phase 6). Safe to
run on a schedule or on demand from the admin API.
"""

import logging
from typing import Optional

from app.services import catalog, prober, derive_common

logger = logging.getLogger("gateway.pipeline")


def refresh(ping: Optional[object] = None) -> dict:
    """Full refresh. `ping` overrides the probe function (for tests)."""
    disc = catalog.discover()
    prb = prober.probe(ping=ping) if ping else prober.probe()
    der = derive_common.derive()

    try:
        from app.core.llm_router import reload_router
        reload_router()
    except Exception as exc:  # pragma: no cover
        logger.warning("Router reload after refresh failed: %s", exc)

    result = {"discovery": disc, "probe": prb, "derive": der}
    logger.info("Pipeline refresh complete: %s", result)
    return result
