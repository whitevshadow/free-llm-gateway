# OmniRoute → Multi-LLM Gateway: Phased Integration Plan

**Source:** [OmniRoute/](OmniRoute/) (MIT, `release/v3.8.49`, commit `9a3b605f3`)
**Target:** this repo — FastAPI backend ([backend/app/](backend/app/)) + Vite/React SPA ([frontend/src/](frontend/src/))
**Written:** 2026-07-23

---

## 0. Read this first — the constraint that shapes everything

OmniRoute and this gateway solve the *same problem* but share **zero runnable code**:

| | OmniRoute | This project |
|---|---|---|
| Backend | Node 22 / TypeScript, Next.js 16 route handlers + Express | Python 3.10+, FastAPI |
| Routing engine | Hand-written `src/lib/combos` + `src/lib/providers` | LiteLLM `Router` ([backend/app/core/llm_router.py](backend/app/core/llm_router.py)) |
| Frontend | Next.js App Router, RSC, `next-intl`, zustand, fumadocs | Vite SPA, react-router, TanStack Query, Tailwind |
| Storage | lowdb / SQLite (`sql.js`, `sqlite-vec`) + Redis | SQLAlchemy → Postgres/SQLite ([backend/schema.sql](backend/schema.sql)) |
| Scale | ~10,500 files, 278 providers, 25k tests | ~70 files, 9 test modules |

**Therefore: this is a port, not a merge.** Nothing gets copied wholesale on the backend. Every
backend feature below is "read their design doc → reimplement against LiteLLM + SQLAlchemy."
Only the *frontend* has genuinely reusable material (JSX structure, layout, Tailwind classes,
chart configs), and even that needs Next-App-Router → react-router rewiring.

Two decisions are locked in below; if you disagree, change them here before Phase 1 starts.

> **Decision A — keep the Vite SPA.** Do *not* swap the frontend to Next.js to copy their dashboard
> verbatim. Their pages import their zustand stores, their `/api/*` route handlers and ~70 npm deps;
> you'd inherit the whole Node backend by the back door. Instead lift their **design system**
> (`docs/architecture/DESIGN_SYSTEM.md`) + page layouts into the existing SPA.
>
> **Decision B — scope to Tier 1 + Tier 2.** OmniRoute has ~55 dashboard routes. A realistic
> capstone lands ~12. Tier 3 is explicitly parked, not forgotten.

**Licence:** MIT. Keep their `LICENSE` text and add an attribution line to [README.md](README.md)
for any file that is a recognisable derivative. Do this in Phase 0, not at the end.

---

## 1. Feature inventory — what's actually worth taking

Ranked by (value to this gateway) ÷ (port cost). Doc column = read this before writing code.

### Tier 1 — core gateway parity (take these)

