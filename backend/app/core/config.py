"""
Configuration — process-level settings only.

WHAT DOES *NOT* LIVE HERE ANY MORE, and must not come back:

  • Provider API keys.  They are per-USER, encrypted, in the provider_keys table.
    An env var is process-global, so a key here would be shared by every user —
    which is exactly the bug the multi-user rewrite removed. There is no
    GROQ_API_KEY, no NVIDIA_API_KEY, no OPENAI_API_KEY.

  • The model pool / router source.  There is no YAML pool and no global model
    list. A user's callable models ARE their deployments; each user's Router is
    built from them on demand (core/llm_router.py).

  • Router behaviour (retries, cooldowns, strategy). That is PER USER, in the
    router_config table, editable via GET/PUT /v1/me/router-config.

Everything remaining below is genuinely process-wide: how the server runs, how it
talks to Postgres, and the key used to encrypt secrets at rest.
"""

from pathlib import Path
from typing import Optional, List

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    """
    Loaded from (highest priority first): OS environment → .env → defaults below.
    """

    # ── App ──────────────────────────────────────────────
    PROJECT_NAME: str = "Multi-LLM Gateway"
    VERSION: str = "2.0.0"
    HOST: str = "0.0.0.0"
    PORT: int = 8000
    DEBUG: bool = True
    ENVIRONMENT: str = "development"   # development | staging | production
    LOG_LEVEL: str = "INFO"            # DEBUG | INFO | WARNING | ERROR

    # ── Security ────────────────────────────────────────
    SECRET_KEY: str = Field(default="CHANGE_ME_IN_PRODUCTION_use_openssl_rand_hex_32")

    # Fernet key encrypting provider secrets at rest. If unset, one is derived from
    # SECRET_KEY (dev convenience) and a warning is logged — set a dedicated key in
    # production, so rotating SECRET_KEY cannot brick every stored provider key.
    # Generate:  python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
    ENCRYPTION_KEY: Optional[str] = None

    # The first ADMIN. Created on first startup with a gateway key, logged once —
    # there is no signup flow, so this is the only way into a fresh database.
    OWNER_EMAIL: str = "admin@localhost"

    # /v1 auth is DB-issued gateway keys only. Secure by default.
    # Setting this false opens the CHAT endpoints for local dev — it does NOT open
    # /v1/admin/* (see api/gateway_auth.py: require_admin never honours the bypass).
    REQUIRE_GATEWAY_AUTH: bool = True

    # ── CORS ────────────────────────────────────────────
    CORS_ORIGINS: str = "*"            # comma-separated; "*" is dev-only

    # ── Database ────────────────────────────────────────
    # POSTGRES ONLY. SQLite cannot express the generated columns, triggers, enums
    # and composite FKs that enforce cross-user key isolation; the app refuses to
    # start on a sqlite:// URL (see core/database.py).
    DATABASE_URL: str = "postgresql://postgres:postgres@localhost:5432/gateway"

    # ── LLM request defaults ────────────────────────────
    REQUEST_TIMEOUT: int = 120         # seconds per upstream call
    DEFAULT_MAX_TOKENS: int = 2048     # supplied when an Anthropic client omits it

    model_config = SettingsConfigDict(
        env_file=BACKEND_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    @property
    def cors_origins_list(self) -> List[str]:
        if self.CORS_ORIGINS == "*":
            return ["*"]
        return [o.strip() for o in self.CORS_ORIGINS.split(",")]


settings = Settings()
