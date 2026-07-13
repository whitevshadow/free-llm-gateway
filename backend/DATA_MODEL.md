# Data Model — Single-User Multi-LLM Gateway (PostgreSQL)

Runnable DDL: [schema.sql](schema.sql). This doc explains the shape and the flow.

One person owns the gateway. They mint **gateway API keys** to unlock the LiteLLM
`/v1` port, and register **provider keys** — *multiple keys per provider* — so the
gateway can call upstream models on their behalf. The gateway discovers each
provider's models, keeps the **working** ones, materializes one **deployment per
API key**, and auto-groups models offered by multiple providers into **common
models** with a strong ordered fallback chain the client can call seamlessly.

---

## Entity-relationship overview

```
                          users
                            │ (1)
              ┌─────────────┼──────────────┐
              │(N)                         │(N)
      gateway_api_keys                provider_keys ──(N:1)──► providers
       (unlock /v1 port)            (user's secrets,            │ (1)
              │                      MANY per provider,         │
              │ used by              encrypted at rest)         │(N)
              │                            │            provider_models
              │                            │ (1)       (raw catalog per provider)
              │                            │                    │ (1:1 when working)
              │                            │                    ▼
              │                            │              master_model  (working node,
              │                            │(N)            health = rollup)
              │                            └──► deployments ◄──┘ (N)
              │                                 (model × KEY — the atomic unit,
              │                                  per-key health + cooldown)
              │                                        │ answered by
        request_logs ◄──────────────────────────────┘
        (usage ledger,                                 master_model
         per-key attribution)                             │ (N)
                                                          │ members
                                                          ▼
                                               common_model_members (ordered, priority)
                                                          │ (N:1)
                                                          ▼
                                                    common_model
                                            (auto-derived, ≥2 providers — the
                                             "virtual" model the client requests)
```

**Ownership rule:** `gateway_api_keys` and `provider_keys` belong to the user
(their secrets). `providers`, `provider_models`, `master_model`, `deployments`,
`common_model`, and `common_model_members` are **global reference data** — no
`user_id`. Multi-user later = add `user_id` to those tables only.

---

## The eleven tables

This is the **single consolidated model**. The legacy tables `chat_sessions`,
`chat_messages`, `token_usage_logs`, `model_deployments`, `model_availability`,
and the old plaintext `provider_keys` were **dropped** — the gateway is now a pure
`/v1` service (Open WebUI is the external frontend and keeps its own chat history),
`request_logs` is the single usage ledger, and the spine below replaces the old
pool/availability tables. `router_config` is kept from the legacy design.

| # | Table | Grain (one row =) | Owner |
|---|---|---|---|
| 1 | `users` | a person (one row today) | — |
| 2 | `gateway_api_keys` | a bearer token that unlocks `/v1` | user |
| 3 | `providers` | an upstream provider (Groq, NVIDIA…) | global |
| 4 | `provider_keys` | one upstream secret (**many per provider**, encrypted) | user |
| 5 | `provider_models` | a model a provider offers (raw catalog) | global |
| 6 | `master_model` | a **working (provider, model)** node | global |
| 7 | `deployments` | **one (model × API key)** — the callable unit | global |
| 8 | `common_model` | a canonical model served by **≥2 providers** | global |
| 9 | `common_model_members` | one rung of a common model's fallback chain | global |
| 10 | `request_logs` | one API call (per-key usage/cost/latency) | user (via key) |
| 11 | `router_config` | single-row litellm.Router behaviour knobs | global |

### 1. `users`
Real table, one row now. `email` (unique), `hashed_password`, `is_active`. A table
(not a singleton) so multi-user is a data change, not a schema change.

### 2. `gateway_api_keys`
The keys external clients (Claude Code, OpenAI SDK) present to reach the LiteLLM
port. **Many per user** for rotate/revoke without downtime. Stored **hashed**
(`key_hash`) — the raw token is shown once at creation; `key_prefix` is a safe
display fragment. Hashed (not encrypted) because the gateway only ever *verifies*
an incoming token, never reads it back.

### 3. `providers`
Global, seeded once. `slug` (`groq`), `litellm_prefix`, `base_url` (local/override,
e.g. Ollama), `requires_key` (Ollama = false), `enabled`, `docs_url`.

### 4. `provider_keys`
The user's upstream secrets — **many per provider** (this is what stacks free
tiers). **Encrypted at rest**: `key_ciphertext` (BYTEA, e.g. Fernet) + a non-secret
`key_masked` preview; plaintext is never stored or returned. `env_slot`
(`GROQ_API_KEY_1`) maps each key to the `os.environ` var LiteLLM reads. Encrypted
(not hashed) because the secret must be decrypted to call upstream.

### 5. `provider_models`
One **unified** catalog table (`provider_id` FK — not one table per provider).
Everything a provider lists / auto-discovery finds, before any health check.
`litellm_model` is the full concrete string; `normalized_name` is the family key
(`gpt-oss-120b`) used to group across providers. Unique on `(provider_id,
upstream_model_id)`.

### 6. `master_model`
The **working (provider, model) node** — one row per `provider_model` that has at
least one working key. Health here is a **rollup**: `is_working` (TRUE if any key
works) and `working_key_count`. The per-key detail lives one level down, in
`deployments`, because exhaustion happens per key.

