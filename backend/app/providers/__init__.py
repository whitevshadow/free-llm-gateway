"""
Provider Registry — the single source of truth for all LLM providers.

WHY A REGISTRY MATTERS:
  Without a registry, provider information gets scattered across route handlers,
  service classes, and config files. When you add a new provider you'd have to
  hunt through 5+ files. With a registry:
    1. Adding a new provider = adding one dict entry here.
    2. Route handlers, services, and fallback logic all read from this module.
    3. The MODEL_MAP, display metadata, and LiteLLM formatting rules live together.

HOW COMPANIES DO IT:
  At scale, this registry would be stored in a database or a YAML config fetched
  at startup. For our project, a Python dict is perfect — it's fast, type-safe,
  and version-controlled.
"""

from typing import Dict, List, Any, Optional
from app.core.config import settings


# ───────────────────────────────────────────────────────────────
# MODEL MAP
# ───────────────────────────────────────────────────────────────
# Maps a human-friendly "provider/model" pair to the exact string
# that LiteLLM expects. LiteLLM uses provider-prefixed model names
# (e.g. "deepseek/deepseek-chat") while OpenAI models have no prefix.
#
# SCALABILITY: to add a new model, just add one line here.
# ───────────────────────────────────────────────────────────────

MODEL_MAP: Dict[str, str] = {
    # OpenAI — no prefix needed, LiteLLM recognises them natively
    "openai/gpt-4o":               "gpt-4o",
    "openai/gpt-4o-mini":          "gpt-4o-mini",
    "openai/gpt-3.5-turbo":        "gpt-3.5-turbo",

    # Anthropic Claude
    "anthropic/claude-3-5-sonnet":         "anthropic/claude-3-5-sonnet-20241022",
    "anthropic/claude-3-5-haiku":          "anthropic/claude-3-5-haiku-20241022",
    "anthropic/claude-3-opus":             "anthropic/claude-3-opus-20240229",

    # Google Gemini
    "gemini/gemini-2.5-flash":     "gemini/gemini-2.5-flash",
    "gemini/gemini-2.5-pro":       "gemini/gemini-2.5-pro",
    "gemini/gemini-2.0-flash":     "gemini/gemini-2.0-flash",

    # DeepSeek
    "deepseek/deepseek-chat":      "deepseek/deepseek-chat",
    "deepseek/deepseek-coder":     "deepseek/deepseek-coder",

    # xAI / Grok
    "xai/grok-beta":               "xai/grok-beta",

    # NVIDIA NIM
    "nvidia/llama-3.1-8b-instruct": "nvidia_nim/meta/llama-3.1-8b-instruct",

    # Ollama (local)
    "ollama/llama3":               "ollama/llama3",
    "ollama/mistral":              "ollama/mistral",
}


# ───────────────────────────────────────────────────────────────
# PROVIDER CATALOG
# ───────────────────────────────────────────────────────────────
# Rich metadata used by the GET /providers endpoint and the frontend.
# `is_configured` is computed dynamically at request-time using the
# helper below, NOT hardcoded to True.
# ───────────────────────────────────────────────────────────────

# Display metadata for the FREE providers the gateway aggregates. `is_configured`
# is computed live from the environment (any of the provider's keys present).
FREE_PROVIDER_INFO: List[Dict[str, Any]] = [
    {"id": "groq",       "name": "Groq",                "icon": "⚡", "note": "Fast free tier: gpt-oss, Llama, Qwen, DeepSeek-distill"},
    {"id": "openrouter", "name": "OpenRouter (:free)",  "icon": "🔀", "note": "Many ':free' models behind one key"},
    {"id": "gemini",     "name": "Google AI Studio",    "icon": "✨", "note": "Generous free Gemini 2.x tier"},
    {"id": "cerebras",   "name": "Cerebras",            "icon": "🟣", "note": "Fast free Llama / Qwen / gpt-oss"},
    {"id": "github",     "name": "GitHub Models",       "icon": "🐙", "note": "Free with a GitHub account / PAT"},
    {"id": "mistral",    "name": "Mistral",             "icon": "🌫️", "note": "Mistral free tier"},
    {"id": "deepseek",   "name": "DeepSeek",            "icon": "🧠", "note": "DeepSeek direct API"},
    {"id": "nvidia_nim", "name": "NVIDIA NIM",          "icon": "🟩", "note": "integrate.api.nvidia.com — DeepSeek, Llama, gpt-oss & more"},
    {"id": "ollama",     "name": "Ollama (Local)",      "icon": "🦙", "note": "Runs locally, no key needed"},
]


