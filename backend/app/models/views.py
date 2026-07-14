"""
Read-only VIEWS. These are what the app and UI actually read.

Defined as SQLAlchemy Core Tables (not ORM classes) because a view has no primary
key to map. Use them with `select()`; never INSERT/UPDATE — Postgres owns them.

They are created by schema.sql, not by SQLAlchemy. `Table(...)` here only
DESCRIBES them so queries can be composed in Python.

WHY YOU MUST USE THESE, and not query Deployment directly:

  * They apply is_callable(status, cooldown_until), which is what lets a
    rate-limited deployment RETURN once its cooldown expires. Filtering on
    Deployment.is_working alone leaves a 429'd key benched forever.
  * They are scoped per user, so a user can never be shown a model they hold no
    key for.
"""

from sqlalchemy import (
    Table, Column, MetaData, BigInteger, String, Boolean, Integer, DateTime,
)
from sqlalchemy.dialects.postgresql import ARRAY

# Separate MetaData: these are views, and must never be swept into a
# create_all()/drop_all() over the ORM's Base.metadata.
view_metadata = MetaData()


# ── "MY MODELS" — the dashboard read model. One row per model family per user. ──
#
#   is_common            CATALOG badge: 2+ providers serve this family ANYWHERE.
#                        Says nothing about what THIS user can call. Never route on it.
#
#   has_backup_key       "If the deployment I'm using fails, is there another to
#                        try?" TRUE at 2+ live deployments — INCLUDING two keys for
#                        the SAME provider. Two Groq keys IS real redundancy.
#                        This is the flag a user actually cares about.
#
#   has_backup_provider  The STRONGER guarantee: 2+ distinct live PROVIDERS.
#                        Survives a whole provider outage, not just one dead key.
#
# Both backup flags count only CALLABLE deployments — a backup that is itself
# dead is not a backup.
v_my_models = Table(
    "v_my_models", view_metadata,
    Column("user_id", BigInteger),
    Column("model", String),                    # bare family name, e.g. 'gpt-oss-120b'
    Column("providers", ARRAY(String)),         # ['Groq', 'NVIDIA NIM']
    Column("is_common", Boolean),
    Column("live_keys", Integer),
    Column("live_providers", Integer),
    Column("has_backup_key", Boolean),
    Column("has_backup_provider", Boolean),
    Column("is_usable", Boolean),
    Column("total_keys", Integer),
    Column("total_providers", Integer),
    Column("last_checked_at", DateTime(timezone=True)),
    Column("mode", String),      # 'chat' | 'embedding'
    Column("publisher", String), # normalized org — appended last (view-replace rule)
)


# ── THE ROUTER'S HOT READ ──────────────────────────────────────────────────────
# Every deployment callable RIGHT NOW, keyed by the bare family name the client
# sends. Feed straight into litellm.Router's model_list. Dead keys, disabled
# models and still-cooling deployments are already filtered out; benched
# deployments whose cooldown has EXPIRED reappear here automatically.
v_live_deployments = Table(
    "v_live_deployments", view_metadata,
    Column("user_id", BigInteger),
    Column("model", String),            # what the client asks for
    Column("deployment_id", BigInteger),
    Column("litellm_model", String),    # what we actually call
    Column("provider_id", BigInteger),
    Column("provider_key_id", BigInteger),
    Column("rpm", Integer),
    Column("mode", String),
)
