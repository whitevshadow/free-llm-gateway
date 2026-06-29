---
title: Lite-LLM WebUI
emoji: 💬
colorFrom: indigo
colorTo: purple
sdk: docker
app_port: 7860
pinned: false
---

# Lite-LLM WebUI

Open WebUI chat interface wired to the gateway Space
(`https://whiteshadows1-gateway.hf.space/v1`).

Set one secret in **Settings → Variables and secrets**:

| Secret | Value |
|---|---|
| `OPENAI_API_KEY` | your gateway `GATEWAY_API_KEY` |

Everything else (base URL, auth, default models) is set in the Dockerfile.
First account you create becomes the admin.
