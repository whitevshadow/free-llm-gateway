"""
Central Router Aggregator

In the single-model consolidation the gateway became a pure OpenAI/Anthropic-
compatible service: the built-in SPA and its native `/api/v1` routes (auth, chat,
analytics, providers, settings — all JWT/user-scoped) were retired in favour of
Open WebUI talking to the `/v1` endpoints. Provider-key and pool management now
live under the gateway-key-guarded `/v1/admin` surface.

This aggregator is intentionally empty; it stays so new gateway-scoped routers can
be mounted here without touching main.py.
"""

from fastapi import APIRouter

api_router = APIRouter()
