# syntax=docker/dockerfile:1
# ─────────────────────────────────────────────────────────────────────────────
#  Multi-LLM Gateway — the API. FastAPI + LiteLLM, and nothing else.
#
#  This image no longer builds or serves a UI. The dashboard was replaced by the
#  ported OmniRoute frontend, which is a Next.js SERVER (server components plus
#  the `/api/*` bridge that calls this service) and therefore cannot be baked in
#  as static files the way the old Vite SPA was. It ships as its own image —
#  see frontend/Dockerfile — and compose runs the two side by side.
#
#  mount_frontend() finds no /app/static here and stays in API-only mode, which
#  is now the intended state rather than a degraded one.
#
#  Build context is the REPO ROOT (it needs backend/).
# ─────────────────────────────────────────────────────────────────────────────

# ── Stage 1: python dependencies ─────────────────────────────────────────────
FROM python:3.11-slim AS deps

WORKDIR /code
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY backend/requirements.txt .
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt


# ── Stage 2: runtime ─────────────────────────────────────────────────────────
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

# Non-root: this process holds users' decrypted provider keys in memory, so it
# should not also own the filesystem.
RUN useradd --create-home --uid 10001 gateway && chown -R gateway:gateway /app
USER gateway

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD ["python", "-c", "import os,urllib.request,sys; p=os.environ.get('PORT','8000'); sys.exit(0 if urllib.request.urlopen('http://localhost:'+p+'/health').status==200 else 1)"]

CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
