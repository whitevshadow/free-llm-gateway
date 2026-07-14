"""
Gateway auth + RBAC — the ONLY credential in this system is the gateway key.

There is no login. A caller proves who they are by presenting a token from
`gateway_api_keys`, which resolves to a User. That resolution is the linchpin of
the whole security model: it is what scopes every request to that user's OWN
provider keys, and what `require_admin` reads `is_admin` from.

HOW CLIENTS SEND IT:
  • OpenAI style    →  Authorization: Bearer <token>
  • Anthropic style →  x-api-key: <token>

TWO DEPENDENCIES:
  current_user   any authenticated user. Use on /v1/chat, /v1/models, /v1/me/*.
  require_admin  additionally requires is_admin. Use on every /v1/admin/* route.

⚠ These RETURN THE USER. The old version returned None, which is why every admin
endpoint was reachable by every key holder: with no identity, nothing downstream
could scope or gate anything.

DEV BYPASS: REQUIRE_GATEWAY_AUTH=false opens the *chat* endpoints for local
curling. It DOES NOT open /v1/admin/* — a misconfigured env var must never expose
key management. See _dev_bypass_user().
"""

import secrets
from typing import Optional

from fastapi import Depends, Header, HTTPException, status
from fastapi.security import APIKeyHeader
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import get_db
from app.models.user import User
from app.services import gateway_keys


def _master_admin(token: str, db: Session) -> Optional[User]:
    """
    Resolve the STABLE master admin key (settings.MASTER_ADMIN_KEY) to the owner
    admin, or None if it isn't configured or doesn't match.

    Checked BEFORE the DB so the master key works even on a freshly reset
    database. The comparison is constant-time (secrets.compare_digest) so a wrong
    key can't be recovered a byte at a time by timing the response. A blank
    configured key never matches — otherwise an unset master key would let an
    empty token authenticate as admin.
    """
    master = settings.MASTER_ADMIN_KEY
    if not master or not token:
        return None
    if not secrets.compare_digest(token, master):
        return None
    # Prefer the named owner; fall back to any admin so a renamed OWNER_EMAIL
    # doesn't lock the master key out.
    return (
        db.query(User)
        .filter(User.is_admin.is_(True))
        .order_by((User.email != settings.OWNER_EMAIL), User.id)
        .first()
    )

# Declared purely so Swagger renders an "Authorize" button and sends the key on
# Try-it-out. auto_error=False: we read the header ourselves (two header names).
bearer_scheme = APIKeyHeader(
    name="Authorization",
    auto_error=False,
    description="Gateway key: `Bearer sk-gw-…` (or send it as `x-api-key`).",
)


def _extract_token(authorization: Optional[str], x_api_key: Optional[str]) -> Optional[str]:
    if x_api_key:
        return x_api_key.strip()
    if authorization:
        value = authorization.strip()
        if value.lower().startswith("bearer "):
            return value[7:].strip()
        return value
    return None


def _unauthorized(detail: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=detail,
        headers={"WWW-Authenticate": "Bearer"},
    )


def _resolve(token: Optional[str], db: Session, *, allow_bypass: bool) -> User:
    """
    Turn a presented token into a User.

    `allow_bypass` is the whole point of this being a separate function: the
    REQUIRE_GATEWAY_AUTH=false dev switch may open the CHAT endpoints, but it must
    never open /v1/admin/*. Admin routes call this with allow_bypass=False, so a
    misconfigured env var cannot hand anyone key-management access.
    """
    if not token:
        if allow_bypass and not settings.REQUIRE_GATEWAY_AUTH:
            user = db.query(User).order_by(User.id).first()
            if user:
                return user
        raise _unauthorized("Missing API key.")

    # The stable master admin key resolves to the owner admin, ahead of the DB —
    # so it keeps working across DB resets and is honoured on admin routes too
    # (require_admin passes allow_bypass=False, but this is a real credential, not
    # the dev bypass, so it is checked here regardless).
    master_user = _master_admin(token, db)
    if master_user:
        return master_user

    key = gateway_keys.verify(db, token)
    if not key:
        raise _unauthorized("Invalid or revoked API key.")

    user = db.query(User).filter(User.id == key.user_id).first()
    if not user or not user.is_active:
        raise _unauthorized("The user for this key is inactive or missing.")
    return user


def current_user(
    authorization: Optional[str] = Depends(bearer_scheme),
    x_api_key: Optional[str] = Header(default=None, alias="x-api-key"),
    db: Session = Depends(get_db),
) -> User:
    """Any authenticated user. Honours the dev bypass on chat routes."""
    token = _extract_token(authorization, x_api_key)
    return _resolve(token, db, allow_bypass=True)


def require_admin(
    authorization: Optional[str] = Depends(bearer_scheme),
    x_api_key: Optional[str] = Header(default=None, alias="x-api-key"),
    db: Session = Depends(get_db),
) -> User:
    """
    Admin-only. Seeding providers and minting keys FOR other users are privileged
    operations that a normal key holder must never reach.

    Deliberately does NOT depend on current_user: it re-resolves with
    allow_bypass=False, so a REAL admin key is required even when
    REQUIRE_GATEWAY_AUTH is off.
    """
    token = _extract_token(authorization, x_api_key)
    user = _resolve(token, db, allow_bypass=False)
    if not user.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin privileges required.",
        )
    return user
