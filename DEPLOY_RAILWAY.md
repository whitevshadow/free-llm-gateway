# Deploying to Railway — gateway as a public API for external clients

This stack becomes **one Railway project with two services**. External clients
call the **gateway**'s public URL.

```
Project: lite-llm
├── Postgres     (Railway plugin)            → internal DATABASE_URL
└── gateway      (this repo's Dockerfile)    → PUBLIC https://gateway-xxx.up.railway.app/v1
```

External clients (OpenAI SDK, Claude Code, curl, another app) →
`https://gateway-xxx.up.railway.app/v1` with the gateway key.

---

## 1. Create the project + Postgres

1. New Project → **Deploy from GitHub repo** (this repo) → in the service
   **Settings → Root Directory** set **`backend`** (it builds `backend/Dockerfile`)
   → name this service **gateway**.
2. **+ New** → **Database** → **PostgreSQL**. Railway provisions it and exposes
   connection variables.

## 2. gateway service — variables

| Variable | Value |
|---|---|
| `DATABASE_URL` | `${{Postgres.DATABASE_URL}}` then append the driver: use `postgresql+psycopg2://...`. Easiest: set `DATABASE_URL` = the Postgres `DATABASE_URL` and it already starts `postgresql://` — change the scheme to `postgresql+psycopg2://`. |
| `REQUIRE_GATEWAY_AUTH` | `true` |
| `GATEWAY_API_KEY` | your key (the one in the root `.env`) |
| `MODEL_POOL_SOURCE` | `auto` |
| `DEFAULT_VIRTUAL_MODEL` | `auto` |
| `GROQ_API_KEY_1` | your Groq key |
| `OPENROUTER_API_KEY_1` | your OpenRouter key |
| `NVIDIA_API_KEY_1` | your NVIDIA key |

**Port:** `backend/Dockerfile` already honors Railway's injected `$PORT`. Just
**Settings → Networking → Generate Domain** to get the public URL.

> No bind-mount on Railway, so the model pool must come from Postgres — that's
> why `MODEL_POOL_SOURCE=auto` + the seed step below.

## 3. One-time setup (after first deploy)

Open a shell on the **gateway** service (Railway → service → ⋮ → Shell, or
`railway run` locally pointed at the project) and run:

```bash
python -m app.scripts.seed_pool_from_yaml   # load the 4-model pool into Postgres
python -m app.scripts.probe_models          # test routes, hide dead ones
```

(The SQLite→Postgres migration is only relevant to your old local data; skip it
on a fresh Railway Postgres.)

## 4. Use the gateway from external clients

Base URL = your gateway domain + `/v1`. Key = `GATEWAY_API_KEY`.

**curl**
```bash
curl https://gateway-xxx.up.railway.app/v1/chat/completions \
  -H "Authorization: Bearer YOUR_GATEWAY_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"model":"auto","messages":[{"role":"user","content":"hello"}]}'
```

**OpenAI SDK (Python)**
```python
from openai import OpenAI
client = OpenAI(
    base_url="https://gateway-xxx.up.railway.app/v1",
    api_key="YOUR_GATEWAY_API_KEY",
)
client.chat.completions.create(
    model="auto",                       # or gpt-oss-120b, llama-3.3-70b, ...
    messages=[{"role": "user", "content": "hi"}],
)
```

**Claude Code / Anthropic-style** — point at `/v1` (Anthropic-compatible route is
also served at `/v1/messages`) with the same key as `x-api-key`.

Models available: `auto`, `gpt-oss-120b`, `gpt-oss-20b`, `llama-3.3-70b`,
`llama-3.1-8b` (+ the discovered NVIDIA/OpenRouter catalog). `GET /v1/models`
lists the live ones.

---

## Notes / gotchas

- **`$PORT` portability:** already handled — `backend/Dockerfile` uses a
  shell-form `CMD` that honors the platform-injected `$PORT` (default 8000 locally).
- **Cooldown state** is in-memory per gateway instance — keep the gateway at **1
  replica** so the smart load-balancing/cooldown logic stays coherent.
- **Rotate the key** by changing `GATEWAY_API_KEY` on the gateway service (and
  update every client that uses it).
- **Don't expose Postgres** publicly — leave it on the private network only.
