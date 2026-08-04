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


# ── Family-key aliases (CURATED, case-by-case — deliberately not automatic) ──
#  Providers disagree about the ORG segment of a model id: Groq writes
#  'meta-llama/llama-4-scout…', GitHub writes 'meta/llama-4-scout…', and
#  Cerebras writes 'gpt-oss-120b' with no org at all. Same weights, three
#  spellings — which split one model into several families, so e.g. Cerebras
#  never joined gpt-oss-120b's fallback group.
#
#  The fix is an explicit alias map, NOT automatic prefix-stripping: nothing
#  merges unless a human wrote the rule, so two different orgs shipping a
#  same-named model can never be silently fused. The cost is symmetrical and
#  accepted: the next provider with its own spelling splits again until an
#  alias is added here.

# Org-segment spellings that are the same organisation.
_ORG_ALIASES = {
    "meta-llama": "meta",
    "mistral-ai": "mistralai",
    "deepseek-ai": "deepseek",
    "ibm-granite": "ibm",
    "zai-org": "z-ai",
}

# Bare ids (no org segment) that are known spellings of an org-prefixed family.
_MODEL_ALIASES = {
    "gpt-oss-120b": "openai/gpt-oss-120b",   # Cerebras's spelling
    "gpt-oss-20b": "openai/gpt-oss-20b",
}

# ── Bare-id org inference (CURATED PREFIXES) ─────────────────────────────────
#  Some providers drop the org segment for their OWN models while aggregators
#  keep it, which splits one model into two families:
#
#      gemini/models/gemini-3-pro-image  → gemini-3-pro-image
#      openrouter/google/gemini-3-pro-image → google/gemini-3-pro-image
#
#  Same weights, two family keys, so they never share a fallback group and the
#  registry reports no provider redundancy for any of them. Enumerating each
#  model in _MODEL_ALIASES would work and would need a new line every time
#  Google ships a variant, so the rule is written once per FAMILY instead.
#
#  Still curated, and still narrow: only names whose prefix identifies exactly
#  one publisher appear here. 'llama' and 'qwen' are deliberately ABSENT even
#  though _BARE_PREFIX_RULES attributes them — those names are fine-tuned and
#  re-published by many orgs, so inferring an org from the prefix would fuse
#  genuinely different models. Publisher attribution can afford to guess; a
#  routing family key cannot.
_BARE_ORG_RULES = [
    ("gemini-", "google"),
    ("imagen-", "google"),
    ("gemma-", "google"),
    ("whisper-", "openai"),
    ("gpt-oss-", "openai"),
]


def normalize_model_name(litellm_model: str) -> str:
    """
    Family key for a full litellm model string (`<prefix>/<upstream id>`).

    Only for strings that CARRY a provider prefix — the first segment is
    discarded as one. Ids that were never prefixed (NVIDIA's NVCF functions are
    listed as bare 'flux.1-dev') must use `family_key`, or an org segment gets
    eaten as if it were a provider.
    """
    return family_key(upstream_id(litellm_model))


def family_key(upstream: str) -> str:
    """
    The shared normalization, applied to an id with NO litellm provider prefix.

    Factored out because `resolve_requested_model` must apply the SAME rules to
    a client's spelling. If the two drifted, a model would be stored under one
    key and be unreachable under the name the registry advertises.
    """
    s = upstream.split(":", 1)[0]   # drop ':free' and similar tags
    s = s.strip().lower()
    # Gemini lists ids as 'models/gemini-2.5-flash'. Discovery already strips
    # that before building the litellm string, but a client can send the raw
    # spelling straight to resolve_requested_model — and 'models' is not an org,
    # so treating it as one would key the family under a namespace nothing else
    # uses.
    if s.startswith("models/"):
        s = s[len("models/"):]
    if s in _MODEL_ALIASES:
        return _MODEL_ALIASES[s]
    if "/" in s:
        org, rest = s.split("/", 1)
        return f"{_ORG_ALIASES.get(org, org)}/{rest}"
    for prefix, org in _BARE_ORG_RULES:
        if s.startswith(prefix):
            return f"{org}/{s}"
    return s


