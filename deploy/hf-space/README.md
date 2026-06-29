---
title: Gateway
emoji: 🌐
colorFrom: indigo
colorTo: purple
sdk: docker
app_port: 7860
pinned: false
---

# Lite-LLM Gateway

OpenAI-compatible LLM gateway (FastAPI + LiteLLM) that load-balances across free
providers (Groq, NVIDIA, OpenRouter) with smart failover and cross-model fallback.

- **API base URL:** `https://whiteshadows1-gateway.hf.space/v1`
- **Models:** `auto`, `gpt-oss-120b`, `llama-3.3-70b`, `gpt-oss-20b`, `llama-3.1-8b`
- Configure keys in **Settings → Variables and secrets** (see below).

Source: https://github.com/whitevshadow/free-llm-gateway
