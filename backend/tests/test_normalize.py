"""Phase 0/5 — the normalization function that groups models across providers."""

from app.services.normalize import normalize_model_name, provider_slug, upstream_id


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
