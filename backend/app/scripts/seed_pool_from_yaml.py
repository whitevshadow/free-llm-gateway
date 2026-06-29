"""
Seed the PostgreSQL model pool from config/model_pool.yaml.

Run ONCE, manually, to move the curated pool (the 4 reliability-core models,
their provider routes, and the router settings) into the DB:

    docker compose exec app python -m app.scripts.seed_pool_from_yaml

After seeding, MODEL_POOL_SOURCE=auto makes the Router build from the DB. Edit
rows in `model_deployments` / `router_config` and reload to apply changes —
no YAML edit or image rebuild needed.

Re-running REPLACES the stored pool with the current YAML (clean reseed).
NVIDIA / OpenRouter auto-discovery is unaffected — it still runs in code and
augments whatever pool is loaded.
"""

from __future__ import annotations

import logging

import yaml

import app.models  # noqa: F401  (register all ORM models)
from app.core.database import SessionLocal, engine, Base
from app.core.llm_router import _pool_path
from app.models.model_pool import ModelDeployment, RouterConfig

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger("seed-pool")

# litellm_params keys that get dedicated columns; everything else goes to `extra`.
_KNOWN = {"model", "api_key", "api_base", "rpm"}


def main() -> None:
    path = _pool_path()
    if not path.exists():
        logger.error("YAML pool not found at %s — nothing to seed.", path)
        return

    with open(path, "r", encoding="utf-8") as fh:
        pool = yaml.safe_load(fh) or {}

    raw_list = pool.get("model_list", []) or []
    rsettings = pool.get("router_settings", {}) or {}

    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        # Clean reseed: clear the existing stored pool.
        deleted = db.query(ModelDeployment).delete()
        db.query(RouterConfig).delete()

        # Insert deployments.
        count = 0
        for entry in raw_list:
            params = dict(entry.get("litellm_params", {}) or {})
            model = params.get("model")
            if not model:
                continue
            extra = {k: v for k, v in params.items() if k not in _KNOWN}
            db.add(
                ModelDeployment(
                    model_name=entry.get("model_name", ""),
                    litellm_model=model,
                    api_key_ref=params.get("api_key"),
                    api_base_ref=params.get("api_base"),
                    rpm=params.get("rpm"),
                    extra=extra or None,
                    enabled=True,
                )
            )
            count += 1

        # Insert the single router-config row (id=1).
        db.add(
            RouterConfig(
                id=1,
                routing_strategy=rsettings.get("routing_strategy", "usage-based-routing-v2"),
                num_retries=rsettings.get("num_retries", 4),
                cooldown_time=rsettings.get("cooldown_time", 30),
                allowed_fails=rsettings.get("allowed_fails", 3),
                fallbacks=rsettings.get("fallbacks"),
            )
        )
        db.commit()

        # Per-model summary.
        names: dict[str, int] = {}
        for entry in raw_list:
            n = entry.get("model_name", "?")
            names[n] = names.get(n, 0) + 1

        logger.info("Cleared %d old deployment row(s).", deleted)
        logger.info("Seeded %d deployment(s) across %d virtual model(s):", count, len(names))
        for n in sorted(names):
            logger.info("  %-16s %d route(s)", n, names[n])
        logger.info(
            "Router settings stored: strategy=%s retries=%s cooldown=%ss fallbacks=%d",
            rsettings.get("routing_strategy"),
            rsettings.get("num_retries"),
            rsettings.get("cooldown_time"),
            len(rsettings.get("fallbacks") or []),
        )
        logger.info("\nDone. The Router will build from PostgreSQL (MODEL_POOL_SOURCE=auto).")
    finally:
        db.close()


if __name__ == "__main__":
    main()
