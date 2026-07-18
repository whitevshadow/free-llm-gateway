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
import io
import logging
import math
import struct
import time
import wave
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from typing import Dict, List, Optional, Tuple

import litellm
from sqlalchemy.orm import Session

from app.core.database import SessionLocal
from app.models.deployment import Deployment
from app.models.enums import ModelHealth, ModelMode
from app.models.provider import Provider
from app.models.provider_key import ProviderKey
from app.models.provider_model import ProviderModel
from app.services import crypto, nvcf, nvidia_riva, presets
from app.services.catalog import audio_kind

logger = logging.getLogger("gateway.prober")

# How many probes may be in flight at once. Deliberately small: these all hit the
# SAME provider with the SAME key, so this is literally the number of concurrent
# requests we are pointing at one free-tier account.
MAX_CONCURRENCY = 4

PROBE_TIMEOUT = 20            # seconds per probe

# ── 429 cooldowns ────────────────────────────────────────────────────────────
# A flat 60s cooldown burns retries against free tiers that enforce DAILY
# quotas. Instead: honour Retry-After when the provider sends it; otherwise
# escalate per deployment on consecutive 429s (strike 1 → 60s, 2 → 2m, 3+ → 5m).
# Strikes reset to 0 on any success, so one throttled minute doesn't haunt a key.
COOLDOWN_LADDER = (60, 120, 300)   # seconds, indexed by consecutive 429s
RETRY_AFTER_CAP = 900              # never trust a header past 15 minutes


def retry_after_seconds(exc: Exception) -> Optional[int]:
    """
    The provider's own Retry-After, in seconds, if the exception carries one.

    litellm exceptions wrap the underlying httpx response; we look there first,
    then on the exception itself. Handles both delta-seconds and HTTP-date
    forms. Returns None when absent or unparseable — never raises.
    """
    headers = getattr(getattr(exc, "response", None), "headers", None)
    if headers is None:
        headers = getattr(exc, "headers", None)
    if not headers:
        return None
    try:
        raw = headers.get("retry-after") or headers.get("Retry-After")
    except Exception:
        return None
    if not raw:
        return None

    raw = str(raw).strip()
    try:
        secs = float(raw)
    except ValueError:
        try:
            secs = (parsedate_to_datetime(raw)
                    - datetime.now(timezone.utc)).total_seconds()
        except Exception:
            return None
    if secs <= 0:
        return None
    return min(math.ceil(secs), RETRY_AFTER_CAP)


def cooldown_seconds(strikes: int, retry_after: Optional[int] = None) -> int:
    """
    How long to bench a deployment after its `strikes`-th consecutive 429.

    The provider's Retry-After always wins — it knows its own limits. Otherwise
    walk the ladder and stay on the last rung.
    """
    if retry_after is not None:
        return retry_after
    return COOLDOWN_LADDER[min(max(strikes, 1), len(COOLDOWN_LADDER)) - 1]


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
    # litellm exceptions carry status_code directly; httpx errors (raised by
    # the NVCF adapter) carry it on .response. Same enum either way.
    code = getattr(exc, "status_code", None)
    if code is None:
        code = getattr(getattr(exc, "response", None), "status_code", None)

    if isinstance(exc, litellm.RateLimitError) or code == 429:
        return ModelHealth.rate_limited, 429, msg     # key WORKS, just throttled
    if isinstance(exc, litellm.AuthenticationError) or code in (401, 403):
        return ModelHealth.auth_error, code, msg      # key is dead; no cooldown helps
    if isinstance(exc, litellm.Timeout) or "timeout" in name.lower():
        return ModelHealth.timeout, None, msg
    if isinstance(exc, litellm.NotFoundError) or code == 404:
        return ModelHealth.unavailable, 404, msg      # listed, but not served to us
    return ModelHealth.error, code, msg


# ── mode-specific probe payloads ─────────────────────────────────────────────

