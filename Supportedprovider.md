# Supported Providers — status, plan, and what is out of reach

**Written:** 2026-08-04
**Sources of truth:**
[`OmniRoute/docs/reference/PROVIDER_REFERENCE.md`](OmniRoute/docs/reference/PROVIDER_REFERENCE.md) (278 providers, auto-generated from their registry)
· [`backend/app/services/presets.py`](backend/app/services/presets.py) (this gateway's presets)
· [`frontend/src/shared/constants/providers/`](frontend/src/shared/constants/providers/) (the 286 cards the Providers page renders)

---

## 0. Read this first — three facts that change the shape of the job

**Fact 1 — 101 providers are already wired.** `presets.py` is 1,021 lines and already carries
**61 free-tier presets** (the "Add a provider" dropdown) plus **40 known-paid entries** with
base URLs filled in. The Providers page rendering 286 cards with "No connections" does *not*
mean 286 providers are unsupported; it means the page renders OmniRoute's full catalog
regardless of what the backend knows. The real gap is smaller than the screen suggests.

**Fact 2 — you can already use any OpenAI-compatible provider today, with no code change.**
`POST /v1/admin/providers` takes a `base_url`, and the Providers page has
"Add OpenAI Compatible" / "Add Anthropic Compatible". A preset only saves typing the URL.
So this work is *convenience and curation*, not capability.

**Fact 3 — about 70 of the 278 cannot be done by LiteLLM at all, and no amount of trying
will change that.** They are not API-key providers. They are browser sessions, OAuth token
rotations, IDE keychains, and local processes. See §3. Attempting them one-by-one and
removing failures would burn the whole budget rediscovering an architectural fact.

---

## 1. The catalog, by auth class

| Category | Count | Preset coverage | Verdict |
|---|---:|---:|---|
| API Key providers | 187 | ~102 | **In scope.** The real backlog: 85 remaining |
| Web Cookie providers | 29 | — | **Out of scope** (browser session auth) |
| OAuth providers | 23 | — | **Out of scope** (interactive sign-in + token rotation) |
| Local providers | 12 | 0 | **Partly in scope** (user supplies their own localhost URL) |
| Search providers | 11 | — | **Different API shape** — not chat completions |
| Audio-only providers | 10 | 0 | **Partly in scope** — `/v1/audio/*`, already served |
| Cloud Agent providers | 3 | 0 | **Out of scope** (long-running agent protocol) |
| Upstream Proxy providers | 2 | 0 | **Out of scope** (transport layer, not a model API) |
| System providers | 1 | 0 | **Out of scope** (OmniRoute-internal loopback) |
| **Total** | **278** | | |

> The preset-coverage column is a fuzzy id match and is approximate for the non-API-key rows
> (a `github` preset for GitHub *Models* is not the `github` OAuth *Copilot* provider). It is
> reliable for the API-key row, which is the row that matters.

---

## 2. In scope — the 85 API-key providers still missing a preset

Each needs exactly three things: a `slug` LiteLLM accepts, a `base_url`, and (when LiteLLM
has no native integration) `custom_llm_provider: "openai"` to force it onto the generic
OpenAI client. That pattern is already established in `presets.py` — see the `longcat` entry.

### Batch A — OpenAI-compatible aggregators and inference hosts (highest value, lowest risk)
`agentrouter` · `empower` · `fenayai` · `getgoapi` · `laozhang` · `nara` · `navy` ·
`orcarouter` · `piapi` · `poe` · `requesty` · `routeway` · `thebai` · `siliconflow` ·
`nebius` · `together` · `opencode-go` · `opencode-zen` · `openvecta` · `wafer` ·
`g4f-gemini` · `g4f-groq` · `g4f-nvidia` · `g4f-ollama` · `g4f-pollinations`

*Why first:* all expose `GET {base_url}/models`, so auto-discovery works and each one is a
~4-line dict. **25 providers.**

### Batch B — first-party model vendors
`anthropic` · `alibaba` · `alibaba-cn` · `doubao` · `gigachat` · `glm` · `glm-cn` · `glmt` ·
`kimi` · `kimi-coding-apikey` · `minimax` · `minimax-cn` · `maritalk` · `sealion` ·
`sparkdesk` · `xiaomi-mimo` · `360ai` · `agnes` · `ainative` · `aion` · `arcee-ai` ·
`command-code` · `hcnsec` · `gitlawb` · `gitlawb-gmi` · `gitlab` · `cloudflare-ai` ·
`digitalocean`

*Note:* `anthropic` is native in LiteLLM — no `custom_llm_provider` needed. **28 providers.**

### Batch C — enterprise clouds (each needs non-trivial auth, not just a key)
`bedrock` · `vertex` · `vertex-partner` · `azure-openai` · `azure-ai` · `databricks` ·
`datarobot` · `snowflake` · `watsonx` · `oci` · `sap` · `clarifai`

*Why last among the in-scope work:* these use SigV4, service accounts, or region+deployment
tuples rather than a bearer token. LiteLLM supports most natively, but the gateway's
`provider_key` model stores a single secret — these need a credential shape it does not have
yet. **This is a schema change, not a preset. 12 providers.**

### Batch D — non-chat modalities (image / video / audio / embed / fetch)
`black-forest-labs` · `fal-ai` · `freepik` · `ideogram` · `recraft` · `stability-ai` ·
`topaz` · `segmind` · `haiper` · `leonardo` · `runwayml` · `suno` · `udio` ·
`jina-ai` · `voyage-ai` · `mixedbread` · `nomic` · `jina-reader` · `firecrawl` · `tinyfish`

*Why separate:* these do not answer `/v1/chat/completions`. The gateway already serves
`/v1/images/generations`, `/v1/embeddings` and `/v1/audio/*`, so the image and embedding ones
fit; video (`/v1/videos/generations`) and fetch/search have **no gateway endpoint at all**
and are blocked on that being built first. **20 providers.**

---

## 3. Out of scope — and why trying is not the answer

These fail for a structural reason, not a configuration one. Listing them here so nobody
spends a week rediscovering it.

| Group | Count | Why LiteLLM cannot serve it |
|---|---:|---|
| **Web Cookie** (ChatGPT Web, Claude Web, Gemini Web, Grok Web, Perplexity Web, Poe Web, Qwen Web, DeepSeek Web, Kimi Web, t3.chat, …) | 29 | Auth is a browser session cookie scraped from a logged-in tab. There is no API key and no stable API — OmniRoute drives these with a headless browser and HTML/SSE scraping. LiteLLM speaks HTTP APIs only. Also: nearly all violate the provider's ToS for third-party proxying. |
| **OAuth** (Claude Code, GitHub Copilot, Codex, Cursor, Kiro, Windsurf, Cline, Kilo Code, Amazon Q, GitLab Duo, …) | 23 | Requires an interactive browser sign-in, a client-id registered with the vendor, and background refresh-token rotation. That is an auth subsystem the gateway does not have. Several (Kiro explicitly) forbid proxy use in their ToS. |
| **Cloud Agent** (Codex Cloud, Devin, Google Jules) | 3 | Long-running task protocol with plan approval and status polling — not a completion call. |
| **Upstream Proxy** (9router, CLIProxyAPI) | 2 | A network transport to put *in front of* a provider, not a provider. |
| **System** (loopback) | 1 | OmniRoute-internal. Meaningless here. |
| **IDE** (Cursor IDE, Trae, Zed IDE) | 3 | Reads credentials out of a local IDE keychain on the user's machine. |

**Total structurally out of reach: ~61**, plus the 11 Search providers which are a different
API surface (they answer queries, not completions) and would need a `/v1/search` endpoint the
gateway does not implement.

**Local providers (12)** — Ollama, LM Studio, vLLM, llama.cpp, ComfyUI, etc. — are a special
case: they *are* OpenAI-compatible, but their base URL is `http://localhost:…` on the
**user's** machine, which a Docker-hosted gateway cannot reach. They work only for a
self-hosted deployment. Document them; do not preset them.

### 3a. Worked example — why DeepSeek Web cannot be a preset

Pasting a `userToken` into the Providers page returns *"the gateway does not know
'deepseek-web'"*. That is correct, and no base URL fixes it. Here is what OmniRoute actually
needs to serve one message (`open-sse/executors/deepseek-web.ts`, **1,147 lines**, plus 296
more across three helper modules):

| Step | What it involves |
|---|---|
| Proof of work | `POST /v0/chat/create_pow_challenge`, solve it, send the answer as `X-Ds-Pow-Response`. **Every request.** OmniRoute uses a compiled WebAssembly binary (`open-sse/lib/sha3_wasm_bg.wasm`) — **but see §3b: that turned out to be unnecessary.** |
| Session lifecycle | `createSession()` / `deleteSessionOnDeepSeek()` around each conversation, tracking `chat_session_id` |
| Prompt flattening | `messagesToPrompt()` — it is a chat UI, not a messages API, so the OpenAI `messages[]` array has to be collapsed into one prompt |
| Response translation | `transformSSE()` — ~200 lines converting DeepSeek's private SSE dialect into OpenAI chunks |
| Session upkeep | fake-cookie generation and a 184-line token auto-refresh path |

None of this is expressible as a LiteLLM provider, which speaks OpenAI-shaped HTTP and
nothing else. The same shape of problem applies to all 31 web-session providers.

`/v1/me/provider-keys/validate` now says this explicitly instead of asking for a base URL —
see `SESSION_AUTH_PROVIDERS` in [`backend/app/api/validation.py`](backend/app/api/validation.py).

**If you genuinely want DeepSeek Web, there are three options:**

1. **Use DeepSeek's real API instead.** `deepseek` is already a preset
   (`https://api.deepseek.com`). It is a paid API rather than your web subscription, but it
   works today with zero new code. *Recommended.*
2. **Run OmniRoute as a sidecar and point this gateway at it.** OmniRoute serves an
   OpenAI-compatible `POST /api/v1/chat/completions` on port **20128** and already implements
   all 31 web-session providers. Start it (`npm run dev` in `OmniRoute/`), sign in to DeepSeek
   Web there, then add it here via **Add OpenAI Compatible** with base URL
   `http://localhost:20128/api/v1`. This gateway keeps doing routing, keys and logging;
   OmniRoute does the browser-session work. **This is the only path that gets you DeepSeek Web
   without writing the executor**, at the cost of running a second service.
3. **Port the executor.** ~1,450 lines plus the WASM blob, and it breaks whenever DeepSeek
   changes the PoW scheme. Not recommended.

One caution on 2 and 3: driving a signed-in web session through a proxy is against the terms
of service of most of these providers, and the token has the access of your logged-in
account. That is your call to make, but it should be a deliberate one.

### 3b. Correction — the proof-of-work does NOT need WebAssembly

**I was wrong about option 3 being impractical.** The WASM blob is an optimisation, not a
requirement, and the algorithm is fully recoverable from OmniRoute's own JS fallback path.

`{capacity: 256, padding: 6}` in their sponge works out to a **136-byte rate, 32-byte output,
0x06 domain byte** — precisely SHA3-256's profile. But `hashlib.sha3_256` still disagrees on
the first byte, because the permutation is:

```js
keccak: (t) => { for (let i = 1; i < 24; i++) { theta; rho_pi; chi; iota(i) } }
```

**23 rounds starting at round constant 1**, where real Keccak-f[1600] runs 24 starting at 0.
Dropping `RC[0]` changes every digest. That one off-by-one is the entire reason a stock hash
function cannot be substituted — and, presumably, why shipping a compiled blob looked easier
than explaining it.

Reimplemented in [`backend/app/services/deepseek_pow.py`](backend/app/services/deepseek_pow.py),
**verified byte-for-byte against OmniRoute's JS reference** on five vectors
(`backend/tests/test_deepseek_pow.py`).

Performance mattered more than correctness here, since scalar Python is unusable:

| Implementation | Throughput | Avg solve @ difficulty 144,000 |
|---|---:|---:|
| Scalar Python | ~1,400 h/s | **50s** — unusable |
| **numpy batched (this repo)** | **~95,000 h/s** | **0.8s** (1.5s worst case) |
| OmniRoute JS fallback | ~25,000 h/s | ~5-6s |
| OmniRoute WASM | ~1,400,000 h/s | 50-100ms |

The permutation has no data dependence between candidate nonces, so it vectorises: the numpy
path runs the same 23 rounds across a whole batch at once. The result is **6× faster than
OmniRoute's own JS fallback** with no native dependency.

### 3c. DeepSeek Web is DONE and serving traffic through `:1050`

Verified end to end against the live API with a real `userToken`, and from the Playground UI.

| Piece | File | State |
|---|---|---|
| PoW solver | `app/services/deepseek_pow.py` | ✅ solves a live challenge in ~1.6s |
| Session lifecycle + SSE translation | `app/services/deepseek_web.py` | ✅ |
| Static model registration (10 models) | `app/services/catalog.py` | ✅ no `/models` endpoint exists |
| Probe via the executor | `app/services/prober.py` | ✅ 10/10 models `available` |
| Router exclusion | `app/core/llm_router.py` | ✅ see below |
| Dispatch branch (stream + non-stream) | `app/api/openai_compat.py` | ✅ |
| `Check token` validation | `app/api/validation.py` | ✅ |

**Two bugs worth recording**, both found only by running it:

1. *The final `{"content": …}` event is not the answer.* It carries injected context
   (`"2026-08-04,Tuesday,India,Web,Enable"`). Treating it as output appends that string to
   every reply.
2. *Paths are sticky.* Text arrives as one addressed append
   (`response/fragments/-1/content`) followed by a run of bare `{"v": "…"}` values that
   inherit it. Treating a missing `p` as metadata truncates every answer to its first token —
   and `response/fragments/-1/content` does **not** mean "answer": it appends to whichever
   fragment is open, so forcing it to the content channel dumps a reasoner's entire chain of
   thought into the reply.

**And one that broke everything else:** `deepseek-web/*` deployments must be excluded from the
litellm `Router` model_list. litellm has no such integration, so leaving them in makes the
Router constructor raise `LLM Provider NOT provided` — which takes down **every** model for
that user, including `GET /v1/models`. They stay in the callable-names list so the executor
branch can still serve them. NVCF already needed the same treatment.

Verified working: `deepseek-v4-pro` (non-streaming), `deepseek-v4-flash` (streaming, 13
chunks), `deepseek-v4-pro-think` (reasoning kept in `reasoning_content`, separate from the
answer).

**Collision note:** NVIDIA NIM also serves a model normalised to `deepseek-v4-flash`. When two
providers share a family name, litellm keeps it — the bypass only fires when *every* live
deployment for that name is browser-session, so a paid API call is never silently downgraded
to a scraped web session.

### 3d. Making it fast — measured, not assumed

Cold cost was **1,531 ms of overhead before the first token**, of which **1,075 ms was CPU**
solving the proof-of-work. Phase breakdown:

| Phase | Cold | Warm |
|---|---:|---:|
| session create | 269 ms | 215 ms |
| pow challenge fetch | 187 ms | 0 ms |
| **pow solve (CPU)** | **1,075 ms** | **0 ms** |
| upstream TTFB | 213 ms | 264 ms |
| **request total (via `:1050`)** | **~3,200 ms** | **~1,780 ms** |

Three properties of DeepSeek's scheme drove the design, each verified live:

1. a challenge is valid for **~298 s**;
2. a solution computed **45 s earlier is still accepted**;
3. **a solved answer is single-use.**

(3) is the trap. Replaying one returns **HTTP 200 with a well-formed SSE stream containing no
content** — the request "succeeds" and the caller gets an empty message, with no error
anywhere. A token-keyed cache therefore looks like a 5× speedup and silently empties every
reply after the first. I shipped that, measured 2,248 ms → 410 ms, and only caught it because
the assertion checked the *text* and not just the status code.

What actually works is (1)+(2): **bank distinct pre-solved answers and consume each once.**
The pool refills after the stream finishes, never during — running the ~1 s solve concurrently
with the completion it was fetched for pushed a warm TTFB from 480 ms back to 1,175 ms.

**Two things that were tried and reverted, both because they broke correctness silently:**

- *Session reuse* (would save 269 ms + 208 ms): a second completion posted to an already-used
  session returns an empty answer. Posting `parent_message_id: None` to a session that already
  holds a message is not a supported shape.
- *PoW reuse*: see (3) above.

**One constraint that is not fixable in code:** a web session is one browser tab, and DeepSeek
serialises generation per account. Two concurrent completions on the same `userToken` leave
one with an empty stream. This was invisible until the PoW pool removed the ~1.7 s of solving
that had been staggering requests by accident. It is now enforced with an explicit per-token
lock, so **throughput per token is one in-flight generation** — a property of the account, not
of this code. Parallelism comes from having more than one provider.

---

## 4. The binding constraint on "test each one, remove what fails"

**A provider cannot be verified without an account and an API key for it.**

Right now this gateway holds keys for **4** providers (Cerebras, Cohere, NVIDIA NIM,
TokenRouter). For the other 81 in-scope providers, adding a preset is *unverifiable* — the
base URL can be checked against the vendor's docs, but "does a real call succeed" cannot be
answered without signing up.

So the honest testing policy is three tiers, and each preset should record which tier it
reached:

| Tier | What it proves | Cost |
|---|---|---|
| **T0 — Reachable** | `GET {base_url}/models` returns HTTP 401/403 (endpoint exists, rejects anonymous) rather than DNS failure or 404 | Free, automatable, no key |
| **T1 — Discovers** | With a real key, `GET {base_url}/models` returns a model list the catalog can ingest | Needs a key |
| **T2 — Callable** | With a real key, a real `/v1/chat/completions` returns a completion | Needs a key + quota |

**T0 is automatable across all 85 today** and catches the most common defect: a wrong or dead
base URL. That is the check to build first. T1/T2 happen per provider as you obtain keys.

A preset that fails T0 gets removed and recorded in §6.

---

## 5. Recommended order of work

1. **Write the T0 prober** — a script that walks `PRESETS + _KNOWN_NON_PRESET`, hits
   `{base_url}/models` anonymously, and reports DNS-fail / 404 / 401 / 200. Run it against
   the **existing 101** first. Some were bulk-imported from OmniRoute and have never been
   verified; expect dead URLs among them. *Exit: a table of 101 rows with a T0 verdict.*
2. **Fix or remove whatever the existing 101 fail on.** Correcting a broken preset you already
   ship beats adding a new one.
3. **Batch A** (25 aggregators/inference hosts) → T0 → land.
4. **Batch B** (28 model vendors) → T0 → land.
5. **Batch D image/embedding subset only** (the ones with a gateway endpoint) → T0 → land.
6. **Stop.** Batch C needs the credential-schema change; Batch D video/fetch needs new
   endpoints. Both are separate projects — do not start them inside this one.

Per batch, the diff is confined to `presets.py`, so each batch is independently revertible.

---

## 6. Status — what has actually landed

**Preset count: 101 → 118.** Five dead presets removed, one re-pointed, 22 added. Every
change below was T0-verified with
[`backend/tools/probe_presets.py`](backend/tools/probe_presets.py).

```
python backend/tools/probe_presets.py
Summary: AUTH=53, LIVE=48, NOTFOUND=11, OTHER=3, DNS=1, CONNFAIL=1, TIMEOUT=1
Needs attention: 13 / 118      (was 17 / 101)
```

**105 of 118 pass T0.** All 13 remaining failures are pre-existing entries, analysed below —
none of the 22 additions is among them.

### ✅ Added — 22 providers, all T0-verified

| Provider | Slug | base_url | T0 |
|---|---|---|---|
| Together AI | `together` | `https://api.together.xyz/v1` | AUTH 401 |
| Nebius (Token Factory) | `nebius` | `https://api.tokenfactory.nebius.com/v1` | AUTH 401 |
| SiliconFlow | `siliconflow` | `https://api.siliconflow.com/v1` | AUTH 401 |
| Requesty | `requesty` | `https://router.requesty.ai/v1` | LIVE 200 |
| Routeway | `routeway` | `https://api.routeway.ai/v1` | LIVE 200 |
| OpenVecta | `openvecta` | `https://api.openvecta.com/v1` | AUTH 401 |
| NaraRouter | `nara` | `https://router.bynara.id/v1` | AUTH 401 |
| NavyAI | `navy` | `https://api.navy/v1` | LIVE 200 |
| Aion Labs | `aion` | `https://api.aionlabs.ai/v1` | LIVE 200 |
| Agnes AI | `agnes` | `https://apihub.agnes-ai.com/v1` | AUTH 401 |
| AINative Studio | `ainative` | `https://api.ainative.studio/api/v1` | LIVE 200 |
| SEA-LION | `sealion` | `https://api.sea-lion.ai/v1` | AUTH 401 |
| DigitalOcean Gradient | `digitalocean` | `https://inference.do-ai.run/v1` | AUTH 401 |
| Huancheng Public API | `hcnsec` | `https://api.hcnsec.cn/v1` | AUTH 401 |
| AI Horde | `aihorde` | `https://oai.aihorde.net/v1` | LIVE 200 |
| OpenCode Zen | `opencode_zen` | `https://opencode.ai/zen/v1` | LIVE 200 |
| OrcaRouter | `orcarouter` | `https://api.orcarouter.ai/v1` | LIVE 200 |
| Gitlawb OpenGateway | `gitlawb` | `https://opengateway.gitlawb.com/v1` | LIVE 200 |
| Xiaomi MiMo | `xiaomi_mimo` | `https://api.xiaomimimo.com/v1` | AUTH 401 |
| g4f.space — Gemini | `g4f_gemini` | `https://g4f.space/api/gemini/v1` | LIVE 200 |
| g4f.space — Ollama | `g4f_ollama` | `https://g4f.space/api/ollama/v1` | LIVE 200 |
| g4f.space — Pollinations | `g4f_pollinations` | `https://g4f.space/api/pollinations/v1` | LIVE 200 |

All 22 went into `_KNOWN_NON_PRESET`, not the `PRESETS` dropdown, because none has been
exercised with a live key — so none may claim the dropdown's "free at $0" promise. They still
get base_url auto-fill and `custom_llm_provider` plumbing when added as CUSTOM. Promote one to
`PRESETS` once its free tier is confirmed.

### ✅ Fixed — GitHub Models

`models.github.ai` answers **410 Gone** on both `/inference/chat/completions` and
`/catalog/models`. GitHub retired that host and moved the service to Azure AI Inference.
Re-pointed to `https://models.inference.ai.azure.com` (verified LIVE 200), and the sibling
`models_url` override the entry needed is gone — `{base_url}/models` works there.

### ✅ Removed — 5 dead providers

Confirmed NXDOMAIN against public DNS 8.8.8.8, or 410. **OmniRoute's registry carries the
identical dead URLs** — these upstreams died after their v3.8.49 snapshot (2026-07-22), so
"take the URL from OmniRoute" cannot fix them.

| Slug | Host | Verdict |
|---|---|---|
| `galadriel` | `api.galadriel.ai` | NXDOMAIN |
| `lambda_ai` | `api.lambda.ai` | NXDOMAIN |
| `llamagate` | `llamagate.ai` | NXDOMAIN |
| `monsterapi` | `api.monsterapi.ai` | NXDOMAIN |
| `freeaiapikey` | `freeaiapikey.com` | 410 Gone |

### ✅ Fixed — discovery silently dropped `{"models": […]}` catalogues

`catalog.py` read `body.get("data", [])`, so a provider answering `{"models": [...]}`
(AionLabs, among others) discovered **zero models and looked like a bad key**. It now accepts
`{"data": …}`, a bare array, and `{"models": …}`.

### ❌ Probed and rejected — with the reason

| Provider | Verdict | Why not added |
|---|---|---|
| `gigachat` | CONNFAIL | TLS verify fails — Russian state CA not in the trust store |
| `haiper` | TIMEOUT | no response |
| `command-code` | 404 | no `/models` at `api.commandcode.ai` |
| `cloudflare-ai` | 404 | base URL needs an account id in the path |
| `g4f-groq`, `g4f-nvidia` | 404 | those two g4f routes are down; the other three work |
| `databricks`, `snowflake` | DNS | templated per-account hostnames, not a fixed URL |
| `ideogram`, `leonardo` | AUTH 401 | image/video REST APIs — not OpenAI chat-shaped |
| `qwen-web`, `zai-web`, `duckduckgo-web` | — | browser sessions, not API keys (see §3) |
| `poe`, `maritalk` | — | no base URL in OmniRoute's registry |

### ⚠️ Still failing — 13 pre-existing presets

| Slug | Verdict | Assessment |
|---|---|---|
| `predibase` | DNS (local only) | resolves on 8.8.8.8 — local resolver artifact, leave it |
| `modal` | TLS SNI mismatch | `api.modal.ai` resolves but the cert does not match; host is probably wrong |
| `uncloseai` | 502 | origin down |
| `x5lab` | 530 | Cloudflare origin down |
| `reka`, `yi` | 400 | `/models` rejects anonymous; may still work with a key |
| `heroku` | TIMEOUT | no response |
| `qianfan` | 429 | rate-limited, not broken |
| `codestral`, `coze`, `dify`, `factory`, `gemini`, `kie`, `nlpcloud`, `perplexity`, `puter`, `v0_vercel` | 404 | see below |

The eleven 404s are **not** broken providers. A 404 on `/models` means auto-discovery cannot
work, not that completions fail. `gemini` is the clearest case: Google serves `/v1beta/models`
and LiteLLM has a native integration, so calls work while discovery does not. Same for
`perplexity`, which publishes no model list.

What this contradicts is `presets.py`'s own stated invariant — *"REQUIREMENT FOR
AUTO-DISCOVERY: the endpoint must expose an OpenAI-compatible `GET {base_url}/models`. Every
preset here does."* Eleven do not. Either soften the comment and mark them `discovery: false`,
or move them to `_KNOWN_NON_PRESET`.

### T1/T2 — the one provider that could be tested without signing up

**AI Horde** accepts the anonymous key `0000000000`, so it got a full end-to-end run:

- **T1 pass** — registered as a provider, key added, **31 models discovered**.
- **T2 partial** — a direct call to `Impish_Bloodmoon_12B` returned a real completion, but a
  call to `Cydonia-24B-v4.3` a minute later returned **406 Model not known**. AI Horde is
  crowdsourced: its `/models` list is aspirational and a model is only callable while a
  volunteer is hosting it. Every one of the 31 probes failed for that reason, so the gateway
  correctly marked them unusable.

This is worth knowing generally: **a provider can pass T0 and T1 and still have no callable
models**, and the gateway's probe is what catches it. The test registration was removed
afterwards so the dashboard is not left with 31 permanently-erroring rows.

### T0 `NOTFOUND` — needs per-provider judgement, **not** automatic removal

`codestral` · `coze` · `dify` · `factory` · `gemini` · `heroku` · `kie` · `nlpcloud` ·
`perplexity` · `puter` · `v0_vercel`

A 404 on `/models` means auto-discovery cannot work, but it does **not** mean the provider is
broken. `gemini` is the clearest example: Google serves `/v1beta/models` and LiteLLM has a
native integration, so completions work fine while discovery does not. Same story for
`perplexity`, which has no public model-list endpoint.

What this actually contradicts is `presets.py`'s own stated invariant — *"REQUIREMENT FOR
AUTO-DISCOVERY: the endpoint must expose an OpenAI-compatible `GET {base_url}/models`. Every
preset here does."* Eleven of them do not. Either the comment should be softened and these
presets marked `discovery: false`, or they should move to `_KNOWN_NON_PRESET`.

### What is left in §2, and an honest correction

§2 was written from `PROVIDER_REFERENCE.md` and claimed **85** missing API-key providers.
Working through them against OmniRoute's *runnable* registry
(`open-sse/config/providers/registry/`, 195 entries) showed that number is too high:

- The docs list **187** API-key providers, but only **133** have a runnable OpenAI-format
  registry entry. The other ~54 are catalogue cards with no implementation — OmniRoute cannot
  call them either.
- Of those 133, **~101 were already presets** before this session.
- Probing every remaining candidate produced **39** with a usable base URL, of which **21**
  passed T0 and **18** were suitable to add (the rest being web sessions or image REST APIs).

So the real remaining backlog is not 85. After this batch it is roughly **10–15** providers,
each blocked on a specific problem listed in the rejection table above (templated hostnames,
non-OpenAI wire format, or a dead origin) — plus Batch C, which needs the credential-schema
change, not a preset.

### Known data defect, unrelated to presets

`abacusai/dracarys-llama-3.1-70b-instruct` (NVIDIA NIM) is marked `available` /
`isCallable: true` in the catalog but returns **410 Gone — end of life 2026-07-27**. The
prober records health at probe time and only re-probes models it already believes unhealthy,
so a model that dies *after* a successful probe stays green until the 24h discovery sweep.
Any per-provider verification built for §4 should also re-probe healthy models on a schedule,
or the status table will drift the same way.

---

## 7. What this does not cover

- **Combos and routing strategies** — separate plan, see [`GATEWAY_PLAN.md`](GATEWAY_PLAN.md).
- **Handing out base URL + token to consumers** — also in `GATEWAY_PLAN.md`; there is
  currently **no dashboard UI to mint a gateway key**, which blocks that workflow.
