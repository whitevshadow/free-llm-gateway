"""
SRS §4 / §5 — declared but not implemented.

These endpoints exist to make a documented gap VISIBLE and machine-readable
instead of silent. The dashboard is built to the SRS, so it renders a
registration form and a three-role selector; both call in here and get a 501
that says exactly what is missing and why. The alternative — a 404 — is
indistinguishable from a routing bug, and a fake success is worse than either.

TWO GAPS
    §4 "Users shall register and log in."
        There is no password table and no registration. The gateway key IS the
        credential (see the module docstring on models/user.py): a caller proves
        identity by presenting a token from `gateway_api_keys`. The browser
        exchanges that key for a session cookie at /v1/auth/login
        (api/session_auth.py). Self-service registration would mean adding a
        second, weaker way in — a password — to a system that deliberately has
        none, so it is not something to add casually to satisfy a sentence.

    §5 "Admin / Developer / Viewer."
        `users.is_admin` is a boolean. Two roles, not three. Every management
        route is gated by `require_admin`, so today Developer and Viewer are the
        same role: "not admin". Splitting them needs a roles table AND a
        permission check on every mutating endpoint — a read-only Viewer that is
        merely hidden from the nav is not read-only.

WHEN THESE ARE IMPLEMENTED, delete this module and its router registration. It
should not outlive the gap it documents.
"""

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.api.gateway_auth import current_user
from app.core.database import get_db
from app.models.user import User

router = APIRouter(tags=["SRS gaps (not implemented)"])

_DOC = "SRS.md"


def _not_implemented(section: str, what: str, why: str, instead: str) -> JSONResponse:
    """
    The single shape every gap returns.

    `code` is stable and machine-readable so the dashboard can branch on it;
    the prose is for whoever is reading the network tab.
    """
    return JSONResponse(
        status_code=501,
        content={
            "error": what,
            "code": "srs_not_implemented",
            "notImplemented": True,
            "srs_section": section,
            "reason": why,
            "use_instead": instead,
            "docs": _DOC,
        },
    )


# ── §4 Authentication ───────────────────────────────────────────────────────

@router.post("/v1/auth/register")
async def register():
    """SRS §4 registration. Not implemented — see the module docstring."""
    return _not_implemented(
        "§4 Authentication",
        "Self-service registration is not implemented.",
        "The gateway has no password store. A gateway key issued by an admin is "
        "the only credential; adding registration would introduce a second, "
        "weaker way in.",
        "An admin mints a key with POST /v1/admin/gateway-keys, and you sign in "
        "with it at /v1/auth/login.",
    )


@router.post("/v1/auth/password")
async def change_password():
    """SRS §4 profile/password management. Not implemented."""
    return _not_implemented(
        "§4 Authentication",
        "Password management is not implemented.",
        "There is no password to change — see /v1/auth/register.",
        "Rotate your credential instead: POST /v1/admin/gateway-keys/rotate.",
    )


# ── §5 RBAC ─────────────────────────────────────────────────────────────────

@router.get("/v1/roles")
async def list_roles(user: User = Depends(current_user), db: Session = Depends(get_db)):
    """
    The roles that actually exist, and the ones the SRS asks for.

    This one is NOT a 501: the dashboard needs to render a role selector, and
    the honest answer is "here are two real roles and one that is documented but
    not enforced". Returning that is more useful than refusing to answer, and it
    lets the UI disable what it cannot honour instead of guessing.
    """
    return {
        "implemented": [
            {
                "id": "admin",
                "name": "Admin",
                "enforced": True,
                "description": (
                    "Manage users and providers, run catalog sync, mint gateway "
                    "keys. Enforced by require_admin on every /v1/admin route."
                ),
            },
            {
                "id": "user",
                "name": "Developer",
                "enforced": True,
                "description": (
                    "Add provider keys, call the gateway, re-probe own keys. "
                    "Any authenticated non-admin."
                ),
            },
        ],
        "declared_not_implemented": [
            {
                "id": "viewer",
                "name": "Viewer",
                "enforced": False,
                "srs_section": "§5 RBAC",
                "description": (
                    "Read-only. Not implemented: users.is_admin is a boolean, so "
                    "a non-admin is a Developer. A Viewer that is only hidden "
                    "from the nav would still be able to POST."
                ),
            },
        ],
        "current_user_role": "admin" if user.is_admin else "user",
        "docs": _DOC,
    }


@router.put("/v1/admin/users/{user_id}/role")
async def set_role(user_id: int):
    """SRS §5 role assignment. Not implemented beyond the admin boolean."""
    return _not_implemented(
        "§5 RBAC",
        "Role assignment is not implemented.",
        "users.is_admin is a boolean — there are two roles, not three, and no "
        "roles table to assign from.",
        "Create the user as an admin or not at creation time: "
        "POST /v1/admin/users.",
    )
