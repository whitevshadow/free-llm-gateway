"""
ORM model registry — the single consolidated data model (see DATA_MODEL.md).

Eleven tables: users + gateway_api_keys + providers + provider_keys (encrypted)
+ provider_models + master_model + deployments + common_model +
common_model_members + request_logs + router_config.
"""

from app.core.database import Base

# ── Identity ────────────────────────────────────────────────────────────────
from app.models.user import User

# ── Enums ───────────────────────────────────────────────────────────────────
from app.models.enums import ModelHealth, ModelMode

# ── Common-model routing spine ──────────────────────────────────────────────
from app.models.provider import Provider
from app.models.gateway_api_key import GatewayApiKey
from app.models.provider_api_key import ProviderApiKey
from app.models.provider_model import ProviderModel
from app.models.master_model import MasterModel
from app.models.deployment import Deployment
from app.models.common_model import CommonModel, CommonModelMember
from app.models.request_log import RequestLog

# ── Router behaviour (single-row settings) ──────────────────────────────────
from app.models.model_pool import RouterConfig

__all__ = [
    "Base",
    "User",
    "ModelHealth",
    "ModelMode",
    "Provider",
    "GatewayApiKey",
    "ProviderApiKey",
    "ProviderModel",
    "MasterModel",
    "Deployment",
    "CommonModel",
    "CommonModelMember",
    "RequestLog",
    "RouterConfig",
]
