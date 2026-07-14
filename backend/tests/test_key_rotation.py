"""
Admin gateway-key rotation tests.

Rotation must: hand back a working new key, kill the old ones when asked, keep
them when not, refuse non-admins, and NEVER disturb a configured master key.

Every revoke-path test uses a DEDICATED throwaway admin, never the shared owner
admin — rotation revokes *all* of a user's keys, and clobbering the session-wide
admin_key would break unrelated tests.
"""

import uuid

import pytest

from app.core.config import settings


@pytest.fixture
def throwaway_admin(db):
    """A fresh admin user + a live gateway key, isolated from the shared owner."""
    from app.models.user import User
    from app.services import gateway_keys

    user = User(email=f"rot-{uuid.uuid4().hex[:8]}@test", is_admin=True)
    db.add(user)
    db.commit()
    db.refresh(user)
    token, _ = gateway_keys.mint(db, user.id, name="original")
    return token


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


def test_rotate_returns_a_working_new_key(app_client, throwaway_admin):
    r = app_client.post("/v1/admin/gateway-keys/rotate", headers=_auth(throwaway_admin),
                        json={"revoke_existing": False})
    assert r.status_code == 200
    new_token = r.json()["token"]
    assert new_token and new_token != throwaway_admin
    # The brand-new key authenticates on an admin route.
    assert app_client.get("/v1/admin/users", headers=_auth(new_token)).status_code == 200


def test_rotate_revokes_the_old_key(app_client, throwaway_admin):
    r = app_client.post("/v1/admin/gateway-keys/rotate", headers=_auth(throwaway_admin),
                        json={"revoke_existing": True})
    assert r.status_code == 200
    body = r.json()
    assert body["revoked_previous"] >= 1

    # Old key is dead…
    assert app_client.get("/v1/admin/users", headers=_auth(throwaway_admin)).status_code == 401
    # …new key lives.
    assert app_client.get("/v1/admin/users", headers=_auth(body["token"])).status_code == 200


def test_rotate_can_keep_existing_keys(app_client, throwaway_admin):
    r = app_client.post("/v1/admin/gateway-keys/rotate", headers=_auth(throwaway_admin),
                        json={"revoke_existing": False})
    assert r.json()["revoked_previous"] == 0
    # Original still works because we asked to keep it.
    assert app_client.get("/v1/admin/users", headers=_auth(throwaway_admin)).status_code == 200


def test_rotate_defaults_to_revoking(app_client, throwaway_admin):
    """No body at all → a real rotation (revoke_existing defaults to true)."""
    r = app_client.post("/v1/admin/gateway-keys/rotate", headers=_auth(throwaway_admin))
    assert r.status_code == 200
    assert r.json()["revoked_previous"] >= 1
    assert app_client.get("/v1/admin/users", headers=_auth(throwaway_admin)).status_code == 401


def test_non_admin_cannot_rotate(app_client, user_key):
    r = app_client.post("/v1/admin/gateway-keys/rotate", headers=_auth(user_key))
    assert r.status_code == 403


def test_rotation_never_revokes_the_master_key(app_client, monkeypatch):
    """A master-key-authenticated rotation mints a DB key but leaves the config
    master key fully intact."""
    master = "sk-gw-master-rotation-test"
    monkeypatch.setattr(settings, "MASTER_ADMIN_KEY", master)

    # Rotate as the master key; keep existing so we don't disturb the owner's
    # shared admin_key (revoke_existing would target the owner admin's DB keys).
    r = app_client.post("/v1/admin/gateway-keys/rotate", headers=_auth(master),
                        json={"revoke_existing": False})
    assert r.status_code == 200
    # The master key still authenticates — it was never a DB row to revoke.
    assert app_client.get("/v1/admin/users", headers=_auth(master)).status_code == 200
