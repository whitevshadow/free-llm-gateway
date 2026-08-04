"""
Known-provider presets — the dropdown in "Add a provider".

Each preset is just a base_url plus the LiteLLM prefix, so the admin only has to
paste an API key. Anything not listed here is added as CUSTOM, where the admin
supplies the base_url themselves.

WHAT A PRESET IS NOT: it is not a key. Providers are global destinations; keys
belong to users. A preset saves you typing a URL, nothing more.

REQUIREMENT FOR AUTO-DISCOVERY: the endpoint must expose an OpenAI-compatible
`GET {base_url}/models`. Every preset here does. A custom provider that doesn't
can still be added — it just won't auto-discover, and its models have to be added
by hand.

`slug` doubles as the LiteLLM prefix, which is why it must match what LiteLLM
expects ('nvidia_nim', not 'nvidia'). Get it wrong and every call to that provider
is routed to a prefix LiteLLM doesn't recognise. The one escape hatch is
`custom_llm_provider` (see below): a slug LiteLLM has never heard of is fine as
long as we force it onto the generic OpenAI integration and strip the prefix
back off before the call.

PROVENANCE: the first nine PRESETS entries and LongCat are hand-written. The rest
were imported from OmniRoute's provider registry — see the block comments at each
import site for what was filtered out and why.
"""

from typing import Dict, List, Optional

