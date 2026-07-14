# syntax=docker/dockerfile:1
# ─────────────────────────────────────────────────────────────────────────────
#  Multi-LLM Gateway — ONE image, ONE port. FastAPI + LiteLLM serve the API AND
#  the compiled React SPA from the same uvicorn process (mount_frontend()).
#
#  The SPA is built in stage 1 and copied into /app/static, where
#  mount_frontend() finds index.html and serves it alongside /v1, /docs, /health
#  — no nginx, no second container, no CORS (everything is one origin). Streaming
#  chat completions work natively because uvicorn does not buffer responses the
#  way a reverse proxy would.
#
#  Build context is the REPO ROOT (it needs backend/ and frontend/).
# ─────────────────────────────────────────────────────────────────────────────

# ── Stage 1: compile the SPA ─────────────────────────────────────────────────
#  vite.config.ts writes to ../backend/static, so from /build/frontend the
#  output lands at /build/backend/static; the runtime stage copies it from there.
FROM node:20-alpine AS frontend
WORKDIR /build/frontend
COPY frontend/package.json frontend/package-lock.json* ./
RUN npm install --no-audit --no-fund
COPY frontend/ ./
RUN npm run build


# ── Stage 2: python dependencies ─────────────────────────────────────────────
FROM python:3.11-slim AS deps

WORKDIR /code
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY backend/requirements.txt .
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt


# ── Stage 3: runtime ─────────────────────────────────────────────────────────
FROM python:3.11-slim AS runtime

WORKDIR /app
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    HOST=0.0.0.0 \
    PORT=8000

COPY --from=deps /usr/local/lib/python3.11/site-packages /usr/local/lib/python3.11/site-packages
COPY --from=deps /usr/local/bin /usr/local/bin

COPY backend/app /app/app

# schema.sql is NOT optional. It is the source of truth for the database, and
# init_schema() reads it at startup to build a fresh one. create_all() cannot
# replace it — it emits no triggers, functions or views, so the is_common /
# is_reachable triggers, is_callable(), and both routing views would silently not
# exist. Ship it, or the app builds a database that lacks every guarantee it
# depends on (including the FKs that stop one user spending another's quota).
COPY backend/schema.sql /app/schema.sql

# The compiled SPA. Its presence flips mount_frontend() out of API-only mode:
# uvicorn now serves index.html + /assets, so the whole app lives on one port.
COPY --from=frontend /build/backend/static /app/static

# Non-root: this process holds users' decrypted provider keys in memory, so it
# should not also own the filesystem.
RUN useradd --create-home --uid 10001 gateway && chown -R gateway:gateway /app
USER gateway

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD ["python", "-c", "import os,urllib.request,sys; p=os.environ.get('PORT','8000'); sys.exit(0 if urllib.request.urlopen('http://localhost:'+p+'/health').status==200 else 1)"]

CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
