"""
Per-key probe (Phase 4) — test every (provider_model × key) and record health.

For each probe target it pings the concrete model with the specific key, then
upserts a `deployments` row (per key) with the result and rolls up the parent
`master_model` (is_working = any key works, working_key_count). The ping function
is injectable so the DB logic can be tested without network/keys.

Health classes (ModelHealth): a 429 counts as REACHABLE (rate_limited → working),
since the key is valid and just throttled; auth/timeout/other errors are not.
"""

import logging
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Callable, Dict, Optional, Tuple

from sqlalchemy.orm import Session

from app.core.database import SessionLocal
from app.models.provider import Provider
from app.models.provider_api_key import ProviderApiKey
from app.models.provider_model import ProviderModel
from app.models.master_model import MasterModel
from app.models.deployment import Deployment
from app.models.enums import ModelHealth
from app.services.normalize import provider_slug, upstream_id, normalize_model_name

logger = logging.getLogger("gateway.prober")

# A ping returns (status, http_code, latency_ms, error).
PingResult = Tuple[ModelHealth, Optional[int], Optional[int], Optional[str]]
PingFn = Callable[[dict], PingResult]

_WORKING = {ModelHealth.available, ModelHealth.rate_limited}


def _default_ping(target: dict) -> PingResult:
    """Real 1-token completion, no retries. Classifies the outcome."""
    import litellm

    kwargs = {
        "model": target["model"],
        "messages": [{"role": "user", "content": "ping"}],
        "max_tokens": 1,
        "timeout": 20,
        "num_retries": 0,
        "max_retries": 0,
    }
    if target.get("api_key"):
        kwargs["api_key"] = target["api_key"]
    if target.get("api_base"):
        kwargs["api_base"] = target["api_base"]
    kwargs.update(target.get("extra_params") or {})

    start = time.perf_counter()
    try:
        litellm.completion(**kwargs)
        return ModelHealth.available, 200, int((time.perf_counter() - start) * 1000), None
    except Exception as exc:  # classify by message/type
        latency = int((time.perf_counter() - start) * 1000)
        msg = str(exc)
        low = msg.lower()
        code = getattr(exc, "status_code", None)
        if "429" in low or "rate limit" in low or "quota" in low:
            return ModelHealth.rate_limited, 429, latency, msg[:500]
        if any(k in low for k in ("authentication", "invalid api key", "unauthorized", "401", "403")):
            return ModelHealth.auth_error, code or 401, latency, msg[:500]
        if "timeout" in low or "timed out" in low:
            return ModelHealth.timeout, code, latency, msg[:500]
        return ModelHealth.error, code, latency, msg[:500]


def probe(targets=None, ping: PingFn = _default_ping, max_workers: int = 16) -> dict:
    """
    Probe every target. `targets` defaults to llm_router.all_probe_targets().
    Keyless targets (no env slot) are skipped — a deployment needs a key row.

    Pings run concurrently (network-bound) in a thread pool; all DB work happens
    on the main thread (SQLAlchemy sessions are not thread-safe).
    """
    if targets is None:
        from app.core.llm_router import all_probe_targets
        targets = all_probe_targets()

    db = SessionLocal()
    try:
        # 1) Resolve rows (main thread): (target, master_id, key_id) per probeable.
        #    Dedupe by (master, key) so the same concrete model+key appearing under
        #    multiple virtual names is probed once (and can't collide on insert).
        pcache: Dict[str, Provider] = {}
        plan = []
        seen_pairs = set()
        for t in targets:
            model = t.get("model", "")
            env_slot = t.get("api_key_var")
            if not model or not env_slot:
                continue  # keyless deployment — skip (no provider_keys row to bind)
            key_row = db.query(ProviderApiKey).filter(ProviderApiKey.env_slot == env_slot).first()
            if not key_row:
                continue  # discovery hasn't imported this key yet
            prov = _provider(db, t.get("provider") or provider_slug(model), pcache)
            pm = _provider_model(db, prov.id, model, t.get("mode", "chat"))
            mm = _master_model(db, pm, prov.id)
            pair = (mm.id, key_row.id)
            if pair in seen_pairs:
                continue
            seen_pairs.add(pair)
            plan.append((t, mm.id, key_row.id))
        db.commit()

        # 2) Ping concurrently (no DB access inside threads).
        with ThreadPoolExecutor(max_workers=max_workers) as ex:
            results = list(ex.map(lambda item: ping(item[0]), plan))

        # 3) Upsert deployments (main thread).
        touched = set()
        for (t, master_id, key_id), (status, http_code, latency_ms, error) in zip(plan, results):
            working = status in _WORKING
            _upsert_deployment(db, master_id, key_id, t["model"], status, http_code, latency_ms, error, working)
            touched.add(master_id)
        db.commit()

        # 4) Roll up each touched master_model from its deployments.
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc)
        working_models = 0
        for master_id in touched:
            deps = db.query(Deployment).filter(Deployment.master_model_id == master_id).all()
            live = [d for d in deps if d.is_working]
            mm = db.query(MasterModel).filter(MasterModel.id == master_id).first()
            mm.is_working = bool(live)
            mm.working_key_count = len(live)
            mm.last_checked_at = now
            if mm.is_working:
                working_models += 1
        db.commit()

        logger.info("Probe: %d deployment(s) tested, %d working master model(s).", len(plan), working_models)
        return {"probed": len(plan), "working_master_models": working_models}
    finally:
        db.close()


# ── upsert helpers ──────────────────────────────────────────────────────────

def _provider(db, slug, cache):
    if slug in cache:
        return cache[slug]
    prov = db.query(Provider).filter(Provider.slug == slug).first()
    if not prov:
        prov = Provider(slug=slug, name=slug, litellm_prefix=slug)
        db.add(prov); db.commit(); db.refresh(prov)
    cache[slug] = prov
    return prov


def _provider_model(db, provider_id, litellm_model, mode):
    up = upstream_id(litellm_model)
    pm = (db.query(ProviderModel)
            .filter(ProviderModel.provider_id == provider_id, ProviderModel.upstream_model_id == up)
            .first())
    if not pm:
        from app.models.enums import ModelMode
        try:
            mode_enum = ModelMode(mode)
        except ValueError:
            mode_enum = ModelMode.chat
        pm = ProviderModel(provider_id=provider_id, upstream_model_id=up, litellm_model=litellm_model,
                           normalized_name=normalize_model_name(litellm_model), mode=mode_enum)
        db.add(pm); db.commit(); db.refresh(pm)
    return pm


def _master_model(db, pm, provider_id):
    mm = db.query(MasterModel).filter(MasterModel.provider_model_id == pm.id).first()
    if not mm:
        mm = MasterModel(provider_model_id=pm.id, provider_id=provider_id,
                         litellm_model=pm.litellm_model, normalized_name=pm.normalized_name)
        db.add(mm); db.commit(); db.refresh(mm)
    return mm


def _upsert_deployment(db, master_id, key_id, litellm_model, status, http_code, latency_ms, error, working):
    from datetime import datetime, timezone
    dep = (db.query(Deployment)
             .filter(Deployment.master_model_id == master_id, Deployment.provider_key_id == key_id)
             .first())
    if not dep:
        dep = Deployment(master_model_id=master_id, provider_key_id=key_id, litellm_model=litellm_model)
        db.add(dep)
    dep.litellm_model = litellm_model
    dep.status = status
    dep.http_code = http_code
    dep.latency_ms = latency_ms
    dep.error = error
    dep.is_working = working
    dep.last_checked_at = datetime.now(timezone.utc)