# ── FREE-TIER PROVIDERS ONLY ─────────────────────────────────────────────────
#  These are the ones in the dropdown. The rule is deliberate: presets are the
#  providers a user can sign up for and use at $0, which is what this gateway is
#  for. Trial-credit providers (Fireworks, Together, SambaNova, Nebius, …) and
#  paid ones (OpenAI, DeepSeek direct) are NOT presets — add them as Custom, so
#  the dropdown never implies "free" about something that isn't.
#
#  Every entry exposes an OpenAI-compatible `GET {base_url}/models`, which is what
#  auto-discovery calls. `slug` is also the LiteLLM routing prefix, so it must be
#  a prefix LiteLLM recognises — that is why these use canonical ids
#  ('nvidia_nim', 'gemini') rather than friendlier spellings.
PRESETS: List[Dict[str, Optional[str]]] = [
    {
        "slug": "groq",
        "name": "Groq",
        "base_url": "https://api.groq.com/openai/v1",
        "docs_url": "https://console.groq.com/keys",
        "hint": "Free. Very fast. Llama, Qwen, gpt-oss, Whisper.",
    },
    {
        "slug": "cerebras",
        "name": "Cerebras",
        "base_url": "https://api.cerebras.ai/v1",
        "docs_url": "https://cloud.cerebras.ai",
        "hint": "Free. Fastest inference. gpt-oss-120b, Llama.",
    },
    {
        "slug": "nvidia_nim",
        "name": "NVIDIA NIM",
        "base_url": "https://integrate.api.nvidia.com/v1",
        "docs_url": "https://build.nvidia.com",
        "hint": "Free dev tier (phone verify). Large catalog + embeddings.",
    },
    {
        "slug": "openrouter",
        "name": "OpenRouter",
        "base_url": "https://openrouter.ai/api/v1",
        "docs_url": "https://openrouter.ai/keys",
        "hint": "Free ':free' models across many labs, one key.",
    },
    {
        "slug": "gemini",
        "name": "Google AI Studio (Gemini)",
        # Same trap as Cohere: this OpenAI-compat URL exists for DISCOVERY
        # ({base}/models lists the catalog). litellm's `gemini/` prefix is a
        # NATIVE integration that treats api_base as the literal endpoint —
        # sending this URL 404s every call, so native_routing omits it.
        "base_url": "https://generativelanguage.googleapis.com/v1beta/openai",
        "native_routing": True,
        "docs_url": "https://aistudio.google.com/apikey",
        "hint": "Free tier. Gemini 2.5/3 Flash, Gemma.",
    },
    {
        "slug": "mistral",
        "name": "Mistral",
        "base_url": "https://api.mistral.ai/v1",
        "docs_url": "https://console.mistral.ai/api-keys",
        "hint": "Free experiment tier (opt-in to training). Mistral + Codestral.",
    },
    {
        "slug": "cohere",
        "name": "Cohere",
        # The compatibility URL exists ONLY for discovery ({base}/models returns
        # OpenAI-shaped JSON). It must NOT be sent as api_base on calls: litellm's
        # `cohere/` prefix is a NATIVE integration that POSTs to api_base verbatim
        # with Cohere's own protocol, so pointing it here 404s every model.
        # native_routing tells the prober and Router to omit api_base and let
        # litellm use Cohere's real endpoint, which it knows itself.
        "base_url": "https://api.cohere.ai/compatibility/v1",
        "native_routing": True,
        "docs_url": "https://dashboard.cohere.com/api-keys",
        "hint": "Free. 1,000 requests/month. Command family + embeddings.",
    },
    {
        "slug": "github",
        "name": "GitHub Models",
        # RE-POINTED 2026-08-04. The previous host, models.github.ai, answers
        # 410 Gone on BOTH /inference/chat/completions and /catalog/models —
        # GitHub retired it and moved the service onto Azure AI Inference. The
        # replacement below returns a live catalog, so the sibling `models_url`
        # override this entry used to need is gone: {base_url}/models works.
        # (The catalog here answers with a bare JSON array rather than
        # {"data": [...]}, which catalog.py already tolerates.)
        "base_url": "https://models.inference.ai.azure.com",
        # Straight to the FINE-GRAINED token page: a classic ghp_ token cannot
        # carry the "Models" permission and gets a bare 401 from inference, which
        # looks like a broken gateway. The requirement lives in the hint so it is
        # visible at the exact moment someone is about to paste the wrong kind.
        "docs_url": "https://github.com/settings/personal-access-tokens/new",
        "hint": (
            "Free. Needs a FINE-GRAINED PAT (github_pat_…) with account "
            "permission “Models: read” — classic ghp_ tokens get 401."
        ),
    },
    {
        "slug": "huggingface",
        "name": "HuggingFace Inference",
        "base_url": "https://router.huggingface.co/v1",
        "docs_url": "https://huggingface.co/settings/tokens",
        "hint": "Free monthly credits across many open models.",
    },
    # ── Imported from the OmniRoute provider catalog ─────────────────────────
    #  Everything below was derived from OmniRoute's provider registry
    #  (open-sse/config/providers/registry/), filtered down to the entries this
    #  gateway can actually serve: format 'openai' with an api-key auth type and
    #  a base_url ending in /chat/completions, so stripping that suffix yields a
    #  real OpenAI-compatible base with a `GET {base}/models` catalog. Providers
    #  whose OmniRoute entry is OAuth, cookie/web-session, or a non-OpenAI wire
    #  format (claude, gemini, cursor, kiro, …) are NOT here — nothing in this
    #  backend speaks those, so listing them would only produce broken keys.
    #
    #  `custom_llm_provider: "openai"` appears on every entry whose slug is not
    #  in litellm.openai_compatible_providers. That list is litellm's own
    #  statement about which prefixes are a plain OpenAI client honouring
    #  api_base; for anything outside it we must force the generic integration
    #  rather than hope a native one exists, exactly as LongCat does below.
    {
        "slug": "ai21",
        "name": "AI21 Labs",
        "base_url": "https://api.ai21.com/studio/v1",
        "docs_url": "https://www.ai21.com",
        "hint": (
            "$10 trial credits on signup (valid 3 months), no credit card "
            "required"
        ),
    },
    {
        "slug": "api_airforce",
        "name": "Api.airforce",
        "base_url": "https://api.airforce/v1",
        "custom_llm_provider": "openai",
        "docs_url": "https://api.airforce",
        "hint": (
            "55 free tier models including Grok-3, Claude 3.7, Qwen3, Kimi-K2, "
            "Gemini 2.5 Flash, DeepSeek-V3"
        ),
    },
    {
        "slug": "baichuan",
        "name": "Baichuan",
        "base_url": "https://api.baichuan-ai.com/v1",
        "custom_llm_provider": "openai",
        "docs_url": "https://baichuan.com",
        "hint": "Free Baichuan models. Popular Chinese LLM startup.",
    },
    {
        "slug": "qianfan",
        "name": "Baidu (ERNIE)",
        "base_url": "https://qianfan.baidubce.com/v2",
        "custom_llm_provider": "openai",
        "docs_url": "https://ernie.baidu.com/",
        "hint": "Free ERNIE Speed/Lite models. China's #2 LLM.",
    },
    {
        "slug": "baseten",
        "name": "Baseten",
        "base_url": "https://inference.baseten.co/v1",
        "docs_url": "https://baseten.co",
        "hint": "$30 free trial credits for GPU inference",
    },
    {
        "slug": "bazaarlink",
        "name": "BazaarLink",
        "base_url": "https://bazaarlink.ai/api/v1",
        "custom_llm_provider": "openai",
        "docs_url": "https://bazaarlink.ai",
        "hint": (
            "Free tier: 4M tokens/day per account with auto:free routing — zero- "
            "cost inference, no credit card required."
        ),
    },
    {
        "slug": "blackbox",
        "name": "Blackbox AI",
        "base_url": "https://api.blackbox.ai/v1",
        "custom_llm_provider": "openai",
        "docs_url": "https://blackbox.ai",
        "hint": (
            "Free tier: unlimited basic chat plus Minimax-M2.5, no credit card "
            "required"
        ),
    },
    {
        "slug": "bluesminds",
        "name": "BluesMinds",
        "base_url": "https://api.bluesminds.com/v1",
        "custom_llm_provider": "openai",
        "docs_url": "https://www.bluesminds.com",
        "hint": (
            "Free daily pi credits — supports 200+ models including GPT-4o, "
            "GPT-4.1, Claude Sonnet 4.5, Gemini 2.0 Flash, DeepSeek V4, Qwen, "
            "Kimi K2"
        ),
    },
    {
        "slug": "byteplus",
        "name": "BytePlus ModelArk",
        "base_url": "https://ark.ap-southeast.bytepluses.com/api/v3",
        "custom_llm_provider": "openai",
        "docs_url": "https://console.byteplus.com/ark",
        "hint": "Free tier.",
    },
    {
        "slug": "bytez",
        "name": "Bytez",
        "base_url": "https://api.bytez.com/models/v2/openai/v1",
        "custom_llm_provider": "openai",
        "docs_url": "https://bytez.com",
        "hint": "$1 free credits, refreshes every 4 weeks",
    },
    {
        "slug": "charm_hyper",
        "name": "Charm Hyper",
        "base_url": "https://hyper.charm.land/v1",
        "custom_llm_provider": "openai",
        "docs_url": "https://hyper.charm.land",
        "hint": "100 free monthly Hypercredits on signup",
    },
    {
        "slug": "coze",
        "name": "Coze",
        "base_url": "https://api.coze.com/v1",
        "custom_llm_provider": "openai",
        "docs_url": "https://coze.com",
        "hint": "Free ByteDance agent platform. Bot building + LLM access.",
    },
    {
        "slug": "dahl",
        "name": "Dahl",
        "base_url": "https://inference.dahl.global/v1",
        "custom_llm_provider": "openai",
        "docs_url": "https://inference.dahl.global",
        "hint": (
            "Free — MiniMax M2.7, Kimi K2.6. Click 'Add Account' to auto-generate "
            "a token."
        ),
    },
    {
        "slug": "deepinfra",
        "name": "DeepInfra",
        "base_url": "https://api.deepinfra.com/v1/openai",
        "docs_url": "https://deepinfra.com",
        "hint": "Free signup credits for API testing and model exploration",
    },
    {
        "slug": "deepseek",
        "name": "DeepSeek",
        "base_url": "https://api.deepseek.com/v1",
        "docs_url": "https://platform.deepseek.com",
        "hint": "5M free tokens on signup - no credit card required",
    },
    {
        "slug": "dgrid",
        "name": "DGrid",
        "base_url": "https://api.dgrid.ai/v1",
        "custom_llm_provider": "openai",
        "docs_url": "https://dgrid.ai",
        "hint": "DGrid Free Models Router: 10 requests/minute and 100 requests/day.",
    },
    {
        "slug": "dify",
        "name": "Dify",
        "base_url": "https://api.dify.ai/v1",
        "custom_llm_provider": "openai",
        "docs_url": "https://dify.ai",
        "hint": "Free open-source AI app builder + RAG platform.",
    },
    {
        "slug": "featherless_ai",
        "name": "Featherless AI",
        "base_url": "https://api.featherless.ai/v1",
        "docs_url": "https://featherless.ai",
        "hint": "Free tier available — no credit card required",
    },
    {
        "slug": "fireworks_ai",
        "name": "Fireworks AI",
        "base_url": "https://api.fireworks.ai/inference/v1",
        "docs_url": "https://fireworks.ai",
        "hint": "$1 free starter credits on signup for API testing",
    },
    {
        "slug": "freemodel_dev",
        "name": "FreeModel.dev",
        "base_url": "https://api.freemodel.dev/v1",
        "custom_llm_provider": "openai",
        "docs_url": "https://freemodel.dev",
        "hint": (
            "$300 free credits on signup — no credit card required. Access "
            "GPT-5.4 and GPT-5.5 (OpenAI's latest flagship models) through an "
            "OpenAI-compatible API."
        ),
    },
    {
        "slug": "freetheai",
        "name": "FreeTheAi",
        "base_url": "https://api.freetheai.xyz/v1",
        "custom_llm_provider": "openai",
        "docs_url": "https://freetheai.xyz",
        "hint": "Free OpenAI-compatible gateway — sign up via Discord for an API key.",
    },
    {
        "slug": "friendliai",
        "name": "FriendliAI",
        "base_url": "https://api.friendli.ai/serverless/v1",
        "docs_url": "https://friendli.ai",
        "hint": "Free tier for serverless inference — no credit card required",
    },
    {
        "slug": "hackclub",
        "name": "Hackclub AI",
        "base_url": "https://ai.hackclub.com/proxy/v1",
        "custom_llm_provider": "openai",
        "docs_url": "https://ai.hackclub.com",
        "hint": "Free AI for Hack Club members — 30+ models, no credit card.",
    },
    {
        "slug": "hyperbolic",
        "name": "Hyperbolic",
        "base_url": "https://api.hyperbolic.xyz/v1",
        "docs_url": "https://hyperbolic.xyz",
        "hint": "$1-5 trial credits on signup for serverless inference",
    },
    {
        "slug": "iflytek",
        "name": "iFlytek Spark",
        "base_url": "https://spark-api-open.xf-yun.com/v1",
        "custom_llm_provider": "openai",
        "docs_url": "https://xinghuo.xfyun.cn",
        "hint": (
            "Spark Lite is free (2 QPS rate-limited), but iFlytek ToS §2.4(3) "
            "prohibits programmatic extraction and requires Chinese real-name "
            "auth — use with…"
        ),
    },
    {
        "slug": "inference_net",
        "name": "Inference.net",
        "base_url": "https://api.inference.net/v1",
        "custom_llm_provider": "openai",
        "docs_url": "https://inference.net",
        "hint": "$25 free credits on signup plus research grants available",
    },
    {
        "slug": "liquid",
        "name": "Liquid AI",
        "base_url": "https://inference.liquid.ai/v1",
        "custom_llm_provider": "openai",
        "docs_url": "https://liquid.ai",
        "hint": (
            "Free LFM2.5-1.2B-Thinking and LFM2.5-1.2B-Instruct models. MIT "
            "spinoff, hybrid architecture."
        ),
    },
    {
        "slug": "llm7",
        "name": "LLM7.io",
        "base_url": "https://api.llm7.io/v1",
        "custom_llm_provider": "openai",
        "docs_url": "https://llm7.io",
        "hint": "No signup required - 2 req/s, 20 RPM, 100 req/hr free tier",
    },
    {
        "slug": "modal",
        "name": "Modal",
        "base_url": "https://api.modal.ai/v1",
        "custom_llm_provider": "openai",
        "docs_url": "https://modal.com/docs",
        "hint": "$30/month free credits for new accounts",
    },
    {
        "slug": "modelscope",
        "name": "ModelScope",
        "base_url": "https://api-inference.modelscope.cn/v1",
        "docs_url": "https://modelscope.cn",
        "hint": "Free tier via ModelScope API-Inference — Alibaba account required.",
    },
    {
        "slug": "morph",
        "name": "Morph",
        "base_url": "https://api.morphllm.com/v1",
        "docs_url": "https://morphllm.com",
        "hint": "Free tier: 250K credits/month, $0",
    },
    {
        "slug": "nlpcloud",
        "name": "NLP Cloud",
        "base_url": "https://api.nlpcloud.io/v1",
        "custom_llm_provider": "openai",
        "docs_url": "https://docs.nlpcloud.com",
        "hint": "Trial credits for new accounts",
    },
    {
        "slug": "nous_research",
        "name": "Nous Research",
        "base_url": "https://inference-api.nousresearch.com/v1",
        "custom_llm_provider": "openai",
        "docs_url": "https://portal.nousresearch.com/help",
        "hint": "Free tier: 50 RPM, 500,000 TPM — no credit card",
    },
    {
        "slug": "novita",
        "name": "Novita AI",
        "base_url": "https://api.novita.ai/openai/v1",
        "docs_url": "https://novita.ai",
        "hint": "$0.50 trial credits on signup (valid about 1 year)",
    },
    {
        "slug": "nscale",
        "name": "nScale",
        "base_url": "https://inference.api.nscale.com/v1",
        "docs_url": "https://nscale.com",
        "hint": "$5 free credits on signup for inference testing",
    },
    {
        "slug": "ollama_cloud",
        "name": "Ollama Cloud",
        "base_url": "https://ollama.com/v1",
        "custom_llm_provider": "openai",
        "docs_url": "https://ollama.com/settings/keys",
        "hint": "Free tier.",
    },
    {
        "slug": "openadapter",
        "name": "OpenAdapter",
        "base_url": "https://api.openadapter.in/v1",
        "custom_llm_provider": "openai",
        "docs_url": "https://openadapter.dev",
        "hint": (
            "Free tier with a generous quota and no credit card — 15+ open-source "
            "models with daily quota. Get your API key at "
            "https://dashboard.openadapter.in."
        ),
    },
    {
        "slug": "pioneer",
        "name": "Pioneer AI",
        "base_url": "https://api.pioneer.ai/v1",
        "custom_llm_provider": "openai",
        "docs_url": "https://pioneer.ai",
        "hint": "$75 free usage credits — no credit card required",
    },
    {
        "slug": "pollinations",
        "name": "Pollinations AI",
        "base_url": "https://gen.pollinations.ai/v1",
        "custom_llm_provider": "openai",
        "docs_url": "https://pollinations.ai",
        "hint": (
            "Free keyless tier: openai, openai-fast, openai-large, qwen-coder, "
            "mistral, deepseek, grok, gemini-flash-lite-3.1, perplexity-fast,…"
        ),
    },
    {
        "slug": "puter",
        "name": "Puter AI",
        "base_url": "https://api.puter.com/puterai/openai/v1",
        "custom_llm_provider": "openai",
        "docs_url": "https://puter.com",
        "hint": (
            "500+ models (GPT-5, Claude Opus 4, Gemini 3 Pro, Grok 4, DeepSeek "
            "V3...) — Users pay via free Puter account"
        ),
    },
    {
        "slug": "qoder",
        "name": "Qoder",
        "base_url": "https://api.qoder.com/v1",
        "custom_llm_provider": "openai",
        "hint": "Free tier.",
    },
    {
        "slug": "reka",
        "name": "Reka",
        "base_url": "https://api.reka.ai/v1",
        "custom_llm_provider": "openai",
        "docs_url": "https://docs.reka.ai/chat/overview",
        "hint": "$10/month recurring free API credits",
    },
    {
        "slug": "sambanova",
        "name": "SambaNova",
        "base_url": "https://api.sambanova.ai/v1",
        "docs_url": "https://sambanova.ai",
        "hint": "$5 free credits on signup (30-day validity), no credit card required",
    },
    {
        "slug": "scaleway",
        "name": "Scaleway AI",
        "base_url": "https://api.scaleway.ai/v1",
        "custom_llm_provider": "openai",
        "docs_url": "https://www.scaleway.com/en/docs/ai-data/generative-apis/",
        "hint": (
            "1M free tokens for new accounts — EU/GDPR compliant (Paris), Qwen3 "
            "235B & Llama 70B"
        ),
    },
    {
        "slug": "sensenova",
        "name": "SenseNova",
        "base_url": "https://token.sensenova.cn/v1",
        "custom_llm_provider": "openai",
        "docs_url": "https://platform.sensenova.cn",
        "hint": "Free SenseTime models. Computer vision leader.",
    },
    {
        "slug": "stepfun",
        "name": "StepFun",
        "base_url": "https://api.stepfun.com/v1",
        "custom_llm_provider": "openai",
        "docs_url": "https://stepfun.com",
        "hint": "Free Step-2 models. Chinese AI company.",
    },
    {
        "slug": "tencent",
        "name": "Tencent Hunyuan",
        "base_url": "https://api.hunyuan.cloud.tencent.com/v1",
        "docs_url": "https://hunyuan.tencent.com",
        "hint": "Free Hunyuan Lite models. WeChat ecosystem.",
    },
    {
        "slug": "tokenrouter",
        "name": "TokenRouter",
        "base_url": "https://api.tokenrouter.com/v1",
        "custom_llm_provider": "openai",
        "docs_url": "https://tokenrouter.com",
        "hint": (
            "Free tier includes the MiniMax 3 model. Get your API key at "
            "https://tokenrouter.com."
        ),
    },
    {
        "slug": "uncloseai",
        "name": "UncloseAI",
        "base_url": "https://hermes.ai.unturf.com/v1",
        "custom_llm_provider": "openai",
        "docs_url": "https://uncloseai.com",
        "hint": (
            "Free forever — no signup, no credit card. OpenAI-compatible "
            "endpoints."
        ),
    },
    {
        "slug": "volcengine",
        "name": "Doubao",
        "base_url": "https://ark.cn-beijing.volces.com/api/v3",
        "docs_url": "https://doubao.com",
        "hint": "Free Doubao models. ByteDance's chatbot.",
    },
    {
        "slug": "zenmux",
        "name": "ZenMux",
        "base_url": "https://zenmux.ai/api/v1",
        "custom_llm_provider": "openai",
        "docs_url": "https://zenmux.ai",
        "hint": (
            "Free tier includes access to Gemini 3 Flash, DeepSeek V3.2, Grok 4.1 "
            "Fast, Mistral Large, and more. Get your API key at "
            "https://zenmux.ai."
        ),
    },
]

