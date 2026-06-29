"""
Tests for system endpoints: GET / and GET /health.

These are the FIRST tests you should write for any backend. They verify
that the server boots, middleware runs, and basic routing works.
"""


def test_root_returns_welcome(client):
    """GET / should return 200 with project name and docs link."""
    response = client.get("/")
    assert response.status_code == 200

    data = response.json()
    assert data["status"] == "success"
    assert "Multi-LLM" in data["data"]["name"]
    assert data["data"]["docs"] == "/docs"


def test_health_returns_healthy(client):
    """GET /health should return 200 with healthy status and uptime."""
    response = client.get("/health")
    assert response.status_code == 200

    data = response.json()
    assert data["status"] == "success"
    assert data["data"]["status"] == "healthy"
    assert "uptime_seconds" in data["data"]
    assert isinstance(data["data"]["configured_providers"], list)


def test_health_has_request_id_header(client):
    """Every response should include an X-Request-ID header (from middleware)."""
    response = client.get("/health")
    assert "X-Request-ID" in response.headers


def test_health_has_process_time_header(client):
    """Every response should include X-Process-Time-Ms (from logging middleware)."""
    response = client.get("/health")
    assert "X-Process-Time-Ms" in response.headers


def test_docs_accessible(client):
    """Swagger UI should be served at /docs."""
    response = client.get("/docs")
    assert response.status_code == 200


def test_nonexistent_route_returns_404(client):
    """Hitting an undefined route should return 404, not 500."""
    response = client.get("/api/v1/nonexistent")
    assert response.status_code in (404, 405)
