"""
Crypto — symmetric encryption for provider secrets at rest (Fernet / AES-128-CBC
+ HMAC) and masking helpers.

KEY SOURCE (see settings.ENCRYPTION_KEY):
  • ENCRYPTION_KEY set to a valid Fernet key  → use it directly.
  • ENCRYPTION_KEY set to any other string     → derive a Fernet key from it.
  • ENCRYPTION_KEY unset                        → derive from SECRET_KEY and warn.

Only ciphertext (bytes) is ever stored; plaintext is decrypted on demand to inject
keys into the environment, and never returned by the API (only a masked preview).
"""

import base64
import hashlib
import logging
from functools import lru_cache

from cryptography.fernet import Fernet

from app.core.config import settings

logger = logging.getLogger("gateway.crypto")


def _derive_fernet_key(material: str) -> bytes:
    """Turn an arbitrary secret string into a valid 32-byte url-safe Fernet key."""
    digest = hashlib.sha256(material.encode("utf-8")).digest()
    return base64.urlsafe_b64encode(digest)


@lru_cache(maxsize=1)
def _fernet() -> Fernet:
    key = settings.ENCRYPTION_KEY
    if key:
        try:
            return Fernet(key.encode("utf-8") if isinstance(key, str) else key)
        except Exception:
            # Not a raw Fernet key — derive a valid one from whatever was provided.
            return Fernet(_derive_fernet_key(key))
    logger.warning(
        "ENCRYPTION_KEY not set — deriving the encryption key from SECRET_KEY. "
        "Set a dedicated ENCRYPTION_KEY in production."
    )
    return Fernet(_derive_fernet_key(settings.SECRET_KEY))


def encrypt(plaintext: str) -> bytes:
    """Encrypt a secret. Returns ciphertext bytes for the BYTEA column."""
    return _fernet().encrypt(plaintext.encode("utf-8"))


def decrypt(ciphertext: bytes) -> str:
    """Decrypt ciphertext bytes back to the original secret string."""
    return _fernet().decrypt(bytes(ciphertext)).decode("utf-8")


def mask(value: str) -> str:
    """Safe, non-secret preview of a key: ••••<last 4>."""
    if not value:
        return ""
    if len(value) <= 4:
        return "••••"
    return "••••" + value[-4:]
