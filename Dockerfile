# syntax=docker/dockerfile:1
# ─────────────────────────────────────────────────────────────
# Multi-LLM Gateway — API-only image (uvicorn + FastAPI + LiteLLM).
#
# The UI is provided by Open WebUI (see docker-compose.yml / start-openwebui.ps1).
# This image only serves the OpenAI-compatible /v1 gateway that Open WebUI talks to.
#
#   Stage 1 (backend-deps) → installs the Python dependencies
#   Stage 2 (runtime)      → slim image running uvicorn
#
# Build:  docker build -t multi-llm-gateway .
# Run:    docker run -p 8000:8000 --env-file backend/.env multi-llm-gateway
# ─────────────────────────────────────────────────────────────

# ── Stage 1: Install backend dependencies ────────────────────
FROM python:3.11-slim AS backend-deps

WORKDIR /code

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY backend/requirements.txt .
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt


# ── Stage 2: Runtime ─────────────────────────────────────────
FROM python:3.11-slim AS runtime

WORKDIR /app

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    HOST=0.0.0.0 \
    PORT=8000

# Bring in the installed Python packages and console scripts
COPY --from=backend-deps /usr/local/lib/python3.11/site-packages /usr/local/lib/python3.11/site-packages
COPY --from=backend-deps /usr/local/bin /usr/local/bin

# Backend source code
COPY backend/app /app/app

# Free-provider model pool (read by the smart Router at startup)
COPY backend/config /app/config

# Directory for the SQLite database (mount a volume here to persist)
RUN mkdir -p /app/data

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8000/health').status==200 else 1)"

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