| # | Feature | Why it matters here | OmniRoute doc | Lands in |
|---|---|---|---|---|
| T1.1 | **Combos** (named chains of provider→model steps) | Your `RouterConfig` is one flat pool. Combos = user-defined ordered fallback chains. This is their flagship. | `docs/routing/AUTO-COMBO.md`, `docs/getting-started/AUTO-COMBO-GUIDE.md` | new `models/combo.py`, `services/combo_router.py` |
| T1.2 | **Routing strategies** (19; take 6) | `priority`, `cost-optimized`, `headroom`, `least-used`, `lkgp`, `round-robin`. LiteLLM natively gives you 3 — the rest are scoring functions. | `docs/routing/AUTO-COMBO.md` | `core/llm_router.py` custom router hooks |
| T1.3 | **`auto` virtual model variants** | `auto/fast`, `auto/cheap`, `auto/coding`, `auto/offline` — one alias per optimisation goal. You already have `auto`; this generalises it. | README §Combos | `services/presets.py`, `core/llm_router.py` |
| T1.4 | **Free-tier quota ledger** | Track documented free-tier limits per provider pool + live consumption. Turns "which key is burnt?" into a number. | `docs/reference/FREE_TIERS.md`, `docs/guides/USAGE_QUOTA_GUIDE.md` | `models/quota.py`, `services/usage_logger.py` |
| T1.5 | **3-layer resilience** (circuit breaker / key cooldown / model lockout) | You have LiteLLM cooldowns only. Their layering is the difference between "one bad key" and "provider blacklisted for 10 min". | `docs/architecture/RESILIENCE_GUIDE.md` | `services/prober.py`, `core/llm_router.py` |
| T1.6 | **Live analytics** (usage, p95 latency, spend, savings) | You log to `request_log`; they render it. Highest visible ROI per hour spent. | `docs/guides/COST_TRACKING.md` | [frontend/src/pages/Dashboard.tsx](frontend/src/pages/Dashboard.tsx) |
| T1.7 | **Provider catalogue + health page** | 278 providers with presets. Take the *shape* (preset manifest), seed with your ~15. | `docs/reference/PROVIDER_REFERENCE.md`, `PROVIDER_PLUGIN_MANIFEST.md` | [backend/app/services/presets.py](backend/app/services/presets.py) |

### Tier 2 — high value, self-contained (take if time allows)

| # | Feature | Notes | OmniRoute doc |
|---|---|---|---|
| T2.1 | **Prompt/context compression** (RTK + Caveman) | Their headline claim (15–95% token savings). Algorithmically self-contained — portable as a pure Python module. Highest "wow" per line of code. | `docs/compression/RTK_COMPRESSION.md`, `COMPRESSION_ENGINES.md` |
| T2.2 | **Playground / Studio** | You have a basic [Playground.tsx](frontend/src/pages/Playground.tsx). Theirs adds multi-model side-by-side compare + param sweeps. | `docs/frameworks/PLAYGROUND_STUDIO.md` |
| T2.3 | **Guardrails** (input/output filters) | Pre/post hooks on the request path. Clean FastAPI middleware port. | `docs/security/GUARDRAILS.md` |
| T2.4 | **Prompt caching** | Pin a prompt prefix to one account → cache hits. Pairs with `cache-optimized` strategy. | README §strategies |
| T2.5 | **Webhooks + audit log** | Event bus on request/key/provider state changes. | `docs/frameworks/WEBHOOKS.md` |
| T2.6 | **MCP server** | Expose the gateway as an MCP tool provider. Big surface (104 tools upstream) — implement 5. | `docs/frameworks/MCP-SERVER.md` |
| T2.7 | **Docs site** (`OmniRoute/src/app/docs/`) | fumadocs + a generated OpenAPI explorer. FastAPI already emits OpenAPI at `/docs` — port only the **API Explorer** ([ApiExplorerClient.tsx](OmniRoute/src/app/docs/components/ApiExplorerClient.tsx)) into the SPA. | `docs/reference/API_REFERENCE.md` |

### Tier 3 — parked (document why, don't build)

A2A, ACP, cloud agents, CLI agents, agent skills, memory/vector store, gamification, leaderboard,
chaos engineering, batch API, translator, MITM/TLS stealth proxy, Electron shell, 43-language i18n,
plugin marketplace, VNC sessions. Each is a project of its own and none is load-bearing for
"one endpoint that never runs out of quota."

---

## 2. Gap map — current state vs. target