### 7. `deployments`  ← the per-key layer you asked for
The **atomic callable unit: one `master_model` × one `provider_key`.** This is
exactly what LiteLLM registers as a `model_list` entry. Three Groq keys for the
same model = three `deployments` rows, each with its **own** `status`,
`latency_ms`, `error`, and `cooldown_until`. So when one free-tier key exhausts
its quota (429), *that row* is benched while its siblings keep serving — the
Router never takes the whole model down for one dead key. Unique on
`(master_model_id, provider_key_id)`.

### 8. `common_model`
**Auto-derived**: after each probe, group working `master_model` rows by
`normalized_name`; any name served by **≥2 providers** becomes a common model —
the "virtual" model the client requests (`gpt-oss-120b`). `provider_count` records
how common it is; `refreshed_at` tracks the last derive pass; `auto_generated`
marks it machine-created (room for manual pins later).

### 9. `common_model_members`
The **ordered fallback chain**: `(common_model_id, master_model_id, priority)`.
`priority = 0` is primary, ascending is fallback order. Each member is a
(provider, model) node; its per-key deployments load-balance *beneath* it.
`UNIQUE(common_model_id, master_model_id)` blocks dupes;
`UNIQUE(common_model_id, priority)` guarantees deterministic order. Members
cascade-delete with their master model.

### 10. `request_logs`
Append-only ledger, one row per `/v1` call, with **per-key attribution**: which
`gateway_api_key`, the `requested_model`, resolved `common_model`, the
`master_model` and exact **`deployment` + `provider_key`** that answered, the
`provider`, tokens, `cost`, `latency_ms`, `status_code`, and `is_fallback`. Model/
key FKs use `ON DELETE SET NULL` so history survives cleanups. Indexed on
`created_at` for time-series dashboards.

---

## Seamless access: calling a common model and getting the whole fallback chain

This is the two-level structure that makes one request "just work" as backends
exhaust:

```
client → POST /v1/chat/completions { "model": "gpt-oss-120b" }   (a common_model)
        │
        ▼
   LiteLLM Router registers, under model_name "gpt-oss-120b":
     ── all deployments (per KEY) of the priority-0 member  ──┐
        e.g. groq+key1, groq+key2, groq+key3                 │  load-balanced,
                                                             │  per-key cooldown
   fallbacks["gpt-oss-120b"] = [ member#1, member#2, … ]  ◄──┘
        │
        ▼
  Level 1 — within the primary member: rotate across its keys; a 429'd key is
            benched (cooldown_until), traffic shifts to the other keys.
  Level 2 — when EVERY key of the primary member is down: fall back to the next
            member (next provider) in common_model_members priority order.
```

- **Per-key rotation & cooldown (Level 1)** = `deployments` rows sharing one
  `model_name`. LiteLLM cools down individual keys in memory; `cooldown_until`
  optionally persists it.
- **Cross-provider fallback (Level 2)** = `common_model_members.priority` emitted
  as LiteLLM's `fallbacks` list. Priority 0 first, then 1, then 2…

So the client only ever names `gpt-oss-120b`; the two levels drain keys, then
providers, in order — no client-side retry logic.

### Rate-limit vs quota exhaustion
- **Rate-limited (429, temporary):** `cooldown_time` (default 30s) benches the
  key; it auto-recovers. Handled entirely at Level 1.
- **Quota exhausted (free tier used up for the day):** a 30s cooldown won't
  revive it, so once all keys of a member are exhausted, **Level 2 fallback** to
  the next provider is what keeps you serving. This is why the ordered
  `common_model_members` chain matters.

---

## Lifecycle

1. **Seed** `providers` (Groq, Gemini, NVIDIA NIM, OpenRouter, Ollama, …).
2. **Add keys** — user saves `provider_keys` (encrypted, many per provider,
   mapped to `env_slot`s).
3. **Discover** — for each provider with an active key, pull its catalog → upsert
   `provider_models`.
4. **Probe (per key)** — ping each (provider_model × active key). Each result
   upserts a `deployments` row with its own health; `master_model` is
   upserted/refreshed as the rollup (`is_working`, `working_key_count`).
5. **Derive** — group working `master_model` rows by `normalized_name`; any name
   with `provider_count ≥ 2` upserts a `common_model` and rewrites its
   `common_model_members`, ordered by latency (best = priority 0).
6. **Serve** — client presents a `gateway_api_key` and requests a `common_model`;
   the Router drains keys (Level 1) then providers (Level 2) as above. A single
   `master_model` can also be called directly (no fallback).
7. **Log** — every call appends a `request_logs` row recording the exact
   `deployment` + `provider_key` that answered and whether it fell back.

---

## Design notes / trade-offs

- **`deployments` = the per-key unit** (your latest requirement). Health and
  cooldown live here, not on `master_model`, because a free-tier key exhausts
  independently of its siblings. `master_model` keeps only a rollup so the derive
  step and `/v1/models` can ask "does this model work at all?" cheaply.
- **Two-level routing** cleanly matches LiteLLM's own model: deployments sharing a
  `model_name` load-balance; different `model_name`s cascade via `fallbacks`.
- **`master_model` vs `provider_models`** stay separate: the catalog churns; the
  working set is what routing trusts.
- **Fallback as rows, not JSON** (your choice): real FKs mean a deleted deployment
  can't leave a dangling fallback; reordering is an `UPDATE priority`.
- **Encryption vs hashing** — provider secrets are *encrypted* (read back to call
  upstream); gateway tokens are *hashed* (only verified).
- The whole `providers → provider_models → master_model → deployments →
  common_model` spine has **no `user_id`**; only secrets and usage are
  user-scoped today.
