# ─────────────────────────────────────────────────────────────
# Start Open WebUI wired to the Multi-LLM Gateway
#
#   1. Make sure your gateway backend is running on :8000
#        (uvicorn app.main:app  →  http://localhost:8000)
#   2. Run this script:   .\start-openwebui.ps1
#   3. Open               http://localhost:3000
#
# Open WebUI talks to the gateway's OpenAI-compatible /v1 endpoint,
# so every virtual model in your pool (auto, deepseek, llama, qwen,
# the NVIDIA catalog, ...) shows up in the model picker automatically.
# ─────────────────────────────────────────────────────────────

$ErrorActionPreference = "Stop"

# --- Point Open WebUI at the gateway ---------------------------------
$env:OPENAI_API_BASE_URL = "http://localhost:8000/v1"
$env:OPENAI_API_KEY      = "sk-no-key-required"   # gateway auth is off (REQUIRE_GATEWAY_AUTH=false)

# --- Local-use conveniences ------------------------------------------
$env:WEBUI_AUTH          = "False"   # skip the login screen for single-user local use
$env:ENABLE_OLLAMA_API   = "False"   # we only use the OpenAI-compatible gateway
$env:WEBUI_NAME          = "Lite-LLM"

# Persist Open WebUI's data (chats, settings) inside the project.
$env:DATA_DIR            = "D:\Lite-LLM\.openwebui-data"

# NOTE: Open WebUI ignores the PORT env var — the port must be passed as a
# CLI flag to `serve`. We pin it to 3000 (the gateway keeps 8000).
$Port = 3000

Write-Host "Starting Open WebUI -> gateway at $($env:OPENAI_API_BASE_URL)" -ForegroundColor Cyan
Write-Host "Open http://localhost:$Port once it finishes booting." -ForegroundColor Green

& "D:\Lite-LLM\.openwebui-venv\Scripts\open-webui.exe" serve --host 0.0.0.0 --port $Port
