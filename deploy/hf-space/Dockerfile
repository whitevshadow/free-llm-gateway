# Hugging Face Docker Space — Lite-LLM gateway (backend only).
#
# Builds the FastAPI gateway from the PUBLIC GitHub repo and serves on port 7860
# (the port HF Spaces require). Put this file + README.md at the root of your
# Space repo (huggingface.co/spaces/whiteshadows1/gateway). All keys are supplied
# via the Space's Secrets — never commit them.

FROM python:3.11-slim

# Build tools + git (to fetch the backend source)
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential git \
    && rm -rf /var/lib/apt/lists/*

# HF Spaces run as a non-root user with uid 1000
RUN useradd -m -u 1000 user
USER user
ENV HOME=/home/user \
    PATH=/home/user/.local/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    HOST=0.0.0.0 \
    PORT=7860

WORKDIR /home/user/app
# Pull the backend source. Change the URL/branch if your repo differs.
# (Pin a tag/commit for reproducible builds, e.g. add `--branch v1`.)
RUN git clone --depth 1 https://github.com/whitevshadow/free-llm-gateway.git repo

WORKDIR /home/user/app/repo/backend
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt

EXPOSE 7860
# Run from the backend dir so `app.main` imports and config/model_pool.yaml resolves.
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-7860}"]
