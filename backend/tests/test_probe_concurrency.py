"""
Probe concurrency (SRS §13.1) — the gate is PER KEY, not per run.

The limit exists to protect one free-tier account from being rate-limited by the
very probes meant to validate it. That reasoning is about a KEY, so the gate has
to be too. A single run-wide semaphore also happens to bound one account's
traffic correctly, which is why the original was easy to miss as wrong — it only
misbehaves on the run that spans several keys.

That run is "Re-test all" on the Deployments screen. With one gate of 4, a user
holding 245 NVIDIA deployments and 26 Cohere ones probes them strictly
interleaved through a 4-wide straw: Cohere's rows wait on NVIDIA's for no reason,
because Cohere's rate limit knows nothing about NVIDIA's. The run takes long
enough that a restart lands mid-flight and most rows keep a stale
`last_checked_at` forever.

So there are two things to assert, and the first one alone is not enough:
  1. no key ever exceeds MAX_CONCURRENCY in flight  — the protection still holds;
  2. two keys DO overlap                            — they are not serialised.
"""

import asyncio

import pytest

from app.models.deployment import Deployment
from app.models.enums import ModelHealth, ModelMode
from app.models.provider import Provider
from app.models.provider_key import ProviderKey
from app.models.provider_model import ProviderModel
from app.models.user import User
from app.services import crypto, prober

# Enough rows per key that a run-wide gate of 4 could not possibly overlap them.
PER_KEY_MODELS = 12


@pytest.fixture
def two_keyed_user(db):
    """One user, two providers, one key each, PER_KEY_MODELS deployments per key."""
    import uuid

    tag = uuid.uuid4().hex[:8]
    user = User(email=f"probe-{tag}@test", is_admin=False)
    db.add(user)
    db.flush()

    key_ids = []
    deployment_ids = []
    for slug in (f"alpha{tag}", f"beta{tag}"):
        provider = Provider(slug=slug, name=slug.title(), base_url="https://example.invalid/v1")
        db.add(provider)
        db.flush()

        key = ProviderKey(
            user_id=user.id,
            provider_id=provider.id,
            label=f"{slug} key",
            key_ciphertext=crypto.encrypt("sk-test"),
            key_masked="••••test",
        )
        db.add(key)
        db.flush()
        key_ids.append(key.id)

        for i in range(PER_KEY_MODELS):
            model = ProviderModel(
                provider_id=provider.id,
                upstream_model_id=f"m{i}",
                litellm_model=f"{slug}/m{i}",
                normalized_name=f"{slug}/m{i}",
                mode=ModelMode.chat,
            )
            db.add(model)
            db.flush()
            dep = Deployment(
                user_id=user.id,
                provider_id=provider.id,
                provider_model_id=model.id,
                provider_key_id=key.id,
                status=ModelHealth.unavailable,
            )
            db.add(dep)
            db.flush()
            deployment_ids.append(dep.id)

    db.commit()
    yield {"user_id": user.id, "key_ids": key_ids, "deployment_ids": deployment_ids}

    # The probe wrote through its own session; drop the rows so a re-run of the
    # suite against a reused database starts clean.
    db.query(Deployment).filter(Deployment.user_id == user.id).delete()
    db.commit()


def _run_probe(monkeypatch, deployment_ids):
    """
    Probe with the network replaced by a recorder.

    `_probe_one` receives the api_key, and both keys here decrypt to the same
    string, so concurrency is tracked by litellm_model prefix instead — that is
    the only per-key signal the fake sees.
    """
    inflight: dict[str, int] = {}
    peak: dict[str, int] = {}
    overlap_seen = False

    async def fake_probe_one(litellm_model, api_key, api_base, mode=None, custom_llm_provider=None):
        nonlocal overlap_seen
        slug = litellm_model.split("/", 1)[0]
        inflight[slug] = inflight.get(slug, 0) + 1
        peak[slug] = max(peak.get(slug, 0), inflight[slug])
        if len([s for s, n in inflight.items() if n > 0]) > 1:
            overlap_seen = True
        try:
            # Long enough that every task has been scheduled before any finishes,
            # so the counters see the real steady state rather than a race.
            await asyncio.sleep(0.05)
        finally:
            inflight[slug] -= 1
        return {
            "status": ModelHealth.available, "http_code": 200,
            "latency_ms": 1, "error": None, "retry_after": None,
        }

    monkeypatch.setattr(prober, "_probe_one", fake_probe_one)
    result = asyncio.run(prober.probe_deployments(list(deployment_ids)))
    return result, peak, overlap_seen


def test_per_key_limit_holds_and_keys_run_in_parallel(db, monkeypatch, two_keyed_user):
    result, peak, overlap_seen = _run_probe(monkeypatch, two_keyed_user["deployment_ids"])

    assert result["probed"] == PER_KEY_MODELS * 2

    # 1. The protection the limit exists for: no single account is ever hit with
    #    more than MAX_CONCURRENCY probes at once.
    for slug, observed in peak.items():
        assert observed <= prober.MAX_CONCURRENCY, (
            f"{slug} peaked at {observed} concurrent probes, "
            f"above MAX_CONCURRENCY={prober.MAX_CONCURRENCY}"
        )

    # 2. The bug this change fixes: with one run-wide gate, the second key's
    #    probes could not start until the first key's had drained.
    assert overlap_seen, "keys were probed serially — the gate is not per key"
    assert len(peak) == 2, "expected both keys to be probed"


def test_results_are_written_back(db, two_keyed_user, monkeypatch):
    """A probe is only useful if it lands: `last_checked_at` must move."""
    _run_probe(monkeypatch, two_keyed_user["deployment_ids"])

    db.expire_all()
    rows = (
        db.query(Deployment)
        .filter(Deployment.id.in_(two_keyed_user["deployment_ids"]))
        .all()
    )
    assert rows, "deployments vanished"
    assert all(d.status is ModelHealth.available for d in rows)
    assert all(d.last_checked_at is not None for d in rows)
