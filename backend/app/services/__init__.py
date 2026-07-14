"""
Services package — business logic layer.

Route handlers call services; services call LiteLLM/database.
Route handlers should NEVER contain business logic directly.

  crypto         encrypt/decrypt provider secrets at rest
  gateway_keys   mint/verify/revoke the tokens clients present to us
  key_store      a user's own provider keys + the fan-out into deployments
  catalog        discover a provider's models from its /v1/models API
  prober         test each (model × key) and record per-key health
  usage_logger   append-only request ledger
  normalize      the model-family key that makes models interchangeable
  bootstrap      first-run admin user + key

Nothing is re-exported here: the old `llm_service` / `fallback` modules were the
retired SPA's completion path, with the routing logic that now lives in
core/llm_router.py (per-user Router) and in Postgres (health + cooldowns).
"""
