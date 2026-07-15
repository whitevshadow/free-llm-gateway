"""
Database engine + session, and the schema bootstrap.

POSTGRES ONLY. SQLite is not supported and cannot be: the data model relies on
GENERATED columns, plpgsql trigger functions, native ENUM types, composite
foreign keys and views — none of which SQLite provides. Those are not
decorations; they are what enforces (a) that a user can only ever use their OWN
provider key, and (b) that the is_common / is_reachable flags cannot drift.

SCHEMA SOURCE OF TRUTH = backend/schema.sql.
The ORM classes in app/models/ MAP ONTO that file; they do not generate it.
`Base.metadata.create_all()` is deliberately NOT used, because it cannot emit
triggers, functions or views — it would build a database that looks right and
silently lacks every guarantee the schema depends on.
"""

from pathlib import Path

from sqlalchemy import create_engine, text, BigInteger
from sqlalchemy.orm import declarative_base, sessionmaker

from app.core.config import settings

# Kept as a name so models read clearly; on Postgres this is simply BIGINT.
BigIntPK = BigInteger

DATABASE_URL = settings.DATABASE_URL

if DATABASE_URL.startswith("sqlite"):
    raise RuntimeError(
        "SQLite is not supported. This gateway requires PostgreSQL — the schema "
        "uses generated columns, triggers, enums and composite foreign keys that "
        "SQLite cannot express, and they are what enforce cross-user key "
        "isolation. Set DATABASE_URL to a postgresql:// URL."
    )

# Managed hosts (Render, Railway, Heroku, Fly) hand out `postgres://` URLs, but
# SQLAlchemy 1.4+ dropped that alias and needs an explicit driver. Normalise to
# psycopg2 so a copy-pasted host URL Just Works instead of raising at startup.
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = "postgresql+psycopg2://" + DATABASE_URL[len("postgres://"):]
elif DATABASE_URL.startswith("postgresql://"):
    DATABASE_URL = "postgresql+psycopg2://" + DATABASE_URL[len("postgresql://"):]

engine = create_engine(
    DATABASE_URL,
    echo=settings.DEBUG,
    pool_pre_ping=True,     # drop dead connections (hosted PG closes idle ones)
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()

SCHEMA_PATH = Path(__file__).resolve().parents[2] / "schema.sql"


def get_db():
    """FastAPI dependency: one session per request."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# ── Micro-migrations ─────────────────────────────────────────────────────────
# schema.sql is applied ONLY to an empty database, so a column added there never
# reaches a database that already has data. Each statement below is idempotent
# (ADD COLUMN IF NOT EXISTS) and mirrors something ALREADY present in schema.sql
# — fresh databases get it from the schema, existing ones from here. Additive,
# in-place changes only; anything structural (new tables, triggers, backfills)
# means it is time for Alembic.
MIGRATIONS = [
    # Escalating 429 cooldowns (SRS §7.3): consecutive-429 counter per deployment.
    "ALTER TABLE deployments "
    "ADD COLUMN IF NOT EXISTS rate_limit_strikes INTEGER NOT NULL DEFAULT 0",
    # Provider pinning (SRS §8.3): per-user soft pin, filters the candidate set.
    "ALTER TABLE router_config "
    "ADD COLUMN IF NOT EXISTS pinned_provider_id BIGINT "
    "REFERENCES providers(id) ON DELETE SET NULL",
    # v_my_models + statuses: the UI classifies WHY a model is down (auth_error
    # vs rate_limited vs 404) instead of a flat "Unavailable". CREATE OR REPLACE
    # VIEW is idempotent and may only APPEND columns — this definition must stay
    # byte-for-byte in sync with the one in schema.sql.
    """
    CREATE OR REPLACE VIEW v_my_models AS
    SELECT
        d.user_id,
        pm.normalized_name                          AS model,
        array_agg(DISTINCT p.name ORDER BY p.name)  AS providers,
        bool_or(pm.is_common)                       AS is_common,
        count(*) FILTER (WHERE p.enabled AND is_callable(d.status, d.cooldown_until))
                                                    AS live_keys,
        count(DISTINCT d.provider_id)
            FILTER (WHERE p.enabled AND is_callable(d.status, d.cooldown_until))
                                                    AS live_providers,
        count(*) FILTER (WHERE p.enabled AND is_callable(d.status, d.cooldown_until)) >= 2
                                                    AS has_backup_key,
        count(DISTINCT d.provider_id)
            FILTER (WHERE p.enabled AND is_callable(d.status, d.cooldown_until)) >= 2
                                                    AS has_backup_provider,
        bool_or(p.enabled AND is_callable(d.status, d.cooldown_until)) AS is_usable,
        count(*)                                    AS total_keys,
        count(DISTINCT d.provider_id)               AS total_providers,
        max(d.last_checked_at)                      AS last_checked_at,
        pm.mode                                     AS mode,
        min(pm.publisher)                           AS publisher,
        array_agg(DISTINCT d.status::text)          AS statuses
    FROM deployments d
    JOIN provider_models pm ON pm.id = d.provider_model_id
    JOIN providers       p  ON p.id  = d.provider_id
    GROUP BY d.user_id, pm.normalized_name, pm.mode
    """,
]


def run_migrations() -> None:
    """Apply the idempotent micro-migrations. Safe to run on every startup."""
    with engine.begin() as conn:
        for stmt in MIGRATIONS:
            conn.execute(text(stmt))


def init_schema() -> bool:
    """
    Apply schema.sql if the database is empty, then the micro-migrations.

    Returns True if the schema was applied, False if it was already present.
    Idempotent by inspection: we look for the `users` table rather than relying
    on IF NOT EXISTS, because schema.sql also creates types, functions, triggers
    and views, and re-running it wholesale on a live database would error.

    This is a bootstrap for fresh deployments, NOT a migration tool — the
    micro-migrations above cover additive column changes only.
    """
    with engine.connect() as conn:
        exists = conn.execute(text("SELECT to_regclass('public.users')")).scalar()

    applied = False
    if not exists:
        sql = SCHEMA_PATH.read_text(encoding="utf-8")
        with engine.begin() as conn:
            conn.execute(text(sql))
        applied = True

    run_migrations()
    return applied
