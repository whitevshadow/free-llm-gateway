"""
Frontend serving — lets the FastAPI app serve the bundled React SPA.

In the single-container deployment, the compiled Vite build is copied to
`/app/static` (see the root Dockerfile) and served by uvicorn alongside the
API. This means ONE process and ONE port (8000) host both the backend and the
frontend.

If no bundled frontend is present (e.g. running the backend locally with
`uvicorn app.main:app --reload`), the app falls back to API-only mode and the
root path returns a small JSON welcome instead.
"""

import logging
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.core.config import settings
from app.utils.responses import success_response

logger = logging.getLogger("gateway")

# Where the Dockerfile copies the compiled SPA (frontend/dist → /app/static)
FRONTEND_DIST = Path(__file__).resolve().parents[2] / "static"


def mount_frontend(app: FastAPI) -> None:
    """
    Mount the compiled SPA if it exists; otherwise register an API-only root.

    Must be called AFTER all API routers are registered so the SPA catch-all
    route is matched last and never shadows real API endpoints.
    """
    index_file = FRONTEND_DIST / "index.html"

    if not index_file.exists():
        logger.info("No bundled frontend at %s — running in API-only mode.", FRONTEND_DIST)

        @app.get("/", tags=["System"], include_in_schema=True)
        def api_root():
            """Root welcome endpoint (API-only mode)."""
            return success_response(
                data={
                    "name": settings.PROJECT_NAME,
                    "version": settings.VERSION,
                    "docs": "/docs",
                },
                message="Welcome to the Multi-LLM Gateway Platform API",
            )

        return

    # Hashed JS/CSS bundles produced by Vite
    assets_dir = FRONTEND_DIST / "assets"
    if assets_dir.exists():
        app.mount("/assets", StaticFiles(directory=assets_dir), name="assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    async def spa_fallback(full_path: str):
        """
        Serve a real static file if it exists (favicon, etc.), otherwise return
        index.html so client-side routing (react-router) handles deep links.
        """
        candidate = FRONTEND_DIST / full_path
        if full_path and candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(index_file)

    logger.info("Serving bundled frontend from %s", FRONTEND_DIST)
