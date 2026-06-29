"""
Configuration module — single source of truth for all application settings.

Uses pydantic-settings to load values from environment variables and .env files.
Every setting has a sensible default so the app can start with zero configuration
for local development, but all secrets MUST be overridden in production.
"""

import os
from pathlib import Path
from typing import Optional, List
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


BACKEND_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    """
    Application settings loaded from environment variables.

    Hierarchy (highest priority first):
      1. Actual OS environment variables
      2. Values in the .env file
      3. Defaults defined below
    """

    # ── App ──────────────────────────────────────────────
    PROJECT_NAME: str = "Multi-LLM Gateway Platform"
    VERSION: str = "1.0.0"
    API_V1_STR: str = "/api/v1"
    HOST: str = "0.0.0.0"
    PORT: int = 8000
    DEBUG: bool = True
    ENVIRONMENT: str = "development"  # development | staging | production
    LOG_LEVEL: str = "INFO"           # DEBUG | INFO | WARNING | ERROR

    # ── Security & Auth ─────────────────────────────────
    SECRET_KEY: str = Field(default="CHANGE_ME_IN_PRODUCTION_use_openssl_rand_hex_32")
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 1440  # 24 hours

    # ── CORS ────────────────────────────────────────────
    # Comma-separated origins for CORS. "*" allows everything (dev only).
    CORS_ORIGINS: str = "*"

    # ── Rate Limiting ───────────────────────────────────
    RATE_LIMIT: str = "60/minute"  # slowapi format

    # ── Database ────────────────────────────────────────
    DATABASE_URL: str = "sqlite:///./gateway.db"

    # ── LLM Request Defaults ────────────────────────────
    REQUEST_TIMEOUT: int = 120       # seconds per LLM call
    DEFAULT_MAX_TOKENS: int = 2048
    DEFAULT_TEMPERATURE: float = 0.7
    MAX_RETRIES: int = 2             # retries before giving up on a provider

    # ── Smart-Routing Pool (litellm.Router) ─────────────
    # The YAML pool describes every free-provider deployment (provider + model +
    # key) behind the gateway. The Router built from it handles rotation, 429
    # cooldowns, retries and fallbacks across equivalent free backends.
    MODEL_POOL_PATH: str = "config/model_pool.yaml"
    # Where the curated model pool (deployments + router settings) is read from:
    #   auto → use the PostgreSQL tables if they have deployments, else the YAML
    #   db   → always use PostgreSQL (empty pool if not seeded)
    #   yaml → always use the YAML file (ignore the DB tables)
    # Seed the DB from the YAML once with: python -m app.scripts.seed_pool_from_yaml
    MODEL_POOL_SOURCE: str = "auto"
    # When an Anthropic/OpenAI client requests a model we don't recognise as a
    # virtual model (e.g. Claude Code sends "claude-3-5-sonnet"), route it here.
    DEFAULT_VIRTUAL_MODEL: str = "auto"
    # When a NVIDIA key is configured, auto-discover EVERY chat model from
    # NVIDIA's catalog and register it as a callable model (by its full id).
    NVIDIA_AUTO_DISCOVER: bool = True
    # Same for OpenRouter: when a key is configured, auto-discover its catalog and
    # register every model by its full id (e.g. "deepseek/deepseek-r1:free").
    OPENROUTER_AUTO_DISCOVER: bool = True
    # Only register OpenRouter models that are free (":free" or zero-priced). Set
    # False to also expose paid models (they need credits on the account).
    OPENROUTER_FREE_ONLY: bool = True
    # When True, the Router hides any deployment a manual availability probe
    # marked unavailable (see app/scripts/probe_models.py). No effect until a
    # probe has actually populated the model_availability table.
    FILTER_BY_AVAILABILITY: bool = True

    # ── Gateway Auth (for /v1 compatibility endpoints) ──
    # The token external software (Claude Code, OpenAI SDK clients) must present.
    # Distinct from the JWT used by the UI routes. Open by default for local dev.
    GATEWAY_API_KEY: Optional[str] = None
    REQUIRE_GATEWAY_AUTH: bool = False

    # ── Provider API Keys ───────────────────────────────
    # Numbered free keys (GROQ_API_KEY_1, GROQ_API_KEY_2, ...) are read directly
    # from the environment by the YAML pool via `os.environ/<VAR>` references, so
    # they do NOT need to be declared here. Legacy single-key fields are kept for
    # backwards compatibility with the existing UI path.
    OPENAI_API_KEY: Optional[str] = None
    ANTHROPIC_API_KEY: Optional[str] = None
    GEMINI_API_KEY: Optional[str] = None
    DEEPSEEK_API_KEY: Optional[str] = None
    XAI_API_KEY: Optional[str] = None
    NVIDIA_NIM_API_KEY: Optional[str] = None
    OLLAMA_API_BASE: str = "http://localhost:11434"

    model_config = SettingsConfigDict(
        env_file=BACKEND_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ── Helpers ─────────────────────────────────────────
    @property
    def cors_origins_list(self) -> List[str]:
        """Parse CORS_ORIGINS string into a list."""
        if self.CORS_ORIGINS == "*":
            return ["*"]
        return [origin.strip() for origin in self.CORS_ORIGINS.split(",")]

    # Maps a free-provider id to the environment-variable prefixes that, when
    # present, mean the provider has at least one usable key in the pool.
    _PROVIDER_ENV_PREFIXES = {
        "groq":       ("GROQ_API_KEY",),
        "openrouter": ("OPENROUTER_API_KEY",),
        "gemini":     ("GEMINI_API_KEY", "GOOGLE_API_KEY"),
        "cerebras":   ("CEREBRAS_API_KEY",),
        "github":     ("GITHUB_MODELS_TOKEN", "GITHUB_API_KEY"),
        "mistral":    ("MISTRAL_API_KEY",),
        "deepseek":   ("DEEPSEEK_API_KEY",),
        "nvidia_nim": ("NVIDIA_API_KEY", "NVIDIA_NIM_API_KEY"),
    }

    def get_configured_providers(self) -> List[str]:
        """
        Return the free providers that have at least one key configured.

        Detection scans the environment for numbered/plain keys (e.g.
        GROQ_API_KEY, GROQ_API_KEY_1, GROQ_API_KEY_2), so adding another free
        account is just another env var — no code change needed.
        """
        env_keys = list(os.environ.keys())
        providers: List[str] = []
        for provider_id, prefixes in self._PROVIDER_ENV_PREFIXES.items():
            for prefix in prefixes:
                if any(
                    k == prefix or k.startswith(f"{prefix}_")
                    for k in env_keys
                    if os.environ.get(k)
                ):
                    providers.append(provider_id)
                    break
        # Ollama is always "available" (local)
        providers.append("ollama")
        return providers


# Singleton instance used across the application
settings = Settings()