# ── KNOWN, BUT NOT FREE ───────────────────────────────────────────────────────
#  Metadata for providers that are NOT offered in the "Add a provider" dropdown
#  — they're paid/credit-based, and putting them in PRESETS would misrepresent
#  the dropdown's "free at $0" promise — but that still benefit from base_url
#  auto-fill and the custom_llm_provider plumbing below once an admin adds them
#  by hand as CUSTOM. Merged into the same lookup as PRESETS; never returned by
#  GET /v1/admin/provider-presets (that endpoint reads PRESETS directly).
_KNOWN_NON_PRESET: List[Dict[str, Optional[str]]] = [
    {
        "slug": "longcat",
        "name": "LongCat",
        "base_url": "https://api.longcat.chat/openai/v1",
        "docs_url": "https://longcat.chat/platform/docs/",
        # litellm has no native 'longcat' integration, so it must be forced
        # through the generic OpenAI-compatible client via custom_llm_provider.
        # Once forced, litellm sends `model` to the upstream API VERBATIM —
        # call_model_id() below strips the 'longcat/' prefix accordingly, or
        # every call 404s against the real endpoint.
        "custom_llm_provider": "openai",
        "hint": "Paid (Token Pack / pay-as-you-go). LongCat-2.0, 1M context.",
    },
    # ── Imported from the OmniRoute provider catalog (paid / credit-based) ───
    #  Same derivation as the OmniRoute block in PRESETS, for providers whose
    #  catalog entry carries no free tier. They stay out of the dropdown so its
    #  "free at $0" promise holds, but keep base_url auto-fill and the
    #  custom_llm_provider plumbing once an admin adds them as Custom.
    {
        "slug": "aiml",
        "name": "AI/ML API",
        "base_url": "https://api.aimlapi.com/v1",
        "docs_url": "https://aimlapi.com",
        "hint": (
            "Paid. Free tier paused (2026) — AI/ML API is now pay-as-you-go only "
            "(min $20 top-up); no recurring free credits."
        ),
    },
    {
        "slug": "bai",
        "name": "b.ai",
        "base_url": "https://api.b.ai/v1",
        "custom_llm_provider": "openai",
        "docs_url": "https://b.ai",
        "hint": "Paid / credit-based.",
    },
    {
        "slug": "chenzk",
        "name": "Chenzk API",
        "base_url": "https://chenzk.top/v1",
        "custom_llm_provider": "openai",
        "docs_url": "https://chenzk.top",
        "hint": (
            "Paid. Create an API key at https://chenzk.top/token, then paste it "
            "here as a Bearer token."
        ),
    },
    {
        "slug": "chutes",
        "name": "Chutes.ai",
        "base_url": "https://llm.chutes.ai/v1",
        "docs_url": "https://chutes.ai",
        "hint": (
            "No free tier as of 2026 — Chutes moved to pay-as-you-go (free Early "
            "Access ended 2026-03)."
        ),
    },
    {
        "slug": "codestral",
        "name": "Codestral",
        "base_url": "https://codestral.mistral.ai/v1",
        "docs_url": "https://mistral.ai",
        "hint": "Paid / credit-based.",
    },
    {
        "slug": "crof",
        "name": "CrofAI",
        "base_url": "https://crof.ai/v1",
        "custom_llm_provider": "openai",
        "docs_url": "https://crof.ai",
        "hint": "Paid / credit-based.",
    },
    {
        "slug": "dashscope",
        "name": "Alibaba Cloud Model Studio",
        "base_url": "https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
        "docs_url": "https://bailian.console.alibabacloud.com/",
        "hint": (
            "Paid. Use a Model Studio API key and select its Singapore or Beijing "
            "region."
        ),
    },
    {
        "slug": "dit",
        "name": "DIT.ai",
        "base_url": "https://api.dit.ai/v1",
        "custom_llm_provider": "openai",
        "docs_url": "https://dit.ai",
        "hint": (
            "Paid. dit.ai (Distributed Intelligence Trade) is an OpenAI- "
            "compatible router/gateway with dynamic per-request pricing, exposing "
            "/v1/chat/completions at…"
        ),
    },
    {
        "slug": "factory",
        "name": "Factory",
        "base_url": "https://api.factory.ai/v1",
        "custom_llm_provider": "openai",
        "docs_url": "https://factory.ai",
        "hint": (
            "Paid. Get your Factory API key at "
            "https://app.factory.ai/settings/api-keys, then paste it as a Bearer "
            "token. OpenAI-compatible endpoint at…"
        ),
    },
    {
        "slug": "heroku",
        "name": "Heroku AI",
        "base_url": "https://us.inference.heroku.com/v1",
        "custom_llm_provider": "openai",
        "docs_url": "https://www.heroku.com",
        "hint": "Paid / credit-based.",
    },
    {
        "slug": "kenari",
        "name": "Kenari",
        "base_url": "https://kenari.id/v1",
        "custom_llm_provider": "openai",
        "docs_url": "https://kenari.id",
        "hint": (
            "Paid. Kenari exposes an OpenAI-compatible chat completions endpoint "
            "at https://kenari.id/v1/chat/completions, plus a live /v1/models "
            "catalog covering…"
        ),
    },
    {
        "slug": "kie",
        "name": "KIE.AI",
        "base_url": "https://api.kie.ai/v1",
        "custom_llm_provider": "openai",
        "docs_url": "https://kie.ai",
        "hint": "Paid / credit-based.",
    },
    {
        "slug": "kilo_gateway",
        "name": "Kilo Gateway",
        "base_url": "https://api.kilo.ai/api/gateway",
        "custom_llm_provider": "openai",
        "docs_url": "https://kilo.ai",
        "hint": "Paid / credit-based.",
    },
    {
        "slug": "moonshot",
        "name": "Kimi (Legacy Moonshot API)",
        "base_url": "https://api.moonshot.ai/v1",
        "docs_url": "https://platform.kimi.ai?aff=omniroute",
        "hint": "Paid / credit-based.",
    },
    {
        "slug": "meta_llama",
        "name": "Meta Llama API",
        "base_url": "https://api.llama.com/compat/v1",
        "docs_url": "https://llama.developer.meta.com",
        "hint": "Paid / credit-based.",
    },
    {
        "slug": "nanogpt",
        "name": "NanoGPT",
        "base_url": "https://nano-gpt.com/api/v1",
        "custom_llm_provider": "openai",
        "docs_url": "https://nano-gpt.com",
        "hint": "Paid / credit-based.",
    },
    {
        "slug": "nube",
        "name": "Nube.sh",
        "base_url": "https://ai.nube.sh/api/v1",
        "custom_llm_provider": "openai",
        "docs_url": "https://nube.sh",
        "hint": "Paid / credit-based.",
    },
    {
        "slug": "openai",
        "name": "OpenAI",
        "base_url": "https://api.openai.com/v1",
        "custom_llm_provider": "openai",
        "docs_url": "https://platform.openai.com",
        "hint": "Paid / credit-based.",
    },
    {
        "slug": "ovhcloud",
        "name": "OVHcloud AI",
        "base_url": "https://oai.endpoints.kepler.ai.cloud.ovh.net/v1",
        "custom_llm_provider": "openai",
        "docs_url": "https://www.ovhcloud.com",
        "hint": "Paid / credit-based.",
    },
    {
        "slug": "perplexity",
        "name": "Perplexity",
        "base_url": "https://api.perplexity.ai",
        "docs_url": "https://www.perplexity.ai",
        "hint": "Paid / credit-based.",
    },
    {
        "slug": "predibase",
        "name": "Predibase",
        "base_url": "https://serving.app.predibase.com/v1",
        "custom_llm_provider": "openai",
        "docs_url": "https://predibase.com",
        "hint": "Paid. $25 free trial credits (30-day validity)",
    },
    {
        "slug": "publicai",
        "name": "PublicAI",
        "base_url": "https://api.publicai.co/v1",
        "docs_url": "https://publicai.co",
        "hint": "Paid. Requires an API key — one-time signup credit, then paid",
    },
    {
        "slug": "qiniu",
        "name": "Qiniu",
        "base_url": "https://api.qnaigc.com/v1",
        "custom_llm_provider": "openai",
        "docs_url": "https://www.qiniu.com",
        "hint": (
            "Paid. Create a Qiniu AI inference API key at "
            "https://portal.qiniu.com/ai-inference/api-key,"
        ),
    },
    {
        "slug": "qwen_cloud_token_plan",
        "name": "Qwen Cloud Token Plan",
        "base_url": "https://token-plan.ap-southeast-1.maas.aliyuncs.com/compatible-mode/v1",
        "custom_llm_provider": "openai",
        "docs_url": "https://www.qwencloud.com/pricing/token-plan",
        "hint": (
            "Paid. Use a Qwen Cloud Token Plan key and select its Singapore or "
            "Beijing region."
        ),
    },
    {
        "slug": "sumopod",
        "name": "SumoPod",
        "base_url": "https://ai.sumopod.com/v1",
        "custom_llm_provider": "openai",
        "docs_url": "https://ai.sumopod.com",
        "hint": (
            "Paid. SumoPod exposes an OpenAI-compatible chat completions endpoint "
            "at https://ai.sumopod.com/v1/chat/completions, plus a live "
            "/v1/models catalog.…"
        ),
    },
    {
        "slug": "synthetic",
        "name": "Synthetic",
        "base_url": "https://api.synthetic.new/openai/v1",
        "docs_url": "https://synthetic.new",
        "hint": "Paid / credit-based.",
    },
    {
        "slug": "upstage",
        "name": "Upstage",
        "base_url": "https://api.upstage.ai/v1",
        "custom_llm_provider": "openai",
        "docs_url": "https://www.upstage.ai",
        "hint": "Paid / credit-based.",
    },
    {
        "slug": "v0_vercel",
        "name": "v0 (Vercel)",
        "base_url": "https://api.v0.dev/v1",
        "custom_llm_provider": "openai",
        "docs_url": "https://v0.dev",
        "hint": "Paid / credit-based.",
    },
    {
        "slug": "venice",
        "name": "Venice.ai",
        "base_url": "https://api.venice.ai/api/v1",
        "custom_llm_provider": "openai",
        "docs_url": "https://venice.ai",
        "hint": "Paid / credit-based.",
    },
    {
        "slug": "vercel_ai_gateway",
        "name": "Vercel AI Gateway",
        "base_url": "https://ai-gateway.vercel.sh/v1",
        "docs_url": "https://vercel.com/docs/ai-gateway",
        "hint": "Paid / credit-based.",
    },
    {
        "slug": "wandb",
        "name": "Weights & Biases Inference",
        "base_url": "https://api.inference.wandb.ai/v1",
        "docs_url": "https://wandb.ai",
        "hint": "Paid / credit-based.",
    },
    {
        "slug": "x5lab",
        "name": "X5Lab",
        "base_url": "https://api.x5lab.dev/v1",
        "custom_llm_provider": "openai",
        "docs_url": "https://x5lab.dev",
        "hint": (
            "Paid. X5Lab exposes an OpenAI-compatible chat completions endpoint "
            "at https://api.x5lab.dev/v1/chat/completions, plus a live /v1/models "
            "catalog. OmniRoute…"
        ),
    },
    {
        "slug": "xai",
        "name": "xAI (Grok)",
        "base_url": "https://api.x.ai/v1",
        "docs_url": "https://x.ai",
        "hint": "Paid / credit-based.",
    },
    {
        "slug": "yi",
        "name": "Yi (01.AI)",
        "base_url": "https://api.lingyiwanwu.com/v1",
        "custom_llm_provider": "openai",
        "docs_url": "https://01.ai",
        "hint": (
            "No free API tier (2026) — Yi-Light retired; platform.01.ai is pay- "
            "as-you-go (Yi-Lightning paid). Open weights are download-only."
        ),
    },
    {
        "slug": "zai",
        "name": "GLM Coding",
        "base_url": "https://api.z.ai/api/coding/paas/v4",
        "docs_url": "https://z.ai/subscribe",
        "hint": "Paid / credit-based.",
    },
    # ── Added 2026-08-04, sourced from OmniRoute's registry ───────────────────
    #  Each was verified at T0 (see backend/tools/probe_presets.py): the base_url
    #  below answers `GET {base_url}/models` with a real, OpenAI-shaped catalog.
    #  That is all T0 proves — none has been exercised with a live key, so none
    #  claims a free tier and none belongs in the PRESETS dropdown, whose promise
    #  is "free at $0". They live here for base_url auto-fill when added as
    #  CUSTOM. Promote an entry to PRESETS only after confirming its free tier.
    #
    #  Every slug here is outside litellm.openai_compatible_providers, so each
    #  needs custom_llm_provider to force the generic OpenAI integration.
    {
        "slug": "opencode_zen",
        "name": "OpenCode Zen",
        "base_url": "https://opencode.ai/zen/v1",
        "custom_llm_provider": "openai",
        "docs_url": "https://opencode.ai/zen",
        "hint": "Catalog lists Claude and GPT families. Pricing unverified.",
    },
    {
        "slug": "orcarouter",
        "name": "OrcaRouter",
        "base_url": "https://api.orcarouter.ai/v1",
        "custom_llm_provider": "openai",
        "docs_url": "https://orcarouter.ai",
        "hint": (
            "Aggregator. Advertises an 'orcarouter/free' model plus paid tiers; "
            "the free tier is unverified."
        ),
    },
    {
        "slug": "gitlawb",
        "name": "Gitlawb OpenGateway",
        "base_url": "https://opengateway.gitlawb.com/v1",
        "custom_llm_provider": "openai",
        "docs_url": "https://gitlawb.com/opengateway",
        "hint": "Aggregator with an 'auto' model that routes to the cheapest capable one.",
    },
    {
        "slug": "xiaomi_mimo",
        "name": "Xiaomi MiMo",
        "base_url": "https://api.xiaomimimo.com/v1",
        "custom_llm_provider": "openai",
        "docs_url": "https://xiaomimimo.com",
        "hint": "Xiaomi's MiMo models. Catalog is auth-gated; pricing unverified.",
    },
    # ── Batch A/B, added 2026-08-04 from OmniRoute's registry ─────────────────
    #  Same rule as the block above: every base_url here was T0-verified — it
    #  answers `GET {base_url}/models` with an OpenAI-shaped catalog (or a 401,
    #  which proves the endpoint exists and gates on a key). None has been called
    #  with a live key, so none claims a free tier and none enters the PRESETS
    #  dropdown.
    #
    #  Excluded from this batch after probing, with the reason recorded in
    #  Supportedprovider.md: gigachat (Russian state CA, TLS verify fails),
    #  haiper (timeout), command-code / cloudflare-ai / g4f-groq / g4f-nvidia
    #  (404), databricks / snowflake (templated per-account hostnames),
    #  ideogram / leonardo (image REST APIs, not OpenAI chat), qwen-web /
    #  zai-web / duckduckgo-web (browser sessions, not API keys).
    {
        "slug": "together",
        "name": "Together AI",
        "base_url": "https://api.together.xyz/v1",
        "docs_url": "https://api.together.ai/settings/api-keys",
        "hint": "Trial credits on signup. Large open-model catalog.",
    },
    {
        "slug": "nebius",
        "name": "Nebius AI (Token Factory)",
        "base_url": "https://api.tokenfactory.nebius.com/v1",
        "custom_llm_provider": "openai",
        "docs_url": "https://nebius.com/services/token-factory",
        "hint": "Trial credits on signup.",
    },
    {
        "slug": "siliconflow",
        "name": "SiliconFlow",
        "base_url": "https://api.siliconflow.com/v1",
        "custom_llm_provider": "openai",
        "docs_url": "https://siliconflow.com",
        "hint": "Credit-based. Large Chinese + open-weight catalog.",
    },
    {
        "slug": "requesty",
        "name": "Requesty",
        "base_url": "https://router.requesty.ai/v1",
        "custom_llm_provider": "openai",
        "docs_url": "https://requesty.ai",
        "hint": "Aggregator routing to many upstreams under one key.",
    },
    {
        "slug": "routeway",
        "name": "Routeway",
        "base_url": "https://api.routeway.ai/v1",
        "custom_llm_provider": "openai",
        "docs_url": "https://routeway.ai",
        "hint": "Aggregator. Catalog includes Claude and GPT families.",
    },
    {
        "slug": "openvecta",
        "name": "OpenVecta",
        "base_url": "https://api.openvecta.com/v1",
        "custom_llm_provider": "openai",
        "docs_url": "https://openvecta.com",
        "hint": "Aggregator. Pricing unverified.",
    },
    {
        "slug": "nara",
        "name": "NaraRouter",
        "base_url": "https://router.bynara.id/v1",
        "custom_llm_provider": "openai",
        "docs_url": "https://bynara.id",
        "hint": "Aggregator. Pricing unverified.",
    },
    {
        "slug": "navy",
        "name": "NavyAI",
        "base_url": "https://api.navy/v1",
        "custom_llm_provider": "openai",
        "docs_url": "https://api.navy",
        "hint": "Aggregator. Catalog is public; pricing unverified.",
    },
    {
        "slug": "aion",
        "name": "Aion Labs",
        "base_url": "https://api.aionlabs.ai/v1",
        "custom_llm_provider": "openai",
        "docs_url": "https://aionlabs.ai",
        # Answers with {"models": [...]} rather than {"data": [...]}; catalog.py
        # handles that shape (it used to discover zero models silently).
        "hint": "Aion 2.0 family. Pricing unverified.",
    },
    {
        "slug": "agnes",
        "name": "Agnes AI",
        "base_url": "https://apihub.agnes-ai.com/v1",
        "custom_llm_provider": "openai",
        "docs_url": "https://agnes-ai.com",
        "hint": "Pricing unverified.",
    },
    {
        "slug": "ainative",
        "name": "AINative Studio",
        "base_url": "https://api.ainative.studio/api/v1",
        "custom_llm_provider": "openai",
        "docs_url": "https://ainative.studio",
        "hint": "Mixed chat + media catalog. Pricing unverified.",
    },
    {
        "slug": "sealion",
        "name": "SEA-LION",
        "base_url": "https://api.sea-lion.ai/v1",
        "custom_llm_provider": "openai",
        "docs_url": "https://sea-lion.ai",
        "hint": "Southeast-Asian language models (AI Singapore).",
    },
    {
        "slug": "digitalocean",
        "name": "DigitalOcean Gradient",
        "base_url": "https://inference.do-ai.run/v1",
        "custom_llm_provider": "openai",
        "docs_url": "https://digitalocean.com/products/gradient",
        "hint": "Paid. DigitalOcean's hosted inference.",
    },
    {
        "slug": "hcnsec",
        "name": "Huancheng Public API",
        "base_url": "https://api.hcnsec.cn/v1",
        "custom_llm_provider": "openai",
        "docs_url": "https://api.hcnsec.cn",
        "hint": "Community API. Pricing unverified.",
    },
    {
        "slug": "aihorde",
        "name": "AI Horde",
        "base_url": "https://oai.aihorde.net/v1",
        "custom_llm_provider": "openai",
        "docs_url": "https://aihorde.net",
        "hint": (
            "Free, crowdsourced GPU volunteers. Anonymous access works; a free "
            "API key raises priority. Queue times vary widely."
        ),
    },
    # g4f.space fronts other providers' free tiers. Unofficial by construction:
    # it is not operated by Google/Ollama/Pollinations and can break or vanish
    # without notice. Kept out of the dropdown for that reason, not just pricing.
    {
        "slug": "g4f_gemini",
        "name": "g4f.space — Gemini",
        "base_url": "https://g4f.space/api/gemini/v1",
        "custom_llm_provider": "openai",
        "docs_url": "https://g4f.space",
        "hint": "Unofficial free proxy in front of Gemini. No stability guarantee.",
    },
    {
        "slug": "g4f_ollama",
        "name": "g4f.space — Ollama",
        "base_url": "https://g4f.space/api/ollama/v1",
        "custom_llm_provider": "openai",
        "docs_url": "https://g4f.space",
        "hint": "Unofficial free proxy in front of hosted Ollama. No stability guarantee.",
    },
    {
        "slug": "g4f_pollinations",
        "name": "g4f.space — Pollinations",
        "base_url": "https://g4f.space/api/pollinations/v1",
        "custom_llm_provider": "openai",
        "docs_url": "https://g4f.space",
        "hint": "Unofficial free proxy in front of Pollinations. No stability guarantee.",
    },
]

