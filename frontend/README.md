# Frontend — Open WebUI

The chat interface for the gateway. **This is not custom code** — it's the
upstream [Open WebUI](https://github.com/open-webui/open-webui) image, wrapped
here so it can be deployed as its own service (deployment #2).

It talks to the backend gateway over its OpenAI-compatible `/v1` API.

## Configure

Copy `.env.example` → `.env` and set:

| Variable | Meaning |
|---|---|
| `OPENAI_API_BASE_URL` | the backend gateway's `/v1` URL |
| `OPENAI_API_KEY` | the backend's `GATEWAY_API_KEY` (must match) |
| `WEBUI_AUTH` | `true` to require login (first signup = admin) |
| `DEFAULT_MODELS` | pre-selected models |

## Run

**Locally (whole stack):** use the repo's `docker-compose.yml` — it builds this
folder as the `open-webui` service.

**Standalone:**
```bash
docker build -t lite-llm-webui ./frontend
docker run -p 3000:8080 --env-file frontend/.env lite-llm-webui
```

**On a platform (Railway/Render/Fly):** create a service with Root Directory
`frontend`, set the env vars above, expose port **8080**, and add a persistent
volume at `/app/backend/data` (stores accounts + chats).
