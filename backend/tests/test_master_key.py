"""
Master admin key tests (stable, config-defined admin credential).

The master key must: reach admin routes, resolve to an admin identity, be
rejected when wrong, and never authenticate when it isn't configured. The
comparison is constant-time, but these tests assert behaviour, not timing.
"""

import pytest

from app.core.config import settings

MASTER = "sk-gw-master-testvalue-do-not-reuse"


@pytest.fixture
def master_key(monkeypatch):
    """Turn the master key on for one test, off everywhere else."""
    monkeypatch.setattr(settings, "MASTER_ADMIN_KEY", MASTER)
    return MASTER


def test_master_key_reaches_admin_routes(app_client, master_key):
    h = {"Authorization": f"Bearer {master_key}"}
    assert app_client.get("/v1/admin/users", headers=h).status_code == 200


def test_master_key_resolves_to_an_admin_identity(app_client, master_key):
    r = app_client.get("/v1/me", headers={"x-api-key": master_key})
    assert r.status_code == 200
    assert r.json()["is_admin"] is True


def test_master_key_works_on_user_routes_too(app_client, master_key):
    r = app_client.get("/v1/models", headers={"Authorization": f"Bearer {master_key}"})
    assert r.status_code == 200


def test_wrong_master_key_is_rejected(app_client, master_key):
    h = {"Authorization": "Bearer sk-gw-master-WRONG"}
    assert app_client.get("/v1/admin/users", headers=h).status_code == 401


def test_master_key_does_not_work_when_unset(app_client):
    """With MASTER_ADMIN_KEY unset (the session default), the token is just invalid."""
    assert settings.MASTER_ADMIN_KEY in (None, "")
    h = {"Authorization": f"Bearer {MASTER}"}
    assert app_client.get("/v1/admin/users", headers=h).status_code == 401


def test_blank_master_key_never_matches_empty_token(app_client, monkeypatch):
    """A blank configured key must not let a missing/empty token authenticate."""
    monkeypatch.setattr(settings, "MASTER_ADMIN_KEY", "")
    assert app_client.get("/v1/admin/users").status_code == 401
    assert app_client.get(
        "/v1/admin/users", headers={"Authorization": "Bearer "}
    ).status_code == 401