_BY_SLUG = {p["slug"]: p for p in PRESETS + _KNOWN_NON_PRESET}


def get(slug: str) -> Optional[Dict[str, Optional[str]]]:
    return _BY_SLUG.get(slug.strip().lower())


def base_url_for(slug: str) -> Optional[str]:
    p = get(slug)
    return p["base_url"] if p else None


def call_api_base(slug: str, stored_base_url: Optional[str]) -> Optional[str]:
    """
    The api_base to pass on an ACTUAL litellm call for this provider — as opposed
    to the base_url used for discovery.

    For most providers these are the same OpenAI-compatible URL. For providers
    marked native_routing (Cohere), litellm's own integration knows the real
    endpoint and treats a supplied api_base as the literal URL to POST to — so we
    must send None or every call 404s against the discovery URL.
    """
    p = get(slug)
    if p and p.get("native_routing"):
        return None
    return stored_base_url


def custom_llm_provider_for(slug: Optional[str]) -> Optional[str]:
    """
    Force litellm onto a specific integration for providers it has no native
    prefix for (LongCat has no 'longcat/' integration in litellm). None means
    "let litellm auto-detect from the model string", which is correct for
    every ordinary preset.
    """
    p = get(slug) if slug else None
    return p.get("custom_llm_provider") if p else None


def call_model_id(slug: Optional[str], litellm_model: str) -> str:
    """
    The `model` string to actually hand to litellm for a call.

    A provider forced onto a specific integration via custom_llm_provider has
    that integration send `model` to the upstream API verbatim, so the stored
    'slug/upstream-id' form must be stripped back to the bare upstream id —
    otherwise the request 404s. Ordinary providers are unaffected: litellm's
    own prefix parsing strips its own prefix, so the full form must stay.
    """
    if slug and custom_llm_provider_for(slug) and litellm_model.startswith(f"{slug}/"):
        return litellm_model[len(slug) + 1:]
    return litellm_model
