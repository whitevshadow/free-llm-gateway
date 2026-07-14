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
# The single source of env config lives at the REPO ROOT (.env), shared by both
# docker compose and local `uvicorn` runs — there is no separate backend/.env.
REPO_ROOT = Path(__file__).resolve().parents[3]

# Hardcoded fallback master admin key — baked in so the gateway has a working
# admin credential with ZERO setup (no .env, no scraping logs). The env var
# MASTER_ADMIN_KEY overrides it.
#
# ⚠ THIS VALUE IS IN SOURCE CONTROL. Anyone with the repo can use it as admin.
#   It is a DEV DEFAULT ONLY. For anything real, set MASTER_ADMIN_KEY in .env to
#   a long random value — the app logs a loud warning at startup while this
#   unchanged default is in effect (see main.py).
DEFAULT_MASTER_ADMIN_KEY = "sk-gw-master-dev-CHANGE-ME-a1b2c3d4e5f6"


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

    # STABLE MASTER ADMIN KEY — a fixed gateway key, defined here rather than minted
    # into the DB. Presenting it authenticates as the owner admin (OWNER_EMAIL),
    # short-circuiting the DB lookup entirely: it never changes, survives a database
    # reset, and never needs scraping from the startup logs. Unset (the default)
    # means no master key exists and only DB-issued keys work.
    #
    # It IS a root credential — treat it exactly like the bootstrap key. Set a long
    # random value (e.g. `sk-gw-master-$(openssl rand -hex 24)`), keep it out of
    # source control, and rotate it by changing this one variable. When set, the
    # bootstrap step skips minting a throwaway admin key (this one is enough).
    #
    # Defaults to the hardcoded DEFAULT_MASTER_ADMIN_KEY above so admin access
    # works out of the box; override it in .env for any real deployment.
    MASTER_ADMIN_KEY: Optional[str] = DEFAULT_MASTER_ADMIN_KEY

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

    # ── Background scheduler (services/scheduler.py) ────
    # Two loops: a daily catalog refresh, and a periodic re-probe of UNHEALTHY
    # deployments only — healthy ones are kept fresh by real traffic for free
    # (record_success/record_failure), so scheduled probes never touch them.
    SCHEDULER_ENABLED: bool = True
    DISCOVERY_INTERVAL_HOURS: int = 24     # catalog refresh cadence
    REPROBE_INTERVAL_MINUTES: int = 20     # unhealthy-deployment re-probe cadence

    model_config = SettingsConfigDict(
        env_file=REPO_ROOT / ".env",   # the ONE .env, at the repo root
        env_file_encoding="utf-8",
        extra="ignore",
    )

    @property
    def cors_origins_list(self) -> List[str]:
        if self.CORS_ORIGINS == "*":
            return ["*"]
        return [o.strip() for o in self.CORS_ORIGINS.split(",")]


settings = Settings()
