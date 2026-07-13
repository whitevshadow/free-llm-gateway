"""
Per-key availability probe + refresh (Phase 4).

Runs the full spine refresh: discovery (providers/models/keys) → per-key probe
(writes `deployments`, rolls up `master_model`) → derive (common models) → Router
reload. Run manually or on a schedule:

    docker compose exec app python -m app.scripts.probe_models
"""

import json
import logging

logging.basicConfig(level=logging.INFO)


def main() -> None:
    from app.services.pipeline import refresh
    result = refresh()
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
