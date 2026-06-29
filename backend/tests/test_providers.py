"""
Tests for the provider registry, the smart Router pool, and the
GET /api/v1/providers endpoint.

The gateway aggregates FREE providers behind virtual models served by
litellm.Router. The catalog therefore exposes a synthetic "gateway" provider
(whose models are the live virtual models) plus the underlying free providers.
The legacy MODEL_MAP still exists for the direct (non-routed) path.
"""

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.providers import (
    MODEL_MAP,
    FREE_PROVIDER_INFO,
    FALLBACK_CHAINS,
    resolve_litellm_model,
    get_default_model,
    get_fallbacks,
    get_provider_catalog_with_status,
)
from app.core.llm_router import list_virtual_models


@pytest.fixture()
def api_client():
    """
    A plain TestClient with no JWT header.

    The provider/`/v1` endpoints don't need a real user (UI auth is bypassed and
    the /v1 gateway auth is open by default), so this avoids the conftest `client`
    fixture's bcrypt-based user creation.
    """
    # oauth2_scheme requires the header to be present, but get_current_user
    # ignores its contents (UI auth is bypassed), so any token works.
    with TestClient(app) as c:
        c.headers["Authorization"] = "Bearer test"
        yield c


# ── Legacy MODEL_MAP (direct path) ──────────────────────

class TestModelMap:
    def test_openai_model_resolves(self):
        assert resolve_litellm_model("openai", "gpt-4o") == "gpt-4o"

    def test_deepseek_model_resolves(self):
        assert resolve_litellm_model("deepseek", "deepseek-chat") == "deepseek/deepseek-chat"

    def test_unknown_returns_none(self):
        assert resolve_litellm_model("unknown_provider", "fake-model") is None

    def test_model_map_has_minimum_entries(self):
        assert len(MODEL_MAP) >= 10


# ── Free-provider catalog ───────────────────────────────

class TestFreeProviderCatalog:
    def test_free_provider_info_lists_groq_and_openrouter(self):
        ids = {p["id"] for p in FREE_PROVIDER_INFO}
        assert "groq" in ids
        assert "openrouter" in ids

    def test_catalog_has_gateway_entry_first(self):
        catalog = get_provider_catalog_with_status()
        assert catalog[0]["id"] == "gateway"
        # Gateway models mirror the live virtual models.
        gateway_models = {m["id"] for m in catalog[0]["models"]}
        assert gateway_models == set(list_virtual_models())

    def test_each_entry_has_id_name_and_status(self):
        for p in get_provider_catalog_with_status():
            assert "id" in p
            assert "name" in p
            assert "is_configured" in p


# ── Virtual models / defaults ───────────────────────────

class TestVirtualModels:
    def test_default_model_is_a_live_virtual_model(self):
        default = get_default_model("gateway")
        live = list_virtual_models()
        # When no keys are set, Ollama's keyless 'local' is the only live model.
        assert default in live

    def test_local_always_available(self):
        # The keyless Ollama deployment is always part of the pool.
        assert "local" in list_virtual_models()


# ── Legacy fallback chains ──────────────────────────────

class TestFallbacks:
    def test_openai_has_fallbacks(self):
        assert len(get_fallbacks("openai")) >= 1

    def test_unknown_provider_returns_empty(self):
        assert get_fallbacks("nonexistent") == []


# ── API endpoint tests ──────────────────────────────────

class TestProvidersEndpoint:
    def test_list_providers_returns_200(self, api_client):
        response = api_client.get("/api/v1/providers")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "success"
        assert isinstance(data["data"], list)
        assert data["data"][0]["id"] == "gateway"

    def test_list_fallbacks_returns_200(self, api_client):
        response = api_client.get("/api/v1/providers/fallbacks")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "success"
        assert isinstance(data["data"], dict)


# ── /v1/models compatibility endpoint ───────────────────

class TestV1Models:
    def test_v1_models_lists_virtual_models(self, api_client):
        response = api_client.get("/v1/models")
        assert response.status_code == 200
        data = response.json()
        assert data["object"] == "list"
        ids = {m["id"] for m in data["data"]}
        assert ids == set(list_virtual_models())
