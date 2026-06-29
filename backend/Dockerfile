# syntax=docker/dockerfile:1
# ─────────────────────────────────────────────────────────────
# Backend deployment — LiteLLM smart-routing gateway (FastAPI, API only).
#
# Build context = THIS backend/ folder. Deploy this folder as one service
# (e.g. on Railway set the service Root Directory to `backend`).
#
#   docker build -t llm-gateway ./backend
#   docker run -p 8000:8000 --env-file backend/.env llm-gateway
# ─────────────────────────────────────────────────────────────

# ── Stage 1: dependencies ────────────────────────────────────
FROM python:3.11-slim AS deps

WORKDIR /code
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt


# ── Stage 2: runtime ─────────────────────────────────────────
FROM python:3.11-slim AS runtime

WORKDIR /app
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    HOST=0.0.0.0 \
    PORT=8000

COPY --from=deps /usr/local/lib/python3.11/site-packages /usr/local/lib/python3.11/site-packages
COPY --from=deps /usr/local/bin /usr/local/bin

COPY app /app/app
COPY config /app/config
RUN mkdir -p /app/data

EXPOSE 8000

# Honour the platform-injected $PORT (Railway/Render/Fly), default 8000 locally.
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD ["python", "-c", "import os,urllib.request,sys; p=os.environ.get('PORT','8000'); sys.exit(0 if urllib.request.urlopen('http://localhost:'+p+'/health').status==200 else 1)"]

CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
