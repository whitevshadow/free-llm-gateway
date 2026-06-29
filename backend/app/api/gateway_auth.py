"""
Gateway auth — lightweight token check for the /v1 compatibility endpoints.

WHY SEPARATE FROM THE JWT:
  The UI routes (/api/v1/...) authenticate users with a JWT. The /v1 endpoints,
  by contrast, are consumed by *software* — Claude Code, the OpenAI SDK, curl,
  Cursor, etc. Those clients present a single static token, the way they would to
  OpenAI or Anthropic. This module validates that token.

HOW CLIENTS SEND IT:
  • OpenAI style    →  Authorization: Bearer <token>
  • Anthropic style →  x-api-key: <token>

BEHAVIOUR:
  • REQUIRE_GATEWAY_AUTH = False (default, local dev) → allow everything.
  • REQUIRE_GATEWAY_AUTH = True  → the presented token must equal GATEWAY_API_KEY.
"""

from typing import Optional

from fastapi import Header, HTTPException, status

from app.core.config import settings


def _extract_token(authorization: Optional[str], x_api_key: Optional[str]) -> Optional[str]:
    if x_api_key:
        return x_api_key.strip()
    if authorization:
        value = authorization.strip()
        if value.lower().startswith("bearer "):
            return value[7:].strip()
        return value
    return None


async def verify_gateway_key(
    authorization: Optional[str] = Header(default=None),
    x_api_key: Optional[str] = Header(default=None, alias="x-api-key"),
) -> None:
    """FastAPI dependency: enforce the gateway token when auth is required."""
    if not settings.REQUIRE_GATEWAY_AUTH:
        return

    if not settings.GATEWAY_API_KEY:
        # Misconfiguration: auth required but no key set. Fail closed.
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Gateway auth is enabled but GATEWAY_API_KEY is not set.",
        )

    token = _extract_token(authorization, x_api_key)
    if token != settings.GATEWAY_API_KEY:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing API key.",
            headers={"WWW-Authenticate": "Bearer"},
        )
