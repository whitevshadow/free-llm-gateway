# Software Requirements Specification (SRS)
# Free LLM Gateway

Version: 0.3
Status: Baseline — reflects the implemented system plus committed design decisions
Supersedes: v0.2 (Draft)

---

# 1. Purpose

Free LLM Gateway is an OpenAI-compatible API gateway that aggregates multiple free
LLM providers (and multiple free keys per provider) behind a single endpoint, so it
behaves like one "unlimited" provider.

The gateway allows users to:

- Use one Base URL and one Gateway API Key
- Route requests automatically across providers and keys
- Configure their own provider API keys (encrypted at rest, never exposed)
- Discover models automatically from provider catalogs
- Fail over when a provider or key becomes unavailable or rate-limited
- Track usage, health, and analytics per deployment

Primary goal:

> Provide a highly available, provider-agnostic, OpenAI-compatible API that
> intelligently routes requests across multiple providers and keys while minimizing
> failures, downtime, and rate limits.

## 1.1 Change summary vs v0.2

v0.2 listed routing, discovery, health, failover, and key balancing as open design
questions. v0.3 records them as **decided and implemented**, with the following
positions:

| Area | v0.2 status | v0.3 decision |
|---|---|---|
| Routing engine | Open (custom scoring formula proposed) | Delegated to LiteLLM `Router`, `usage-based-routing-v2`; no custom score |
| Provider vs key routing | Two-stage (provider first, then key) | Single-stage: flat deployment list, LiteLLM balances across providers and keys in one decision |
| Model discovery | 🚧 | Implemented (`catalog.py`): trust `/models`, upsert, disable-not-delete |
| Health monitoring | 🚧 | Implemented (`prober.py`): per-deployment probes, mode-aware, typed health enum |
| Circuit breaker | Open (threshold questions) | LiteLLM `allowed_fails` + cooldown per deployment; failures persisted to Postgres |
| Redis | Open | Deferred until the gateway runs more than one process |
| Quota estimation | Open | Rejected — reactive 429 handling only |
| Scheduler | Open | Implemented (`scheduler.py`): daily discovery + periodic unhealthy re-probe |

---

# 2. Scope

## Delivered

- ✅ Authentication (register, login, gateway API keys)
- ✅ RBAC (Admin / Developer / Viewer)
- ✅ User provider keys (encrypted, per-user isolation)
- ✅ Provider management (admin-seeded, preset base URLs)
- ✅ Model discovery and catalog (`catalog.py`)
- ✅ Health probing per deployment (`prober.py`)
- ✅ Per-user routing, failover, and key load balancing (`llm_router.py`, LiteLLM Router)
- ✅ OpenAI-compatible (`/v1/chat/completions`, `/v1/embeddings`, `/v1/models`) and
  Anthropic-compatible (`/v1/messages`) endpoints

## Delivered in this revision

- ✅ Background scheduler (daily discovery + periodic re-probe of unhealthy
  deployments) — `services/scheduler.py`, §10
- ✅ Retry-After-aware, escalating 429 cooldowns (60 s → 2 m → 5 m per
  deployment, provider header wins) — §7.3
- ✅ User pinning of a preferred provider (soft pin, candidate-set filter) — §8.3

## Remaining

- 🚧 Usage analytics surfaced in dashboard (counters exist; feedback loop is out of scope) — §20

---

# 3. High-Level Architecture

```
                        Client
                           │
                           ▼
        OpenAI / Anthropic Compatible API  (/v1/*)
                           │
              Gateway Auth Middleware
                           │
                    Per-User Router          ◄── rebuilt from Postgres, TTL 30 s
                (LiteLLM Router instance)
                           │
      ┌────────────────────┼────────────────────┐
      ▼                    ▼                    ▼
 Model Catalog        Prober (health)      Failure Write-back
 (catalog.py)         (prober.py)          (record_success/failure)
      │                    │                    │
      └────────────────────┴────────────────────┘
                           ▼
                POSTGRES — durable truth
     (providers, keys, models, deployments, health, cooldowns)
                           │
        ┌────────┬─────────┼──────────┬─────────┐
        ▼        ▼         ▼          ▼         ▼
      Groq  OpenRouter  Gemini   Cerebras   NVIDIA NIM …
```

