# Gateway plan — configure models, build combos, hand out a URL + key

**Written:** 2026-08-04
**Goal, in your words:** *configure the models, do the combos and strategies, and use the
model anywhere by calling the base URL and an access token, so anyone can use the combos.*

This document says what works today, what is missing, and the order to build it in.

---

## 1. The target workflow, and where it breaks today

```
  ┌────────────┐   ┌──────────┐   ┌─────────┐   ┌────────────┐   ┌──────────────┐
  │ add        │──▶│ discover │──▶│ build a │──▶│ mint a     │──▶│ consumer calls│
  │ provider   │   │ models   │   │ combo   │   │ gateway key│   │ baseURL + key │
  │ keys       │   │          │   │+strategy│   │            │   │               │
  └────────────┘   └──────────┘   └─────────┘   └────────────┘   └──────────────┘
      ✅ works        ✅ works        ❌ MISSING     ⚠️ API only        ✅ works
```

| Step | Status | Evidence |
|---|---|---|
| 1. Add provider keys | **Works** | Providers page; 4 providers connected, keys encrypted per user |
| 2. Discover models | **Works** | 286 deployments, 122 NVIDIA models catalogued and probed |
| 3. Combos + strategies | **Not built** | Zero backend routes, zero bridge routes — see §2 |
| 4. Mint a gateway key | **API only, no UI** | `POST /v1/admin/gateway-keys` works; no dashboard screen — see §3 |
| 5. Consumer calls it | **Works** | `POST http://localhost:1050/v1/chat/completions` with `Authorization: Bearer sk-gw-…` returns completions |

So the two things standing between you and the goal are **combos** (step 3) and **a key-minting
UI** (step 4). Everything else already works end to end — I verified step 5 by calling the
gateway directly and getting a real completion back from Cerebras.

---

## 2. Combos — the honest state

**Nothing exists.** Confirmed by search: no `combo` in `backend/app/`, no `combo` route in
the bridge table. The Combos and Combo Studio pages are OmniRoute UI shells whose every data
call answers 501. The nav entries are kept because combos are the goal, not because they work.

The UI expects these 17 endpoints:

```
/api/combos                      /api/combos/:id            /api/combos/builder/options
/api/combos/metrics              /api/combos/reorder        /api/combos/test
/api/models/alias                /api/playground/simulate-route
/api/settings/combo-defaults     /api/usage/combo-health    /api/usage/call-logs
/api/pricing                     /api/provider-nodes        /api/providers
/api/settings                    /api/settings/proxies/assignments   /api/settings/proxy
```

Reusing that whole UI means implementing all 17. That is the expensive path.

### What a combo actually is

An ordered list of `(provider, model)` steps plus a strategy that picks the order at call
time. You call the combo by name as if it were a model:

```bash
curl http://localhost:1050/v1/chat/completions \
  -H "Authorization: Bearer sk-gw-…" \
  -d '{"model": "my-combo", "messages": [...]}'
```

The gateway tries step 1; on failure (429, 5xx, dead model) it falls through to step 2, and so
on. This is exactly what you already saw fail manually: NVIDIA answered `410 Gone` and the
error came straight back, because `Available Model Group Fallbacks=None`.

### The shortcut worth taking

**LiteLLM's `Router` already implements this.** `backend/app/core/llm_router.py` builds a
router per user. LiteLLM natively supports `fallbacks`, `model_group_alias`, and the
`simple-shuffle` / `least-busy` / `latency-based` strategies — and the gateway already
persists `routing_strategy`, `num_retries`, `cooldown_time`, `allowed_fails` in user settings
(I saw them in `GET /api/settings`).

So a combo is mostly **a stored named alias plus a fallback chain fed into the Router you
already build**, not a new routing engine.

### Recommended build order

**Phase 1 — combos that work, with minimal UI (~the 80% win)**

1. `models/combo.py` — `Combo(id, user_id, name, strategy, steps[])`,
   `ComboStep(combo_id, position, provider_slug, model)`. Follow `schema.sql` conventions:
   composite FK on `(user_id, provider_key_id)` so a combo can never reference another
   user's key.
2. `services/combo_router.py` — resolve a combo name to a LiteLLM `model_list` +
   `fallbacks` chain. Strategies to support first: `priority` (declared order),
   `cost-optimized`, `least-used`. The other three from OmniRoute can wait.
3. `core/llm_router.py` — when the requested `model` matches a combo name, expand it before
   the call. This is the whole feature: one lookup at request time.
4. `api/combos.py` — `GET/POST/PATCH/DELETE /v1/me/combos`, plus `POST /v1/me/combos/{id}/test`.
5. Bridge: map `/api/combos*` → those routes.

