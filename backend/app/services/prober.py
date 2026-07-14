"""
Prober — test whether a specific (model × key) pair actually works.

Health is PER KEY, so the unit of probing is a DEPLOYMENT, not a model. One
exhausted Groq key must be benched without touching its sibling.

CONCURRENCY IS CAPPED, and that is not an optimisation. Adding one Groq key fans
out ~30 deployments; probing them all at once fires 30 simultaneous requests at a
free tier and rate-limits the very key we are trying to validate. We probe a few
at a time.

A 429 IS NOT A FAILURE. It means the key is valid and merely throttled — it earns
a cooldown, not a death sentence. An auth error is the opposite: no cooldown will
ever fix it. Collapsing those two into one boolean is exactly what the
`model_health` enum exists to prevent.

Runs in the BACKGROUND: adding a key returns immediately with its deployments
marked 'unavailable', and this promotes them as results land.
"""

import asyncio
import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple

import litellm
from sqlalchemy.orm import Session

from app.core.database import SessionLocal
from app.models.deployment import Deployment
from app.models.enums import ModelHealth, ModelMode
from app.models.provider import Provider
from app.models.provider_key import ProviderKey
from app.models.provider_model import ProviderModel
from app.services import crypto

logger = logging.getLogger("gateway.prober")

# How many probes may be in flight at once. Deliberately small: these all hit the
# SAME provider with the SAME key, so this is literally the number of concurrent
# requests we are pointing at one free-tier account.
MAX_CONCURRENCY = 4

PROBE_TIMEOUT = 20            # seconds per probe
RATE_LIMIT_COOLDOWN = 60      # seconds to bench a deployment that 429s while probing


# ── Probe progress, per user ─────────────────────────────────────────────────
#  Probes run in the background, so without this the UI has no way to say "how
#  far along" — only "still going". In-memory is the right weight: progress is
#  ephemeral, single-process, and worthless after a restart anyway.
#
#  Concurrent jobs for one user (e.g. Discover re-probing two keys = two
#  background tasks) AGGREGATE: totals add up, ticks share one counter, so the
#  bar reads "212 models" instead of restarting at 106 halfway through.
_progress: Dict[int, Dict[str, float]] = {}

# If a job crashes mid-flight, done never reaches total. Rather than a bar stuck
# at 97% forever, a job with no tick for this long is reported as inactive.
_STALE_AFTER = 90.0  # seconds


def _job_start(user_id: int, count: int) -> None:
    now = time.time()
    p = _progress.get(user_id)
    if p and p["done"] < p["total"] and (now - p["updated"]) < _STALE_AFTER:
        p["total"] += count          # another batch joined a running job
        p["updated"] = now
    else:
        _progress[user_id] = {"total": float(count), "done": 0.0, "updated": now}


def _job_tick(user_id: int) -> None:
    p = _progress.get(user_id)
    if p:
        p["done"] += 1
        p["updated"] = time.time()


def get_progress(user_id: int) -> Dict:
    """What GET /v1/me/probe-status returns. active=False once done or stale."""
    p = _progress.get(user_id)
    if not p:
        return {"active": False, "total": 0, "done": 0}
    stale = (time.time() - p["updated"]) > _STALE_AFTER
    done, total = int(p["done"]), int(p["total"])
    return {"active": done < total and not stale, "total": total, "done": done}


def _classify(exc: Exception) -> Tuple[ModelHealth, Optional[int], str]:
    """Map a litellm exception onto our health enum. These distinctions matter."""
    name = type(exc).__name__
    msg = str(exc)
    code = getattr(exc, "status_code", None)

    if isinstance(exc, litellm.RateLimitError) or code == 429:
        return ModelHealth.rate_limited, 429, msg     # key WORKS, just throttled
    if isinstance(exc, litellm.AuthenticationError) or code in (401, 403):
        return ModelHealth.auth_error, code, msg      # key is dead; no cooldown helps
    if isinstance(exc, litellm.Timeout) or "timeout" in name.lower():
        return ModelHealth.timeout, None, msg
    if isinstance(exc, litellm.NotFoundError) or code == 404:
        return ModelHealth.unavailable, 404, msg      # listed, but not served to us
    return ModelHealth.error, code, msg