**Core invariant:** Postgres is durable truth. Each user's Router is a short-lived
in-memory *view* of that truth, rebuilt from the `v_live_deployments` view when
older than 30 seconds. In-flight failures are written back to the DB immediately,
so the two never drift for longer than the TTL. If they disagree, the DB is right.

**Unit of everything:** the **deployment** — one (provider, key, model) triple.
Health, cooldowns, probing, and routing all operate on deployments, never on
providers or models in the abstract. One exhausted Groq key is benched without
touching its sibling key.

---

# 4. Functional Requirements — Authentication

Users shall:

- Register and log in
- Manage their profile
- Generate Gateway API Keys, used in place of provider keys

`/v1` auth is open by default for local development; setting
`REQUIRE_GATEWAY_AUTH=true` with `GATEWAY_API_KEY` requires
`Authorization: Bearer <token>` or `x-api-key` on every request.

---

# 5. RBAC

| Role | Capabilities |
|---|---|
| Admin | Manage users, manage providers, run catalog sync, view logs, configure gateway |
| Developer | Add provider keys, use the gateway, view models, trigger re-probe of own keys |
| Viewer | Read-only |

---

# 6. Provider and Key Management

- Each user may connect multiple providers; each provider may hold multiple keys.
- Provider keys are **encrypted at rest** (`crypto.py`) and decrypted only at the
  moment they are passed into a request. Keys are never placed in `os.environ`
  and never returned by any API.
- Adding a key immediately materializes deployments for every enabled model of
  that provider, marked `unavailable`, then a background probe promotes the ones
  that actually work (§13).
- Per-user isolation is structural: each user's Router is built only from that
  user's deployments, with keys passed per-entry. There is no shared key
  namespace, so no cross-user leakage is possible.
- **One narrow exception:** admin-driven catalog discovery borrows any active key
  for the provider to make a single read-only `/models` call (§9). The
  user-facing discovery path uses the caller's own key.

## 6.1 Deployment state (replaces v0.2 "API Key Pool")

Each deployment row carries:

| Field | Meaning |
|---|---|
| `status` | `available` / `rate_limited` / `auth_error` / `timeout` / `unavailable` / `error` |
| `http_code` | Last observed HTTP status |
| `latency_ms` | Last probe latency |
| `cooldown_until` | Set only for `rate_limited`; expiry auto-revives the deployment |
| `rate_limit_strikes` | Consecutive 429s; drives the escalating cooldown ladder (§7.3); reset on success |
| `last_checked_at` / `last_used_at` | Probe vs real-traffic recency |
| `error` | Last error message (truncated to 500 chars) |
| `is_working` | **Generated column** derived from `status` — never written directly |

`rpm` limits, where known, are passed to LiteLLM per deployment so its
usage-based routing respects them.

---

# 7. Key Selection, Cooldowns, and Load Balancing

## 7.1 Decision: no custom scoring engine

v0.2 proposed a weighted score (40 % quota, 20 % latency, 20 % success rate, …).
**Rejected.** LiteLLM's `usage-based-routing-v2` already balances on load and
latency across deployments, is battle-tested, and requires no maintenance. A
hand-rolled score with fixed weights is a second scheduler with no evidence it
performs better. This decision is revisited only if a concrete routing failure is
measured that LiteLLM cannot express (§24).

Per-user router settings (stored in `router_config`, with defaults):

| Setting | Default |
|---|---|
| `routing_strategy` | `usage-based-routing-v2` |
| `num_retries` | 4 |
| `cooldown_time` | 30 s |
| `allowed_fails` | 3 |

## 7.2 Candidate filtering

The `v_live_deployments` view — not application code — decides what is callable:
it excludes revoked keys, disabled models, and deployments still cooling down,
and automatically re-includes deployments whose cooldown has expired. The Router
is built from exactly this set, so filtering and routing cannot disagree.

## 7.3 Cooldown policy

- A 429 is **not a failure** — the key works and is merely throttled. It earns a
  cooldown (`cooldown_until`), after which the view revives it automatically.
- An auth error (401/403) is the opposite: the key is dead and no cooldown may
  ever resurrect it. Any non-429 result **clears** `cooldown_until`.
