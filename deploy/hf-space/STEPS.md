# Deploy the backend to your Hugging Face Space (free)

Your Space: **https://huggingface.co/spaces/whiteshadows1/gateway**
Resulting API: **https://whiteshadows1-gateway.hf.space/v1**

## 1. Put the two files in the Space repo

```bash
git clone https://huggingface.co/spaces/whiteshadows1/gateway
cd gateway
# copy these two files from deploy/hf-space/ in your project:
#   Dockerfile   README.md
# (replace the placeholder ones HF created)
git add Dockerfile README.md
git commit -m "Deploy Lite-LLM gateway"
git push   # use an HF access token (write) as the password
```

The Dockerfile clones your **public** GitHub repo and builds the backend — so the
Space rebuilds your latest code each time you "Factory rebuild".

## 2. Set Secrets (Settings → Variables and secrets)

Add these as **Secrets** (not Variables — they're sensitive). Use the SAME values
you have in your local `.env` files:

| Secret | Value |
|---|---|
| `GROQ_API_KEY_1` | your Groq key |
| `OPENROUTER_API_KEY_1` | your OpenRouter key |
| `NVIDIA_API_KEY_1` | your NVIDIA key |
| `GATEWAY_API_KEY` | your `sk-gw-...` key |
| `REQUIRE_GATEWAY_AUTH` | `true` |

Optional (cleaner / persistent):
| Secret | Value | Why |
|---|---|---|
| `NVIDIA_AUTO_DISCOVER` | `false` | show only the curated pool, not NVIDIA's full catalog |
| `OPENROUTER_AUTO_DISCOVER` | `false` | same, for OpenRouter |
| `DATABASE_URL` | a Neon Postgres URL | persist keys/availability across restarts (else SQLite, ephemeral) |
| `DEBUG` | `False` | quieter logs |

After saving secrets, the Space restarts and builds. Watch the **Logs** tab.

## 3. Use it from clients / the frontend

Base URL = `https://whiteshadows1-gateway.hf.space/v1`, key = your `GATEWAY_API_KEY`.

```bash
curl https://whiteshadows1-gateway.hf.space/v1/chat/completions \
  -H "Authorization: Bearer YOUR_GATEWAY_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"model":"auto","messages":[{"role":"user","content":"hello"}]}'
```

For Open WebUI (wherever you host it):
```
OPENAI_API_BASE_URL = https://whiteshadows1-gateway.hf.space/v1
OPENAI_API_KEY      = YOUR_GATEWAY_API_KEY
```

## Caveats (free tier)

- **Sleeps after ~48h idle** → first request after sleep is slow (~30–60s) while it wakes.
- **Ephemeral storage** → without `DATABASE_URL`, the SQLite DB resets on rebuild.
  That's fine: keys come from Secrets and the model pool from the bundled YAML, so
  the gateway is fully functional stateless. Add Neon only if you want persistence.
- **Public app** → that's why `REQUIRE_GATEWAY_AUTH=true` matters; without the key,
  `/v1` returns 401.
- Works with streaming (it's a real long-lived container, not serverless).
