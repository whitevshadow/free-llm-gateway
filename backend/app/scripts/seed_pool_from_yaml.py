"""
Seed the routing spine from the pool (Phase 9).

Imports providers, models, and env keys into the new tables (providers,
provider_models, provider_keys) by running discovery over the YAML pool +
auto-discovered catalogs. Run once after configuring keys, then run the probe:

    docker compose exec app python -m app.scripts.seed_pool_from_yaml
    docker compose exec app python -m app.scripts.probe_models

After a successful probe+derive, set ROUTER_SOURCE=db to serve common models.
"""

import json
import logging

logging.basicConfig(level=logging.INFO)


def main() -> None:
    from app.services.catalog import discover
    result = discover()
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
