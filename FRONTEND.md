# Frontend Plan

React + Vite + TypeScript + Tailwind, built to `backend/static/`, served by the
existing `mount_frontend()` — one container, one port, no second service.

---

## 0. Read this first: three things that don't exist yet

**The dashboard you asked for cannot be built against the current API.** The data
is there; the endpoints are not. Before any UI work, the backend needs:

| Endpoint | Why it's needed | Status |
|---|---|---|
| `GET /v1/me` | Returns `{id, email, is_admin}`. **The app cannot know whether to show the Admin nav without it**, and it's how we validate a pasted key. | ❌ build |
| `GET /v1/me/usage` | Top models, top providers, totals, daily series. Page 1 *is* this endpoint. | ❌ build |
| `GET /v1/me/logs` | Recent calls — the drill-down from page 1. | ❌ build |
| `GET /v1/admin/users/{id}/keys` | Admin needs to see a user's keys to manage them. | ❌ build |
| everything else | providers, models, provider-keys, router-config | ✅ exists |

`request_logs` already records `user_id`, `provider_id`, `provider_key_id`,
`provider_model_id`, `prompt_tokens`, `completion_tokens`, `total_tokens`
(generated), `latency_ms`, `status_code`. Every number the dashboard needs is a
`GROUP BY` away. **Nothing about the schema needs to change.**

---

## 1. Auth: there is no login

The gateway key **is** the credential. There is no email/password — we deliberately
removed `hashed_password` from `users`. So:

```
First visit  ->  "Paste your gateway key"  [ sk-gw-________ ]  [Connect]
                     |
                     v
             GET /v1/me   (validates the key, returns is_admin)
                     |
             200 -> store in localStorage, render app
             401 -> "That key isn't valid or has been revoked."
```

- Key lives in `localStorage`, sent as `Authorization: Bearer <key>` on every call.
- **Admin nav is rendered from `is_admin`** — but that is cosmetic only. The
  backend enforces it (`require_admin` → 403). Hiding a nav item is not security;
  the API is the security. A user who hand-crafts a request to `/v1/admin/users`
  still gets a 403.
- "Sign out" clears localStorage.
- A `401` from *any* call bounces back to the key screen (the key was revoked).

**Where a new user gets their key:** an admin creates them
(`POST /v1/admin/users`), mints a key (`POST /v1/admin/gateway-keys`), and hands
it over out-of-band. That is the whole onboarding flow. There is no self-signup.

---

## 2. Pages

### Page 1 — Dashboard (`/`)

Your ask: **top 5 models by token usage**, **top providers**.

```
┌─────────────────────────────────────────────────────────────┐
│  12,431 requests   ·   4.2M tokens   ·   3 providers        │  ← totals strip
├───────────────────────────────┬─────────────────────────────┤
│  TOP 5 MODELS BY TOKENS       │  TOP PROVIDERS              │
│  ▄▄▄▄▄▄▄▄▄▄▄▄▄  gpt-oss-120b  │      ◕  Groq        62%     │
│  ▄▄▄▄▄▄▄▄▄      llama-3.3-70b │         NVIDIA      31%     │
│  ▄▄▄▄▄          qwen-2.5-72b  │         Cerebras     7%     │
│  ▄▄▄            deepseek-r1   │                             │
│  ▄▄             gemma-2-9b    │  (donut; tokens + requests) │
├───────────────────────────────┴─────────────────────────────┤
│  TOKENS OVER TIME            [7d] [30d] [90d]               │
│  ▁▂▃▅▃▂▄▆█▅▃▂▁▃▅▇▆▄▃▂▁▂▄▆                                   │
├─────────────────────────────────────────────────────────────┤
│  RECENT ACTIVITY                        [see all →]         │
│  09:41  gpt-oss-120b   Groq #2    412 tok   380ms   200     │
│  09:40  gpt-oss-120b   Groq #1    ——        ——      429  ⚠  │
└─────────────────────────────────────────────────────────────┘
```

