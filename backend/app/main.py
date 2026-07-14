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
  3. schema.sql is applied if the database is empty (NOT create_all — that
     cannot emit the triggers/views the integrity guarantees depend on).
  4. The first admin user + their gateway key are bootstrapped and logged once.
  5. Middleware stack is mounted.
  6. Routes are registered.
  7. The server starts listening on HOST:PORT.

Routers are built PER USER on first request. There is no global router and no
global key namespace: keys are decrypted per call and passed into litellm.
"""

import time
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.core.config import settings
from app.api.router import api_router
from app.api import openai_compat, anthropic_compat, admin, me
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

    # Apply schema.sql if the database is empty.
    #
    # NOT Base.metadata.create_all(): that cannot emit triggers, functions or
    # views, so it would build a database that LOOKS right while silently
    # lacking every guarantee the schema depends on — the is_common /
    # is_reachable triggers, is_callable(), and both routing views.
    # schema.sql is the source of truth; the ORM only maps onto it.
    try:
        import app.models  # noqa: F401  (registers all ORM models)
        from app.core.database import init_schema
        if init_schema():
            logger.info("Empty database — applied schema.sql.")
        else:
            logger.info("Database schema already present.")
    except Exception as exc:
        logger.error(f"Database initialisation failed: {exc}")

    # Ensure the owner user + a gateway key exist so the admin API is reachable
    # on a fresh DB (the bootstrap key is logged once).
    try:
        from app.services.bootstrap import ensure_admin_and_bootstrap_key
        ensure_admin_and_bootstrap_key()
    except Exception as exc:
        logger.error(f"Bootstrap failed: {exc}")

    # NOTE: keys are NOT injected into os.environ any more. A process has one
    # environment, so that was single-tenant by construction — two users' Groq
    # keys collided in the same variable. Keys are now decrypted per request and
    # passed straight into litellm. See services/key_store.py.
    #
    # Routers are built PER USER, lazily, on first request (core/llm_router.py),
    # so there is nothing to preload here.
    logger.info("Routers are built per-user on demand; no global pool to preload.")
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
        "Multi-user LLM gateway. Bring your own provider keys; the gateway "
        "load-balances every model you can reach across all of your keys and "
        "providers, benching whatever gets rate-limited.\n\n"
        "**Auth:** every endpoint needs a gateway key — click **Authorize** and "
        "paste `Bearer sk-gw-…`. Admins seed providers (`/v1/admin/*`); users add "
        "their own keys (`/v1/me/*`) and call `/v1/chat/completions`."
    ),
    version=settings.VERSION,
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan,
)

# The Swagger "Authorize" button comes from the APIKeyHeader scheme declared in
# api/gateway_auth.py, which every /v1 route depends on. The docs PAGE stays
# public (it is only a schema listing); the API behind it is not — every endpoint
# still 401s without a valid gateway key.


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
    Health check for Docker / load balancers / monitoring.

    Deliberately reports NO model or key information: there is no global pool any
    more, and what is callable is per-user. Ask /v1/me/models as a user for that.
    """
    from app.core import llm_router

    return success_response(
        data={
            "status": "healthy",
            "uptime_seconds": round(time.time() - _start_time, 1),
            "environment": settings.ENVIRONMENT,
            **llm_router.router_health(),
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
app.include_router(me.router, tags=["Me"])


# ───────────────────────────────────────────────────────────────
# SERVE FRONTEND SPA (single-container deployment)
# ───────────────────────────────────────────────────────────────
# Must run LAST so the SPA catch-all route never shadows API endpoints.

mount_frontend(app)
