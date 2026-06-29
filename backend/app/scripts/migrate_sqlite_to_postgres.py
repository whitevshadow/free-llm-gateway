"""
One-off migration: copy the old SQLite database into the new PostgreSQL DB.

Run ONCE, manually, after switching DATABASE_URL to Postgres:

    docker compose exec app python -m app.scripts.migrate_sqlite_to_postgres
    # or point at a specific file:
    docker compose exec app python -m app.scripts.migrate_sqlite_to_postgres /app/data/gateway.db

WHAT IT DOES:
  • Creates the full schema in Postgres (Base.metadata.create_all).
  • Copies every table the app knows about (provider_keys, users, chat_*,
    token_usage_logs) from the SQLite file into Postgres.
  • Each table is migrated in its OWN transaction, so a hiccup on one table
    (e.g. an odd legacy row) never blocks the critical provider_keys.
  • Skips a table if the Postgres side already has rows (safe to re-run).
  • Resets each table's id sequence so future inserts don't collide.

The most important thing this preserves is your saved API keys (provider_keys).
"""

from __future__ import annotations

import os
import sys
import logging

from sqlalchemy import create_engine, MetaData, select, text

# Importing the package registers every ORM model on Base.metadata.
import app.models  # noqa: F401
from app.core.database import engine as target_engine, Base

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger("migrate")

DEFAULT_SQLITE = "/app/data/gateway.db"


def _reset_sequence(conn, table_name: str) -> None:
    """Advance the Postgres id sequence past the max migrated id."""
    try:
        conn.execute(
            text(
                f"SELECT setval(pg_get_serial_sequence('{table_name}', 'id'), "
                f"COALESCE((SELECT MAX(id) FROM {table_name}), 1))"
            )
        )
    except Exception as exc:  # table without a serial 'id', etc.
        logger.info("  (sequence reset skipped for %s: %s)", table_name, exc)


def main() -> None:
    sqlite_path = sys.argv[1] if len(sys.argv) > 1 else os.environ.get(
        "OLD_SQLITE_PATH", DEFAULT_SQLITE
    )

    backend = target_engine.url.get_backend_name()
    if not backend.startswith("postgres"):
        logger.error(
            "Target DATABASE_URL is not PostgreSQL (got '%s'). Aborting.", target_engine.url
        )
        sys.exit(1)

    logger.info("Target Postgres: %s", target_engine.url.render_as_string(hide_password=True))

    # 1) Ensure the full schema exists in Postgres.
    Base.metadata.create_all(bind=target_engine)
    logger.info("Postgres schema ready.")

    # 2) If there's no SQLite file, there's nothing to copy.
    if not os.path.exists(sqlite_path):
        logger.info("No SQLite DB at %s — schema created, nothing to migrate.", sqlite_path)
        return

    src_engine = create_engine(f"sqlite:///{sqlite_path}")
    src_meta = MetaData()
    src_meta.reflect(bind=src_engine)
    logger.info("Source SQLite: %s (%d table(s))", sqlite_path, len(src_meta.tables))

    summary: dict[str, str] = {}

    # 3) Copy table by table, in dependency order, each isolated.
    for table in Base.metadata.sorted_tables:
        name = table.name
        if name not in src_meta.tables:
            summary[name] = "not in source"
            continue

        src_table = src_meta.tables[name]
        try:
            with src_engine.connect() as sconn:
                rows = [dict(r._mapping) for r in sconn.execute(select(src_table))]

            if not rows:
                summary[name] = "0 rows"
                continue

            with target_engine.begin() as tconn:
                already = tconn.execute(select(table).limit(1)).first()
                if already:
                    summary[name] = "skipped (target not empty)"
                    continue
                tconn.execute(table.insert(), rows)
                _reset_sequence(tconn, name)
            summary[name] = f"copied {len(rows)} row(s)"
        except Exception as exc:
            summary[name] = f"FAILED: {exc}"
            logger.warning("  %s migration failed: %s", name, exc)

    logger.info("\nMigration summary:")
    for name, result in summary.items():
        logger.info("  %-22s %s", name, result)
    logger.info("\nDone. Saved keys live in 'provider_keys'.")


if __name__ == "__main__":
    main()