**Empty state matters more than the charts.** A brand-new user has zero requests,
so the default view is a blank page unless we handle it. If `total_keys === 0`,
skip the charts entirely and show the onboarding CTA (§ Page 2).

That `429` row is not noise — it's the product working. One key got rate-limited
and a sibling served the retry. Worth rendering as a *feature*, not an error:
show it in amber with "fell back to Groq #2", not red.

### Page 2 — Providers & Keys (`/providers`) — normal user

Your ask: user configures their providers + API keys.

```
┌──────────────────────────────────────────────────────────────┐
│  YOUR PROVIDER KEYS                          [+ Add key]     │
├──────────────────────────────────────────────────────────────┤
│  Groq                                                        │
│    ● Groq #1     ••••4f2a    28/30 models   [Test] [Delete] │
│    ● Groq #2     ••••9c11    30/30 models   [Test] [Delete] │
│      └ two keys = two free-tier budgets. When #1 is rate-    │
│        limited, #2 keeps serving.                            │
│                                                              │
│  NVIDIA NIM                                                  │
│    ◐ NVIDIA #1   ••••1b83    probing…       [Delete]        │
│                                                              │
│  Cerebras                              [+ Add a Cerebras key]│
└──────────────────────────────────────────────────────────────┘
```

Add-key dialog: **provider is a dropdown, not a text field** — users can only pick
providers an admin has seeded (`GET /v1/admin/providers` is admin-only, so we need
the list exposed to users too; see §5 gaps).

**The async flow must be visible.** `POST /v1/me/provider-keys` returns
`{"status": "probing"}` immediately and fans out ~30 deployments probed in the
background. So the row appears instantly in a `probing…` state and the page polls
`GET /v1/me/models` until counts settle. If we render it as a normal synchronous
form, it will look broken for ~20 seconds.

### Page 3 — Admin (`/admin`) — admin only

Your ask: add providers, create users + their keys.

```
┌── Providers ─────────────────────────────────────────────────┐
│  slug        name         base_url              models       │
│  groq        Groq         (default)             30   [Sync]  │
│  nvidia_nim  NVIDIA NIM   integrate.api.nvi…    47   [Sync]  │
│                                          [+ Add provider]    │
└──────────────────────────────────────────────────────────────┘
┌── Users ─────────────────────────────────────────────────────┐
│  email            admin   keys   [+ Create user]             │
│  admin@localhost   ✓       1     [Mint key]                  │
│  alice@corp.com    —       2     [Mint key] [Revoke]         │
└──────────────────────────────────────────────────────────────┘
```

**Mint-key modal is the critical one.** The raw token is returned **exactly once**
and is unrecoverable. The modal must therefore:
- show the token in a big monospace block with a copy button,
- say plainly "This is the only time you will see this key",
- require an explicit "I've copied it" click to dismiss.

A toast that auto-dismisses would silently lose a user's only credential.

**Sync** = `POST /v1/admin/providers/{slug}/discover`. Note it needs at least one
active key for that provider to exist (it borrows one to call `/v1/models`), so if
there are no keys, the button should be disabled with that reason — not just fail.

---

## 3. Pages I'm suggesting you add

### Page 4 — My Models (`/models`) — **the most important one you didn't ask for**

This is the page that shows what the whole gateway is *for*, and none of the three
pages above surfaces it.

```
┌──────────────────────────────────────────────────────────────────┐
│  model            providers          status      redundancy      │
│  gpt-oss-120b     Groq, NVIDIA       ● live      🛡 provider     │
│  llama-3.3-70b    Groq ×2            ● live      🔑 key          │
│  qwen-2.5-72b     Groq               ● live      ⚠ none          │
│  deepseek-r1      NVIDIA             ◐ cooling   ⚠ none          │
└──────────────────────────────────────────────────────────────────┘
```

Straight from `GET /v1/me/models` (`v_my_models`). The three redundancy states map
to real columns, and **the distinction is the product**:

