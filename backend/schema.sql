-- ============================================================================
--  Multi-LLM Gateway — Single-User Data Model (PostgreSQL)
-- ----------------------------------------------------------------------------
--  Flow:
--    user ─┬─ gateway_api_keys        (bearer tokens that unlock the /v1 port)
--          └─ provider_keys ──► providers   (the user's upstream secrets, encrypted)
--
--    providers ─► provider_models ─► master_model ─► common_model
--    (raw catalog)   (per provider)   (working node)   (auto-derived, ≥2 providers)
--                         provider_keys ┘  │               │
--                         (per key)   deployments          │
--                                     (model × KEY,   common_model_members
--                                      1 per key)     (ordered fallback → master_model)
--
--    request_logs  = append-only usage/analytics ledger
--
--  Conventions:
--    * BIGINT identity PKs, TIMESTAMPTZ everywhere (UTC), snake_case.
--    * updated_at is maintained by the touch_updated_at() trigger.
--    * "global" reference tables carry no user_id (single-user; multi-user later
--      only needs user_id added to those tables).
-- ============================================================================

BEGIN;

-- ── Shared helpers ──────────────────────────────────────────────────────────
CREATE OR REPLACE FUNCTION touch_updated_at() RETURNS trigger AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- Well-defined status values for a probed model.
DO $$ BEGIN
    CREATE TYPE model_health AS ENUM (
        'available', 'rate_limited', 'unavailable', 'auth_error', 'timeout', 'error'
    );
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

-- What a model is used for (a provider can list non-chat models too).
DO $$ BEGIN
    CREATE TYPE model_mode AS ENUM ('chat', 'embedding', 'rerank', 'image', 'audio');
EXCEPTION WHEN duplicate_object THEN NULL; END $$;


-- ════════════════════════════════════════════════════════════════════════════
--  1. USERS  (real table; exactly one row today)
-- ════════════════════════════════════════════════════════════════════════════
CREATE TABLE users (
    id              BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    email           TEXT        NOT NULL UNIQUE,
    hashed_password TEXT        NOT NULL,
    full_name       TEXT,
    is_active       BOOLEAN     NOT NULL DEFAULT TRUE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TRIGGER trg_users_touch BEFORE UPDATE ON users
    FOR EACH ROW EXECUTE FUNCTION touch_updated_at();


-- ════════════════════════════════════════════════════════════════════════════
--  2. GATEWAY_API_KEYS  (many per user — the token external clients present)
--     Bearer tokens are stored HASHED (never plaintext); the raw value is shown
--     once at creation. key_prefix is a non-secret display fragment.
-- ════════════════════════════════════════════════════════════════════════════
CREATE TABLE gateway_api_keys (
    id           BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    user_id      BIGINT      NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name         TEXT        NOT NULL DEFAULT 'default',   -- human label
    key_hash     TEXT        NOT NULL UNIQUE,              -- e.g. sha256(token)
    key_prefix   TEXT        NOT NULL,                     -- e.g. 'sk-gw-…4f2a'
    is_active    BOOLEAN     NOT NULL DEFAULT TRUE,
    last_used_at TIMESTAMPTZ,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    revoked_at   TIMESTAMPTZ
);
CREATE INDEX idx_gwkeys_user   ON gateway_api_keys(user_id);
CREATE INDEX idx_gwkeys_active ON gateway_api_keys(is_active) WHERE is_active;


-- ════════════════════════════════════════════════════════════════════════════
--  3. PROVIDERS  (global reference; seeded once)
-- ════════════════════════════════════════════════════════════════════════════
CREATE TABLE providers (
    id             BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    slug           TEXT        NOT NULL UNIQUE,   -- 'groq', 'nvidia_nim', 'ollama'
    name           TEXT        NOT NULL,          -- 'Groq', 'NVIDIA NIM'
    litellm_prefix TEXT        NOT NULL,          -- prefix in the litellm model str
    base_url       TEXT,                          -- override / local (Ollama)
    requires_key   BOOLEAN     NOT NULL DEFAULT TRUE,  -- Ollama = FALSE
    enabled        BOOLEAN     NOT NULL DEFAULT TRUE,
    docs_url       TEXT,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TRIGGER trg_providers_touch BEFORE UPDATE ON providers
    FOR EACH ROW EXECUTE FUNCTION touch_updated_at();


-- ════════════════════════════════════════════════════════════════════════════
--  4. PROVIDER_KEYS  (user-owned secrets, MANY per provider, ENCRYPTED at rest)
--     key_ciphertext holds the AEAD/Fernet ciphertext; the plaintext is never
--     stored or returned. key_masked is a safe preview (••••1234).
--     env_slot maps the key to the os.environ var LiteLLM reads (GROQ_API_KEY_1).
-- ════════════════════════════════════════════════════════════════════════════
CREATE TABLE provider_keys (
    id             BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    user_id        BIGINT      NOT NULL REFERENCES users(id)     ON DELETE CASCADE,
    provider_id    BIGINT      NOT NULL REFERENCES providers(id) ON DELETE CASCADE,
    label          TEXT        NOT NULL,               -- 'Groq account #1'
    env_slot       TEXT        NOT NULL UNIQUE,         -- 'GROQ_API_KEY_1'
    key_ciphertext BYTEA       NOT NULL,                -- encrypted secret
    key_masked     TEXT        NOT NULL,                -- '••••4f2a'
    is_active      BOOLEAN     NOT NULL DEFAULT TRUE,
    last_used_at   TIMESTAMPTZ,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_pkeys_user     ON provider_keys(user_id);
CREATE INDEX idx_pkeys_provider ON provider_keys(provider_id);
CREATE TRIGGER trg_pkeys_touch BEFORE UPDATE ON provider_keys
    FOR EACH ROW EXECUTE FUNCTION touch_updated_at();


-- ════════════════════════════════════════════════════════════════════════════
--  5. PROVIDER_MODELS  (global RAW catalog — one unified table, provider FK)
--     Everything a provider lists / auto-discovery finds. Not yet health-checked.
--     normalized_name is the family key used to group models into common_models
--     (e.g. 'groq/openai/gpt-oss-120b' and 'nvidia_nim/openai/gpt-oss-120b'
--      both normalize to 'gpt-oss-120b').
-- ════════════════════════════════════════════════════════════════════════════
CREATE TABLE provider_models (
    id                BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    provider_id       BIGINT      NOT NULL REFERENCES providers(id) ON DELETE CASCADE,
    upstream_model_id TEXT        NOT NULL,          -- id as the provider names it
    litellm_model     TEXT        NOT NULL,          -- full 'groq/openai/gpt-oss-120b'
    display_name      TEXT,
    normalized_name   TEXT        NOT NULL,          -- family key for grouping
    mode              model_mode  NOT NULL DEFAULT 'chat',
    context_window    INTEGER,
    max_output_tokens INTEGER,
    is_free           BOOLEAN     NOT NULL DEFAULT TRUE,
    supports_stream   BOOLEAN     NOT NULL DEFAULT TRUE,
    enabled           BOOLEAN     NOT NULL DEFAULT TRUE,
    discovered_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (provider_id, upstream_model_id)
);
CREATE INDEX idx_pmodels_provider   ON provider_models(provider_id);
CREATE INDEX idx_pmodels_normalized ON provider_models(normalized_name);
CREATE TRIGGER trg_pmodels_touch BEFORE UPDATE ON provider_models
    FOR EACH ROW EXECUTE FUNCTION touch_updated_at();


-- ════════════════════════════════════════════════════════════════════════════
--  6. MASTER_MODEL  (the WORKING (provider, model) node — 1 row per provider_model).
--     Health here is a ROLLUP across its per-key deployments (#7): the model is
--     "working" if at least one of its keys works. Per-key detail lives in
--     deployments, because exhaustion is per API key.
-- ════════════════════════════════════════════════════════════════════════════
CREATE TABLE master_model (
    id                 BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    provider_model_id  BIGINT      NOT NULL UNIQUE
                                   REFERENCES provider_models(id) ON DELETE CASCADE,
    provider_id        BIGINT      NOT NULL REFERENCES providers(id) ON DELETE CASCADE,
    litellm_model      TEXT        NOT NULL,          -- denormalized concrete string
    normalized_name    TEXT        NOT NULL,          -- denormalized family key
    is_working         BOOLEAN     NOT NULL DEFAULT FALSE,  -- TRUE if ANY key works
    working_key_count  INTEGER     NOT NULL DEFAULT 0,      -- # of live deployments
    last_checked_at    TIMESTAMPTZ,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_master_working    ON master_model(is_working) WHERE is_working;
CREATE INDEX idx_master_normalized ON master_model(normalized_name);
CREATE INDEX idx_master_provider   ON master_model(provider_id);
CREATE TRIGGER trg_master_touch BEFORE UPDATE ON master_model
    FOR EACH ROW EXECUTE FUNCTION touch_updated_at();


-- ════════════════════════════════════════════════════════════════════════════
--  7. DEPLOYMENTS  (the ATOMIC callable unit: one master_model × one provider_key)
--     This is exactly what LiteLLM registers as a model_list entry. Multiple keys
--     for the same provider+model = multiple rows here, each with its OWN health
--     and cooldown — so one exhausted free-tier key is benched independently while
--     its siblings keep serving. Per-key health is a live snapshot on the row.
-- ════════════════════════════════════════════════════════════════════════════
CREATE TABLE deployments (
    id               BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    master_model_id  BIGINT      NOT NULL REFERENCES master_model(id)  ON DELETE CASCADE,
    provider_key_id  BIGINT      NOT NULL REFERENCES provider_keys(id) ON DELETE CASCADE,
    litellm_model    TEXT        NOT NULL,           -- concrete string (denormalized)
    is_working       BOOLEAN     NOT NULL DEFAULT TRUE,
    status           model_health NOT NULL DEFAULT 'available',
    http_code        INTEGER,
    latency_ms       INTEGER,
    error            TEXT,
    rpm              INTEGER,                          -- per-key requests/min hint
    cooldown_until   TIMESTAMPTZ,                      -- optional persisted cooldown
    last_checked_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_used_at     TIMESTAMPTZ,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (master_model_id, provider_key_id)          -- one deployment per (model,key)
);
CREATE INDEX idx_depl_master   ON deployments(master_model_id);
CREATE INDEX idx_depl_key       ON deployments(provider_key_id);
CREATE INDEX idx_depl_working   ON deployments(is_working) WHERE is_working;
CREATE TRIGGER trg_depl_touch BEFORE UPDATE ON deployments
    FOR EACH ROW EXECUTE FUNCTION touch_updated_at();


-- ════════════════════════════════════════════════════════════════════════════
--  8. COMMON_MODEL  (auto-derived: a normalized_name served by ≥2 providers).
--     This is the "virtual" model clients request; its members form the fallback
--     chain. provider_count captures how "common" it is.
-- ════════════════════════════════════════════════════════════════════════════
CREATE TABLE common_model (
    id             BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    name           TEXT        NOT NULL UNIQUE,       -- 'gpt-oss-120b', 'llama-3.3-70b'
    display_name   TEXT,
    description    TEXT,
    provider_count INTEGER     NOT NULL DEFAULT 0,    -- distinct providers serving it
    is_active      BOOLEAN     NOT NULL DEFAULT TRUE,
    auto_generated BOOLEAN     NOT NULL DEFAULT TRUE,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    refreshed_at   TIMESTAMPTZ NOT NULL DEFAULT now() -- last auto-derive pass
);
CREATE TRIGGER trg_common_touch BEFORE UPDATE ON common_model
    FOR EACH ROW EXECUTE FUNCTION touch_updated_at();


-- ════════════════════════════════════════════════════════════════════════════
--  9. COMMON_MODEL_MEMBERS  (ordered fallback chain: common_model → master_model)
--     priority 0 = primary; ascending = fallback order. Each member is a
--     (provider, model) node; its per-key deployments (#7) load-balance beneath it.
--     A member is dropped if its master_model goes away (cascade).
-- ════════════════════════════════════════════════════════════════════════════
CREATE TABLE common_model_members (
    id              BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    common_model_id BIGINT  NOT NULL REFERENCES common_model(id) ON DELETE CASCADE,
    master_model_id BIGINT  NOT NULL REFERENCES master_model(id) ON DELETE CASCADE,
    priority        INTEGER NOT NULL DEFAULT 0,        -- 0 = primary, then 1,2,…
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (common_model_id, master_model_id),         -- no dup members
    UNIQUE (common_model_id, priority)                 -- deterministic ordering
);
CREATE INDEX idx_members_common ON common_model_members(common_model_id, priority);
CREATE INDEX idx_members_master ON common_model_members(master_model_id);


-- ════════════════════════════════════════════════════════════════════════════
-- 10. REQUEST_LOGS  (append-only usage / analytics ledger)
--     Records what was asked and which exact deployment (model + KEY) answered,
--     so you can see per-key usage and spot exhausted keys. FKs use
--     ON DELETE SET NULL so history survives when a key/model is removed.
-- ════════════════════════════════════════════════════════════════════════════
CREATE TABLE request_logs (
    id                 BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    gateway_api_key_id BIGINT   REFERENCES gateway_api_keys(id) ON DELETE SET NULL,
    requested_model    TEXT     NOT NULL,             -- what the client asked for
    common_model_id    BIGINT   REFERENCES common_model(id)  ON DELETE SET NULL,
    answered_model_id  BIGINT   REFERENCES master_model(id)  ON DELETE SET NULL,
    answered_deploy_id BIGINT   REFERENCES deployments(id)   ON DELETE SET NULL,
    provider_key_id    BIGINT   REFERENCES provider_keys(id) ON DELETE SET NULL,
    provider_id        BIGINT   REFERENCES providers(id)     ON DELETE SET NULL,
    prompt_tokens      INTEGER  NOT NULL DEFAULT 0,
    completion_tokens  INTEGER  NOT NULL DEFAULT 0,
    total_tokens       INTEGER  NOT NULL DEFAULT 0,
    cost               NUMERIC(12,6) NOT NULL DEFAULT 0,
    latency_ms         INTEGER,
    status_code        INTEGER  NOT NULL DEFAULT 200,
    is_fallback        BOOLEAN  NOT NULL DEFAULT FALSE, -- did it leave the primary?
    error_message      TEXT,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_logs_created  ON request_logs(created_at);
CREATE INDEX idx_logs_gwkey    ON request_logs(gateway_api_key_id);
CREATE INDEX idx_logs_provider ON request_logs(provider_id);
CREATE INDEX idx_logs_pkey     ON request_logs(provider_key_id);
CREATE INDEX idx_logs_common   ON request_logs(common_model_id);


-- ════════════════════════════════════════════════════════════════════════════
-- 11. ROUTER_CONFIG  (single-row settings the litellm.Router builder reads)
--     Kept from the legacy pool design. The model list itself comes from the
--     common-model spine above; this only holds routing behaviour knobs.
-- ════════════════════════════════════════════════════════════════════════════
CREATE TABLE router_config (
    id               INTEGER     PRIMARY KEY DEFAULT 1,
    routing_strategy TEXT        NOT NULL DEFAULT 'usage-based-routing-v2',
    num_retries      INTEGER     NOT NULL DEFAULT 4,
    cooldown_time    INTEGER     NOT NULL DEFAULT 30,  -- seconds benched after 429
    allowed_fails    INTEGER     NOT NULL DEFAULT 3,
    fallbacks        JSONB,                            -- [{primary: [fallback, …]}]
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT router_config_singleton CHECK (id = 1)
);

COMMIT;
