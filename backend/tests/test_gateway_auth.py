"""
Phase 2 — /v1 auth (DB-issued gateway keys), crypto, and provider-key management.
"""

from app.core.database import SessionLocal
from app.models.user import User
from app.services import gateway_keys, crypto


def _mint() -> str:
    """Mint a gateway key directly (bypassing the guarded admin endpoint)."""
    db = SessionLocal()
    try:
        user = db.query(User).first()
        token, _ = gateway_keys.mint(db, user.id, name="pytest")
        return token
    finally:
        db.close()


# ── crypto ──────────────────────────────────────────────────────────────────

def test_crypto_round_trip():
    secret = "gsk_secret_ABCD1234"
    ct = crypto.encrypt(secret)
    assert bytes(ct) != secret.encode()          # not plaintext
    assert crypto.decrypt(ct) == secret          # recoverable
    assert crypto.mask(secret) == "••••1234"     # safe preview


# ── /v1 auth enforcement ────────────────────────────────────────────────────

def test_v1_requires_key(client):
    assert client.get("/v1/models").status_code == 401


def test_v1_rejects_bad_key(client):
    r = client.get("/v1/models", headers={"Authorization": "Bearer sk-gw-nope"})
    assert r.status_code == 401


def test_v1_accepts_minted_key(client):
    token = _mint()
    r = client.get("/v1/models", headers={"Authorization": "Bearer " + token})
    assert r.status_code == 200


def test_health_stays_open(client):
    assert client.get("/health").status_code == 200


# ── admin key management (guarded) ──────────────────────────────────────────

def test_admin_mint_and_provider_key_flow(client):
    auth = {"Authorization": "Bearer " + _mint()}

    # mint a second key via the admin endpoint — raw token returned once
    r = client.post("/v1/admin/gateway-keys", json={"name": "ci"}, headers=auth)
    assert r.status_code == 200
    body = r.json()
    assert body["token"].startswith("sk-gw-")
    assert client.get("/v1/models", headers={"Authorization": "Bearer " + body["token"]}).status_code == 200

    # save a provider key; response is masked, listing carries no plaintext
    r = client.put(
        "/v1/admin/provider-keys",
        json={"provider": "groq", "env_slot": "GROQ_API_KEY_TEST", "value": "gsk_live_4242"},
        headers=auth,
    )
    assert r.status_code == 200
    assert r.json()["masked"] == "••••4242"

    listing = client.get("/v1/admin/provider-keys", headers=auth).json()["keys"]
    row = next(k for k in listing if k["env_slot"] == "GROQ_API_KEY_TEST")
    assert row["masked"] == "••••4242"
    assert "value" not in row and "key_ciphertext" not in row
