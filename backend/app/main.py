"""
Application Entry Point — main.py

This is where everything comes together:
  • FastAPI app instantiation
  • Middleware stack (order matters: outermost runs first)
  • Database table auto-creation
  • Global exception handler
  • Route registration
  • Root & health endpoints

STARTUP CHECKLIST (what happens when you run `uvicorn app.main:app`):
  1. Settings are loaded from .env via pydantic-settings.
  2. Logging is configured.
  3. Database engine is created, tables are auto-generated.
  4. Provider API keys are injected into os.environ (by llm_service import).
  5. Middleware stack is mounted.
  6. Routes are registered.
  7. The server starts listening on HOST:PORT.
"""

import time
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.core.config import settings
from app.core.database import engine, Base
from app.api.router import api_router
from app.api import openai_compat, anthropic_compat, admin
from app.middleware.request_id import RequestIDMiddleware
from app.middleware.logging_middleware import LoggingMiddleware
from app.exceptions import GatewayException
from app.utils.responses import success_response, error_response
from app.core.frontend import mount_frontend

# ───────────────────────────────────────────────────────────────
# LOGGING CONFIGURATION
# ───────────────────────────────────────────────────────────────

logging.basicConfig(
    level=getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO),
    format="%(asctime)s │ %(levelname)-7s │ %(name)s │ %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("gateway")

# Silence overly chatty libraries
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logging.getLogger("litellm").setLevel(logging.WARNING)


# ───────────────────────────────────────────────────────────────
# LIFESPAN (replaces deprecated @app.on_event)
# ───────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Runs ONCE at startup and ONCE at shutdown.

    Startup:  create database tables, log provider status.
    Shutdown: placeholder for cleanup (close pools, flush logs, etc.).
    """
    # ── Startup ─────────────────────────────────────────
    logger.info("=" * 60)
    logger.info(f"  {settings.PROJECT_NAME} v{settings.VERSION}")
    logger.info(f"  Environment: {settings.ENVIRONMENT}")
    logger.info(f"  Debug: {settings.DEBUG}")
    logger.info("=" * 60)

    # Auto-create tables. Import the model package first so EVERY table (incl.
    # provider_keys and model_availability) is registered on Base.metadata.
    try:
        import app.models  # noqa: F401  (registers all ORM models)
        Base.metadata.create_all(bind=engine)
        logger.info("Database tables initialised successfully.")
    except Exception as exc:
        logger.error(f"Database initialisation failed: {exc}")

    # Inject any keys configured via the Settings UI into the environment BEFORE
    # the Router is built, so persisted keys activate their deployments.
    try:
        from app.services.key_store import apply_persisted_keys
        apply_persisted_keys()
    except Exception as exc:
        logger.error(f"Could not apply persisted keys: {exc}")

    # Log provider configuration status
    configured = settings.get_configured_providers()
    logger.info(f"Configured providers: {', '.join(configured) if configured else 'NONE'}")

    # Build the smart Router from the free-provider pool and log its size.
    try:
        from app.core.llm_router import get_router, router_health
        get_router()
        health = router_health()
        logger.info(
            "Smart Router ready: %d deployment(s) across %d virtual model(s): %s",
            health["total_deployments"],
            len(health["virtual_models"]),
            ", ".join(health["virtual_models"].keys()) or "(none — set a free API key)",
        )
    except Exception as exc:
        logger.error(f"Router initialisation failed: {exc}")
    logger.info("=" * 60)

    yield  # ← App is running and serving requests here

    # ── Shutdown ────────────────────────────────────────
    logger.info("Shutting down Multi-LLM Gateway...")


# ───────────────────────────────────────────────────────────────
# APP INSTANCE
# ───────────────────────────────────────────────────────────────

app = FastAPI(
    title=settings.PROJECT_NAME,
    description=(
        "Unified API Gateway for OpenAI, Anthropic Claude, Google Gemini, "
        "DeepSeek, xAI Grok, NVIDIA NIM, and Ollama. "
        "Powered by LiteLLM with smart routing, automatic fallback, "
        "streaming, and token analytics."
    ),
    version=settings.VERSION,
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan,
)


# ───────────────────────────────────────────────────────────────
# MIDDLEWARE STACK
# ───────────────────────────────────────────────────────────────
# Order matters! Middleware is applied bottom-to-top (last added = outermost).
# Request flow:  CORS → RequestID → Logging → Route Handler

app.add_middleware(LoggingMiddleware)
app.add_middleware(RequestIDMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ───────────────────────────────────────────────────────────────
# GLOBAL EXCEPTION HANDLER
# ───────────────────────────────────────────────────────────────

@app.exception_handler(GatewayException)
async def gateway_exception_handler(request: Request, exc: GatewayException):
    """
    Catches ALL custom GatewayException subclasses and returns a clean
    JSON error response instead of a raw 500 traceback.
    """
    logger.error(
        "gateway_exception",
        extra={
            "error_code": exc.error_code,
            "message": exc.message,
            "status_code": exc.status_code,
            "path": request.url.path,
        },
    )
    return JSONResponse(
        status_code=exc.status_code,
        content=error_response(
            message=exc.message,
            error_code=exc.error_code,
            status_code=exc.status_code,
        ),
    )


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    """
    Safety net for any unhandled exception. Logs the full traceback
    server-side but returns a generic message to the client (never leak
    internal details to users).
    """
    logger.exception(f"Unhandled exception on {request.method} {request.url.path}")
    return JSONResponse(
        status_code=500,
        content=error_response(
            message="An unexpected internal error occurred. Please try again later.",
            error_code="INTERNAL_SERVER_ERROR",
        ),
    )


# ───────────────────────────────────────────────────────────────
# ROOT & HEALTH ENDPOINTS
# ───────────────────────────────────────────────────────────────

_start_time = time.time()


@app.get("/health", tags=["System"])
def health_check():
    """
    Health check endpoint. Used by Docker, load balancers, and monitoring.

    Returns:
      - status: "healthy"
      - uptime_seconds: how long the server has been running
      - environment: dev/staging/prod
      - configured_providers: list of providers with API keys set
    """
    try:
        from app.core.llm_router import router_health
        pool = router_health()
    except Exception:
        pool = {"virtual_models": {}, "total_deployments": 0, "cooling_down": []}

    return success_response(
        data={
            "status": "healthy",
            "uptime_seconds": round(time.time() - _start_time, 1),
            "environment": settings.ENVIRONMENT,
            "configured_providers": settings.get_configured_providers(),
            "router": pool,
        },
        message="All systems operational",
    )


# ───────────────────────────────────────────────────────────────
# REGISTER ALL ROUTES
# ───────────────────────────────────────────────────────────────

app.include_router(api_router)

# ───────────────────────────────────────────────────────────────
# COMPATIBILITY ENDPOINTS (consumed by external software)
# ───────────────────────────────────────────────────────────────
# Mounted at root so paths are exactly /v1/chat/completions, /v1/models, and
# /v1/messages — what OpenAI clients and Claude Code expect. Registered BEFORE
# mount_frontend so the SPA catch-all never shadows them.

app.include_router(openai_compat.router, tags=["OpenAI-compatible"])
app.include_router(anthropic_compat.router, tags=["Anthropic-compatible"])
app.include_router(admin.router, tags=["Admin"])


# ───────────────────────────────────────────────────────────────
# SERVE FRONTEND SPA (single-container deployment)
# ───────────────────────────────────────────────────────────────
# Must run LAST so the SPA catch-all route never shadows API endpoints.

mount_frontend(app)
