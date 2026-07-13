# System Architecture — Multi-LLM Gateway (Backend)

A FastAPI backend that unifies many free/paid LLM providers (Groq, Gemini,
OpenRouter, NVIDIA NIM, DeepSeek, Ollama, OpenAI, Anthropic, …) behind one
OpenAI- and Anthropic-compatible API. A single `litellm.Router` load-balances
across every configured key/provider, cools down rate-limited backends, retries,
and falls back — so a pile of limited free tiers behaves like one dependable
endpoint.

---

## 1. High-level shape

```
                       ┌────────────────────────────────────────────┐
  External clients     │                FastAPI app                 │
  (Claude Code,        │                                            │
   OpenAI SDK) ──/v1──►│  Compatibility layer   Native UI API       │
                       │  /v1/chat/completions   /api/v1/auth        │
  Web UI (SPA) ──/api─►│  /v1/messages           /api/v1/chat        │
                       │  /v1/models             /api/v1/analytics   │
                       │  /v1/embeddings         /api/v1/providers   │
                       │  /v1/admin/*            /api/v1/settings     │
                       │           │                    │            │
                       │           ▼                    ▼            │
                       │      llm_service ──► llm_router (singleton) │
                       │           │            litellm.Router       │
                       │           ▼                    │            │
                       │    usage_logger / key_store    │            │
                       └───────────┼────────────────────┼───────────┘
                                   ▼                    ▼
                             PostgreSQL           LLM providers
                           (or SQLite dev)      (Groq, Gemini, …)
```

Two client surfaces share one routing core:

- **Compatibility endpoints** (`/v1/*`) — mounted at root so paths match exactly
  what OpenAI clients and Claude Code expect. Stateless, guarded by a single
  gateway API key.
- **Native UI API** (`/api/v1/*`) — user accounts, JWT auth, persisted chat
  sessions, analytics, runtime settings. Backs the web SPA.

Both funnel LLM calls through `llm_service` → the shared `litellm.Router`.

---

## 2. Data model

SQLAlchemy ORM over **PostgreSQL** in production (`postgresql+psycopg2://…`),
**SQLite** for local dev (`sqlite:///./gateway.db`). Tables are auto-created at
startup from `Base.metadata` (`Base.metadata.create_all`) — there is no Alembic
migration layer. All models live in [app/models/](app/models/) and are registered
via [app/models/__init__.py](app/models/__init__.py).

There are **8 tables** in three logical groups.

### 2.1 Users & conversations (the UI's persistent state)

**`users`** — [app/models/user.py](app/models/user.py)

| Column | Type | Notes |
|---|---|---|
| `id` | Integer PK | |
| `email` | String, unique, indexed, not null | login identity |
| `hashed_password` | String, not null | bcrypt/passlib hash |
| `full_name` | String, nullable | |
| `is_active` | Boolean, default `True` | |
| `created_at` / `updated_at` | DateTime (UTC) | `updated_at` auto-bumps |

One user → many chat sessions (`cascade="all, delete-orphan"`).

**`chat_sessions`** — [app/models/chat.py](app/models/chat.py)

| Column | Type | Notes |
|---|---|---|
| `id` | **String PK** (UUID) | client-generatable id |
| `title` | String, default `"New Chat"` | |
| `user_id` | Integer → `users.id`, not null | owner |
| `created_at` / `updated_at` | DateTime (UTC) | |

One session → many messages, ordered by `created_at`, cascade-deleted.

**`chat_messages`** — [app/models/chat.py](app/models/chat.py)

| Column | Type | Notes |
|---|---|---|
| `id` | Integer PK | |
| `session_id` | String → `chat_sessions.id`, not null | |
| `role` | String | `system` / `user` / `assistant` |
| `content` | Text | |
| `provider`, `model` | String, nullable | which backend answered |
| `prompt_tokens`, `completion_tokens`, `total_tokens` | Integer | per-message usage |
| `cost` | Float | USD estimate |
| `latency` | Float | seconds |
| `created_at` | DateTime (UTC) | |

