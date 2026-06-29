"""
Parallel availability probe — test EVERY model/provider once, store the results.

Run ONCE, manually (it is NOT wired into startup):

    docker compose exec app python -m app.scripts.probe_models

WHAT IT DOES:
  1. Enumerates every deployment in the pool PLUS every NVIDIA auto-discovered
     model (unfiltered) via llm_router.all_probe_targets().
  2. Sends each a 1-token "ping" concurrently (bounded by CONCURRENCY), with a
     short timeout and zero retries so results are fast and honest.
  3. Classifies each result:
       • answered            → available
       • 429 / rate limited  → available (the model is reachable, just busy)
       • 404 / not found     → unavailable  (e.g. NVIDIA function not enabled)
       • 401 / 403 auth       → unavailable  (key rejected for that model)
       • timeout / other      → unavailable
  4. Upserts ONE row per concrete model into `model_availability`
     (available if ANY backing key answered).
  5. Best-effort: pings the gateway's /v1/admin/reload-router so the live
     Router immediately hides the dead models (no restart needed).

After this runs, /v1/models and the Open WebUI picker show only working models.
"""

from __future__ import annotations

import asyncio
import time
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import litellm

from app.core.database import SessionLocal, engine, Base
from app.core.llm_router import all_probe_targets
from app.models.model_availability import ModelAvailability

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger("probe")

# Keep litellm quiet — we do our own reporting.
litellm.suppress_debug_info = True

CONCURRENCY = 10        # simultaneous pings (modest, to avoid self-inflicted 429s)
PING_TIMEOUT = 30       # seconds per ping


def _classify(exc: Exception) -> Tuple[bool, str, Optional[int]]:
    """Map a LiteLLM exception to (available, status, http_code)."""
    name = type(exc).__name__.lower()
    code = getattr(exc, "status_code", None)
    msg = str(exc).lower()

    if "ratelimit" in name or code == 429 or "rate limit" in msg or " 429" in msg:
        return True, "rate_limited", 429
    if "notfound" in name or code == 404 or "not found" in msg:
        return False, "unavailable", 404
    if (
        "authentication" in name
        or "permission" in name
        or code in (401, 403)
        or "invalid api key" in msg
        or "unauthorized" in msg
    ):
        return False, "auth_error", code or 401
    if "timeout" in name or "timed out" in msg:
        return False, "timeout", None
    return False, "error", code if isinstance(code, int) else None


async def _ping(sem: asyncio.Semaphore, target: Dict[str, Any]) -> Dict[str, Any]:
    """Send one 1-token completion and return a normalised result dict."""
    model = target["model"]
    kwargs: Dict[str, Any] = {
        "model": model,
        "messages": [{"role": "user", "content": "ping"}],
        "max_tokens": 1,
        "num_retries": 0,
        "timeout": PING_TIMEOUT,
    }
    if target.get("api_key"):
        kwargs["api_key"] = target["api_key"]
    if target.get("api_base"):
        kwargs["api_base"] = target["api_base"]
    # DeepSeek reasoning models "think" first; disable it so the ping is snappy.
    if "deepseek" in model.lower():
        kwargs["extra_body"] = {"chat_template_kwargs": {"thinking": False}}

    async with sem:
        start = time.perf_counter()
        try:
            await litellm.acompletion(**kwargs)
            latency = int((time.perf_counter() - start) * 1000)
            return {
                "model": model, "available": True, "status": "available",
                "http_code": 200, "latency_ms": latency, "error": None,
            }
        except Exception as exc:
            latency = int((time.perf_counter() - start) * 1000)
            available, status, code = _classify(exc)
            return {
                "model": model, "available": available, "status": status,
                "http_code": code, "latency_ms": latency, "error": str(exc)[:500],
            }


def _better(new: Dict[str, Any], cur: Dict[str, Any]) -> bool:
    """Prefer an available result; among equals, prefer lower latency."""
    if new["available"] != cur["available"]:
        return new["available"]
    return (new["latency_ms"] or 10**9) < (cur["latency_ms"] or 10**9)


async def _run() -> None:
    # Load runtime-saved keys (now in Postgres) into the environment first.
    try:
        from app.services.key_store import apply_persisted_keys
        applied = apply_persisted_keys()
        logger.info("Applied %d persisted key(s).", applied)
    except Exception as exc:
        logger.warning("Could not apply persisted keys: %s", exc)

    targets = all_probe_targets()
    if not targets:
        logger.error("No deployments to probe — is any provider key configured?")
        return

    # One ping per unique (concrete model, key); collect provider + virtuals per model.
    uniq: Dict[Tuple[str, str], Dict[str, Any]] = {}
    meta: Dict[str, Dict[str, Any]] = {}
    for t in targets:
        uniq.setdefault((t["model"], t.get("api_key") or ""), t)
        m = meta.setdefault(t["model"], {"provider": t.get("provider"), "virtuals": set()})
        if t.get("virtual_model"):
            m["virtuals"].add(t["virtual_model"])

    logger.info(
        "Probing %d unique deployment(s) across %d concrete model(s) (concurrency=%d)...",
        len(uniq), len(meta), CONCURRENCY,
    )

    sem = asyncio.Semaphore(CONCURRENCY)
    results = await asyncio.gather(*(_ping(sem, t) for t in uniq.values()))

    # Aggregate to one result per concrete model (available if ANY key answered).
    agg: Dict[str, Dict[str, Any]] = {}
    for r in results:
        cur = agg.get(r["model"])
        if cur is None or _better(r, cur):
            agg[r["model"]] = r

    # Persist.
    Base.metadata.create_all(bind=engine)
    now = datetime.now(timezone.utc)
    db = SessionLocal()
    written = 0
    try:
        for model, r in agg.items():
            row = db.query(ModelAvailability).filter(
                ModelAvailability.concrete_model == model
            ).first()
            if not row:
                row = ModelAvailability(concrete_model=model)
                db.add(row)
            mm = meta.get(model, {})
            row.provider = mm.get("provider")
            row.virtual_models = ",".join(sorted(mm.get("virtuals") or []))
            row.available = r["available"]
            row.status = r["status"]
            row.http_code = r["http_code"]
            row.latency_ms = r["latency_ms"]
            row.error = None if r["available"] else r["error"]
            row.checked_at = now
            written += 1
        db.commit()
    finally:
        db.close()

    available = sum(1 for r in agg.values() if r["available"])
    logger.info(
        "\n%s\nProbe complete: %d available / %d unavailable (of %d models). Saved %d row(s).",
        "=" * 60, available, len(agg) - available, len(agg), written,
    )

    # Show a few of the dead ones so the user sees the filter working.
    dead = sorted(m for m, r in agg.items() if not r["available"])
    if dead:
        logger.info("Hidden (unavailable), e.g.: %s%s",
                    ", ".join(dead[:8]), " ..." if len(dead) > 8 else "")

    # Best-effort: make the live gateway re-filter immediately.
    try:
        import httpx
        httpx.post("http://localhost:8000/v1/admin/reload-router", timeout=30)
        logger.info("Triggered live Router reload — only available models are now served.")
    except Exception as exc:
        logger.info(
            "Could not auto-reload the live Router (%s).\n"
            "Apply manually with:  docker compose restart app", exc,
        )


def main() -> None:
    asyncio.run(_run())


if __name__ == "__main__":
    main()