async def _probe_one(
    litellm_model: str, api_key: str, api_base: Optional[str],
    mode: ModelMode = ModelMode.chat,
) -> Dict:
    """
    One cheap request, with the key passed IN — never via os.environ.

    THE PROBE MUST MATCH THE MODE. An embedding model cannot answer a chat
    completion, so probing it with one would mark every embedding model dead
    forever. Chat models get a 1-token completion; embedding models embed the
    word "ping".
    """
    started = datetime.now(timezone.utc)
    try:
        if mode is ModelMode.embedding:
            kwargs: Dict = {}
            # NVIDIA's retrieval models are asymmetric and REQUIRE input_type;
            # without it the probe 400s and a healthy model looks broken.
            if litellm_model.startswith("nvidia_nim/"):
                kwargs["input_type"] = "query"
            await litellm.aembedding(
                model=litellm_model,
                input=["ping"],
                api_key=api_key,
                api_base=api_base,
                timeout=PROBE_TIMEOUT,
                **kwargs,
            )
        else:
            await litellm.acompletion(
                model=litellm_model,
                messages=[{"role": "user", "content": "ping"}],
                max_tokens=1,
                api_key=api_key,
                api_base=api_base,
                timeout=PROBE_TIMEOUT,
            )
        latency = int((datetime.now(timezone.utc) - started).total_seconds() * 1000)
        return {"status": ModelHealth.available, "http_code": 200,
                "latency_ms": latency, "error": None}
    except Exception as exc:
        status, code, msg = _classify(exc)
        return {"status": status, "http_code": code, "latency_ms": None, "error": msg[:500]}


async def probe_deployments(deployment_ids: List[int]) -> Dict:
    """
    Probe the given deployments, at most MAX_CONCURRENCY at a time, writing the
    results back. Opens its own session — this runs detached from the request that
    queued it.
    """
    if not deployment_ids:
        return {"probed": 0}

    sem = asyncio.Semaphore(MAX_CONCURRENCY)
    db: Session = SessionLocal()

    try:
        rows = (
            db.query(Deployment, ProviderModel, ProviderKey, Provider)
            .join(ProviderModel, ProviderModel.id == Deployment.provider_model_id)
            .join(ProviderKey, ProviderKey.id == Deployment.provider_key_id)
            .join(Provider, Provider.id == Deployment.provider_id)
            .filter(Deployment.id.in_(deployment_ids))
            .all()
        )
        if not rows:
            return {"probed": 0}

        # All rows in one call belong to one user (probe_key / probe_user).
        owner_id = rows[0][0].user_id
        _job_start(owner_id, len(rows))

        # Decrypt once per key, not once per deployment.
        plaintext: Dict[int, str] = {}
        for _, _, key, _ in rows:
            if key.id not in plaintext:
                plaintext[key.id] = crypto.decrypt(key.key_ciphertext)

        async def run(dep, model, key, provider):
            async with sem:
                result = await _probe_one(
                    model.litellm_model, plaintext[key.id], provider.base_url,
                    mode=model.mode,
                )
                # Tick as each probe RETURNS, not when results are written — the
                # bar should move during the slow part, not jump at the end.
                _job_tick(owner_id)
                return dep.id, result

        results = await asyncio.gather(
            *(run(d, m, k, p) for d, m, k, p in rows), return_exceptions=True
        )

        by_id = {d.id: d for d, _, _, _ in rows}
        counts: Dict[str, int] = {}
        now = datetime.now(timezone.utc)

        for item in results:
            if isinstance(item, Exception):
                logger.warning("Probe task crashed: %s", item)
                continue
            dep_id, res = item
            dep = by_id[dep_id]
            dep.status = res["status"]
            dep.http_code = res["http_code"]
            dep.latency_ms = res["latency_ms"]
            dep.error = res["error"]
            dep.last_checked_at = now

            # A 429 gets a cooldown, so is_callable() revives it automatically once
            # it expires. Anything else CLEARS the cooldown: a dead key must never
            # be resurrected by a stale timer.
            if res["status"] is ModelHealth.rate_limited:
                dep.cooldown_until = now + timedelta(seconds=RATE_LIMIT_COOLDOWN)
            else:
                dep.cooldown_until = None

            # is_working is GENERATED from status — never assign it.
            counts[res["status"].value] = counts.get(res["status"].value, 0) + 1

        db.commit()
        logger.info("Probed %d deployment(s): %s", len(rows), counts)
        return {"probed": len(rows), **counts}
    finally:
        db.close()


async def probe_key(provider_key_id: int) -> Dict:
    """Probe every deployment of one key — what runs in the background after a key is added."""
    db = SessionLocal()
    try:
        ids = [
            d.id for d in db.query(Deployment).filter(
                Deployment.provider_key_id == provider_key_id
            )
        ]
    finally:
        db.close()
    return await probe_deployments(ids)


async def probe_user(user_id: int) -> Dict:
    """Re-probe everything a user owns (the periodic refresh)."""
    db = SessionLocal()
    try:
        ids = [d.id for d in db.query(Deployment).filter(Deployment.user_id == user_id)]
    finally:
        db.close()
    return await probe_deployments(ids)
