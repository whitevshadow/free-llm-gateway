# Implementation Plan — Serve Common Models Without Breaking

> ## Status (implemented)
> Phases 0–6 complete; 7–9 substantially complete. `pytest`: 16 passing.
> - **Done:** single 11-table model; Fernet-encrypted provider keys; DB-issued
>   gateway keys (secure by default) + startup bootstrap key; admin key/refresh
>   endpoints; discovery → per-key probe → derive → DB-driven Router with
>   two-level fallback (per-key load-balance + ordered cross-provider cascade),
>   all behind `ROUTER_SOURCE`.
> - **Deferred:** (1) per-answer `request_logs` attribution — `common_model_id`
>   is linked, but `answered_deploy_id`/`provider_key_id`/`provider_id` are not
>   yet threaded from the Router response (best-effort per the risk register);
>   (2) an in-process scheduler — use cron / a job to run
>   `python -m app.scripts.probe_models` on an interval.
>
> **To go live (cutover):** configure keys (.env or `PUT /v1/admin/provider-keys`)
> → `python -m app.scripts.seed_pool_from_yaml` → `python -m app.scripts.probe_models`
> (real pings; fills `deployments` + `common_model`) → set `ROUTER_SOURCE=db`.
> Verify with `GET /v1/admin/common-models` and `GET /v1/models`.



Bridges the design ([schema.sql](schema.sql) / [DATA_MODEL.md](DATA_MODEL.md)) to
running code. Today those files are blueprints: no ORM models, no derive job, and
the Router still builds from the YAML pool. This plan wires the new spine in
**behind a feature flag**, so the existing `/v1` path keeps working until the new
path is proven, then cuts over.

**Goal:** a client calls `POST /v1/chat/completions {"model":"gpt-oss-120b"}` (a
`common_model`) and the request drains keys (Level 1) then providers (Level 2) in
`common_model_members` priority order — with per-API-key cooldown — and every call
is logged with the exact key that answered.

---

## Guiding constraints

