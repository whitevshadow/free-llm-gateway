"""
Known-provider presets — the dropdown in "Add a provider".

Each preset is just a base_url plus the LiteLLM prefix, so the admin only has to
paste an API key. Anything not listed here is added as CUSTOM, where the admin
supplies the base_url themselves.

WHAT A PRESET IS NOT: it is not a key. Providers are global destinations; keys
belong to users. A preset saves you typing a URL, nothing more.

REQUIREMENT FOR AUTO-DISCOVERY: the endpoint must expose an OpenAI-compatible
`GET {base_url}/models`. Every preset here does. A custom provider that doesn't
can still be added — it just won't auto-discover, and its models have to be added
by hand.

`slug` doubles as the LiteLLM prefix, which is why it must match what LiteLLM
expects ('nvidia_nim', not 'nvidia'). Get it wrong and every call to that provider
is routed to a prefix LiteLLM doesn't recognise.
"""

from typing import Dict, List, Optional

# ── FREE-TIER PROVIDERS ONLY ─────────────────────────────────────────────────
#  These are the ones in the dropdown. The rule is deliberate: presets are the
#  providers a user can sign up for and use at $0, which is what this gateway is
#  for. Trial-credit providers (Fireworks, Together, SambaNova, Nebius, …) and
#  paid ones (OpenAI, DeepSeek direct) are NOT presets — add them as Custom, so
#  the dropdown never implies "free" about something that isn't.
#
#  Every entry exposes an OpenAI-compatible `GET {base_url}/models`, which is what
#  auto-discovery calls. `slug` is also the LiteLLM routing prefix, so it must be
#  a prefix LiteLLM recognises — that is why these use canonical ids
#  ('nvidia_nim', 'gemini') rather than friendlier spellings.
PRESETS: List[Dict[str, Optional[str]]] = [
    {
        "slug": "groq",
        "name": "Groq",
        "base_url": "https://api.groq.com/openai/v1",
        "docs_url": "https://console.groq.com/keys",
        "hint": "Free. Very fast. Llama, Qwen, gpt-oss, Whisper.",
    },
    {
        "slug": "cerebras",
        "name": "Cerebras",
        "base_url": "https://api.cerebras.ai/v1",
        "docs_url": "https://cloud.cerebras.ai",
        "hint": "Free. Fastest inference. gpt-oss-120b, Llama.",
    },
    {
        "slug": "nvidia_nim",
        "name": "NVIDIA NIM",
        "base_url": "https://integrate.api.nvidia.com/v1",
        "docs_url": "https://build.nvidia.com",
        "hint": "Free dev tier (phone verify). Large catalog + embeddings.",
    },
    {
        "slug": "openrouter",
        "name": "OpenRouter",
        "base_url": "https://openrouter.ai/api/v1",
        "docs_url": "https://openrouter.ai/keys",
        "hint": "Free ':free' models across many labs, one key.",
    },
    {
        "slug": "gemini",
        "name": "Google AI Studio (Gemini)",
        "base_url": "https://generativelanguage.googleapis.com/v1beta/openai",
        "docs_url": "https://aistudio.google.com/apikey",
        "hint": "Free tier. Gemini 2.5/3 Flash, Gemma.",
    },
    {
        "slug": "mistral",
        "name": "Mistral",
        "base_url": "https://api.mistral.ai/v1",
        "docs_url": "https://console.mistral.ai/api-keys",
        "hint": "Free experiment tier (opt-in to training). Mistral + Codestral.",
    },
    {
        "slug": "cohere",
        "name": "Cohere",
        # The compatibility URL exists ONLY for discovery ({base}/models returns
        # OpenAI-shaped JSON). It must NOT be sent as api_base on calls: litellm's
        # `cohere/` prefix is a NATIVE integration that POSTs to api_base verbatim
        # with Cohere's own protocol, so pointing it here 404s every model.
        # native_routing tells the prober and Router to omit api_base and let
        # litellm use Cohere's real endpoint, which it knows itself.
        "base_url": "https://api.cohere.ai/compatibility/v1",
        "native_routing": True,
        "docs_url": "https://dashboard.cohere.com/api-keys",
        "hint": "Free. 1,000 requests/month. Command family + embeddings.",
    },
    {
        "slug": "github",
        "name": "GitHub Models",
        "base_url": "https://models.github.ai/inference",
        # GitHub is the odd one out: chat lives under /inference but the model
        # catalog is a SIBLING path, not {base_url}/models. Without this
        # override, discovery 404s and the key looks broken when it isn't.
        "models_url": "https://models.github.ai/catalog/models",
        # Straight to the FINE-GRAINED token page: a classic ghp_ token cannot
        # carry the "Models" permission and gets a bare 401 from inference, which
        # looks like a broken gateway. The requirement lives in the hint so it is
        # visible at the exact moment someone is about to paste the wrong kind.
        "docs_url": "https://github.com/settings/personal-access-tokens/new",
        "hint": (
            "Free. Needs a FINE-GRAINED PAT (github_pat_…) with account "
            "permission “Models: read” — classic ghp_ tokens get 401."
        ),
    },
    {
        "slug": "huggingface",
        "name": "HuggingFace Inference",
        "base_url": "https://router.huggingface.co/v1",
        "docs_url": "https://huggingface.co/settings/tokens",
        "hint": "Free monthly credits across many open models.",
    },
]

_BY_SLUG = {p["slug"]: p for p in PRESETS}


def get(slug: str) -> Optional[Dict[str, Optional[str]]]:
    return _BY_SLUG.get(slug.strip().lower())


def base_url_for(slug: str) -> Optional[str]:
    p = get(slug)
    return p["base_url"] if p else None


def call_api_base(slug: str, stored_base_url: Optional[str]) -> Optional[str]:
    """
    The api_base to pass on an ACTUAL litellm call for this provider — as opposed
    to the base_url used for discovery.

    For most providers these are the same OpenAI-compatible URL. For providers
    marked native_routing (Cohere), litellm's own integration knows the real
    endpoint and treats a supplied api_base as the literal URL to POST to — so we
    must send None or every call 404s against the discovery URL.
    """
    p = get(slug)
    if p and p.get("native_routing"):
        return None
    return stored_base_url
