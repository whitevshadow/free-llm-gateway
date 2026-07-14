"""
Scheduler — the two background loops the gateway runs (SRS §10).

Discovery and probing must NEVER happen during a user request; everything here
runs detached, started once from the app's lifespan.

  DISCOVERY LOOP (daily)
      Refresh every enabled provider's catalog, then fan the catalog out to
      every active key (idempotent — only NEW models create deployments). New
      deployments start 'unavailable' and are promoted by the re-probe loop.
      Providers are refreshed individually inside discover_all, so one
      provider's outage cannot block the others, and a failed discovery keeps
      the last known model list (fetch returns [] and the provider is skipped).

  RE-PROBE LOOP (every ~20 minutes)
      Re-probe UNHEALTHY deployments only: error / timeout / unavailable.
      What it deliberately does NOT touch:
        available     — real traffic keeps them fresh (record_success/failure);
                        probing them would spend free-tier quota on nothing.
        rate_limited  — the cooldown already self-heals via is_callable();
                        probing early would ADD a request to a throttled key.
        auth_error    — dead keys are never resurrected by a timer. The user
                        replacing the key (or a manual re-probe) revives them.

Both loops sleep FIRST: startup already has add-key probes and admin syncs in
flight, and piling scheduled work on top would rate-limit the keys being added.
One iteration failing is logged and skipped — the loop itself must survive.
"""

import asyncio
import logging
from typing import Dict, List

from app.core.config import settings
from app.core.database import SessionLocal
from app.models.deployment import Deployment
from app.models.enums import ModelHealth
from app.models.provider_key import ProviderKey

logger = logging.getLogger("gateway.scheduler")

# The re-probe loop's first pass runs shortly after startup (not a full interval
# later) so deployments that went stale while the process was down recover fast.
FIRST_REPROBE_DELAY = 90  # seconds

# Statuses the re-probe loop retries. See the module docstring for why
# available / rate_limited / auth_error are excluded.
_REPROBE_STATUSES = (ModelHealth.error, ModelHealth.timeout, ModelHealth.unavailable)

_tasks: List[asyncio.Task] = []


async def _discovery_pass() -> None:
    """Refresh every provider's catalog, then fan out new models to all keys."""
    from app.core import llm_router
    from app.services import catalog, key_store

    db = SessionLocal()
    try:
        results = catalog.discover_all(db)
        logger.info("Scheduled discovery: %s", results)

        # Fan out so existing keys gain deployments for newly discovered models.
        # Idempotent per key — models that already have a deployment are skipped.
        created = 0
        for key in db.query(ProviderKey).filter(ProviderKey.is_active.is_(True)):
            created += key_store.fan_out(db, key)
        if created:
            logger.info("Scheduled discovery created %d new deployment(s); "
                        "the re-probe loop will promote them.", created)
    finally:
        db.close()

    # Catalogs changed under every cached Router; rebuild them on next request.
    llm_router.invalidate()


async def _reprobe_pass() -> None:
    """Re-probe every unhealthy deployment, one user at a time."""
    from app.core import llm_router
    from app.services import prober

    db = SessionLocal()
    try:
        rows = (
            db.query(Deployment.user_id, Deployment.id)
            .filter(Deployment.status.in_(_REPROBE_STATUSES))
            .all()
        )
    finally:
        db.close()

    if not rows:
        return

    by_user: Dict[int, List[int]] = {}
    for user_id, dep_id in rows:
        by_user.setdefault(user_id, []).append(dep_id)

    # Sequential per user: probe_deployments already caps in-flight probes at
    # MAX_CONCURRENCY, and running users in parallel would multiply the load
    # pointed at providers several users share.
    for user_id, dep_ids in by_user.items():
        result = await prober.probe_deployments(dep_ids)
        llm_router.invalidate(user_id)
        logger.info("Scheduled re-probe for user %s: %s", user_id, result)


async def _loop(name: str, interval: float, pass_fn, first_delay: float) -> None:
    """Run `pass_fn` every `interval` seconds, forever. Failures are logged, not fatal."""
    await asyncio.sleep(first_delay)
    while True:
        try:
            await pass_fn()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Scheduled %s pass failed; next run in %ds.", name, interval)
        await asyncio.sleep(interval)


def start() -> None:
    """Launch both loops. Called once from the app lifespan; no-op if disabled."""
    if not settings.SCHEDULER_ENABLED:
        logger.info("Scheduler disabled (SCHEDULER_ENABLED=false).")
        return
    if _tasks:
        return  # already running

    discovery_interval = settings.DISCOVERY_INTERVAL_HOURS * 3600
    reprobe_interval = settings.REPROBE_INTERVAL_MINUTES * 60

    _tasks.append(asyncio.create_task(
        _loop("discovery", discovery_interval, _discovery_pass,
              first_delay=discovery_interval),
        name="scheduler-discovery",
    ))
    _tasks.append(asyncio.create_task(
        _loop("re-probe", reprobe_interval, _reprobe_pass,
              first_delay=FIRST_REPROBE_DELAY),
        name="scheduler-reprobe",
    ))
    logger.info(
        "Scheduler started: discovery every %dh, unhealthy re-probe every %dmin.",
        settings.DISCOVERY_INTERVAL_HOURS, settings.REPROBE_INTERVAL_MINUTES,
    )


async def stop() -> None:
    """Cancel both loops. Called from the app lifespan on shutdown."""
    for task in _tasks:
        task.cancel()
    for task in _tasks:
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass
    _tasks.clear()
