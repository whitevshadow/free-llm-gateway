"""
Router-config endpoint tests.

The point of these is not that the row round-trips — it is that the values
actually reach the litellm.Router the user's next request is served by. A
settings endpoint that doesn't change behaviour is just a database with extra
steps.
"""


def test_defaults_before_anything_is_set(app_client, user_key):
    r = app_client.get("/v1/me/router-config",
                       headers={"Authorization": f"Bearer {user_key}"})
    assert r.status_code == 200
    body = r.json()
    assert body["is_default"] is True
    assert body["routing_strategy"] == "usage-based-routing-v2"
    assert body["num_retries"] == 4


def test_partial_update_keeps_other_fields(app_client, user_key):
    h = {"Authorization": f"Bearer {user_key}"}

    r = app_client.put("/v1/me/router-config", headers=h, json={"num_retries": 7})
    assert r.status_code == 200
    body = r.json()
    assert body["num_retries"] == 7
    assert body["cooldown_time"] == 30          # untouched
    assert body["is_default"] is False

    # only cooldown this time; num_retries must survive
    r = app_client.put("/v1/me/router-config", headers=h, json={"cooldown_time": 90})
    assert r.json()["num_retries"] == 7
    assert r.json()["cooldown_time"] == 90


def test_invalid_strategy_is_rejected_here_not_inside_litellm(app_client, user_key):
    """A bad strategy must 400 at the edge, not 502 mid-chat with a baffling error."""
    r = app_client.put(
        "/v1/me/router-config",
        headers={"Authorization": f"Bearer {user_key}"},
        json={"routing_strategy": "make-it-fast-please"},
    )
    assert r.status_code == 400
    assert "routing_strategy" in r.json()["detail"]


def test_out_of_range_is_rejected(app_client, user_key):
    r = app_client.put(
        "/v1/me/router-config",
        headers={"Authorization": f"Bearer {user_key}"},
        json={"num_retries": 999},
    )
    assert r.status_code == 422


def test_config_actually_reaches_the_litellm_router(app_client, user_key, db):
    """The settings must show up on the real Router object, not just in a table."""
    from app.core import llm_router
    from app.models.gateway_api_key import GatewayApiKey
    from app.services.gateway_keys import _hash

    h = {"Authorization": f"Bearer {user_key}"}
    app_client.put("/v1/me/router-config", headers=h,
                   json={"routing_strategy": "simple-shuffle", "num_retries": 2,
                         "cooldown_time": 11})

    user_id = db.query(GatewayApiKey).filter(
        GatewayApiKey.key_hash == _hash(user_key)
    ).one().user_id

    router = llm_router.get_router(user_id, db)
    assert router.routing_strategy == "simple-shuffle"
    assert router.cooldown_time == 11


def test_reset_restores_defaults(app_client, user_key):
    h = {"Authorization": f"Bearer {user_key}"}
    app_client.put("/v1/me/router-config", headers=h, json={"num_retries": 9})
    r = app_client.delete("/v1/me/router-config", headers=h)
    assert r.json()["num_retries"] == 4
    assert app_client.get("/v1/me/router-config", headers=h).json()["is_default"] is True


def test_router_config_requires_auth(app_client):
    assert app_client.get("/v1/me/router-config").status_code == 401
