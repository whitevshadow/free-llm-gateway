"""
RBAC + isolation tests.

These exist because the old code had NO role checks at all: every /v1/admin
endpoint used the same dependency as /v1/chat, so any key holder was a full
administrator. These tests are what stop that regressing.
"""

import pytest


def test_no_key_is_rejected(app_client):
    assert app_client.get("/v1/models").status_code == 401
    assert app_client.get("/v1/admin/users").status_code == 401


def test_garbage_key_is_rejected(app_client):
    r = app_client.get("/v1/models", headers={"Authorization": "Bearer sk-gw-nonsense"})
    assert r.status_code == 401


def test_user_key_is_accepted_on_user_routes(app_client, user_key):
    r = app_client.get("/v1/models", headers={"Authorization": f"Bearer {user_key}"})
    assert r.status_code == 200
    # A user with no provider keys can call nothing. That is correct, not a bug.
    assert r.json()["data"] == []


def test_non_admin_CANNOT_reach_admin_routes(app_client, user_key):
    """The whole point. A valid key must not confer admin rights."""
    h = {"Authorization": f"Bearer {user_key}"}
    assert app_client.get("/v1/admin/users", headers=h).status_code == 403
    assert app_client.get("/v1/admin/gateway-keys", headers=h).status_code == 403
    assert app_client.post(
        "/v1/admin/providers", headers=h,
        json={"slug": "evil", "name": "Evil"},
    ).status_code == 403
    assert app_client.post(
        "/v1/admin/gateway-keys", headers=h, json={"user_id": 1},
    ).status_code == 403


def test_admin_can_reach_admin_routes(app_client, admin_key):
    h = {"Authorization": f"Bearer {admin_key}"}
    assert app_client.get("/v1/admin/users", headers=h).status_code == 200

    r = app_client.post(
        "/v1/admin/providers", headers=h,
        json={"slug": "groq", "name": "Groq"},
    )
    assert r.status_code in (200, 409)   # 409 if a previous test already made it


def test_user_cannot_see_another_users_provider_keys(app_client, admin_key, user_key):
    """Each user's key list is scoped to them — never a global list."""
    admin_h = {"Authorization": f"Bearer {admin_key}"}
    user_h = {"Authorization": f"Bearer {user_key}"}

    app_client.post("/v1/admin/providers", headers=admin_h,
                    json={"slug": "groq", "name": "Groq"})

    # admin adds a key of their own
    app_client.post("/v1/me/provider-keys", headers=admin_h,
                    json={"provider": "groq", "value": "gsk_admin_secret", "label": "admin key"})

    # the other user must not see it
    r = app_client.get("/v1/me/provider-keys", headers=user_h)
    assert r.status_code == 200
    assert r.json()["keys"] == []


def test_secret_is_never_returned(app_client, admin_key):
    h = {"Authorization": f"Bearer {admin_key}"}
    app_client.post("/v1/admin/providers", headers=h, json={"slug": "groq", "name": "Groq"})
    app_client.post("/v1/me/provider-keys", headers=h,
                    json={"provider": "groq", "value": "gsk_super_secret_value", "label": "k1"})

    body = app_client.get("/v1/me/provider-keys", headers=h).text
    assert "gsk_super_secret_value" not in body