- The provider's `Retry-After` header, when present, always wins (capped at
  15 minutes — never trust a header past that).
- Otherwise consecutive 429s walk a per-deployment ladder: strike 1 → 60 s,
  strike 2 → 2 m, strike 3+ → 5 m (`rate_limit_strikes` column). Free tiers
  often enforce daily quotas where a flat 60 s only burns retries.
- Any success — real traffic or probe — resets the strike count to 0; a
  timeout between two 429s does not (it says nothing about the quota).

## 7.4 Decision: no quota estimation

Remaining quota is unknowable for most free providers, and a wrong estimate is
worse than none. The gateway is deliberately **reactive**: route, observe 429s,
cool down, retry elsewhere. "Requests today / tokens today" counters are
dashboard data, not routing inputs (§20).

---

# 8. Routing

## 8.1 Decision: single-stage routing

v0.2 proposed two stages (score providers, then score keys within the winner).
**Rejected.** Every (provider, key, model) deployment is registered flat under
one public `model_name`; LiteLLM balances across providers *and* keys in a single
decision. A provider-level scheduler on top would be a second, drifting source of
routing truth.

## 8.2 Request flow

```
client asks for "gpt-oss-120b"
        │
        ▼
per-user Router (all callable deployments named gpt-oss-120b,
                 across every provider and every key)
        │
        ▼
LiteLLM picks one deployment → on failure retries another
        │
        ├─ success → record_success(deployment_id)   → DB
        └─ failure → record_failure(deployment_id)   → DB + router invalidated
```

Failure attribution works because each `model_list` entry carries its
`deployment_id` in `model_info`, so an in-flight 429 is written back to the exact
(model, key) row — visible to the dashboard and durable across restarts, not
trapped in LiteLLM's in-memory cooldown cache.

## 8.3 Pinning

Users may pin a preferred provider (`pinned_provider_id` in `router_config`,
set via `PUT /v1/me/router-config`; `0` clears the pin). It is a **soft pin**,
applied as a filter on the deployment list before the Router is built — never
a score override:

- Per model, if the pinned provider has a callable deployment, only its
  deployments are offered for that model.
- Models the pinned provider does not serve keep their full candidate set —
  a pin narrows candidates, it never empties them.
- Health wins over the pin: an unhealthy pinned deployment is already absent
  from the candidate view, so that model falls back to other providers.

## 8.4 Virtual models

Convenience aliases (`auto`, `deepseek`, `qwen`, `mistral`, `llama`,
`gemini-flash`, …) map to families of deployments. Claude Code's `claude-*`
model names route to `DEFAULT_VIRTUAL_MODEL` (`auto`). Unknown embedding model
names map to the default `embed` model.

---

# 9. Model Discovery

Implemented in `catalog.py`. Admin-driven: an admin seeds a provider, then runs
discovery, which asks the provider what it serves and upserts the result. Nobody
hand-maintains a model list.

## 9.1 Flow

```
provider /models endpoint  (preset may override the URL — e.g. GitHub Models)
        │
        ▼
fetch model ids            (tolerates OpenAI {"data":[…]} and bare-array shapes)
        │
        ▼
infer mode                 (chat / embedding / rerank, by name heuristics)
        │
        ▼
filter junk                (OCR, guard/safety models; rerankers excluded —
                            they can serve neither endpoint)
        │
        ▼
normalize name             (stable family name, publisher extraction)
        │
        ▼
upsert provider_models     (by provider_id + upstream_model_id)
        │
        ▼
dropped models → enabled=false   (disable, NEVER delete — deployments
                                  still reference them)
```

## 9.2 Decisions

- **Trust `/models`; do not verify with inference during discovery.** Validation
  per key is the prober's job (§13). Conflating them would fire hundreds of
  completions on every sync.
- **A failed discovery keeps the last known list.** `fetch_model_ids` returns
  `[]` on any failure and discovery bails out without touching existing rows.
  Discovery degrades; it never destroys.
- **Disable, never delete.** Deleting would cascade away users' deployment rows
  and lose history. `enabled=false` is the deprecation mechanism.
- **`is_common` / `provider_count` are maintained by a Postgres trigger.**
  Application code must never write them — a second writer would drift.