*Exit criteria:* `{"model": "my-combo"}` succeeds against step 2 when step 1 is a model you
have deliberately broken. That single test is the whole feature.

**Phase 2 — the Studio UI**

Only after Phase 1 works. Wire `/api/combos/builder/options`, `/api/combos/metrics`,
`/api/combos/reorder`, `/api/usage/combo-health`. If the drag-and-drop Studio proves
expensive, a plain ordered list with up/down buttons delivers the same capability.

**Explicitly deferred:** `/api/settings/proxy*` (upstream proxies — unrelated to combos),
`/api/pricing` (needs a cost table you do not have).

---

## 3. Handing out base URL + access token

### What already works

```bash
# The gateway is OpenAI-compatible. Any OpenAI SDK works:
curl http://localhost:1050/v1/chat/completions \
  -H "Authorization: Bearer sk-gw-…" \
  -H "Content-Type: application/json" \
  -d '{"model":"openai/gpt-oss-120b","messages":[{"role":"user","content":"hi"}]}'
```

Backend endpoints that exist and work:

| Endpoint | Purpose |
|---|---|
| `POST /v1/admin/gateway-keys` | mint a key for a user |
| `GET /v1/admin/gateway-keys` | list keys (prefix only — the secret is shown once) |
| `POST /v1/admin/gateway-keys/rotate` | rotate |
| `DELETE /v1/admin/gateway-keys/{id}` | revoke |
| `POST /v1/admin/users` | create the user a key belongs to |

### The gap

**No dashboard screen mints a gateway key.** The bridge's `/api/keys` maps to
`/v1/me/provider-keys` — those are your *upstream* keys (NVIDIA, Cohere), not the
`sk-gw-…` tokens you hand to consumers. There is no bridge route for `gateway-keys` at all.

The **Settings → Access Tokens** page is not this either: it calls `/api/cli/tokens`, an
OmniRoute CLI-remote-management concept with no gateway equivalent, which is why it shows
*"Could not load access tokens."*

### Fix (small, ~half a day)

1. Add bridge routes: `GET/POST/DELETE /api/gateway-keys` → `/v1/admin/gateway-keys`.
2. Point the **API Keys** page at them, or repurpose **Settings → Access Tokens** (it already
   has the right UI: name, scope, expiry, create, "secret shown once").
3. Show the base URL next to it — `/api/network/info` already returns the correct
   `http://localhost:1050/v1`.

**Do this before combos.** It is much smaller, and it is the step that makes the gateway
usable by anyone other than you.

### One caution on "so anyone can use the combos"

A gateway key spends **your** provider quota. Anyone holding it can burn your NVIDIA and
Cerebras free tiers. Before distributing keys:

- Per-key rate limits and quotas — **not implemented** (Tier 1 T1.4 in `OMNIROUTE_INTEGRATION.md`).
- `request_log` already records per-key usage, and `GET /v1/me/usage` reports it, so you can
  *see* abuse; you cannot yet *stop* it.
- Mint one key per consumer, never share one. Revocation is per-key, so a shared key cannot
  be cut off without cutting off everyone.

---

## 4. Recommended order

| # | Work | Size | Why this order |
|---|---|---|---|
| 1 | Gateway-key UI (§3) | S | Unblocks "anyone can use it" immediately; no new data model |
| 2 | Fix the 4 dead presets (`Supportedprovider.md` §6) | S | You are shipping broken URLs today |
| 3 | Combos Phase 1 (§2) | M | The actual feature; LiteLLM does the hard part |
| 4 | Per-key quotas | M | Needed before keys go to people you do not control |
| 5 | Combos Studio UI (§2 Phase 2) | M | Polish on a working feature |
| 6 | New provider presets (`Supportedprovider.md` §2) | L | Pure breadth; do it in batches whenever |

---

## 5. Related fixes already landed this session

- Provider **Test connection** button (was 404 on every provider — a stale route file shadowed
  the bridge and probed a key id as a provider slug).
- Endpoints page advertised **`localhost:20128`**, a port nothing listens on; now reads the
  real gateway URL, and the endpoint cards mark the 9 endpoints this gateway does not serve
  instead of offering them.
- `/dashboard/health` crashed on load (`system.uptime` of `undefined`).
- **Playground**: streaming errors were swallowed — a dead model rendered an empty bubble with
  a green `200` badge. Errors now surface. The model list is scoped to the selected provider
  (it was matching on a prefix convention this gateway does not use, so the dropdown was
  empty and the default model belonged to a different provider), and unsupported endpoints
  are disabled.
- Sidebar pruned of the parked Tier-3 sections (Agentic Features, Other Features, Audit, CLI
  tools, proxy, webhooks) — nav that promised capabilities the product does not have.