Note the **denormalized usage columns** on each assistant message — token/cost/
latency are stored inline so a conversation renders without joining analytics.

### 2.2 Analytics (usage ledger)

**`token_usage_logs`** — [app/models/analytics.py](app/models/analytics.py)

| Column | Type | Notes |
|---|---|---|
| `id` | Integer PK | |
| `user_id` | Integer → `users.id`, **nullable** | null for stateless `/v1` calls |
| `provider`, `model` | String, not null | |
| `prompt_tokens`, `completion_tokens`, `total_tokens` | Integer | |
| `cost` | Float | USD |
| `latency` | Float | seconds |
| `status_code` | Integer, default 200 | HTTP/error state |
| `error_message` | String, nullable | on failure |
| `created_at` | DateTime (UTC), indexed | time-series queries |

This is the append-only ledger powering the analytics dashboard. The UI chat path
writes a row inline (it has a DB session + user); the stateless `/v1` endpoints
write via [app/services/usage_logger.py](app/services/usage_logger.py) with
`user_id = NULL`. Logging is best-effort and never raises — analytics must never
break a completion.

### 2.3 Routing configuration & runtime state (the "control plane")

These four tables are the DB-backed equivalent of `config/model_pool.yaml`. When
populated (seed with `app/scripts/seed_pool_from_yaml.py`), the Router builds from
here instead of the file, so models and routing can be tuned by editing rows —
no file edit or image rebuild. Source is selected by `MODEL_POOL_SOURCE`
(`auto` → DB if seeded else YAML; `db`; `yaml`).

**`model_deployments`** — [app/models/model_pool.py](app/models/model_pool.py)
One row = one concrete (provider, model, key) backend.

| Column | Type | Notes |
|---|---|---|
| `id` | Integer PK | |
| `model_name` | String, indexed | **virtual** name clients request, e.g. `auto`, `gpt-oss-120b` |
| `litellm_model` | String | concrete string, e.g. `groq/openai/gpt-oss-120b` |
| `api_key_ref` | String, nullable | **reference** `os.environ/GROQ_API_KEY_1`, never the secret |
| `api_base_ref` | String, nullable | e.g. Ollama base URL |
| `rpm` | Integer, nullable | requests-per-minute hint for the Router |
| `extra` | JSON, nullable | free-form extra `litellm_params` |
| `enabled` | Boolean, indexed | soft-delete / quick toggle |
| `created_at` / `updated_at` | DateTime | |

Many rows can share one `model_name` — that is exactly how load balancing works:
several keys/providers behind the same virtual model.

**`router_config`** — single-row (`id=1`) table holding `litellm.Router` behaviour.

| Column | Type | Default |
|---|---|---|
| `routing_strategy` | String | `usage-based-routing-v2` |
| `num_retries` | Integer | 4 |
| `cooldown_time` | Integer | 30 (seconds benched after 429) |
| `allowed_fails` | Integer | 3 |
| `fallbacks` | JSON | list of `{primary_model: [fallback, …]}` |

**`provider_keys`** — [app/models/provider_key.py](app/models/provider_key.py)
Runtime-configured API keys from the Settings UI (as opposed to `.env`).

| Column | Type | Notes |
|---|---|---|
| `id` | Integer PK | |
| `env_var` | String, unique, indexed | the slot it fills, e.g. `GROQ_API_KEY_1` |
| `provider` | String, nullable | display id, e.g. `groq` |
| `value` | String | **the secret** (plaintext — see note) |
| `created_at` / `updated_at` | DateTime | |

At startup `apply_persisted_keys()` injects each row into `os.environ`, so a
UI-saved key activates its deployments exactly like an `.env` key. **Security
note (from the source):** values are stored plaintext; the API only ever returns
a masked preview. Harden by encrypting `value` with `SECRET_KEY`.

