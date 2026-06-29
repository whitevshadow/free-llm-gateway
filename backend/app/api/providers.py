"""
Providers Router — lists available LLM providers, models, and fallback chains.

ENDPOINTS:
  GET /api/v1/providers           → full catalog with configuration status
  GET /api/v1/providers/fallbacks → active fallback chain definitions
"""

from fastapi import APIRouter, Depends
from typing import List, Dict, Any

from app.api.auth import get_current_user
from app.models.user import User
from app.providers import get_provider_catalog_with_status, FALLBACK_CHAINS
from app.utils.responses import success_response

router = APIRouter()


@router.get("")
def list_providers(current_user: User = Depends(get_current_user)):
    """
    Returns all supported providers with their models and configuration status.

    The `is_configured` flag is computed live from environment variables,
    so it always reflects the current server state.
    """
    catalog = get_provider_catalog_with_status()
    return success_response(
        data=catalog,
        message=f"{len(catalog)} providers available",
    )


@router.get("/fallbacks")
def list_fallbacks(current_user: User = Depends(get_current_user)):
    """
    Returns the active fallback chains.

    When a primary provider fails, the gateway tries these alternatives
    in the order listed.
    """
    return success_response(
        data=FALLBACK_CHAINS,
        message="Active fallback chain configuration",
    )
