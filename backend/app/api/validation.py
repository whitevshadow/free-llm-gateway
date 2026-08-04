"""
Check a provider key BEFORE storing it — SRS §6, §13.

WHY NOT JUST CALL /models
    That was the first attempt and it is wrong. Several providers publish their
    model catalog PUBLICLY: NVIDIA NIM returns 200 with the full list for a key
    of `nvapi-obviously-not-real`. A validator built on /models therefore passes
    every fake key for those providers, which is worse than having no validator —
    it tells the user their credential is good right up until every request 401s.

    So validation makes the same call the PROBER makes: one real, minimal,
    authenticated request. If the provider answers it, the key works.

WHY IT REUSES prober._probe_one AND _classify
    Those already encode the distinctions this endpoint has to report, and they
    are the same ones routing acts on (SRS §13.2). Duplicating the mapping here
    would let the two drift, and then "Check" would say something the router
    disagrees with. In particular:

      429  the key WORKS and is throttled. Reported valid, with a warning.
           Calling a rate-limited key "invalid" is the single most misleading
           thing this endpoint could do — the user would delete a good key.
      401/403  dead credential. The only genuine "invalid".
      404  the chosen model is not served to this key. Says nothing about the
           key, so it is reported as inconclusive rather than a failure.

WHY IT WRITES NOTHING
    "Check" runs before Save. Storing a key to test it would leave a junk row —
    and a fan-out of deployments — behind on every failed attempt.
"""

import logging
from typing import Dict, Optional

import httpx
from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.gateway_auth import current_user
from app.core.database import get_db
from app.models.enums import ModelHealth, ModelMode
from app.models.provider import Provider
from app.models.provider_model import ProviderModel
from app.models.user import User
from app.services import deepseek_web, presets

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Validation"])


# Providers whose credential is a browser session or an OAuth grant rather than
# an API key. They are unreachable from here BY CONSTRUCTION, not by omission:
#
#   web session — the credential is a cookie/bearer lifted from a signed-in tab,
#     and the endpoint behind it is the web app's private API, not an OpenAI one.
#     DeepSeek Web is the representative case: OmniRoute needs ~1,450 lines of
#     bespoke executor plus a compiled WebAssembly proof-of-work solver
#     (sha3_wasm_bg.wasm) to answer a single message — per-request PoW challenge,
#     chat-session create/delete, and a custom SSE dialect translated back into
#     OpenAI chunks. None of that is expressible as a LiteLLM provider.
#
#   oauth — needs an interactive sign-in, a client id registered with the vendor,
#     and background refresh-token rotation. That is an auth subsystem this
#     gateway does not have.
#
# Listed so /validate can say WHY instead of asking for a base URL that cannot
# exist. Sourced from frontend/src/shared/constants/providers/{web-cookie,oauth}.ts.
SESSION_AUTH_PROVIDERS: Dict[str, str] = {
    **{
        slug: "a browser web session"
        for slug in (
            # NB: `deepseek-web` is deliberately ABSENT — it is the one
            # browser-session provider this gateway implements natively
            # (services/deepseek_web.py), so it validates like any other key.
            "adapta-web", "adobe-firefly", "blackbox-web", "chatgpt-web",
            "claude-web", "copilot-m365-web", "copilot-web",
            "doubao-web", "gemini-business", "gemini-web", "grok-web",
            "hailuo-web", "huggingchat", "hyperagent", "inner-ai", "kimi-web",
            "lmarena", "microsoft-designer-web", "muse-spark-web", "notion-web",
            "perplexity-web", "poe-web", "promptql", "qwen-web", "t3-web",
            "v0-vercel-web", "venice-web", "yuanbao-web", "zai-web",
            "zenmux-free",
        )
    },
    **{
        slug: "an interactive OAuth sign-in"
        for slug in (
            "agy", "amazon-q", "antigravity", "claude", "cline", "clinepass",
            "codebuddy-cn", "codex", "cursor", "devin-cli", "ghe-copilot",
            "gitlab-duo", "grok-cli", "kilocode", "kimi-coding", "kiro",
            "qoder", "trae", "windsurf", "xai-oauth", "zed", "zed-hosted",
        )
    },
}

CATALOG_TIMEOUT = 12


class ValidateKeyRequest(BaseModel):
    provider: str = Field(..., description="Provider slug, e.g. 'nvidia_nim'")
    api_key: str = Field(..., description="The key to test. Never stored.")
    base_url: Optional[str] = Field(
        None, description="Required for a provider the gateway does not know yet."
    )
    validation_model: Optional[str] = Field(
        None, description="Model to test with. Defaults to one the gateway knows."
    )


def _models_url(slug: str, base_url: Optional[str]) -> Optional[str]:
    """
    Where this provider lists its models. Presets may override the path —
    GitHub Models publishes its catalog on a different host from completions,
    so deriving `/models` from the base URL would 404.
    """
    preset = presets.get(slug)
    if preset and preset.get("models_url"):
        return preset["models_url"]
    base = base_url or (preset or {}).get("base_url")
    return f"{base.rstrip('/')}/models" if base else None


