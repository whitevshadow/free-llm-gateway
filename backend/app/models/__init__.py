"""
ORM model registry.

EIGHT TABLES, mapping onto backend/schema.sql — which is the SOURCE OF TRUTH.
These classes describe the database; they do not generate it. `create_all()` is
not used, because it cannot emit the triggers, functions and views the schema
depends on.

  GLOBAL CATALOG (admin-seeded, no user scope)
    Provider ──► ProviderModel

  PER-USER
    User ──► GatewayApiKey                (tokens clients present TO us)
         └─► ProviderKey ──┐              (the user's OWN upstream secrets)
                           ├──► Deployment  (the atomic callable unit)
         ProviderModel ────┘

    RequestLog, RouterConfig

REMOVED in the multi-user rewrite — do not re-add:
  MasterModel         per-user model rollup; Deployment already is it.
  CommonModel         replaced by ProviderModel.is_common (a flag, not a table).
  CommonModelMember   the fallback chain; there is no ordering any more.

Read-only views live in app.models.views (v_my_models, v_live_deployments) — use
those to route or to show a user their models, never Deployment directly.
"""

from app.core.database import Base

# ── Enums ───────────────────────────────────────────────────────────────────
from app.models.enums import ModelHealth, ModelMode

# ── Identity + RBAC ─────────────────────────────────────────────────────────
from app.models.user import User
from app.models.gateway_api_key import GatewayApiKey

# ── Global catalog ──────────────────────────────────────────────────────────
from app.models.provider import Provider
from app.models.provider_model import ProviderModel

# ── Per-user ────────────────────────────────────────────────────────────────
from app.models.provider_key import ProviderKey
from app.models.deployment import Deployment

# ── Ledger + router behaviour ───────────────────────────────────────────────
from app.models.request_log import RequestLog
from app.models.router_config import RouterConfig
# Append-only health transitions, backing the dashboard's health timeline
# (SRS §20). Written only when a deployment's status actually changes.
from app.models.deployment_status_event import DeploymentStatusEvent
# Per-user dashboard preferences. Deliberately NOT routing settings — those
# stay typed in router_config (see the model's docstring).
from app.models.user_setting import UserSetting
# Provider-level circuit breaker (SRS §14). Global by design — see the model.
from app.models.provider_circuit import ProviderCircuit
# Named, ordered routing chains addressable as a model name (OMNIROUTE Phase 1).
# Orthogonal to RouterConfig: that tunes the pool, this defines a candidate list.
from app.models.combo import Combo

# ── Read-only views ─────────────────────────────────────────────────────────
from app.models.views import v_my_models, v_live_deployments

__all__ = [
    "Base",
    "ModelHealth",
    "ModelMode",
    "User",
    "GatewayApiKey",
    "Provider",
    "ProviderModel",
    "ProviderKey",
    "Deployment",
    "RequestLog",
    "RouterConfig",
    "DeploymentStatusEvent",
    "UserSetting",
    "ProviderCircuit",
    "Combo",
    "v_my_models",
    "v_live_deployments",
]