def _probe_wav() -> io.BytesIO:
    """
    A fresh ~0.25s 8kHz mono WAV of a faint tone, generated in memory — the
    smallest thing a transcription endpoint will accept as real audio. Fresh
    per call: the upload consumes the stream, so it cannot be a shared constant.
    """
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(8000)
        frames = 2000
        w.writeframes(b"".join(
            struct.pack("<h", 800 if (i // 20) % 2 == 0 else -800)
            for i in range(frames)
        ))
    buf.seek(0)
    buf.name = "ping.wav"   # litellm/openai read the filename from here
    return buf


# TTS models refuse a call without a voice, and voices are provider-specific:
# Groq's PlayAI models only accept PlayAI voices, OpenAI-compatible ones take
# 'alloy'. Wrong voice = 400 = a healthy model probed dead, so this map matters.
_TTS_VOICES = {"groq": "Fritz-PlayAI"}
_TTS_VOICE_DEFAULT = "alloy"


def probe_tts_voice(litellm_model: str) -> str:
    slug = litellm_model.split("/", 1)[0]
    return _TTS_VOICES.get(slug, _TTS_VOICE_DEFAULT)


async def _probe_one(
    litellm_model: str, api_key: str, api_base: Optional[str],
    mode: ModelMode = ModelMode.chat,
    custom_llm_provider: Optional[str] = None,
) -> Dict:
    """
    One cheap request, with the key passed IN — never via os.environ.

    THE PROBE MUST MATCH THE MODE. An embedding model cannot answer a chat
    completion, so probing it with one would mark every embedding model dead
    forever. Chat models get a 1-token completion; embedding models embed the
    word "ping"; whisper-family models transcribe a ~0.25s generated WAV; TTS
    models speak "ping"; image models generate one small image.

    `custom_llm_provider` forces litellm onto a specific integration for
    providers it has no native prefix for (LongCat) — see
    presets.custom_llm_provider_for. Once forced, the call must use the bare
    upstream id, not the stored 'slug/id' form, or the request 404s.
    """
    started = datetime.now(timezone.utc)
    call_model = litellm_model
    extra: Dict[str, str] = {}
    if custom_llm_provider:
        extra["custom_llm_provider"] = custom_llm_provider
        call_model = litellm_model.split("/", 1)[1] if "/" in litellm_model else litellm_model
    try:
        if nvidia_riva.is_riva_model(litellm_model):
            # NVIDIA Riva ASR — litellm speaks this protocol natively, but it
            # needs the bare model id, the Riva gRPC endpoint, and the NVCF
            # function id, not the provider's normal chat api_base/api_key.
            model_id, function_id = nvidia_riva.split_function_id(litellm_model)
            await litellm.atranscription(
                model=model_id,
                file=_probe_wav(),
                api_key=api_key,
                api_base=nvidia_riva.GRPC_ENDPOINT,
                nvcf_function_id=function_id,
                timeout=PROBE_TIMEOUT,
            )
        elif nvcf.is_nvcf_model(litellm_model):
            # NVIDIA cloud function — litellm cannot speak this surface at all;
            # the adapter runs the cheapest real generation instead.
            await nvcf.probe_image(litellm_model, api_key)
        elif mode is ModelMode.embedding:
            kwargs: Dict = dict(extra)
            # NVIDIA's retrieval models are asymmetric and REQUIRE input_type;
            # without it the probe 400s and a healthy model looks broken.
            if litellm_model.startswith("nvidia_nim/"):
                kwargs["input_type"] = "query"
            await litellm.aembedding(
                model=call_model,
                input=["ping"],
                api_key=api_key,
                api_base=api_base,
                timeout=PROBE_TIMEOUT,
                **kwargs,
            )
        elif mode is ModelMode.audio:
            if audio_kind(litellm_model) == "transcription":
                await litellm.atranscription(
                    model=call_model,
                    file=_probe_wav(),
                    api_key=api_key,
                    api_base=api_base,
                    timeout=PROBE_TIMEOUT,
                    **extra,
                )
            else:
                await litellm.aspeech(
                    model=call_model,
                    input="ping",
                    voice=probe_tts_voice(litellm_model),
                    api_key=api_key,
                    api_base=api_base,
                    timeout=PROBE_TIMEOUT,
                    **extra,
                )
        elif mode is ModelMode.image:
            await litellm.aimage_generation(
                model=call_model,
                prompt="ping",
                api_key=api_key,
                api_base=api_base,
                timeout=PROBE_TIMEOUT,
                **extra,
            )
        else:
            await litellm.acompletion(
                model=call_model,
                messages=[{"role": "user", "content": "ping"}],
                max_tokens=1,
                api_key=api_key,
                api_base=api_base,
                timeout=PROBE_TIMEOUT,
                **extra,
            )
        latency = int((datetime.now(timezone.utc) - started).total_seconds() * 1000)
        return {"status": ModelHealth.available, "http_code": 200,
                "latency_ms": latency, "error": None, "retry_after": None}
    except Exception as exc:
        status, code, msg = _classify(exc)
        return {"status": status, "http_code": code, "latency_ms": None,
                "error": msg[:500], "retry_after": retry_after_seconds(exc)}


# Deployments with a probe currently queued or in flight. Re-testing a
# deployment that is ALREADY being tested buys no new information and burns
# free-tier quota — clicking "Re-test all" twice must cost nothing extra.
# One asyncio loop, so a plain set is safe (no await between check and add).
_inflight: set = set()


async def probe_deployments(deployment_ids: List[int]) -> Dict:
    """
    Probe the given deployments, at most MAX_CONCURRENCY at a time, writing the
    results back. Opens its own session — this runs detached from the request that
    queued it.

    Deployments already in an active probe run are SKIPPED, not re-queued: the
    in-flight probe will write their result momentarily, and a duplicate request
    would only waste the key's quota and inflate the progress bar.
    """
    requested = len(deployment_ids)
    deployment_ids = [i for i in deployment_ids if i not in _inflight]
    skipped = requested - len(deployment_ids)
    if skipped:
        logger.info("Skipped %d deployment(s) already being probed.", skipped)
    if not deployment_ids:
        return {"probed": 0, "skipped_inflight": skipped}

    _inflight.update(deployment_ids)
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
                    model.litellm_model, plaintext[key.id],
                    # Not provider.base_url verbatim: native-routing providers
                    # (Cohere) must get None or the probe 404s (see presets).
                    presets.call_api_base(provider.slug, provider.base_url),
                    mode=model.mode,
                    custom_llm_provider=presets.custom_llm_provider_for(provider.slug),
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
            # be resurrected by a stale timer. Strikes reset only on SUCCESS —
            # a timeout between two 429s says nothing about the quota.
            if res["status"] is ModelHealth.rate_limited:
                dep.rate_limit_strikes = (dep.rate_limit_strikes or 0) + 1
                dep.cooldown_until = now + timedelta(
                    seconds=cooldown_seconds(dep.rate_limit_strikes, res["retry_after"])
                )
            else:
                dep.cooldown_until = None
                if res["status"] is ModelHealth.available:
                    dep.rate_limit_strikes = 0

            # is_working is GENERATED from status — never assign it.
            counts[res["status"].value] = counts.get(res["status"].value, 0) + 1

        db.commit()
        logger.info("Probed %d deployment(s): %s", len(rows), counts)
        return {"probed": len(rows), "skipped_inflight": skipped, **counts}
    finally:
        _inflight.difference_update(deployment_ids)
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
