"""
Model-name normalization — the join key that groups the same model across
providers into a common model.

A litellm model string is `<provider_prefix>/<upstream id>`. Stripping the
provider prefix and any `:tag` suffix yields a family key that is identical for
the same model on different providers:

    groq/openai/gpt-oss-120b          → openai/gpt-oss-120b
    nvidia_nim/openai/gpt-oss-120b    → openai/gpt-oss-120b   (matches ↑)
    openrouter/openai/gpt-oss-120b:free → openai/gpt-oss-120b (matches ↑)
    groq/llama-3.3-70b-versatile      → llama-3.3-70b-versatile
"""


def provider_slug(litellm_model: str) -> str:
    """The provider prefix, e.g. 'groq' from 'groq/openai/gpt-oss-120b'."""
    return litellm_model.split("/", 1)[0] if "/" in litellm_model else "unknown"


def upstream_id(litellm_model: str) -> str:
    """The model id as the provider names it (everything after the prefix)."""
    return litellm_model.split("/", 1)[1] if "/" in litellm_model else litellm_model


def normalize_model_name(litellm_model: str) -> str:
    """Family key for cross-provider grouping (prefix + tag stripped, lowercased)."""
    s = upstream_id(litellm_model)
    s = s.split(":", 1)[0]          # drop ':free' and similar tags
    return s.strip().lower()
