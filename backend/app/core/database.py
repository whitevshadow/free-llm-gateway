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


def init_schema() -> bool:
    """
    Apply schema.sql if the database is empty.

    Returns True if the schema was applied, False if it was already present.
    Idempotent by inspection: we look for the `users` table rather than relying
    on IF NOT EXISTS, because schema.sql also creates types, functions, triggers
    and views, and re-running it wholesale on a live database would error.

    This is a bootstrap for fresh deployments, NOT a migration tool. Once the
    schema changes against a database with real data, move to Alembic.
    """
    with engine.connect() as conn:
        exists = conn.execute(text("SELECT to_regclass('public.users')")).scalar()
        if exists:
            return False

    sql = SCHEMA_PATH.read_text(encoding="utf-8")
    with engine.begin() as conn:
        conn.execute(text(sql))
    return True
