"""
Phase 3–6 — the discovery → probe → derive → DB-router pipeline, end to end,
with fake targets + a fake ping so it is deterministic and needs no network/keys.
"""

import pytest

from app.core.database import SessionLocal
from app.core.config import settings
from app.models.enums import ModelHealth
from app.models.provider import Provider
from app.models.provider_model import ProviderModel
from app.models.master_model import MasterModel
from app.models.deployment import Deployment
from app.models.common_model import CommonModel, CommonModelMember
from app.services import catalog, prober, derive_common, key_store

# Two providers both serve gpt-oss-120b (→ common model); groq also has a
# solo model (→ no common model). Groq has TWO keys for the shared model.
FAKE_TARGETS = [
    {"model": "groq/openai/gpt-oss-120b", "provider": "groq", "api_key": "gsk_1",
     "api_base": None, "api_key_var": "GROQ_API_KEY_1", "mode": "chat", "extra_params": {}},
    {"model": "groq/openai/gpt-oss-120b", "provider": "groq", "api_key": "gsk_2",
     "api_base": None, "api_key_var": "GROQ_API_KEY_2", "mode": "chat", "extra_params": {}},
    {"model": "nvidia_nim/openai/gpt-oss-120b", "provider": "nvidia_nim", "api_key": "nv_1",
     "api_base": None, "api_key_var": "NVIDIA_API_KEY_1", "mode": "chat", "extra_params": {}},
    {"model": "groq/llama-3.3-70b-versatile", "provider": "groq", "api_key": "gsk_1",
     "api_base": None, "api_key_var": "GROQ_API_KEY_1", "mode": "chat", "extra_params": {}},
]


def _fake_ping(target):
    # Groq answers faster than NVIDIA → groq should rank priority 0.
    latency = 50 if target["provider"] == "groq" else 100
    return (ModelHealth.available, 200, latency, None)


def test_pipeline_end_to_end():
    # Phase 3: discovery
    disc = catalog.discover(targets=FAKE_TARGETS)
    assert disc["providers"] >= 2 and disc["provider_models"] >= 3

    # decrypt imported keys into the environment (as startup would)
    key_store.apply_persisted_keys()

    # Phase 4: per-key probe (fake ping — all available)
    prb = prober.probe(targets=FAKE_TARGETS, ping=_fake_ping)
    assert prb["probed"] == 4  # 4 (model×key) deployments

    # Phase 5: derive
    derive_common.derive()

    db = SessionLocal()
    try:
        # gpt-oss-120b served by 2 providers → one common model, 2 ordered members
        cm = db.query(CommonModel).filter(CommonModel.name == "openai/gpt-oss-120b").first()
        assert cm is not None and cm.is_active and cm.provider_count == 2
        members = (
            db.query(CommonModelMember, MasterModel)
            .join(MasterModel, CommonModelMember.master_model_id == MasterModel.id)
            .filter(CommonModelMember.common_model_id == cm.id)
            .order_by(CommonModelMember.priority)
            .all()
        )
        assert [m.priority for m, _ in members] == [0, 1]
        assert members[0][1].litellm_model.startswith("groq/")        # faster = primary
        assert members[1][1].litellm_model.startswith("nvidia_nim/")  # slower = fallback

        # groq's master for the shared model has 2 working keys
        groq_master = members[0][1]
        assert groq_master.working_key_count == 2

        # solo model does NOT become a common model
        assert db.query(CommonModel).filter(CommonModel.name == "llama-3.3-70b-versatile").first() is None
    finally:
        db.close()

    # Phase 6: build the Router from the DB spine
    prev = settings.ROUTER_SOURCE
    settings.ROUTER_SOURCE = "db"
    try:
        from app.core.llm_router import reload_router, list_virtual_models, router_health
        reload_router()
        vms = list_virtual_models()
        assert "openai/gpt-oss-120b" in vms          # common model is served
        assert not any("##fb" in v for v in vms)     # internal fallback names hidden
        health = router_health()
        # 2 groq keys + 1 nvidia key = 3 deployments across the common name + fallback
        assert health["total_deployments"] >= 3
    finally:
        settings.ROUTER_SOURCE = prev
        from app.core.llm_router import reload_router
        reload_router()