def _catalog_ids(slug: str, base_url: Optional[str], api_key: str) -> list[str]:
    """
    Model ids the provider advertises. Used ONLY to pick something to test with
    when the gateway has no catalog of its own yet — never as proof the key is
    good, for the reason in the module docstring.
    """
    url = _models_url(slug, base_url)
    if not url:
        return []
    try:
        resp = httpx.get(
            url, headers={"Authorization": f"Bearer {api_key}"}, timeout=CATALOG_TIMEOUT
        )
        resp.raise_for_status()
        body = resp.json()
        items = body if isinstance(body, list) else body.get("data", [])
        return [m["id"] for m in items if isinstance(m, dict) and m.get("id")]
    except Exception as exc:
        logger.info("Catalog read failed while validating %s: %s", slug, exc)
        return []


class TestModelRequest(BaseModel):
    """
    Test one model the user already holds a key for.

    No api_key field on purpose: this tests a model the gateway can already
    reach, using the stored key. Accepting a key here would make it a second,
    quieter validation endpoint with different semantics.
    """

    provider: str = Field(..., description="Provider slug")
    model: str = Field(..., description="Model id — upstream or normalized")


@router.post("/v1/me/models/test")
async def test_model(
    payload: TestModelRequest,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    """
    Fire one real request at a model, with MY key — what the dashboard's
    per-model "Test" button calls.

    This is a probe, so it goes through the same code the scheduler uses and
    reports the same six statuses (SRS §13.2). It deliberately does NOT write
    the result back: a manual test is a question, and answering it should not
    silently re-rank routing. The scheduled probe owns deployment health.
    """
    from app.services.prober import _probe_one  # local: heavy import

    slug = payload.provider.strip().lower()
    row = db.query(Provider).filter(Provider.slug == slug).first()
    if not row:
        return {"ok": False, "error": f"No provider {slug!r}."}

    # Accept either the upstream id or the normalized public name — the UI has
    # both on screen and it is not obvious which one a button is holding.
    model = (
        db.query(ProviderModel)
        .filter(
            ProviderModel.provider_id == row.id,
            (ProviderModel.upstream_model_id == payload.model)
            | (ProviderModel.normalized_name == payload.model)
            | (ProviderModel.litellm_model == payload.model),
        )
        .first()
    )
    if not model:
        return {"ok": False, "error": f"{payload.model!r} is not in {row.name}'s catalog."}

    from app.services import crypto
    from app.models.provider_key import ProviderKey

    key_row = (
        db.query(ProviderKey)
        .filter(
            ProviderKey.user_id == user.id,
            ProviderKey.provider_id == row.id,
            ProviderKey.is_active.is_(True),
        )
        .order_by(ProviderKey.id)
        .first()
    )
    if not key_row:
        return {"ok": False, "error": f"You hold no active key for {row.name}."}

    result = await _probe_one(
        model.litellm_model,
        crypto.decrypt(key_row.key_ciphertext),
        presets.call_api_base(slug, row.base_url),
        mode=model.mode,
        custom_llm_provider=presets.custom_llm_provider_for(slug),
    )
    status: ModelHealth = result["status"]
    return {
        "ok": status is ModelHealth.available,
        "status": status.value,
        "http_code": result.get("http_code"),
        "latency_ms": result.get("latency_ms"),
        "error": result.get("error"),
        "model": model.normalized_name,
        "tested_model": model.litellm_model,
        "key_masked": key_row.key_masked,
    }


@router.post("/v1/me/provider-keys/validate")
async def validate_provider_key(
    payload: ValidateKeyRequest,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    """
    Test a key against its provider without saving it.

    Always returns HTTP 200 — the verdict is in the body. Returning 401 for a bad
    provider key would be indistinguishable from the CALLER's own session having
    expired, and the dashboard would log them out for pasting a typo.
    """
    from app.services.prober import _classify, _probe_one  # local: heavy import

    slug = payload.provider.strip().lower()
    key = payload.api_key.strip()

    if not key:
        return {"valid": False, "error": "No API key supplied.", "method": None}

    row = db.query(Provider).filter(Provider.slug == slug).first()
    preset = presets.get(slug)
    # A registered provider's stored base URL wins: it is what the router will
    # actually call, so validating anything else tests the wrong endpoint.
    base_url = (row.base_url if row else None) or payload.base_url or (preset or {}).get("base_url")

    # Session-auth providers must be rejected BEFORE the generic "supply a base
    # URL" message, which is actively misleading for them: there is no base URL
    # that makes a browser session work here, so that advice sends the user
    # hunting for a value that cannot exist. The dashboard renders OmniRoute's
    # full catalogue, which is why these cards are reachable at all.
    if slug in SESSION_AUTH_PROVIDERS:
        kind = SESSION_AUTH_PROVIDERS[slug]
        return {
            "valid": False,
            "error": (
                f"{slug!r} authenticates with {kind}, not an API key, so this "
                "gateway cannot call it. Requests here are made with LiteLLM over "
                "a plain OpenAI-compatible HTTP API; these providers need a "
                "browser session (or an interactive OAuth sign-in with token "
                "rotation) and a provider-specific wire protocol. No base URL "
                "will change that. See Supportedprovider.md §3."
            ),
            "method": None,
        }

    # DeepSeek Web has no base URL to speak of — it is a fixed private endpoint —
    # so it is checked with the executor's own probe, which asks DeepSeek for a
    # PoW challenge. That call 401s on a dead session token and succeeds on a
    # live one, which is exactly the question "Check token" is asking.
    if slug == deepseek_web.PROVIDER_SLUG:
        try:
            await deepseek_web.probe(f"{deepseek_web.PROVIDER_SLUG}/probe", key)
        except deepseek_web.DeepSeekWebError as exc:
            return {"valid": False, "error": str(exc), "method": "pow_challenge"}
        except Exception as exc:  # network, TLS, upstream shape change
            return {
                "valid": False,
                "error": f"Could not reach DeepSeek Web: {exc}",
                "method": "pow_challenge",
            }
        return {
            "valid": True,
            "error": None,
            "method": "pow_challenge",
            "detail": (
                "Session token accepted by chat.deepseek.com. Web session tokens "
                "expire — replace it when calls start failing with 401."
            ),
        }

    if not base_url:
        return {
            "valid": False,
            "error": (
                f"The gateway does not know {slug!r} and no base URL was supplied. "
                "Add the provider's OpenAI-compatible base URL and try again."
            ),
            "method": None,
        }

    # ── pick something to test with ──
    # Prefer a model already in the gateway's catalog: it is known to be the
    # right shape for this provider, and carries the litellm prefix and mode.
    litellm_model: Optional[str] = None
    mode = ModelMode.chat

    if payload.validation_model:
        litellm_model = payload.validation_model
    elif row:
        known = (
            db.query(ProviderModel)
            .filter(
                ProviderModel.provider_id == row.id,
                ProviderModel.enabled.is_(True),
                ProviderModel.mode == ModelMode.chat,
            )
            .order_by(ProviderModel.id)
            .first()
        )
        if known:
            litellm_model = known.litellm_model
            mode = known.mode

    catalog_count: Optional[int] = None
    if not litellm_model:
        ids = _catalog_ids(slug, base_url, key)
        catalog_count = len(ids)
        if ids:
            # slug doubles as the litellm prefix — same construction catalog.py uses.
            litellm_model = f"{slug}/{ids[0]}"

    if not litellm_model:
        return {
            "valid": False,
            "error": (
                f"Could not find a model to test {slug} with. The provider listed "
                "none, and the gateway has no catalog for it yet. Supply a "
                "validation model id."
            ),
            "method": None,
        }

    # ── the real check: one minimal authenticated request ──
    result = await _probe_one(
        litellm_model,
        key,
        presets.call_api_base(slug, base_url),
        mode=mode,
        custom_llm_provider=presets.custom_llm_provider_for(slug),
    )

    status: ModelHealth = result["status"]
    tested = {"method": "completion", "tested_model": litellm_model}
    if catalog_count is not None:
        tested["model_count"] = catalog_count

    if status is ModelHealth.available:
        return {
            "valid": True,
            "error": None,
            "warning": None,
            "latency_ms": result.get("latency_ms"),
            **tested,
        }

    if status is ModelHealth.rate_limited:
        # The key is GOOD. Saying otherwise would get a working key deleted.
        return {
            "valid": True,
            "error": None,
            "warning": (
                "The key is valid but currently rate limited (HTTP 429). It will "
                "serve traffic once the provider's window resets."
            ),
            "status_code": 429,
            **tested,
        }

    if status is ModelHealth.auth_error:
        hint = ""
        if slug == "github":
            hint = " GitHub Models needs a fine-grained PAT with the Models:read permission — a classic token is rejected."
        elif slug == "huggingface":
            hint = " HuggingFace needs a token with Inference permission."
        return {
            "valid": False,
            "error": f"The provider rejected this key (HTTP {result.get('http_code') or 401}).{hint}",
            "status_code": result.get("http_code"),
            **tested,
        }

    if status is ModelHealth.unavailable:
        # Says nothing about the key — that one model is not served to it.
        return {
            "valid": True,
            "error": None,
            "warning": (
                f"Could not confirm the key: {litellm_model} is not served to it "
                "(HTTP 404). Try a different validation model."
            ),
            "status_code": 404,
            **tested,
        }

    if status is ModelHealth.timeout:
        return {
            "valid": False,
            "error": f"{slug} did not respond in time.",
            **tested,
        }

    return {
        "valid": False,
        "error": (result.get("error") or f"{slug} returned an error.")[:300],
        "status_code": result.get("http_code"),
        **tested,
    }
