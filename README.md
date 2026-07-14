# Multi-LLM Gateway Platform

A unified AI API Gateway that aggregates many **free** AI providers (and multiple
free keys per provider) behind a **single endpoint** and smartly switches when one
hits a rate limit / quota / outage — so it behaves like one "unlimited" provider.
Usable from **Claude Code** and **any OpenAI-compatible software**. Built on
[LiteLLM's](https://github.com/BerriAI/litellm) `Router` for load balancing, 429
cooldowns, key rotation and fallbacks.

---

## Free-Provider Gateway (start here)

A **virtual model** (`auto`, `gpt-oss-120b`, `deepseek`, `qwen`, `mistral`, `llama`,
`gemini-flash`) maps to many deployments — multiple keys and multiple providers
serving the same model. The Router picks any healthy one and benches the rest on
failure. Add another free account = add another env key + a deployment line in
[`backend/config/model_pool.yaml`](backend/config/model_pool.yaml). No code change.

### Configure free keys (each one multiplies your limits)

In the repo-root `.env` — fill in only what you have:

```dotenv
GROQ_API_KEY_1=...      # https://console.groq.com/keys   (gpt-oss-120b/20b, Llama, Qwen)
GROQ_API_KEY_2=...      # a second free account => more throughput
OPENROUTER_API_KEY_1=...# https://openrouter.ai/keys       (many :free models)
GEMINI_API_KEY_1=...    # https://aistudio.google.com/apikey
CEREBRAS_API_KEY_1=...  # https://cloud.cerebras.ai
GITHUB_MODELS_TOKEN=... # GitHub PAT
MISTRAL_API_KEY_1=...   # https://console.mistral.ai/api-keys
```

### Use it with Claude Code

```bash
export ANTHROPIC_BASE_URL=http://localhost:8000
export ANTHROPIC_AUTH_TOKEN=dev          # any value unless REQUIRE_GATEWAY_AUTH=true
claude
```

Claude Code's `claude-*` model names route to `DEFAULT_VIRTUAL_MODEL` (`auto`).
Tool calling and streaming work via LiteLLM's Anthropic adapter (`POST /v1/messages`).

### Use it with any OpenAI client

```python
from openai import OpenAI
client = OpenAI(base_url="http://localhost:8000/v1", api_key="dev")
client.chat.completions.create(
    model="auto",                         # or gpt-oss-120b, deepseek, qwen, ...
    messages=[{"role": "user", "content": "hello"}],
)
```

### Compatibility endpoints

| Endpoint | Purpose |
|---|---|
| `POST /v1/messages` | Anthropic-compatible (Claude Code) |
| `POST /v1/chat/completions` | OpenAI-compatible (any OpenAI client) |
| `POST /v1/embeddings` | OpenAI-compatible embeddings (NVIDIA NIM free tier) |
| `GET  /v1/models` | List the live virtual models |
| `GET  /health` | Status + router pool size + cooldowns |

`/v1` auth is **open by default** for local dev; set `REQUIRE_GATEWAY_AUTH=true` and
`GATEWAY_API_KEY=...` to require `Authorization: Bearer <token>` or `x-api-key`.

Embedding models (free via NVIDIA NIM): `embed` (default — llama-nemotron-embed-1b-v2,
multilingual), `nemotron-embed-vl` (multimodal), `nv-embedqa-e5-v5` (English QA),
`nv-embed-v1` (general, non-commercial), `bge-m3`. Unknown embedding model names map
to `embed`. NVIDIA's retrieval models are asymmetric — requests default to
`input_type: "query"`; add `"input_type": "passage"` to the body when embedding
documents for an index:

```python
client.embeddings.create(model="embed", input=["hello world"])                        # query side
client.embeddings.create(model="embed", input=chunks, extra_body={"input_type": "passage"})  # index side
```

> The original JWT-gated chat UI and analytics (`/api/v1/...`) still work and now run
> through the same smart Router. Note: `passlib` 1.7.4 needs `bcrypt < 4.1` (pinned in
> requirements) — without it the JWT/password tests fail on a backend self-test.

---

## Architecture Overview

```
                      +-----------------------------+
                      |   OpenAI / Anthropic-style  |
                      | clients (SDKs, Claude Code) |
                      +--------------+--------------+
                                     |
                                     | API Requests & Streaming (SSE)
                                     v
                      +-----------------------------+
                      |   FastAPI Backend Gateway   |
                      |  (JWT Auth, Route Policies) |
                      +--------------+--------------+
                                     |
                                     | Database Sessions
                                     v
                      +-----------------------------+
                      |     SQLite Database         |
                      |   (SQLAlchemy & Alembic)    |
                      +--------------+--------------+
                                     |
                                     | Standardized Payloads
                                     v
                      +-----------------------------+
                      |        LiteLLM Engine       |
                      |  (Unified Model Interface)  |
                      +--------------+--------------+
                                     |
             +-----------------------+-----------------------+
             |                       |                       |
             v                       v                       v
      +--------------+        +--------------+        +--------------+
      |  OpenAI API  |        |  Claude API  |        |  Gemini API  |
      +--------------+        +--------------+        +--------------+
             |                       |                       |
             v                       v                       v
      +--------------+        +--------------+        +--------------+
      | DeepSeek API |        |  xAI / Grok  |        |  NVIDIA NIM  |
      +--------------+        +--------------+        +--------------+
```

### Key Highlights
- **Unified Payloads**: Send standard OpenAI-compatible requests and receive unified responses.
- **Failover / Fallback Routing**: Seamlessly route queries to alternative models if primary providers return rate limits or downtime errors.
- **SSE Streaming**: Token-by-token real-time streaming directly to the browser.
- **Cost & Token Analytics**: Persistent logging of token inputs/outputs and response latencies to track budgets and API health.

---

## Directory Structure

```
multi-llm-gateway/
├── backend/            # FastAPI Backend Code
│   ├── app/            # Source Code
│   │   ├── api/        # REST APIs (Auth, Chat, Providers, Analytics)
│   │   ├── core/       # Config, Security, Database & frontend serving
│   │   ├── models/     # SQLAlchemy Database Models
│   │   ├── schemas/    # Pydantic Schemas
│   │   ├── services/   # Business Logic (LiteLLM, Routing)
│   │   └── main.py     # Main Entrypoint
│   ├── tests/          # Tests
│   ├── Dockerfile      # Backend image
│   └── requirements.txt
├── deploy/             # Hugging Face Space deploy config
├── docker-compose.yml  # One-command deployment (Postgres + gateway)
└── README.md
```

---

## Run with Docker

`docker compose up` starts **PostgreSQL + the gateway** — that's the whole stack.
Any OpenAI/Anthropic-compatible client is the "UI".

```bash
# 1. One .env at the repo root — SECRET_KEY + ENCRYPTION_KEY (and optionally
#    MASTER_ADMIN_KEY for a stable admin credential).
cp .env.example .env                   # then edit .env

# 2. Build and start (one command). Frontend + API share ONE port.
docker compose up --build
```

Then open:
- **UI** → http://localhost:8080
- **API docs** → http://localhost:8080/docs
- **Health** → http://localhost:8080/health

PostgreSQL data is persisted in the `pg_data` Docker volume.

### Without Compose (single image, gateway + UI)

```bash
docker build -t multi-llm-gateway .        # build context is the repo root
docker run -p 8080:8000 --env-file .env multi-llm-gateway
```

---

## Setup & Local Installation (Development)

### Prerequisites
- Python 3.10+
- Docker & Docker Compose (optional for local deployment)

### Backend Setup
```powershell
cd backend

# Create & activate a virtual environment
python -m venv venv
.\venv\Scripts\Activate.ps1   # Windows PowerShell
source venv/bin/activate       # Unix/macOS

# Install dependencies
pip install -r requirements.txt
pip install -r requirements-dev.txt

# Create environment configuration
copy .env.example .env

# Run FastAPI app in development
uvicorn app.main:app --reload
```

---

## Initial Milestones

1. **Phase 0 — Planning & Setup (Active)**: Core folder organization, dependencies configuration, basic FastAPI startup, and Vite/React routing setup.
2. **Phase 1 — Auth & Database Integration**: SQLite schemas for Users, Sessions, Messages, and JWT sign/verify endpoints.
3. **Phase 2 — Core Gateway with LiteLLM**: Connecting LiteLLM, handling single & streaming requests, response normalization.
4. **Phase 3 — Smart Routing & Fallbacks**: Dynamic fallback configuration and failover trigger tests.
5. **Phase 4 — Frontend Chat UI & Analytics**: Dynamic panels for chats, dashboard charts (latency & costs), and provider selector.
6. **Phase 5 — Docker & Production Deployment**: Docker multi-stage builds and docker-compose deployment configuration.

---

## Coding Standards & Conventions

- **Python (PEP 8)**: Formatted with `black`, linted with `ruff`.
- **Commit Messages**: Semantic Commits (e.g., `feat:`, `fix:`, `chore:`, `docs:`).
