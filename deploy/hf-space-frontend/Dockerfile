# Hugging Face Docker Space — Open WebUI (frontend), wired to the gateway Space.
#
# Open WebUI is a prebuilt image; we just set config via ENV and the HF port (7860).
# The ONE secret (OPENAI_API_KEY = your gateway key) is set in the Space's
# Settings → Secrets, never baked into this public file.

FROM ghcr.io/open-webui/open-webui:main

ENV PORT=7860 \
    HOST=0.0.0.0 \
    # Point the UI at the gateway Space's OpenAI-compatible endpoint.
    OPENAI_API_BASE_URL=https://whiteshadows1-gateway.hf.space/v1 \
    # Require login; first account created becomes admin.
    WEBUI_AUTH=true \
    ENABLE_SIGNUP=true \
    ENABLE_OLLAMA_API=false \
    WEBUI_NAME=Lite-LLM \
    DEFAULT_MODELS=gpt-oss-120b,llama-3.3-70b,gpt-oss-20b,llama-3.1-8b

EXPOSE 7860