What already exists (don't rebuild):

- OpenAI + Anthropic compat, embeddings, images, audio — [backend/app/api/](backend/app/api/)
- Gateway API keys, RBAC, master admin — [gateway_auth.py](backend/app/api/gateway_auth.py), [gateway_keys.py](backend/app/services/gateway_keys.py)
- Encrypted provider-key store + probing + rotation — [key_store.py](backend/app/services/key_store.py), [prober.py](backend/app/services/prober.py)
- Model discovery + normalisation — [catalog.py](backend/app/services/catalog.py), [normalize.py](backend/app/services/normalize.py)
- Request logging + usage rollups — [usage_logger.py](backend/app/services/usage_logger.py), `GET /v1/me/usage`
- SPA shell with 7 pages — [frontend/src/App.tsx](frontend/src/App.tsx)

Missing, in dependency order: `combo` data model → strategy engine → quota ledger → resilience
layers → compression → analytics UI → studio UI.

---

## 3. Phases

Each phase is independently shippable and independently demoable. Exit criteria are the contract —
don't start the next phase until they're green.

---

### Phase 0 — Groundwork (0.5 day)

**Goal:** the repo can absorb OmniRoute work without churn.

1. Add MIT attribution for OmniRoute to [README.md](README.md) + keep `OmniRoute/LICENSE`.
2. Decide the `OmniRoute/` checkout's fate: it is currently **inside** the project tree
   (`d:\Capstone\litellm\OmniRoute`) and will be picked up by Docker builds, linters and `git`.
   → Either move it to `d:\Capstone\OmniRoute` (sibling, recommended) or add it to
   [.gitignore](.gitignore) + [.dockerignore](.dockerignore). **Do this first**; it silently
   inflates every image build otherwise.
3. Create `docs/` in this repo; copy the *reference* docs you'll actually consult
   (`RESILIENCE_GUIDE.md`, `AUTO-COMBO.md`, `RTK_COMPRESSION.md`, `FREE_TIERS.md`, `DESIGN_SYSTEM.md`).
4. Baseline: `pytest` green, record current test count and `/health` output.

**Exit:** clean `git status`, tests green, `docker compose up --build` unchanged in size ±5%.

---

### Phase 1 — Combos: data model + resolution (2–3 days)

**Goal:** a user can define a named chain and call it as a model.

- `models/combo.py`: `Combo(id, user_id, name, description, enabled)` +
  `ComboStep(combo_id, order, provider_slug, model_id, strategy, weight, max_tokens_hint)`.
- Migration in [backend/schema.sql](backend/schema.sql) + `services/bootstrap.py` seeding.
- `services/combo_router.py`: resolve `combo_name` → ordered LiteLLM deployment list.
- Wire into [openai_compat.py](backend/app/api/openai_compat.py) `_resolve_or_404` so
  `model: "my-combo"` resolves alongside virtual models.
- CRUD: `GET/POST/PATCH/DELETE /v1/me/combos`.
- Tests: `tests/test_combos.py` — resolution, ownership isolation, unknown-combo 404, cycle guard.

**Exit:** `curl -d '{"model":"my-combo",...}' /v1/chat/completions` routes through step 1, and
falls to step 2 when step 1 is cooled down.

**Ref:** `OmniRoute/docs/routing/AUTO-COMBO.md`

---

### Phase 2 — Routing strategies (2–3 days)

**Goal:** each combo step picks its target by a named policy.

Implement 6 as pluggable scorers in `services/strategies/`:

| Strategy | Signal | Source of truth |
|---|---|---|
| `priority` | step order | combo definition |
| `round-robin` | counter | in-memory / Redis |
| `least-used` | in-flight count | LiteLLM router state |
| `cost-optimized` | $/1k tokens | `provider_model` pricing column (add it) |
| `headroom` | remaining free quota | Phase 3 ledger (stub → returns ∞ until then) |
| `lkgp` | last successful target | `request_log` tail, cached |

Extend `RouterConfig` ([models/router_config.py](backend/app/models/router_config.py)) with a
default strategy; expose `auto/fast`, `auto/cheap`, `auto/coding`, `auto/offline` as preset combos
built from the user's healthy deployments.

**Exit:** `tests/test_strategies.py` — each strategy deterministic under a fixed fake pool;
`GET /v1/models` lists the four `auto/*` aliases.

---

### Phase 3 — Quota ledger & free-tier tracking (2 days)

**Goal:** "how much free quota do I have left, per provider, right now?"

- `models/quota.py`: `FreeTierLimit(provider_slug, model_pattern, tokens_per_month, rpm, rpd, window)`
  + `QuotaUsage(user_id, provider_key_id, window_start, tokens_in, tokens_out, requests)`.
- Seed limits from a YAML catalogue mirroring their pool-dedup methodology
  (**read `docs/reference/FREE_TIERS.md` before writing the seed file** — the dedup rule is the
  whole point; naïve per-key summing overstates by ~6×).
- Increment from [usage_logger.py](backend/app/services/usage_logger.py) on every completion.
- `GET /v1/me/quota` → per-provider used/remaining/resets-at.
- Unblock `headroom` strategy from Phase 2.

**Exit:** ledger totals reconcile with `request_log` sums in `tests/test_quota.py`; window rollover
tested across a month boundary.

---

### Phase 4 — Resilience layers (1–2 days)

**Goal:** one bad key never takes down a provider; one bad provider never takes down a combo.

Three independent layers on top of LiteLLM's cooldown:

1. **Key cooldown** — 429/401 on a key benches *that key* (exponential backoff, cap 15 min).
2. **Model lockout** — repeated failures on `provider:model` bench the pair, other models survive.
3. **Circuit breaker** — provider-level open/half-open/closed with a probe request to re-close.

Surface all three in `GET /health` and a new `GET /v1/me/health/providers`.

**Exit:** `tests/test_resilience.py` — injected 429 storm benches the key, not the provider;
circuit reopens after the cooldown; existing [test_cooldown.py](backend/tests/test_cooldown.py) still green.

**Ref:** `OmniRoute/docs/architecture/RESILIENCE_GUIDE.md`

---

### Phase 5 — Frontend foundation & design system (2 days)

**Goal:** the SPA can host the new pages without each one inventing its own styling.

- Read `OmniRoute/docs/architecture/DESIGN_SYSTEM.md`; port tokens into
  [frontend/tailwind.config.js](frontend/tailwind.config.js).
- Expand [components/ui.tsx](frontend/src/components/ui.tsx) into `components/ui/` —
  Card, Table, Badge, Tabs, Dialog, Toast, StatTile, EmptyState, Skeleton.
- Replace the flat top nav in [App.tsx](frontend/src/App.tsx) with a **sidebar shell** matching their
  `(dashboard)` layout — grouped sections (Overview / Routing / Providers / Usage / Settings).
- Add `recharts` (they use it too) + a shared chart theme.
- Screenshots for reference: `OmniRoute/docs/screenshots/`.

**Exit:** all 7 existing pages render inside the new shell with no visual regressions; dark mode consistent.

---

### Phase 6 — Dashboard pages for Phases 1–4 (3–4 days)

Port these OmniRoute routes, one PR each:

| New SPA page | Modelled on | Backed by |
|---|---|---|
| `Combos.tsx` + `ComboEditor.tsx` | `dashboard/combos`, `combos/[id]` | Phase 1 CRUD |
| `Analytics.tsx` | `dashboard/analytics` | `GET /v1/me/usage` |
| `Quota.tsx` / `FreeTiers.tsx` | `dashboard/free-tiers`, `dashboard/quota` | Phase 3 |
| `Health.tsx` | `dashboard/health`, `dashboard/provider-stats` | Phase 4 |
| Upgraded `Providers.tsx` | `dashboard/providers` | existing endpoints |
| Upgraded `Logs.tsx` | `dashboard/logs` | `GET /v1/me/logs` |

Combo editor: their drag-and-drop uses `@dnd-kit` — same library works in Vite, port directly.
Skip their `@xyflow/react` node-graph editor (Tier 3).

**Exit:** every Phase 1–4 API has a UI; no page shows a raw JSON dump.

---

### Phase 7 — Compression engine (3–4 days, optional but high impact)

**Goal:** cut tokens on tool-heavy conversations before they hit the provider.

- Port RTK (structural token reduction) and Caveman (aggressive lexical strip) as pure Python in
  `services/compression/`. **Read `docs/compression/COMPRESSION_ENGINES.md` +
  `RTK_COMPRESSION.md` first** — their rule format is data, not code, so the rule packs port as-is.
- Opt-in per request (`extra_body: {"compression": "rtk"}`) and per combo step.
- Record `tokens_saved` in `request_log`; surface on the analytics page.
- Guardrail: compression must be **lossless for code blocks and tool JSON** — test that first.

**Exit:** `tests/test_compression.py` — round-trip semantics preserved on a fixture corpus;
measured savings reported in the test output.

---

### Phase 8 — API explorer, docs & polish (2 days)

- Port `OmniRoute/src/app/docs/components/ApiExplorerClient.tsx` → an SPA page fed by FastAPI's
  live `/openapi.json` (no fumadocs, no build-time generation).
- Update [README.md](README.md) architecture diagram + feature table; refresh [SRS.md](SRS.md).
- Update [FRONTEND.md](FRONTEND.md) with the new page map.
- Docker: verify image size, `docker compose up --build` end-to-end.

**Exit:** fresh clone → `docker compose up` → working dashboard with all shipped features.

---

## 4. Sequencing & estimate

```
P0 ──► P1 ──► P2 ──► P3 ──► P4 ──► P6 ──► P8
        │                    ▲       ▲
        └──► P5 ─────────────┘       │
                          P7 ────────┘   (parallel / optional)
```

| Phase | Days | Blocking? |
|---|---|---|
| 0 Groundwork | 0.5 | yes |
| 1 Combos | 2–3 | yes |
| 2 Strategies | 2–3 | yes |
| 3 Quota | 2 | unblocks `headroom` |
| 4 Resilience | 1–2 | no |
| 5 Design system | 2 | parallel with 1–4 |
| 6 Dashboard pages | 3–4 | needs 1–5 |
| 7 Compression | 3–4 | independent |
| 8 Explorer/docs | 2 | last |

**Tier 1 only:** ~13 working days. **Tier 1 + Tier 2 subset (P7):** ~17.

---

## 5. Risks

| Risk | Mitigation |
|---|---|
| Porting TS→Python invites subtle logic drift | Port the *tests* alongside the code — their `__tests__` dirs contain the edge cases |
| `OmniRoute/` inside the project tree bloats Docker builds & git | Phase 0 item 2 — move or ignore it |
| Scope creep into Tier 3 (agents, MCP, memory) | Tier 3 list above is the "no" list; revisit only after Phase 8 |
| Compression breaking tool-call JSON | Losslessness test written before the engine |
| Their free-tier numbers go stale | Seed from YAML with an `audited_at` field; don't hardcode in Python |
| Next.js idioms leaking into the Vite SPA | Never copy a `page.tsx` wholesale — extract JSX + Tailwind, rewrite data fetching as TanStack Query |

---

## 6. Where to read what

| Question | File |
|---|---|
| What is a combo, exactly? | `OmniRoute/docs/routing/AUTO-COMBO.md` |
| How do the 19 strategies score? | same + README §Combos |
| How do circuit breakers layer? | `docs/architecture/RESILIENCE_GUIDE.md` |
| How is free-tier math deduped? | `docs/reference/FREE_TIERS.md` |
| How does compression work? | `docs/compression/COMPRESSION_ENGINES.md`, `RTK_COMPRESSION.md` |
| What do the pages look like? | `docs/screenshots/`, `docs/architecture/DESIGN_SYSTEM.md` |
| Full route/module map | `docs/architecture/REPOSITORY_MAP.md`, `CODEBASE_DOCUMENTATION.md` |
| Their API surface | `docs/reference/API_REFERENCE.md`, `docs/openapi.yaml` |
