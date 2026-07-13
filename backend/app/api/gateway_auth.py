"""
Gateway auth — token check for the /v1 compatibility endpoints.

The /v1 endpoints are consumed by software (Claude Code, the OpenAI SDK, curl,
Cursor). Those clients present a single bearer token, the way they would to
OpenAI or Anthropic. This module validates that token against the DB-issued
gateway keys (hashed in `gateway_api_keys`).

HOW CLIENTS SEND IT:
  • OpenAI style    →  Authorization: Bearer <token>
  • Anthropic style →  x-api-key: <token>

BEHAVIOUR:
  • REQUIRE_GATEWAY_AUTH = False → allow everything (local dev).
  • REQUIRE_GATEWAY_AUTH = True (default) → the presented token must hash to an
    active row in gateway_api_keys. Mint one via /v1/admin/gateway-keys; a
    bootstrap key is minted + logged on first startup.
"""

from typing import Optional

from fastapi import Header, HTTPException, status

from app.core.config import settings
from app.core.database import SessionLocal
from app.services import gateway_keys


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
    """FastAPI dependency: enforce a valid DB-issued gateway key when required."""
    if not settings.REQUIRE_GATEWAY_AUTH:
        return

    token = _extract_token(authorization, x_api_key)
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing API key.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    db = SessionLocal()
    try:
        row = gateway_keys.verify(db, token)
    finally:
        db.close()

    if not row:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or revoked API key.",
            headers={"WWW-Authenticate": "Bearer"},
        )