def resolve_requested_model(requested: str, available: set) -> str | None:
    """
    Map whatever spelling a CLIENT sent onto one of the user's live families.

    Canonical names are org-prefixed ('openai/gpt-oss-120b'), but clients may
    reasonably send any provider's spelling — 'gpt-oss-120b',
    'meta-llama/llama-4-scout…' — and breaking them over an org prefix would be
    hostile. Resolution order:

      1. exact match;
      2. the alias-normalized form (same rules as the family key);
      3. UNAMBIGUOUS last-segment match — 'gpt-4.1' resolves to 'openai/gpt-4.1'
         iff exactly one live family ends in 'gpt-4.1'. Two candidates = no
         match, because guessing between different models loses silently.
    """
    if requested in available:
        return requested

    # Exactly the rules the family key was built with — see family_key.
    n = family_key(requested)
    if n in available:
        return n

    tail = n.split("/")[-1]
    matches = [a for a in available if a.split("/")[-1] == tail]
    return matches[0] if len(matches) == 1 else None


# ── Publisher normalization ──────────────────────────────────────────────────
#  Providers spell the same org several ways ('mistralai' on NVIDIA,
#  'mistral-ai' on GitHub) and Groq lists a handful of models with no org prefix
#  at all. This is the one curated mapping that makes the publisher filter read
#  as ONE row per organisation instead of near-duplicates.

_PUBLISHER_ALIASES = {
    "nvidia": "NVIDIA",
    "openai": "OpenAI",
    "meta": "Meta", "meta-llama": "Meta",
    "google": "Google", "deepmind": "DeepMind",
    "mistralai": "Mistral AI", "mistral-ai": "Mistral AI", "mistral": "Mistral AI",
    "microsoft": "Microsoft",
    "qwen": "Qwen",
    "deepseek": "DeepSeek", "deepseek-ai": "DeepSeek",
    "ibm": "IBM", "ibm-granite": "IBM",
    "cohere": "Cohere",
    "writer": "Writer",
    "groq": "Groq",
    "canopylabs": "Canopy Labs",
    "stepfun-ai": "StepFun",
    "minimaxai": "MiniMax",
    "moonshotai": "Moonshot AI",
    "bytedance": "ByteDance",
    "z-ai": "Z.ai", "zai-org": "Z.ai",
    "baai": "BAAI",
    "snowflake": "Snowflake",
    "01-ai": "01.AI",
    "abacusai": "Abacus AI",
    "ai21labs": "AI21 Labs",
    "aisingapore": "AI Singapore",
    "bigcode": "BigCode",
    "databricks": "Databricks",
    "upstage": "Upstage",
    "sarvamai": "Sarvam AI",
    "xai": "xAI", "x-ai": "xAI",
}

# Un-prefixed ids (Groq, Google AI Studio), attributed by name. Order matters:
# first match wins.
_BARE_PREFIX_RULES = [
    ("whisper", "OpenAI"),
    ("gpt", "OpenAI"),
    ("gemini", "Google"),
    ("imagen", "Google"),
    ("llama", "Meta"),
    ("gemma", "Google"),
    ("qwen", "Qwen"),
    ("mixtral", "Mistral AI"),
    ("mistral", "Mistral AI"),
    ("deepseek", "DeepSeek"),
    ("allam", "SDAIA"),       # ALLaM — Saudi Data & AI Authority
]


def publisher_for(upstream_model_id: str) -> str:
    """
    The organisation behind a model, normalized for display.

    'meta/llama-3.3-70b-instruct' → 'Meta'; 'whisper-large-v3' → 'OpenAI'.
    Unknown org prefixes are title-cased rather than dumped into an 'Other'
    bucket — a new publisher appearing in a catalog should show up by name.
    """
    mid = upstream_model_id.strip()
    if "/" in mid:
        org = mid.split("/", 1)[0].lower()
        return _PUBLISHER_ALIASES.get(org) or org.replace("-", " ").replace("_", " ").title()
    low = mid.lower()
    for prefix, pub in _BARE_PREFIX_RULES:
        if low.startswith(prefix):
            return pub
    return (mid.split("-", 1)[0] or mid).title()
