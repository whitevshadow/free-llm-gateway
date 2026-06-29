"""
Central Router Aggregator

WHY THIS EXISTS:
  Instead of importing every sub-router in main.py, we aggregate them here.
  When you add a new feature (e.g. /api/v1/billing), you add ONE line here
  instead of touching main.py. This keeps main.py clean and focused on
  app lifecycle concerns only.
"""

from fastapi import APIRouter
from app.core.config import settings
from app.api import auth, chat, providers, analytics, settings as settings_api

api_router = APIRouter()

# Each sub-router is mounted under the API version prefix
api_router.include_router(
    auth.router,
    prefix=f"{settings.API_V1_STR}/auth",
    tags=["Authentication"],
)

api_router.include_router(
    chat.router,
    prefix=f"{settings.API_V1_STR}/chat",
    tags=["Chat & Completions"],
)

api_router.include_router(
    providers.router,
    prefix=f"{settings.API_V1_STR}/providers",
    tags=["Providers & Catalog"],
)

api_router.include_router(
    analytics.router,
    prefix=f"{settings.API_V1_STR}/analytics",
    tags=["Analytics & Dashboard"],
)

api_router.include_router(
    settings_api.router,
    prefix=f"{settings.API_V1_STR}/settings",
    tags=["Settings & API Keys"],
)