## 9.3 Key usage during discovery

Scheduled and admin discovery borrow **one** active key per provider (a single
read-only `/models` call — the sole exception to per-user key isolation, §6),
**rotating daily** across that provider's active keys: the rotation index is
derived from the calendar date, so the same key serves every discovery within
one day (a manual refresh never burns a second key) and the next day moves to
the next key. Stateless by design — it survives restarts with no stored
counter, and over time no key holder pays more than their share of catalog
calls. The user-facing discovery endpoint is unchanged: it passes the caller's
own key so users only ever spend their own quota.

---

# 10. Discovery & Probe Scheduler

Implemented in `services/scheduler.py` — two asyncio loops started from the
app lifespan. Discovery never happens during user requests. Triggers:

| Trigger | Status |
|---|---|
| On provider added | ✅ |
| Manual refresh (admin Sync; user re-probe) | ✅ |
| Daily scheduled discovery (`DISCOVERY_INTERVAL_HOURS`, default 24) | ✅ |
| Periodic re-probe of unhealthy deployments (`REPROBE_INTERVAL_MINUTES`, default 20) | ✅ |

Behavior:

- The discovery loop refreshes every enabled provider's catalog, then fans the
  catalog out to every active key (idempotent — only new models create
  deployments, which start `unavailable` and are promoted by the re-probe loop).
- Providers are refreshed individually (`discover_provider`) so one provider's
  outage cannot block the others; `discover_all` is just the loop.
- The re-probe loop retries **only** `error` / `timeout` / `unavailable`
  deployments. It deliberately skips: `available` (real traffic keeps them
  fresh via `record_success`/`record_failure` — probing them spends free-tier
  quota on nothing), `rate_limited` (the cooldown self-heals; probing early
  adds a request to a throttled key), and `auth_error` (dead keys are never
  resurrected by a timer).
- Users are probed sequentially, ≤ 4 probes in flight, to stay polite to
  providers several users share.
- One iteration failing is logged and skipped; the loops themselves survive.
- `SCHEDULER_ENABLED=false` disables both loops (used by the test suite).

---

# 11. Catalog Diff Semantics

Every discovery run compares the provider's current list against the DB:

| Case | Action |
|---|---|
| New model | Insert, `enabled=true` |
| Existing model | Update `litellm_model`, `normalized_name`, `publisher`, `mode`; re-enable |
| Dropped model | `enabled=false` (historical rows are never deleted) |

The v0.2 four-state lifecycle (ACTIVE / DEPRECATED / DISABLED / REMOVED)
collapsed to the boolean `enabled` plus per-deployment health — the extra states
duplicated information already derivable from those two.

---

# 12. Model Registry

The gateway always serves model queries from its own database. `/models` on a
provider is **never** called during a user request. `GET /v1/models` returns the
bare family names the calling user can invoke right now, derived from their
built Router.

**Stable IDs (decided):** the `normalized_name` is the stable public model ID.
Provider model IDs are ephemeral deployment details; if a provider renames a
model, the public name does not change.

---

# 13. Health Monitoring

Implemented in `prober.py`. Health is **per deployment** (model × key), because
that is the unit that fails.

## 13.1 Probe mechanics

- Chat models: one 1-token completion (`"ping"`).
- Embedding models: embed the word `"ping"` (NVIDIA retrieval models get
  `input_type: "query"` — required, or a healthy model looks broken).
- **The probe must match the mode** — probing an embedding model with a chat
  completion would mark it dead forever.
- Concurrency capped at 4 in-flight probes. Not an optimization: probes for one
  key all hit the same free-tier account; 30 simultaneous probes would
  rate-limit the very key being validated.
- 20 s timeout per probe. Keys passed in per call, never via environment.

## 13.2 Health classification (replaces v0.2 five-state proposal)

| Status | Meaning | Cooldown? |
|---|---|---|
| `available` | Works | — |
| `rate_limited` | Key valid, throttled (429) | Yes — auto-revives on expiry |
| `auth_error` | Key dead (401/403) | Never — no timer may resurrect it |
| `timeout` | No response in 20 s | No |
| `unavailable` | Listed but not served (404) | No |
| `error` | Anything else | No |