# ───────────────────────────────────────────────────────────────
# FALLBACK CHAINS
# ───────────────────────────────────────────────────────────────
# When a primary provider fails (rate-limit, downtime, auth error),
# we try these alternatives in order.
#
# Format: { "provider_id": [ ("fallback_provider", "fallback_model"), ... ] }
# ───────────────────────────────────────────────────────────────

FALLBACK_CHAINS: Dict[str, List[Dict[str, str]]] = {
    "openai": [
        {"provider": "anthropic", "model": "claude-3-5-sonnet"},
        {"provider": "gemini",    "model": "gemini-2.5-flash"},
    ],
    "anthropic": [
        {"provider": "openai",  "model": "gpt-4o"},
        {"provider": "gemini",  "model": "gemini-2.5-flash"},
    ],
    "gemini": [
        {"provider": "openai",  "model": "gpt-4o-mini"},
    ],
    "deepseek": [
        {"provider": "openai",  "model": "gpt-4o-mini"},
        {"provider": "gemini",  "model": "gemini-2.5-flash"},
    ],
    "xai": [
        {"provider": "openai",  "model": "gpt-4o-mini"},
    ],
    "nvidia": [
        {"provider": "openai",  "model": "gpt-3.5-turbo"},
    ],
}


# ───────────────────────────────────────────────────────────────
# HELPER FUNCTIONS
# ───────────────────────────────────────────────────────────────

def resolve_litellm_model(provider: str, model: str) -> Optional[str]:
    """
    Convert a user-facing (provider, model) pair to the LiteLLM model string.

    Returns None if the pair is not registered.
    """
    key = f"{provider}/{model}"
    return MODEL_MAP.get(key)


def is_provider_configured(provider_id: str) -> bool:
    """Check if a provider has its API key set in the environment."""
    return provider_id in settings.get_configured_providers()


def get_provider_catalog_with_status() -> List[Dict[str, Any]]:
    """
    Build the catalog for GET /providers and the UI.

    The first entry is the synthetic "gateway" provider whose models are the
    live virtual models from the smart Router (auto, gpt-oss-120b, deepseek, ...).
    Selecting any of these routes through the free-provider pool. The remaining
    entries are the underlying free providers with live configuration status.
    """
    # Imported here to avoid any import-time ordering issues.
    from app.core.llm_router import list_virtual_models

    configured = settings.get_configured_providers()

    virtual_models = [
        {"id": vm, "name": vm, "context_window": 128_000}
        for vm in list_virtual_models()
    ]
    gateway_entry = {
        "id": "gateway",
        "name": "Free Gateway (auto-routed)",
        "icon": "🛰️",
        "is_configured": bool(virtual_models),
        "models": virtual_models,
    }

    provider_entries = [
        {**info, "is_configured": info["id"] in configured, "models": []}
        for info in FREE_PROVIDER_INFO
    ]

    return [gateway_entry, *provider_entries]


def get_default_model(provider: str) -> Optional[str]:
    """
    Return a sensible default model id for a provider.

    For the synthetic "gateway" provider (and anything unrecognised), this is the
    configured DEFAULT_VIRTUAL_MODEL if it's live, else the first live virtual
    model.
    """
    from app.core.llm_router import list_virtual_models

    virtual = list_virtual_models()
    if settings.DEFAULT_VIRTUAL_MODEL in virtual:
        return settings.DEFAULT_VIRTUAL_MODEL
    return virtual[0] if virtual else None


def get_fallbacks(provider: str) -> List[Dict[str, str]]:
    """Return the fallback chain for a provider."""
    return FALLBACK_CHAINS.get(provider, [])