**`model_availability`** — [app/models/model_availability.py](app/models/model_availability.py)
Snapshot of which concrete deployments actually respond. Populated by the manual
probe `app/scripts/probe_models.py`, which pings every deployment once in
parallel.

| Column | Type | Notes |
|---|---|---|
| `id` | Integer PK | |
| `concrete_model` | String, unique, indexed | e.g. `nvidia_nim/google/codegemma-7b` |
| `virtual_models` | String, nullable | comma-joined names routing here (info) |
| `provider` | String, indexed | prefix, e.g. `groq` |
| `available` | Boolean, indexed | answered a 1-token ping (429 counts as reachable) |
| `status` | String | `available` / `rate_limited` / `unavailable` / `auth_error` / `timeout` / `error` |
| `http_code` | Integer, nullable | |
| `latency_ms` | Integer, nullable | |
| `error` | Text, nullable | |
| `checked_at` | DateTime, indexed | |

When `FILTER_BY_AVAILABILITY` is on, the Router hides anything marked unavailable,
so `/v1/models` only lists models that genuinely respond. No effect until a probe
has run.

### 2.4 Relationship diagram

```
users ──1:N──► chat_sessions ──1:N──► chat_messages
  │                                     (denormalized usage cols)
  └──1:N──► token_usage_logs   (user_id NULLABLE — /v1 calls are user-less)

Control plane (no FKs — driven by string references, not joins):
  model_deployments.api_key_ref ─"os.environ/VAR"─► provider_keys.env_var ─► os.environ
  model_deployments.model_name  ────────────────────► model_availability.virtual_models
  router_config (id=1)          ── single row of Router settings
```

The control-plane tables intentionally have **no foreign keys** to each other:
they are linked by string references (`os.environ/<VAR>`) resolved at Router
build time, mirroring the YAML format so YAML and DB are interchangeable.

---

## 3. Application layers

Layered so route handlers never touch `litellm` directly — the dependency points
inward (Dependency Inversion). Swap LiteLLM and only `llm_service` /
`llm_router` change.

| Layer | Location | Responsibility |
|---|---|---|
| **Entry / lifespan** | [app/main.py](app/main.py) | app instance, middleware, table creation, key injection, Router warm-up, global exception handler, health |
| **Config** | [app/core/config.py](app/core/config.py) | pydantic-settings, single source of truth, provider detection |
| **API routers** | [app/api/](app/api/) | HTTP surface (see §4) |
| **Schemas** | [app/schemas/](app/schemas/) | Pydantic request/response models (user, chat, analytics) |
| **Services** | [app/services/](app/services/) | `llm_service`, `usage_logger`, `key_store`, `fallback` |
| **Routing core** | [app/core/llm_router.py](app/core/llm_router.py) | builds & owns the singleton `litellm.Router` |
| **Persistence** | [app/core/database.py](app/core/database.py) + [app/models/](app/models/) | engine, session, ORM |
| **Security** | [app/core/security.py](app/core/security.py), [app/api/gateway_auth.py](app/api/gateway_auth.py) | JWT (UI) + gateway key (`/v1`) |
| **Middleware** | [app/middleware/](app/middleware/) | request-id, structured logging |

### The routing core (`llm_router.py`)

The heart of the system. A **module-level singleton** `litellm.Router` — it must
be shared because it holds in-memory per-deployment usage counters and cooldown
state. Built lazily on first use; `reload_router()` forces a rebuild (after keys
change).

Build pipeline:

1. **Load pool** from DB (`model_deployments` + `router_config`) or YAML per
   `MODEL_POOL_SOURCE`.
2. **Resolve & filter keys** — resolve `os.environ/<VAR>` refs; drop any
   deployment whose key is unset (keyless local providers like Ollama are kept).
3. **Auto-discovery** — if a NVIDIA or OpenRouter key is set, fetch that
   provider's model catalog and register every chat model as a deployment
   (OpenRouter defaults to free-only). Cached by key signature.