"Degraded" (v0.2 §14) is **not stored**. If the dashboard needs it, it is
derived at read time (e.g. success rate < 90 % over recent probes), never
persisted as another status that can drift.

## 13.3 Probe triggers and progress

- On key added: background probe of that key's deployments; the API returns
  immediately with deployments `unavailable`, promoted as results land.
- On demand: user re-probe (`probe_user`), key re-probe (`probe_key`).
- Progress is exposed at `GET /v1/me/probe-status` (in-memory, per user;
  concurrent jobs aggregate; jobs silent for 90 s are reported inactive).

## 13.4 Success/failure from real traffic

Every real completion updates the DB: `record_success` marks the deployment
used and clears any stale error state ("it worked — whatever we thought was
wrong isn't any more"); `record_failure` classifies the exception with the same
`_classify` used by the prober and invalidates the user's cached Router.

---

# 14. Circuit Breaking (decided)

There is no separate circuit-breaker service. The behavior is composed from:

1. LiteLLM's per-deployment `allowed_fails=3` + `cooldown_time=30` (in-flight,
   in-memory), and
2. durable per-deployment status + `cooldown_until` in Postgres (written by
   probe and by `record_failure`), enforced by `v_live_deployments`.

Recovery is automatic: cooldown expiry re-includes the deployment in the view;
the next Router rebuild (≤ 30 s) picks it up. Dead keys (`auth_error`) never
auto-recover; only a successful probe or real request revives a deployment.

---

# 15. Failover (decided)

Failover is LiteLLM's retry loop over the flat deployment list:

```
attempt deployment → fail → retry another deployment
(up to num_retries=4, across keys AND providers, same public model name)
→ all exhausted → error to client
```

Retryable by LiteLLM: 429, timeouts, 5xx, network errors. Auth errors fail fast
per deployment and are persisted so the deployment leaves the candidate set.

---

# 16. Model Aliases

Different providers name the same model differently
(`meta/llama-3.3-70b-instruct` vs `llama-3.3-70b-chat`). `normalize.py` maps
provider model IDs onto stable normalized family names; deployments sharing a
normalized name are interchangeable under one public model. Virtual models
(§8.4) layer friendly aliases on top.

---

# 17. Caching (decided)

| What | Where | Why |
|---|---|---|
| Per-user Router | In-process dict, 30 s TTL | Bounded staleness vs DB truth |
| Router invalidation | Explicit, on any change to what a user can call (key added/removed, probe finished, catalog refresh) | New keys usable immediately, not after TTL |
| Model registry / health | Postgres | Durable truth |

**Redis: deferred.** The gateway is single-process; in-memory caching with DB
truth is simpler and correct. The trigger condition is written down: **adopt
Redis (or another shared invalidation bus) when the gateway runs more than one
process** — because the Router cache and `invalidate()` are per-process, a key
added via worker A would not invalidate worker B's router until TTL. Not before.

---

# 18. Database

Core tables/views (see `backend/schema.sql`, `DATA_MODEL.md`):

- `users`, `roles`, `gateway_api_keys`
- `providers`, `provider_keys` (ciphertext only), `provider_models`
- `deployments` — the (provider, key, model) unit; health lives here
- `router_config` — per-user routing settings
- `v_live_deployments` — **the** definition of "callable right now"
- usage / discovery / health logs

Rules: `is_working` is generated from `status`; `is_common` / `provider_count`
are trigger-maintained. Application code never writes any of them.

---

# 19. API Surface

| Endpoint | Purpose |
|---|---|
| `POST /v1/messages` | Anthropic-compatible (Claude Code; tools + streaming) |
| `POST /v1/chat/completions` | OpenAI-compatible chat |
| `POST /v1/embeddings` | OpenAI-compatible embeddings |
| `GET /v1/models` | Live virtual models for the calling user |
| `GET /v1/me/probe-status` | Probe progress |
| `GET /health` | Status + router pool size + cooldowns |
| `/v1/me/*`, admin endpoints | Key/provider management, catalog sync |

---

# 20. Metrics & Analytics (🚧 dashboard surface)

Collected per deployment: latency, last success/failure, HTTP codes, cooldowns.
Usage logging exists (`usage_logger.py`).

