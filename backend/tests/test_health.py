def test_health_is_public(app_client):
    r = app_client.get("/health")
    assert r.status_code == 200
    assert r.json()["data"]["status"] == "healthy"
