"""
One-off maintenance scripts.

Empty for now. The old SQLite→Postgres migration and the YAML pool seeder were
removed with the designs they served: there is no SQLite (Postgres is the only
supported database) and no YAML pool (the model catalog is discovered from each
provider's /v1/models endpoint).
"""