- **Never break the live `/v1` path.** All new routing sits behind
  `ROUTER_SOURCE` (extends the existing `MODEL_POOL_SOURCE` idea in
  [config.py](app/core/config.py#L64)). Default stays on the current YAML/DB pool
  until Phase 6 is verified.
- **Single-user, Postgres.** New reference tables carry no `user_id`.
- **Reuse, don't rewrite.** The existing `litellm.Router`, cooldown, and fallback
  machinery in [llm_router.py](app/core/llm_router.py) already do Level 1 + Level 2
  — we feed it a `model_list` built from the new tables instead of the YAML.

---

## Phase 0 — Decisions to lock first (no code)

- [ ] **Migrations:** SQLAlchemy `create_all` (fast, matches current app) vs adopt
      **Alembic** (versioned, safer for a schema this size). Recommend Alembic now
      — the schema will change again.
- [ ] **Encryption key source:** reuse `settings.SECRET_KEY` to derive a Fernet key
      vs a dedicated `ENCRYPTION_KEY` env var. Recommend a dedicated var so
      rotating the JWT secret doesn't brick stored provider keys.
- [ ] **normalized_name rule:** exact function that maps `groq/openai/gpt-oss-120b`
      → `gpt-oss-120b`. This is the join key for the whole common-model concept —
      get it written and unit-tested before Phase 5.

---

## Phase 1 — Schema as code

Make the ten tables real and creatable.

- [ ] Add ORM models under [app/models/](app/models/): `gateway_api_key.py`,
      `provider.py`, `provider_key_v2.py`, `provider_model.py`, `master_model.py`,
      `deployment.py`, `common_model.py`, `common_model_member.py`,
      `request_log.py`. (Keep the old models importable during migration.)
- [ ] Register them in [app/models/__init__.py](app/models/__init__.py).
- [ ] Set up **Alembic** (or a `schema.sql` runner) and generate the initial
      migration; confirm it matches [schema.sql](schema.sql) 1:1 (enums, indexes,
      unique constraints, cascade rules).
- [ ] **Verify:** run the migration on a scratch Postgres; `\dt` shows 10 tables,
      `\d deployments` shows the `(master_model_id, provider_key_id)` unique.

## Phase 2 — Secrets & auth

- [ ] `app/services/crypto.py`: `encrypt(plaintext)->bytes`, `decrypt(bytes)->str`,
      `mask(plaintext)->str` (Fernet). Provider keys stored as `key_ciphertext` +
      `key_masked`; plaintext never persisted or returned.
- [ ] Gateway keys: `mint()` returns raw token once, stores `sha256` in `key_hash`
      + a `key_prefix`. `verify(token)` hashes and looks up.
- [ ] Wire `verify(token)` into the existing `verify_gateway_key` dependency
      ([app/api/gateway_auth.py](app/api/gateway_auth.py)) so `/v1` accepts DB-issued
      keys, not just the single env `GATEWAY_API_KEY`.
- [ ] On startup, decrypt active `provider_keys` and inject into `os.environ` by
      `env_slot` — the same bridge [key_store.py](app/services/key_store.py) already
      does, so LiteLLM's `os.environ/<VAR>` refs resolve.
- [ ] **Verify:** save a key via API → row is ciphertext, GET returns only `••••1234`;
      a minted gateway token authenticates a `/v1/models` call, a wrong one 401s.

## Phase 3 — Discovery → `provider_models`

- [ ] `app/services/catalog.py`: for each `provider` with an active key, fetch its
      model catalog and **upsert** `provider_models` (set `litellm_model`,
      `normalized_name`, `mode`, `is_free`). Reuse the NVIDIA/OpenRouter fetchers
      already in [llm_router.py](app/core/llm_router.py#L210) (`_fetch_nvidia_model_ids`,
      `_fetch_openrouter_model_ids`); add static catalogs for Groq/Gemini/etc.
- [ ] **Verify:** run discovery with one Groq key set → `provider_models` has Groq
      rows with correct `normalized_name`.

## Phase 4 — Per-key probe → `deployments` + `master_model` rollup

Replace the "1 row per concrete model, available if ANY key answered" behavior of
the current [probe_models.py](app/scripts/probe_models.py) with **per-key** results.

- [ ] For every `(provider_model × active provider_key)`, send a 1-token ping in
      parallel; upsert a `deployments` row with `status`, `http_code`,
      `latency_ms`, `error`, `last_checked_at` (429 = reachable).
- [ ] Roll up each `master_model`: `is_working = any(deployment.is_working)`,
      `working_key_count`, `last_checked_at`. Create the `master_model` row when a
      `provider_model` first gets a working key.
- [ ] **Verify:** with two Groq keys (one revoked), the model's `master_model` is
      `is_working=true`, `working_key_count=1`, and the revoked key's `deployment`
      row shows `status='auth_error'`.

## Phase 5 — Derive job → `common_model` + `common_model_members`

- [ ] `app/services/derive_common.py`: group **working** `master_model` by
      `normalized_name`; for any name with **≥2 distinct providers**, upsert a
      `common_model` (`provider_count`, `refreshed_at`) and **rewrite** its
      `common_model_members` ordered by latency (best `master_model` = `priority 0`).
- [ ] Idempotent: re-running reconciles (adds new working members, removes dead
      ones, re-numbers `priority`). Respect the `UNIQUE(common_model_id, priority)`
      constraint (renumber in one transaction).
- [ ] **Verify:** a model on Groq+NVIDIA yields one `common_model` with 2 members,
      priority 0/1 by latency; a model on only one provider yields none.

## Phase 6 — Router builder from the new tables (the core)

New builder alongside the existing `_build_router()`, selected by `ROUTER_SOURCE`.

- [ ] `build_model_list_from_db()`: for each `common_model`, for each member
      (`master_model`), for each **working `deployment` (per key)** → emit one
      LiteLLM `model_list` entry `{model_name: common_model.name,
      litellm_params:{model: deployment.litellm_model, api_key: os.environ/<slot>}}`.
      This makes Level-1 per-key load-balancing automatic.
- [ ] Build `fallbacks` from member `priority`: within a common name, LiteLLM
      cools down keys; when a whole member is down, cascade to the next. (If
      priority must be *strict*, register members under sub-names and add explicit
      `fallbacks`; see DATA_MODEL "two levels".)
- [ ] Feed `num_retries`/`cooldown_time`/`allowed_fails` from `router_config`
      (already supported by the current Router path).
- [ ] Keep `FILTER_BY_AVAILABILITY` semantics: only working deployments are emitted.
- [ ] **Verify:** `router_health()` lists each `common_model` with the expected
      deployment count (= sum of working keys across members).

## Phase 7 — Serving & logging

- [ ] `/v1/models` ([openai_compat.py](app/api/openai_compat.py)) lists
      `common_model.name`s (active) instead of / in addition to YAML virtuals.
- [ ] `/v1/chat/completions` resolves the requested model through the DB-built
      Router. Unknown model → `DEFAULT_VIRTUAL_MODEL` (existing behavior).
- [ ] After each call, write a `request_logs` row with `gateway_api_key_id`,
      `requested_model`, resolved `common_model_id`, the answering `deployment`/
      `provider_key`/`provider`, tokens, cost, latency, `is_fallback`. Extend
      [usage_logger.py](app/services/usage_logger.py). Best-effort, never raises.
- [ ] Map LiteLLM's `response.model` back to a `deployment` to fill
      `answered_deploy_id` (the Router hides which key answered — match on the
      concrete `litellm_model` + note it's best-effort).
- [ ] **Verify:** call `gpt-oss-120b`, get a completion, see one `request_logs`
      row with the answering key; revoke that key mid-test and confirm the next
      call logs a different key / `is_fallback=true`.

## Phase 8 — Ops (refresh cadence + admin)

- [ ] Admin endpoints ([admin.py](app/api/admin.py)): trigger discovery, probe,
      derive, and `reload_router` on demand (gateway-key guarded).
- [ ] Schedule probe+derive on an interval (background task / cron) so exhausted
      keys and new models are picked up without a restart. Router reload after each
      derive.
- [ ] **Verify:** exhaust a key's daily quota → next probe marks its `deployment`
      unavailable → derive drops it from members → traffic shifts, no 500s.

## Phase 9 — Cutover

- [ ] Flip `ROUTER_SOURCE` default to the DB path once Phases 6–7 pass.
- [ ] Seed one migration from the existing YAML pool into the new tables (adapt
      [seed_pool_from_yaml.py](app/scripts/seed_pool_from_yaml.py)).
- [ ] Keep YAML path available as a fallback for one release, then remove.

---

## Definition of done

1. Fresh DB + one provider key per two providers → discovery, probe, derive run
   clean; at least one `common_model` with ≥2 ordered members exists.
2. `GET /v1/models` lists the common model; `POST /v1/chat/completions` on it
   returns a completion.
3. Killing the primary key/provider mid-traffic degrades smoothly (Level 1 then
   Level 2), no client-visible error, `request_logs` shows the fallback.
4. Provider secrets are ciphertext at rest; gateway tokens are hashed; `/v1` auth
   works off DB-issued keys.
5. Existing YAML-pool behavior still works when `ROUTER_SOURCE` is set back.

---

## Risk register

| Risk | Mitigation |
|---|---|
| LiteLLM hides which key answered → weak `answered_deploy_id` | Best-effort match on `litellm_model`; treat per-key attribution as approximate, per-provider as exact |
| `UNIQUE(common_model_id, priority)` collisions on re-derive | Renumber priorities inside one transaction; stage to temp then swap |
| Encryption key loss bricks all provider keys | Dedicated `ENCRYPTION_KEY`, documented backup; store `key_masked` so users can re-enter |
| Quota-exhausted key keeps getting re-benched every 30s | Level-2 fallback covers it; optionally set longer `cooldown_until` on `status='rate_limited'` daily-quota errors |
| Probe cost/time as catalog grows (NVIDIA/OpenRouter are large) | Parallelize, cache catalogs by key signature (already done), probe on a schedule not per-request |

---

## Suggested build order (smallest shippable slices)

1. **Phase 1** — schema real. (Nothing serves yet, but the foundation exists.)
2. **Phase 2** — secrets/auth. (Keys can be stored safely.)
3. **Phases 3→4→5** — the data pipeline fills `deployments` and `common_model`.
4. **Phase 6** — the Router builds from it (flag OFF by default).
5. **Phase 7** — serve + log behind the flag; verify end-to-end.
6. **Phases 8→9** — schedule + cut over.
