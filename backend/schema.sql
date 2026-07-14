-- ============================================================================
--  Multi-LLM Gateway — Data Model (PostgreSQL)
-- ----------------------------------------------------------------------------
--  SIX TABLES. Two layers.
--
--    GLOBAL CATALOG — seeded by an ADMIN. No user_id. Shared by everyone.
--      providers ──► provider_models
--
--    PER-USER — what THIS user brings, and what THIS user can therefore call.
--      users ──► gateway_api_keys        (tokens clients present TO us)
--            └─► provider_keys ──┐       (the user's OWN upstream secrets)
--                                ├──► deployments
--            provider_models ────┘
--
--    request_logs   = append-only usage ledger
--    router_config  = per-user litellm.Router knobs
--
--  ── WHO CAN DO WHAT ────────────────────────────────────────────────────────
--    ADMIN adds PROVIDERS (name + base_url). Providers are global; a user cannot
--    create one. A provider is a DESTINATION, and carries no API key — a key is
--    a USER'S credential for that destination, and lives in provider_keys.
--
--    USER adds PROVIDER KEYS, picking from the providers the admin seeded.
--
--  ── HOW A USER GETS MODELS (the important one) ─────────────────────────────
--    There is NO "my models" table, and there must not be one. Adding a key FANS
--    OUT into deployments: one row per (that provider's models × that new key).
--
--      Alice adds a Groq key ─► Groq lists 30 models ─► 30 deployment rows,
--                               each probed for health.
--
--    So "the models Alice can call" is just her deployments. She cannot see a
--    model she holds no key for, because the row can only EXIST through her key
--    (FK-enforced) — not because a WHERE clause filters it out. Access is
--    structural, not conditional.
--
--  ── THE ATOMIC UNIT ────────────────────────────────────────────────────────
--    A DEPLOYMENT is (user × catalog model × that user's key) = exactly one
--    entry in litellm's model_list. Two keys for the same provider+model = two
--    deployments, each with its OWN health and cooldown — so one exhausted
--    free-tier key is benched while its siblings keep serving. That per-key
--    independence is the reason this gateway exists.
--
--  ── MODEL NAMING ───────────────────────────────────────────────────────────
--    Clients send the BARE FAMILY NAME ('gpt-oss-120b'). normalized_name is that
--    key: 'groq/openai/gpt-oss-120b' and 'nvidia_nim/openai/gpt-oss-120b' both
--    normalize to 'gpt-oss-120b', which is what makes them interchangeable.
--
--  ── NO ORDERING, ANYWHERE ──────────────────────────────────────────────────
--    No priority columns. The litellm Router picks among callable deployments at
--    request time and cools down whatever 429s.
--
--  Conventions:
--    * BIGINT identity PKs, TIMESTAMPTZ (UTC), snake_case.
--    * Derived values are GENERATED columns wherever possible, so no two columns
--      can ever disagree about the same fact.
--    * Secrets stored ENCRYPTED (BYTEA). Never plaintext.
-- ============================================================================

BEGIN;

-- ── Shared helpers ──────────────────────────────────────────────────────────
CREATE EXTENSION IF NOT EXISTS pgcrypto;   -- gen_random_uuid()

CREATE OR REPLACE FUNCTION touch_updated_at() RETURNS trigger AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- Status of a probed deployment.
DO $$ BEGIN
    CREATE TYPE model_health AS ENUM (
        'available', 'rate_limited', 'unavailable', 'auth_error', 'timeout', 'error'
    );
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

-- What a model is for (providers list non-chat models too).
DO $$ BEGIN
    CREATE TYPE model_mode AS ENUM ('chat', 'embedding', 'rerank', 'image', 'audio');
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

-- ── CAN WE CALL THIS DEPLOYMENT RIGHT NOW? ──────────────────────────────────
--   The single definition of "callable", used by every view and by the
--   reachability trigger. Defined once so the answer can't differ by caller.
--
--   A deployment is callable if it is healthy, OR if it was benched but its
--   cooldown has now expired — in which case we retry it. That second clause is
--   what makes a rate-limited free-tier key SELF-HEAL: a 429 benches it for 30s,
--   and the next request after that simply tries it again. Without it, a 429'd
--   key would stay dead until a probe happened to re-check it.
--
--   auth_error and friends set no cooldown, so they are NOT revived by this —
--   a revoked key stays dead until a probe (or the user) fixes it.
--
--   STABLE, not IMMUTABLE: it reads now(), so it cannot be used in an index.
CREATE OR REPLACE FUNCTION is_callable(
    status model_health,
    cooldown_until TIMESTAMPTZ
) RETURNS BOOLEAN AS $$
    SELECT status = 'available'
        OR (cooldown_until IS NOT NULL AND cooldown_until <= now());
$$ LANGUAGE sql STABLE;


-- ════════════════════════════════════════════════════════════════════════════
--  1. USERS — a thin identity row. THERE IS NO LOGIN.
--     The gateway key IS the credential: a caller proves who they are by
--     presenting a token from gateway_api_keys (#2), which resolves to a user_id.
--     Hence no password and no name — nothing here is a secret.
--
--     email is an optional human label for the dashboard, NOT a credential.
--     Someone must mint the first gateway key out-of-band (startup bootstrap),
--     because there is no signup flow to do it.
-- ════════════════════════════════════════════════════════════════════════════
CREATE TABLE users (
    id         BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    public_id  UUID        NOT NULL UNIQUE DEFAULT gen_random_uuid(),
    email      TEXT        UNIQUE,          -- optional label, NOT a credential
    is_admin   BOOLEAN     NOT NULL DEFAULT FALSE,  -- may seed providers (#3)
    is_active  BOOLEAN     NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TRIGGER trg_users_touch BEFORE UPDATE ON users
    FOR EACH ROW EXECUTE FUNCTION touch_updated_at();


-- ════════════════════════════════════════════════════════════════════════════
--  2. GATEWAY_API_KEYS — the user's "master key(s)": what clients present to US.
--     Stored HASHED; the raw token is shown exactly once, at creation.
--     key_prefix is a non-secret fragment so the UI can tell keys apart.
--     Many per user, so a key can be rotated with no downtime.
--
--     THIS is what identifies the caller. Resolving a token → user_id is what
--     scopes every request to that user's own provider keys.
--
--     revoked_at is the single source of truth for liveness; is_active is
--     GENERATED from it, so the two can never disagree.
-- ════════════════════════════════════════════════════════════════════════════
CREATE TABLE gateway_api_keys (
    id           BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    user_id      BIGINT      NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name         TEXT        NOT NULL DEFAULT 'default',
    key_hash     TEXT        NOT NULL UNIQUE,      -- sha256(token)
    key_prefix   TEXT        NOT NULL,             -- 'sk-gw-…4f2a'
    revoked_at   TIMESTAMPTZ,                      -- NULL = live
    is_active    BOOLEAN     GENERATED ALWAYS AS (revoked_at IS NULL) STORED,
    last_used_at TIMESTAMPTZ,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_gwkeys_user   ON gateway_api_keys(user_id);
CREATE INDEX idx_gwkeys_lookup ON gateway_api_keys(key_hash) WHERE is_active;


-- ════════════════════════════════════════════════════════════════════════════
--  ───────────────────────  GLOBAL CATALOG (admin-seeded)  ───────────────────
-- ════════════════════════════════════════════════════════════════════════════

-- ── 3. PROVIDERS — the destinations. Added by an ADMIN. ─────────────────────
--     `slug` doubles as the LiteLLM prefix ('groq' → 'groq/openai/gpt-oss-120b').
--     There was a separate litellm_prefix column; it was always a copy of slug,
--     so it is gone. If a provider ever needs a prefix that differs from its
--     slug, that column comes back — nothing else here assumes they're the same.
--
--     NO API KEY COLUMN, deliberately. A provider is a DESTINATION, shared by
--     all users. A key is a USER'S credential for it → provider_keys (#5).
--
--     EVERY PROVIDER REQUIRES A KEY: deployments.provider_key_id is NOT NULL, so
--     a model is only ever callable THROUGH a key. That rules out keyless/local
--     providers (Ollama) by construction — a deliberate trade for full FK
--     enforcement of the ownership invariants. Supporting Ollama later means
--     making provider_key_id nullable and adding a CHECK to compensate.
CREATE TABLE providers (
    id         BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    slug       TEXT        NOT NULL UNIQUE,   -- 'groq' — also the litellm prefix
    name       TEXT        NOT NULL,          -- 'Groq'
    base_url   TEXT,                          -- endpoint override, if any
    enabled    BOOLEAN     NOT NULL DEFAULT TRUE,
    docs_url   TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TRIGGER trg_providers_touch BEFORE UPDATE ON providers
    FOR EACH ROW EXECUTE FUNCTION touch_updated_at();


-- ── 4. PROVIDER_MODELS — THE model table. Everything every provider lists. ───
--     The global catalog. NOT "the models a user can call" — that is deployments.
--
--       normalized_name  the FAMILY key ('gpt-oss-120b'). What clients request.
--       provider_count   how many DISTINCT providers serve that family.
--       is_common        GENERATED: provider_count >= 2.
--       is_reachable     does ANY user hold a working key for this model?
--
--     ⚠ BOTH FLAGS ARE CROSS-USER. NEITHER IS A ROUTING SIGNAL.
--
--       is_common     "2+ providers serve this family, somewhere in the world."
--       is_reachable  "at least ONE user, somewhere, can currently call this."
--
--     is_reachable=TRUE does NOT mean YOU can call it — it may be someone else's
--     key that works. These are for the ADMIN/STATUS page only. To route, or to
--     show a user their own models, use the per-user views (#9), which are
--     computed from that user's own deployments.
CREATE TABLE provider_models (
    id                BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    provider_id       BIGINT      NOT NULL REFERENCES providers(id) ON DELETE CASCADE,
    upstream_model_id TEXT        NOT NULL,      -- id as the provider names it
    litellm_model     TEXT        NOT NULL,      -- 'groq/openai/gpt-oss-120b'
    display_name      TEXT,
    normalized_name   TEXT        NOT NULL,      -- family key: 'gpt-oss-120b'
    publisher         TEXT,                      -- normalized org: 'Meta', 'Mistral AI'
    mode              model_mode  NOT NULL DEFAULT 'chat',
    context_window    INTEGER,
    max_output_tokens INTEGER,
    is_free           BOOLEAN     NOT NULL DEFAULT TRUE,
    supports_stream   BOOLEAN     NOT NULL DEFAULT TRUE,
    enabled           BOOLEAN     NOT NULL DEFAULT TRUE,

    -- ── catalog-wide flags (cross-user; NOT routing signals — see above) ──
    provider_count    INTEGER     NOT NULL DEFAULT 1,      -- trigger-maintained
    is_common         BOOLEAN     GENERATED ALWAYS AS (provider_count >= 2) STORED,
    is_reachable      BOOLEAN     NOT NULL DEFAULT FALSE,  -- trigger-maintained

    discovered_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT now(),

    UNIQUE (provider_id, upstream_model_id),
    -- Composite-FK target: lets a deployment prove its key and its model belong
    -- to the SAME provider (a Groq key must never call an NVIDIA model).
    UNIQUE (id, provider_id)
);
CREATE INDEX idx_pmodels_provider   ON provider_models(provider_id);
CREATE INDEX idx_pmodels_normalized ON provider_models(normalized_name);
CREATE INDEX idx_pmodels_common     ON provider_models(normalized_name) WHERE is_common;
CREATE TRIGGER trg_pmodels_touch BEFORE UPDATE ON provider_models
    FOR EACH ROW EXECUTE FUNCTION touch_updated_at();


-- ── Trigger: keep is_common honest ──────────────────────────────────────────
--     Recomputes provider_count for any family whose count is now wrong, which
--     flips is_common (a GENERATED column) automatically. Once per STATEMENT, so
--     a bulk discovery INSERT costs one pass, not one per row.
--
--     Only ENABLED models count, so disabling a provider's copy correctly drops
--     the family back to "not common" if it leaves only one provider.
--     WHEN (pg_trigger_depth() = 0) stops the trigger's own UPDATE re-firing it.
CREATE OR REPLACE FUNCTION refresh_common_flags() RETURNS trigger AS $$
BEGIN
    WITH counts AS (
        SELECT normalized_name, count(DISTINCT provider_id) AS n
          FROM provider_models
         WHERE enabled
         GROUP BY normalized_name
    )
    UPDATE provider_models pm
       SET provider_count = COALESCE(c.n, 0)
      FROM (SELECT DISTINCT normalized_name FROM provider_models) fam
      LEFT JOIN counts c ON c.normalized_name = fam.normalized_name
     WHERE pm.normalized_name = fam.normalized_name
       AND pm.provider_count IS DISTINCT FROM COALESCE(c.n, 0);
    RETURN NULL;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_pmodels_common
    AFTER INSERT OR UPDATE OR DELETE ON provider_models
    FOR EACH STATEMENT
    WHEN (pg_trigger_depth() = 0)
    EXECUTE FUNCTION refresh_common_flags();


-- ════════════════════════════════════════════════════════════════════════════
--  ────────────────────────────────  PER-USER  ───────────────────────────────
-- ════════════════════════════════════════════════════════════════════════════

-- ── 5. PROVIDER_KEYS — the user's OWN upstream secrets. ─────────────────────
--     The user picks a provider (from the admin-seeded catalog) and adds their
--     key for it. Encrypted at rest; the plaintext is never stored and never
--     returned by the API. key_masked ('••••4f2a') is the UI-safe preview.
--
--     MANY keys per (user, provider) is the load-bearing property of this app:
--     free tiers exhaust PER KEY, so two Groq keys = two independent budgets.
--     That is why this is a table, not a JSONB column on users.
--
--     Adding a row here FANS OUT into deployments (#6) — one per model that
--     provider serves. That fan-out is how a user acquires callable models.
CREATE TABLE provider_keys (
    id             BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    user_id        BIGINT      NOT NULL REFERENCES users(id)     ON DELETE CASCADE,
    provider_id    BIGINT      NOT NULL REFERENCES providers(id) ON DELETE CASCADE,
    label          TEXT        NOT NULL,          -- 'Groq account #1'
    key_ciphertext BYTEA       NOT NULL,          -- encrypted secret
    key_masked     TEXT        NOT NULL,          -- '••••4f2a'
    is_active      BOOLEAN     NOT NULL DEFAULT TRUE,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT now(),

    UNIQUE (user_id, provider_id, label),
    UNIQUE (id, user_id),      -- composite-FK target: proves same OWNER
    UNIQUE (id, provider_id)   -- composite-FK target: proves same PROVIDER
);
CREATE INDEX idx_pkeys_user     ON provider_keys(user_id);
CREATE INDEX idx_pkeys_provider ON provider_keys(user_id, provider_id) WHERE is_active;
CREATE TRIGGER trg_pkeys_touch BEFORE UPDATE ON provider_keys
    FOR EACH ROW EXECUTE FUNCTION touch_updated_at();


-- ── 6. DEPLOYMENTS — the ATOMIC callable unit, AND the user's model list. ───
--     (user × catalog model × that user's key) = one litellm model_list entry.
--     The probe result lives HERE, because a "tested connection" is only ever
--     true of a SPECIFIC KEY — never of a user or a model in the abstract.
--
--     TWO INVARIANTS, ENFORCED BY THE DATABASE, not by app code:
--
--       (a) SAME OWNER    — FK (provider_key_id, user_id): a deployment can only
--           use a key belonging to the same user. Alice physically cannot spend
--           Bob's Groq quota, even given a bug in the app.
--
--       (b) SAME PROVIDER — FKs on (…, provider_id): the key and the model must
--           belong to the same provider. A Groq key cannot be bound to an NVIDIA
--           model. Postgres rejects the row outright.
--
--     status is the SOURCE OF TRUTH for health; is_working is GENERATED from it,
--     so a probe cannot write is_working=true alongside status='auth_error'.
--     But note: is_working ≠ callable. Use is_callable(status, cooldown_until) —
--     it also revives a benched key whose cooldown has expired.
--
--     litellm_model is NOT stored here: it would be a copy of
--     provider_models.litellm_model and could drift. Join for it.
CREATE TABLE deployments (
    id                BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    user_id           BIGINT       NOT NULL REFERENCES users(id)     ON DELETE CASCADE,
    provider_id       BIGINT       NOT NULL REFERENCES providers(id) ON DELETE CASCADE,
    provider_model_id BIGINT       NOT NULL,
    provider_key_id   BIGINT       NOT NULL,

    -- ── per-key health: the probe writes here ──
    status            model_health NOT NULL DEFAULT 'available',
    is_working        BOOLEAN      GENERATED ALWAYS AS (status = 'available') STORED,
    http_code         INTEGER,
    latency_ms        INTEGER,
    error             TEXT,
    rpm               INTEGER,                 -- per-key requests/min hint
    cooldown_until    TIMESTAMPTZ,             -- benched until this time after a 429
    last_checked_at   TIMESTAMPTZ  NOT NULL DEFAULT now(),
    last_used_at      TIMESTAMPTZ,
    created_at        TIMESTAMPTZ  NOT NULL DEFAULT now(),
    updated_at        TIMESTAMPTZ  NOT NULL DEFAULT now(),

    UNIQUE (provider_model_id, provider_key_id),   -- one deployment per (model, key)

    -- (a) same owner
    FOREIGN KEY (provider_key_id, user_id)
        REFERENCES provider_keys(id, user_id)       ON DELETE CASCADE,
    -- (b) same provider, on both sides
    FOREIGN KEY (provider_key_id, provider_id)
        REFERENCES provider_keys(id, provider_id)   ON DELETE CASCADE,
    FOREIGN KEY (provider_model_id, provider_id)
        REFERENCES provider_models(id, provider_id) ON DELETE CASCADE
);
CREATE INDEX idx_depl_user  ON deployments(user_id);
CREATE INDEX idx_depl_model ON deployments(provider_model_id);
CREATE INDEX idx_depl_key   ON deployments(provider_key_id);
-- The hot path: "everything this user could call." is_callable() reads now() and
-- so cannot be indexed; this narrows to the candidate rows and the view filters.
CREATE INDEX idx_depl_live  ON deployments(user_id, status, cooldown_until);
CREATE TRIGGER trg_depl_touch BEFORE UPDATE ON deployments
    FOR EACH ROW EXECUTE FUNCTION touch_updated_at();


-- ── Trigger: keep provider_models.is_reachable honest ───────────────────────
--     A catalog model is "reachable" if ANY user has a callable deployment of it.
--
--     `UPDATE OF status, cooldown_until` is load-bearing: it keeps this OFF the
--     hot path. Every request touches last_used_at, and we do NOT want a
--     catalog-wide recompute per request — only when health actually changes.
CREATE OR REPLACE FUNCTION refresh_reachability() RETURNS trigger AS $$
BEGIN
    UPDATE provider_models pm
       SET is_reachable = live.ok
      FROM (
            SELECT pm2.id,
                   EXISTS (
                       SELECT 1
                         FROM deployments d
                         JOIN provider_keys pk ON pk.id = d.provider_key_id
                        WHERE d.provider_model_id = pm2.id
                          AND pk.is_active
                          AND is_callable(d.status, d.cooldown_until)
                   ) AS ok
              FROM provider_models pm2
           ) live
     WHERE pm.id = live.id
       AND pm.is_reachable IS DISTINCT FROM live.ok;
    RETURN NULL;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_depl_reach
    AFTER INSERT OR DELETE OR UPDATE OF status, cooldown_until ON deployments
    FOR EACH STATEMENT
    WHEN (pg_trigger_depth() = 0)
    EXECUTE FUNCTION refresh_reachability();


-- ════════════════════════════════════════════════════════════════════════════
--  7. REQUEST_LOGS — append-only usage / analytics ledger.
--     Records what was asked and WHICH KEY answered, so you can see per-key burn
--     and spot an exhausted key. Reference FKs are ON DELETE SET NULL so history
--     survives a key or model being removed; only user_id cascades.
--
--     total_tokens is GENERATED, so it can never disagree with its parts.
--
--     NO is_fallback COLUMN: it meant "did we leave the primary?", but there is
--     no primary any more (no ordering, by design), so the question is
--     unanswerable. Which provider answered is already recorded.
-- ════════════════════════════════════════════════════════════════════════════
CREATE TABLE request_logs (
    id                 BIGINT   GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    user_id            BIGINT   NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    gateway_api_key_id BIGINT   REFERENCES gateway_api_keys(id) ON DELETE SET NULL,

    requested_model    TEXT     NOT NULL,   -- bare family name the client sent
    answered_deploy_id BIGINT   REFERENCES deployments(id)     ON DELETE SET NULL,
    provider_model_id  BIGINT   REFERENCES provider_models(id) ON DELETE SET NULL,
    provider_key_id    BIGINT   REFERENCES provider_keys(id)   ON DELETE SET NULL,
    provider_id        BIGINT   REFERENCES providers(id)       ON DELETE SET NULL,

    prompt_tokens      INTEGER  NOT NULL DEFAULT 0,
    completion_tokens  INTEGER  NOT NULL DEFAULT 0,
    total_tokens       INTEGER  GENERATED ALWAYS AS
                                (prompt_tokens + completion_tokens) STORED,
    cost               NUMERIC(12,6) NOT NULL DEFAULT 0,
    latency_ms         INTEGER,
    status_code        INTEGER  NOT NULL DEFAULT 200,
    error_message      TEXT,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_logs_user  ON request_logs(user_id, created_at DESC);
CREATE INDEX idx_logs_gwkey ON request_logs(gateway_api_key_id);
CREATE INDEX idx_logs_pkey  ON request_logs(provider_key_id, created_at DESC);


-- ════════════════════════════════════════════════════════════════════════════
--  8. ROUTER_CONFIG — litellm.Router behaviour knobs, one row per user.
--     The model list is NOT here; it comes from deployments. This is only how
--     the router BEHAVES.
-- ════════════════════════════════════════════════════════════════════════════
CREATE TABLE router_config (
    user_id          BIGINT      PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    routing_strategy TEXT        NOT NULL DEFAULT 'usage-based-routing-v2',
    num_retries      INTEGER     NOT NULL DEFAULT 4,
    cooldown_time    INTEGER     NOT NULL DEFAULT 30,   -- seconds benched after a 429
    allowed_fails    INTEGER     NOT NULL DEFAULT 3,
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TRIGGER trg_router_touch BEFORE UPDATE ON router_config
    FOR EACH ROW EXECUTE FUNCTION touch_updated_at();


-- ════════════════════════════════════════════════════════════════════════════
--  9. VIEWS — the per-user reads. THESE are what the app and UI use.
-- ════════════════════════════════════════════════════════════════════════════

-- "MY MODELS" — the dashboard read model, and the answer to requirement 3.
-- One row per model family per user, built ONLY from that user's deployments,
-- so a user can never see a model they hold no key for.
--
-- ── THREE FLAGS. THEY ARE NOT THE SAME QUESTION. ───────────────────────────
--
--   is_common            CATALOG fact: 2+ providers serve this family, ANYWHERE
--                        in the world. Says nothing about what YOU can call.
--                        It is a badge. Never route on it.
--
--   has_backup_key       "If the deployment I'm using fails, is there another
--                        one to try?" TRUE when the user has 2+ LIVE deployments
--                        of this model — INCLUDING two keys for the SAME
--                        provider. Two Groq keys IS real redundancy: when key #1
--                        is rate-limited, key #2 serves. This is the flag that
--                        answers what a user actually wants to know.
--
--   has_backup_provider  The STRONGER guarantee: 2+ distinct PROVIDERS. Survives
--                        a whole provider going down, not just one key being
--                        exhausted. Two Groq keys do NOT satisfy this — if Groq
--                        itself is down, both are dead.
--
-- Nothing here is called just "fallback", because that word hides which of these
-- two very different guarantees is meant.
--
-- Note both backup flags count only CALLABLE deployments — a backup that is
-- itself dead is not a backup.
-- A deployment only counts as live if ITS PROVIDER IS ENABLED: disabling a
-- provider (admin toggle) must immediately zero its contribution to every
-- user's live/backup counts, without deleting anything.
CREATE VIEW v_my_models AS
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
    -- mode is part of the grouping: chat and embedding models are different
    -- surfaces (/chat/completions vs /embeddings) and the UI shows them apart.
    -- Appended LAST on purpose — CREATE OR REPLACE VIEW may only add columns
    -- at the end, which is what lets a live DB upgrade without a rebuild.
    pm.mode                                     AS mode,
    min(pm.publisher)                           AS publisher   -- appended last
FROM deployments d
JOIN provider_models pm ON pm.id = d.provider_model_id
JOIN providers       p  ON p.id  = d.provider_id
GROUP BY d.user_id, pm.normalized_name, pm.mode;


-- THE ROUTER'S HOT READ — every deployment callable RIGHT NOW, keyed by the
-- bare family name the client sends. Feed straight into litellm's model_list.
-- Dead keys, disabled models and still-cooling deployments are filtered out;
-- benched deployments whose cooldown has EXPIRED reappear here automatically.
-- p.enabled is enforced HERE, in the view, so no caller can forget it: an admin
-- disabling a provider instantly removes its deployments from every user's
-- routing set, with nothing deleted and nothing for app code to remember.
CREATE VIEW v_live_deployments AS
SELECT
    d.user_id,
    pm.normalized_name AS model,          -- what the client asks for
    d.id               AS deployment_id,
    pm.litellm_model,                     -- what we actually call
    d.provider_id,
    d.provider_key_id,
    d.rpm,
    pm.mode                               -- appended last (view-replace rule)
FROM deployments d
JOIN provider_models pm ON pm.id = d.provider_model_id
JOIN provider_keys   pk ON pk.id = d.provider_key_id
JOIN providers       p  ON p.id  = d.provider_id
WHERE p.enabled
  AND pk.is_active
  AND pm.enabled
  AND is_callable(d.status, d.cooldown_until);

COMMIT;