4. **Availability filter** — hide concrete models the probe marked unavailable.
5. **Prune fallbacks** — drop fallback entries pointing at virtual models with no
   live deployment.
6. **Construct** `Router(model_list, fallbacks, timeout, **router_settings)`.

Introspection helpers (`router_health`, `list_virtual_models`,
`all_probe_targets`, `list_key_slots`) feed `/health`, `/v1/models`, the probe,
and the Settings UI.

### The LLM service (`llm_service.py`)

Uniform completion façade. Two paths:

- **Smart-Router path** — if the requested `model` is a live virtual model, route
  through the shared `Router` (`.completion` / `.acompletion`), inheriting its
  load balancing, cooldowns, retries and fallbacks. The UI chat and `/v1` both
  benefit.
- **Legacy direct path** — otherwise resolve via the provider registry and call
  `litellm.completion` directly with a manual retry loop (breaks early on
  auth errors).

Every response is normalized to one dict (`content`, `provider`, `model`, token
counts, `cost`, `latency`, `error`). Streaming is an async generator yielding SSE
chunks.

---

## 4. API surface

**Compatibility (root-mounted, gateway-key guarded)**

| Endpoint | Purpose |
|---|---|
| `POST /v1/chat/completions` | OpenAI chat completions (streaming supported) |
| `POST /v1/embeddings` | OpenAI embeddings |
| `GET /v1/models` | list live virtual models |
| `POST /v1/messages` | Anthropic Messages (Claude Code) |
| `POST /v1/admin/reload-router`, `GET /v1/admin/pool`, `GET /v1/admin/availability` | ops |

**Native UI API (`/api/v1/*`, JWT auth)**

| Prefix | Endpoints |
|---|---|
| `/auth` | `register`, `login`, `me` |
| `/chat` | send prompt; CRUD chat `sessions`, `sessions/{id}`, `sessions/{id}/prompt` |
| `/providers` | list providers, `fallbacks` |
| `/analytics` | `dashboard`, `overview` |
| `/settings` | `providers`, `PUT/DELETE keys`, `test` |

**System:** `GET /health` (uptime, configured providers, Router pool summary).

Two independent auth schemes: **JWT** (`SECRET_KEY`, HS256, 24h) for UI routes;
a single **gateway API key** (`GATEWAY_API_KEY`, `REQUIRE_GATEWAY_AUTH`) for
`/v1`. CORS, request-id, and structured logging middleware wrap every request
(`CORS → RequestID → Logging → handler`).

---

## 5. Request lifecycles

**External `/v1/chat/completions`:**
```
client → verify_gateway_key → openai_compat → llm_service (Router path)
       → litellm.Router (balance/cooldown/retry/fallback) → provider
       → normalize → usage_logger (user_id=NULL) → OpenAI-shaped response
```

**UI chat prompt (`/api/v1/chat/sessions/{id}/prompt`):**
```
SPA (JWT) → chat router → load session → llm_service → Router → provider
        → persist assistant ChatMessage (with usage cols)
        → write TokenUsageLog (with user_id) → response/SSE
```

---

## 6. Deployment & startup

`docker-compose.yml` runs **postgres** (16-alpine, healthchecked) + **app**
(FastAPI/uvicorn on :8000). The app waits for a healthy DB, mounts
`backend/config` read-only for live YAML tweaks, and keeps the old SQLite volume
mounted for one-off migration (`migrate_sqlite_to_postgres`).

**Startup sequence** ([app/main.py](app/main.py) `lifespan`):
1. Load settings from `.env`; configure logging.
2. Register all ORM models, `create_all` tables.
3. `apply_persisted_keys()` — inject `provider_keys` rows into `os.environ`.
4. Log configured providers.
5. Build the Router (warm the singleton) and log pool size.

**Maintenance scripts** ([app/scripts/](app/scripts/)):
`seed_pool_from_yaml` (YAML → DB tables), `probe_models` (fill
`model_availability`), `migrate_sqlite_to_postgres`.
