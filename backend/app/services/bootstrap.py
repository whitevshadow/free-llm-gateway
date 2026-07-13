"""
Bootstrap — make the gateway usable on first startup.

With /v1 auth DB-only and secure-by-default, and key management behind the
gateway-guarded admin API, a fresh database is a chicken-and-egg: you can't mint
the first key without a key. This ensures a single owner user exists and, if no
active gateway key exists, mints one and logs it ONCE so the operator can grab it
from the startup logs.
"""

import logging

from app.core.config import settings
from app.core.database import SessionLocal
from app.models.user import User
from app.models.gateway_api_key import GatewayApiKey
from app.services import gateway_keys

logger = logging.getLogger("gateway.bootstrap")


def ensure_owner_and_bootstrap_key() -> None:
    db = SessionLocal()
    try:
        user = db.query(User).first()
        if not user:
            # No login flow anymore — hashed_password is a required-but-unused
            # column, so store a non-usable sentinel instead of a real hash.
            user = User(
                email=settings.OWNER_EMAIL,
                hashed_password="disabled",
                full_name="Owner",
            )
            db.add(user)
            db.commit()
            db.refresh(user)
            logger.info("Created owner user %s.", settings.OWNER_EMAIL)

        active = (
            db.query(GatewayApiKey)
            .filter(GatewayApiKey.is_active.is_(True))
            .count()
        )
        if active == 0:
            token, _ = gateway_keys.mint(db, user.id, name="bootstrap")
            bar = "=" * 66
            logger.warning(bar)
            logger.warning("BOOTSTRAP GATEWAY KEY (shown once — copy it now):")
            logger.warning("    %s", token)
            logger.warning("Send it as:  Authorization: Bearer <key>   or   x-api-key: <key>")
            logger.warning("Mint more / rotate via  POST /v1/admin/gateway-keys")
            logger.warning(bar)
    finally:
        db.close()