**Decision:** usage counters (requests/tokens per key, per model, per provider)
are **observability data, not routing inputs**. Routing stays reactive (§7.4).
Remaining work is surfacing them: provider status board, health timeline,
latency graph, top models, error feed.

---

# 21. Security

- Provider keys encrypted at rest; decrypted per request; never in `os.environ`;
  never returned by any API.
- Structural per-user isolation: one Router per user, built only from that
  user's deployments (a shared singleton router is single-tenant by
  construction and is explicitly forbidden).
- Gateway API keys + optional required auth on `/v1`.
- RBAC on all management surfaces.
- Audit logging on key and provider mutations.

---

# 22. Non-Functional Requirements

- **Availability:** a single provider or key failure must never fail a request
  that any other callable deployment could serve.
- **Staleness bound:** routing decisions reflect DB truth within 30 s
  (Router TTL), or immediately on explicit invalidation.
- **Restart safety:** all health/cooldown state survives restarts (Postgres);
  only probe-progress bars and the router cache are ephemeral by design.
- **Probe politeness:** ≤ 4 concurrent probes per job; probes are 1-token /
  single-embedding requests.
- **Degradation:** discovery and bookkeeping failures are logged and skipped;
  they never fail a user completion ("never let bookkeeping break a completion").

---

# 23. Future Enhancements (unchanged from v0.2, all out of scope)

Model recommendation engine · smart cost optimizer · auto benchmarking ·
semantic model search · provider marketplace · prompt routing · streaming
optimizer · capability detection · regional routing.

---

# 24. Resolved Design Questions (v0.2 §24 dispositions)

| Question (v0.2) | Disposition |
|---|---|
| Discovery cadence | Daily + on-add + manual. Adaptive: rejected for now |
| Failed discovery keeps last list? | Yes — implemented behavior, now contractual |
| Verify models with tiny inference at discovery? | No — trust `/models`; the prober validates per key |
| Best routing algorithm | LiteLLM `usage-based-routing-v2`; custom scoring rejected |
| Should routing learn from history? | No — reactive only; usage data is observability |
| User pinning | Yes, as candidate-set filtering — implemented (§8.3) |
| Priority vs score interaction | Moot — no custom score exists |
| Key selection method | LiteLLM usage-based over flat deployment list |
| Cooldown after 429 | Retry-After when sent (≤ 15 m), else 60 s → 2 m → 5 m ladder — implemented (§7.3) |
| Quota estimation | Rejected |
| Healthy vs degraded | Typed enum stored; "degraded" derived at read time only |
| Health check method | Mode-matched 1-token completion / single embedding |
| Check frequency active vs inactive | Traffic-driven for healthy; scheduled 20 min for unhealthy — implemented (§10) |
| Alias maintenance | `normalize.py` normalized names + virtual model map |
| Stable gateway model IDs | Yes — normalized name is the public ID |
| Deprecated model surfacing | `enabled=false`; drops out of `/v1/models` automatically |
| Cache routing decisions? | The Router itself is the cache (30 s TTL) |
| Health shared across users? | No — health is per key, keys are per user. Provider-wide outage detection may later be derived read-only |
| Event-driven vs scheduled | Event-driven for key add / traffic write-back; scheduled for discovery + unhealthy re-probe |
| LiteLLM responsibilities | Deployment selection, retries, in-flight cooldowns, streaming, provider adaptation |
| Gateway responsibilities | Durable health truth, discovery, encryption, per-user isolation, failure write-back |
| LiteLLM sufficient long-term? | Yes at this scale. Revisit only on a measured routing failure LiteLLM cannot express |

---

# 25. Open Items (genuinely still open)

1. **Dashboard** — surface existing health/usage data (§20, §21 of v0.2).
2. **Multi-process story** — shared cache invalidation (Redis) when, and only
   when, the gateway scales past one process.

Delivered since v0.3 was drafted (see §1.1, §2): the scheduler (§10), cooldown
escalation with `Retry-After` (§7.3), and provider pinning (§8.3). Schema
changes ship as idempotent micro-migrations applied at startup
(`core/database.py: MIGRATIONS`) and mirrored in `schema.sql` for fresh
databases.
