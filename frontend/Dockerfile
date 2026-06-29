# syntax=docker/dockerfile:1
# ─────────────────────────────────────────────────────────────
# Frontend deployment — Open WebUI (the chat interface).
#
# Open WebUI is a PREBUILT image — there is no UI source code here. This thin
# wrapper just lets the chat UI be deployed as its own independent service/folder
# (e.g. on Railway set the service Root Directory to `frontend`).
#
# All configuration is supplied via environment variables at deploy time —
# see .env.example. The most important one points it at your backend gateway:
#   OPENAI_API_BASE_URL = https://<your-gateway>/v1
#   OPENAI_API_KEY      = <the backend's GATEWAY_API_KEY>
#
# Pin a specific version by changing the tag (e.g. :v0.9.6) instead of :main.
# ─────────────────────────────────────────────────────────────

FROM ghcr.io/open-webui/open-webui:main
