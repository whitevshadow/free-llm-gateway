"""
Browser session auth — the dashboard's way in.

WHY THIS EXISTS
    The API's only credential is a gateway key (see gateway_auth.py), presented
    on every call as a Bearer token. That is right for programs and wrong for a
    browser: a dashboard cannot hold a bearer token across a page load without
    parking it in localStorage, where any injected script can read it.

    So the dashboard trades its key ONCE, here, for a short-lived signed session
    in an httpOnly cookie. JavaScript cannot read that cookie; a request forgery
    cannot read the response. The key itself never touches browser storage.

WHAT THE "PASSWORD" IS
    The login form asks for a password, because that is the form the ported
    OmniRoute UI renders. What it actually accepts is a GATEWAY KEY — either a
    DB-issued one from `gateway_api_keys` or the master admin key. There is
    still no password table and no signup: this is the same credential the API
    takes, exchanged for a cookie. One credential, two transports.

THE COOKIE
    Name  `auth_token`   — matches what the ported UI already checks for.
    Flags httpOnly, SameSite=Lax, Secure when SESSION_COOKIE_SECURE is on.
          Lax (not Strict) so following a link into the dashboard keeps you
          logged in; the gateway has no cross-site form posts to protect.
    Body  {"sub": <user_id>, "exp": …, "iat": …} signed HS256 with SECRET_KEY.

    The session carries a user id, NOT the key. Revoking a gateway key therefore
    does not kill a live session — sessions expire on their own
    (SESSION_TTL_HOURS). Keep the TTL short rather than reaching for a
    revocation list; the alternative is server-side session state, which this
    service deliberately does not have.
"""

from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, Request, Response, status
from fastapi.responses import JSONResponse
from jose import JWTError, jwt
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import get_db
from app.models.user import User

router = APIRouter(tags=["auth"])

SESSION_COOKIE = "auth_token"
ALGORITHM = "HS256"


class LoginRequest(BaseModel):
    # `password` is the field name the ported dashboard posts. It carries a
    # gateway key — see the module docstring.
    password: str


def issue_session(user: User) -> str:
    """Sign a session token for `user`."""
    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(user.id),
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(hours=settings.SESSION_TTL_HOURS)).timestamp()),
        "adm": bool(user.is_admin),
    }
    return jwt.encode(payload, settings.SECRET_KEY, algorithm=ALGORITHM)


def read_session(token: Optional[str]) -> Optional[int]:
    """
    Verify a session token and return its user id, or None.

    Every failure mode — bad signature, expired, malformed, unparseable subject
    — collapses to None on purpose. The caller's only correct response to any of
    them is "not logged in", and distinguishing them in an error message tells
    an attacker which half of a forged token to fix.
    """
    if not token:
        return None
    try:
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[ALGORITHM])
    except JWTError:
        return None
    sub = payload.get("sub")
    if sub is None:
        return None
    try:
        return int(sub)
    except (TypeError, ValueError):
        return None


def session_user(request: Request, db: Session) -> Optional[User]:
    """Resolve the session cookie on `request` to a live, active user."""
    user_id = read_session(request.cookies.get(SESSION_COOKIE))
    if user_id is None:
        return None
    user = db.query(User).filter(User.id == user_id).first()
    if not user or not user.is_active:
        return None
    return user


def _set_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        key=SESSION_COOKIE,
        value=token,
        httponly=True,
        secure=settings.SESSION_COOKIE_SECURE,
        samesite="lax",
        max_age=settings.SESSION_TTL_HOURS * 3600,
        path="/",
    )


@router.post("/v1/auth/login")
async def login(body: LoginRequest, request: Request, db: Session = Depends(get_db)):
    """
    Exchange a gateway key for a session cookie.

    Resolution deliberately reuses gateway_auth._resolve with allow_bypass=False:
    a browser session must always require a real credential, even when
    REQUIRE_GATEWAY_AUTH is off for local curling. Otherwise flipping a dev env
    var would hand an unauthenticated visitor the dashboard.
    """
    from app.api.gateway_auth import _resolve  # local: avoids a circular import

    from fastapi import HTTPException

    try:
        user = _resolve(body.password.strip(), db, allow_bypass=False)
    except HTTPException:
        # Collapse "no such key" and "revoked key" into one answer — a login form
        # that distinguishes them is a key-validity oracle.
        return JSONResponse(
            {"error": "Invalid credentials."}, status_code=status.HTTP_401_UNAUTHORIZED
        )

    response = JSONResponse(
        {
            "success": True,
            "authenticated": True,
            "user": {"id": user.id, "email": user.email, "isAdmin": user.is_admin},
        }
    )
    _set_cookie(response, issue_session(user))
    return response


@router.post("/v1/auth/logout")
async def logout():
    """Drop the session cookie. Always succeeds — logging out is idempotent."""
    response = JSONResponse({"success": True, "authenticated": False})
    response.delete_cookie(SESSION_COOKIE, path="/")
    return response


@router.get("/v1/auth/status")
async def auth_status(request: Request, db: Session = Depends(get_db)):
    """
    Whether the caller holds a valid session. Never 401s.

    The dashboard polls this to decide between the app shell and the login page,
    so "no" is a normal answer, not an error.
    """
    user = session_user(request, db)
    if not user:
        return {"authenticated": False}
    return {
        "authenticated": True,
        "user": {"id": user.id, "email": user.email, "isAdmin": user.is_admin},
    }
