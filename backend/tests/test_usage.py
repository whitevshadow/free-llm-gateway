"""
Usage/analytics endpoint tests.

Seeds real request_logs rows and asserts the aggregates come back right — a
dashboard that sums wrong is worse than no dashboard.
"""

import pytest


@pytest.fixture(scope="module")
def seeded(db, user_key):
    """A user with 2 Groq keys and traffic across 2 models."""
    from app.models.gateway_api_key import GatewayApiKey
    from app.models.provider import Provider
    from app.models.provider_key import ProviderKey
    from app.models.provider_model import ProviderModel
    from app.models.deployment import Deployment
    from app.models.request_log import RequestLog
    from app.services.gateway_keys import _hash

    uid = db.query(GatewayApiKey).filter(
        GatewayApiKey.key_hash == _hash(user_key)
    ).one().user_id

    groq = db.query(Provider).filter(Provider.slug == "groq").first()
    if not groq:
        groq = Provider(slug="groq", name="Groq")
        db.add(groq); db.commit(); db.refresh(groq)

    m = db.query(ProviderModel).filter(
        ProviderModel.provider_id == groq.id,
        ProviderModel.upstream_model_id == "gpt-oss-120b",
    ).first()
    if not m:
        m = ProviderModel(provider_id=groq.id, upstream_model_id="gpt-oss-120b",
                          litellm_model="groq/gpt-oss-120b", normalized_name="gpt-oss-120b")
        db.add(m); db.commit(); db.refresh(m)

    k1 = ProviderKey(user_id=uid, provider_id=groq.id, label="usage-k1",
                     key_ciphertext=b"\xaa", key_masked="****0001")
    k2 = ProviderKey(user_id=uid, provider_id=groq.id, label="usage-k2",
                     key_ciphertext=b"\xbb", key_masked="****0002")
    db.add_all([k1, k2]); db.commit()

    d1 = Deployment(user_id=uid, provider_id=groq.id, provider_model_id=m.id,
                    provider_key_id=k1.id)
    db.add(d1); db.commit()

    # key1: 2 calls on gpt-oss-120b (300 tokens). key2: 1 call on llama (50 tokens).
    db.add_all([
        RequestLog(user_id=uid, requested_model="gpt-oss-120b", provider_id=groq.id,
                   provider_key_id=k1.id, prompt_tokens=100, completion_tokens=50,
                   latency_ms=200, status_code=200),
        RequestLog(user_id=uid, requested_model="gpt-oss-120b", provider_id=groq.id,
                   provider_key_id=k1.id, prompt_tokens=100, completion_tokens=50,
                   latency_ms=300, status_code=200),
        RequestLog(user_id=uid, requested_model="llama-3.3-70b", provider_id=groq.id,
                   provider_key_id=k2.id, prompt_tokens=40, completion_tokens=10,
                   latency_ms=100, status_code=429, error_message="rate limited"),
    ])
    db.commit()
    return uid


def test_whoami(app_client, user_key):
    r = app_client.get("/v1/me", headers={"Authorization": f"Bearer {user_key}"})
    assert r.status_code == 200
    assert r.json()["is_admin"] is False
    assert "provider_key_count" in r.json()


def test_providers_readable_by_normal_user(app_client, user_key, admin_key):
    """Without this a user cannot populate the 'add a key' dropdown at all."""
    app_client.post("/v1/admin/providers",
                    headers={"Authorization": f"Bearer {admin_key}"},
                    json={"slug": "groq", "name": "Groq"})
    r = app_client.get("/v1/providers", headers={"Authorization": f"Bearer {user_key}"})
    assert r.status_code == 200
    assert any(p["slug"] == "groq" for p in r.json()["providers"])


def test_usage_aggregates(app_client, user_key, seeded):
    r = app_client.get("/v1/me/usage?days=30",
                       headers={"Authorization": f"Bearer {user_key}"})
    assert r.status_code == 200
    body = r.json()

    # total_tokens is a GENERATED column: 150+150+50
    assert body["totals"]["requests"] == 3
    assert body["totals"]["total_tokens"] == 350
    assert body["totals"]["errors"] == 1          # the 429

    # top model by tokens
    assert body["top_models"][0]["model"] == "gpt-oss-120b"
    assert body["top_models"][0]["tokens"] == 300
    assert body["top_models"][0]["requests"] == 2

    assert body["top_providers"][0]["provider"] == "Groq"
    assert body["top_providers"][0]["tokens"] == 350

    # per-key burn: k1 carried 300 tokens, k2 only 50
    by_label = {k["label"]: k for k in body["per_key"]}
    assert by_label["usage-k1"]["tokens"] == 300
    assert by_label["usage-k2"]["tokens"] == 50


def test_logs_name_the_answering_key(app_client, user_key, seeded):
    r = app_client.get("/v1/me/logs?limit=10",
                       headers={"Authorization": f"Bearer {user_key}"})
    assert r.status_code == 200
    logs = r.json()["logs"]
    assert len(logs) >= 3
    assert all(l["key_label"] for l in logs)      # every call is attributed to a key
    assert any(l["status_code"] == 429 for l in logs)


def test_logs_filter_errors(app_client, user_key, seeded):
    r = app_client.get("/v1/me/logs?status=error",
                       headers={"Authorization": f"Bearer {user_key}"})
    assert all(l["status_code"] >= 400 for l in r.json()["logs"])


def test_usage_is_scoped_to_me(app_client, admin_key, seeded):
    """The admin must not see the other user's traffic in their own usage."""
    r = app_client.get("/v1/me/usage", headers={"Authorization": f"Bearer {admin_key}"})
    assert r.json()["totals"]["requests"] == 0
