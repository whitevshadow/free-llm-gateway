"""
Tests for POST /api/v1/chat — the stateless chat endpoint.

TESTING STRATEGY:
  We can't call real LLM providers in CI (no API keys, costs money, slow).
  So we use unittest.mock to patch `generate_completion` and
  `completion_with_fallback` to return predictable responses.
  This is exactly how backend teams at every company test external APIs.
"""

from unittest.mock import patch, MagicMock


MOCK_SUCCESS_RESULT = {
    "success": True,
    "content": "Hello! I'm a mocked AI response.",
    "provider": "openai",
    "model": "gpt-4o-mini",
    "prompt_tokens": 10,
    "completion_tokens": 15,
    "total_tokens": 25,
    "cost": 0.000025,
    "latency": 0.45,
    "error": None,
}

MOCK_FAILURE_RESULT = {
    "success": False,
    "content": "",
    "provider": "openai",
    "model": "gpt-4o-mini",
    "prompt_tokens": 0,
    "completion_tokens": 0,
    "total_tokens": 0,
    "cost": 0.0,
    "latency": 0.0,
    "error": "Rate limit exceeded",
}


class TestStatelessChat:
    """Tests for POST /api/v1/chat"""

    @patch("app.api.chat.completion_with_fallback", return_value=MOCK_SUCCESS_RESULT)
    def test_chat_success(self, mock_llm, client):
        """A valid chat request should return 200 with unified format."""
        response = client.post(
            "/api/v1/chat",
            json={
                "provider": "openai",
                "model": "gpt-4o-mini",
                "message": "Hello, who are you?",
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "success"
        assert data["data"]["provider"] == "openai"
        assert data["data"]["response"] == "Hello! I'm a mocked AI response."
        assert data["data"]["tokens_used"]["total"] == 25
        assert data["data"]["latency_seconds"] == 0.45

    @patch("app.api.chat.generate_completion", return_value=MOCK_SUCCESS_RESULT)
    def test_chat_without_fallback(self, mock_llm, client):
        """When use_fallback=false, should call generate_completion directly."""
        response = client.post(
            "/api/v1/chat",
            json={
                "provider": "openai",
                "model": "gpt-4o-mini",
                "message": "Hello",
                "use_fallback": False,
            },
        )
        assert response.status_code == 200
        mock_llm.assert_called_once()

    def test_chat_invalid_provider(self, client):
        """An unknown provider/model combo should return 400."""
        response = client.post(
            "/api/v1/chat",
            json={
                "provider": "nonexistent",
                "model": "fake-model",
                "message": "Hello",
            },
        )
        assert response.status_code == 400

    def test_chat_empty_message_rejected(self, client):
        """An empty message should be rejected by Pydantic validation."""
        response = client.post(
            "/api/v1/chat",
            json={
                "provider": "openai",
                "model": "gpt-4o-mini",
                "message": "",
            },
        )
        assert response.status_code == 422  # Pydantic validation error

    def test_chat_missing_provider_rejected(self, client):
        """Missing required fields should return 422."""
        response = client.post(
            "/api/v1/chat",
            json={"message": "Hello"},
        )
        assert response.status_code == 422


class TestChatSessions:
    """Tests for session-based chat endpoints."""

    def test_create_session(self, client):
        response = client.post("/api/v1/chat/sessions")
        assert response.status_code == 200
        data = response.json()
        assert data["title"] == "New Chat"
        assert "id" in data

    def test_list_sessions(self, client):
        # Create one first
        client.post("/api/v1/chat/sessions")
        response = client.get("/api/v1/chat/sessions")
        assert response.status_code == 200
        assert isinstance(response.json(), list)
        assert len(response.json()) >= 1

    def test_get_nonexistent_session(self, client):
        response = client.get("/api/v1/chat/sessions/nonexistent-id-12345")
        assert response.status_code == 404