- 🛡 `has_backup_provider` — 2+ live providers. Survives a whole provider outage.
- 🔑 `has_backup_key` — 2+ live keys, possibly same provider. Survives *this key*
  being exhausted. **Two Groq keys count.**
- ⚠ neither — single point of failure. *This is the row that should nudge the user
  to add a second key,* and it's the most actionable thing in the entire UI.

Do **not** show `is_common` as if it means "you have a fallback". It's a catalog
badge meaning "2+ providers serve this somewhere in the world" — it can be true
while the user has no backup at all. Show it as a dim tag, or not at all.

### Page 5 — Router Settings (`/settings`)

`GET/PUT/DELETE /v1/me/router-config`. Four fields: `routing_strategy` (dropdown),
`num_retries`, `cooldown_time`, `allowed_fails`. Small page, already fully backed
by the API. Include a "Reset to defaults" button (the `DELETE`).

### Page 6 — Activity / Logs (`/logs`)

The drill-down from the dashboard's recent-activity strip. Filterable by model,
provider, key, status. Its real job: **per-key burn** — which of your keys is
carrying the load, which is exhausted. That's the question the multi-key design
exists to answer, and a table is the honest way to show it.

### Page 7 — Playground (`/playground`) *(optional, high value)*

A minimal chat box that calls `POST /v1/chat/completions` with the user's key. It
answers "is my setup actually working?" in one click, and shows which deployment
answered. Without it, a user who adds a key has no way to confirm it works except
wiring up an external client.

---

## 4. Structure

```
frontend/
  src/
    lib/
      api.ts          typed fetch wrapper; injects Bearer; 401 -> key screen
      types.ts        mirrors the API responses
    components/
      Layout.tsx      nav (Admin tab only when is_admin), sign out
      KeyGate.tsx     the "paste your gateway key" screen
      StatCard.tsx  Charts.tsx  DataTable.tsx  RedundancyBadge.tsx
    pages/
      Dashboard.tsx   Providers.tsx  Models.tsx
      Admin.tsx       Settings.tsx   Logs.tsx   Playground.tsx
    App.tsx           react-router; everything behind KeyGate
  vite.config.ts      build.outDir = ../backend/static
```

- **Charts:** Recharts (bar for top models, donut for providers, area for the series).
- **Data:** TanStack Query — gives polling for free, which the probing flow needs.
- **Build:** `npm run build` → `backend/static/` → already served. No deploy change.

---

## 5. Backend work required, in order

1. **`GET /v1/me`** → `{id, email, is_admin}`. Blocks *everything* — the app can't
   boot without it.
2. **`GET /v1/providers`** → the seeded provider list, readable by any user. Today
   only `/v1/admin/providers` exists, so a normal user literally cannot populate
   the "add a key" dropdown.
3. **`GET /v1/me/usage?days=30`** → totals, `top_models`, `top_providers`, `daily`,
   `per_key`. This is page 1.
4. **`GET /v1/me/logs?limit=50`** → recent calls, with the answering key.
5. **`GET /v1/admin/users/{id}/keys`** → for the admin user table.

(1) and (2) are small. (3) is a handful of `GROUP BY` queries over `request_logs`.

---

## 6. Build order

| # | Ship | Why this order |
|---|---|---|
| 1 | Backend endpoints (§5) | Nothing renders without them. |
| 2 | Shell: KeyGate + Layout + `api.ts` | Every page needs auth + nav. |
| 3 | **Providers & Keys** (page 2) | A user with no keys has no data — so the config page must work *before* the dashboard has anything to show. |
| 4 | **My Models** (page 4) | Immediately proves the key worked. Highest payoff per line of code. |
| 5 | **Dashboard** (page 1) | Now it has real data to display. |
| 6 | **Admin** (page 3) | Needed to onboard the second user. |
| 7 | Settings, Logs, Playground | Polish. |

Page 2 before page 1 is deliberate: building the dashboard first means staring at
an empty state you can't populate.
