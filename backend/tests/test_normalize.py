"""Phase 0/5 — the normalization function that groups models across providers."""

from app.services.normalize import (
    family_key,
    normalize_model_name,
    provider_slug,
    resolve_requested_model,
    upstream_id,
)


def test_cross_provider_names_collapse():
    a = normalize_model_name("groq/openai/gpt-oss-120b")
    b = normalize_model_name("nvidia_nim/openai/gpt-oss-120b")
    c = normalize_model_name("openrouter/openai/gpt-oss-120b:free")
    assert a == b == c == "openai/gpt-oss-120b"


def test_distinct_models_stay_distinct():
    assert normalize_model_name("groq/llama-3.3-70b-versatile") == "llama-3.3-70b-versatile"
    assert normalize_model_name("groq/llama-3.3-70b-versatile") != normalize_model_name("cerebras/llama-3.3-70b")


def test_prefix_and_upstream_helpers():
    assert provider_slug("groq/openai/gpt-oss-120b") == "groq"
    assert upstream_id("groq/openai/gpt-oss-120b") == "openai/gpt-oss-120b"
    assert provider_slug("bare-model") == "unknown"


def test_bare_id_joins_its_org_prefixed_twin():
    """
    The split this rule exists for: Google AI Studio drops the org segment for
    its own models, aggregators keep it. Without inference these are two
    families, so the registry reports no provider redundancy for any Gemini
    model even when two providers serve it.
    """
    direct = normalize_model_name("gemini/gemini-2.5-flash-image")
    via_aggregator = normalize_model_name("openrouter/google/gemini-2.5-flash-image")
    assert direct == via_aggregator == "google/gemini-2.5-flash-image"

    assert normalize_model_name("groq/whisper-large-v3") == "openai/whisper-large-v3"


def test_ambiguous_bare_names_are_not_given_an_org():
    """
    'llama' and 'qwen' are re-published by many orgs, so a prefix rule would
    fuse different models under one routable name. Publisher attribution may
    guess; the family key may not.
    """
    assert normalize_model_name("groq/llama-3.3-70b-versatile") == "llama-3.3-70b-versatile"
    assert normalize_model_name("cerebras/qwen-3-32b") == "qwen-3-32b"


def test_models_segment_is_not_treated_as_an_org():
    """Gemini lists ids as 'models/<id>'; 'models' is a namespace, not an org."""
    assert family_key("models/gemini-2.5-flash") == "google/gemini-2.5-flash"


def test_client_may_still_send_the_bare_spelling():
    """
    Renaming the family key must not break callers. A client that sends the
    provider's own spelling resolves to the canonical name.
    """
    available = {"google/gemini-2.5-flash-image", "openai/whisper-large-v3"}
    assert resolve_requested_model("gemini-2.5-flash-image", available) == (
        "google/gemini-2.5-flash-image"
    )
    assert resolve_requested_model("whisper-large-v3", available) == (
        "openai/whisper-large-v3"
    )
    assert resolve_requested_model("models/gemini-2.5-flash-image", available) == (
        "google/gemini-2.5-flash-image"
    )
