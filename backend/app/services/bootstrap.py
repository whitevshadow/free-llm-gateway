"""
Bootstrap — make the gateway usable on first startup.

There is no signup flow, and minting a key requires an admin key: a fresh database
is therefore a chicken-and-egg. This creates the first ADMIN user and mints their
gateway key, logging it ONCE so the operator can grab it from the startup logs.

That key is the root of the whole system: it is the only way to seed providers and
to mint keys for other users.
"""

import logging

from app.core.config import settings
from app.core.database import SessionLocal
from app.models.user import User
from app.models.gateway_api_key import GatewayApiKey
from app.services import gateway_keys

logger = logging.getLogger("gateway.bootstrap")


def ensure_admin_and_bootstrap_key() -> None:
    """Create the admin user + their key if the database has no admin yet."""
    db = SessionLocal()
    try:
        admin = db.query(User).filter(User.is_admin.is_(True)).first()
        if not admin:
            admin = User(email=settings.OWNER_EMAIL, is_admin=True)
            db.add(admin)
            db.commit()
            db.refresh(admin)
            logger.info("Created admin user %s.", settings.OWNER_EMAIL)

        # A configured master key IS the admin credential — stable and DB-independent.
        # Don't also mint a throwaway bootstrap key nobody would use; just point the
        # operator at the one they set.
        if settings.MASTER_ADMIN_KEY:
            logger.info(
                "MASTER_ADMIN_KEY is set — using it as the admin credential for %s. "
                "No bootstrap key minted.", settings.OWNER_EMAIL,
            )
            return

        live = (
            db.query(GatewayApiKey)
            .filter(
                GatewayApiKey.user_id == admin.id,
                GatewayApiKey.revoked_at.is_(None),
            )
            .count()
        )
        if live == 0:
            token, _ = gateway_keys.mint(db, admin.id, name="bootstrap-admin")
            bar = "=" * 66
            logger.warning(bar)
            logger.warning("BOOTSTRAP ADMIN KEY (shown once — copy it now):")
            logger.warning("    %s", token)
            logger.warning("Send as:  Authorization: Bearer <key>   or   x-api-key: <key>")
            logger.warning("Seed providers:  POST /v1/admin/providers")
            logger.warning("Mint user keys:  POST /v1/admin/gateway-keys")
            logger.warning("Tip: set MASTER_ADMIN_KEY in .env for a stable admin key.")
            logger.warning(bar)
    finally:
        db.close()
