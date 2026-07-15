"""
NVCF adapter — NVIDIA image models, spoken natively.

NVIDIA's image models (FLUX family) do NOT live on the OpenAI-compatible
endpoint litellm talks to (integrate.api.nvidia.com); they are NVIDIA Cloud
Functions, invoked at api.nvcf.nvidia.com with their own request shape and a
202-and-poll flow for slow generations. litellm has no integration for this
surface (verified: its nvidia_nim prefix 404s on image calls), so the gateway
carries this small adapter itself.

Catalog rows created from NVCF functions carry litellm_model = 'nvcf/<function
id>' — a prefix litellm never sees. The prober and /v1/images/generations
recognise it and route here instead of through the litellm Router.

Only function families with a PROVEN payload shape are exposed (FLUX text-to-
image, verified live). NVIDIA's ASR/TTS functions exist on the same surface but
speak Riva-specific protocols this adapter does not implement yet — ingesting
them would only manufacture rows that can never work.
"""

from __future__ import annotations

import base64
import logging
from typing import Dict, List, Optional, Tuple

import httpx

logger = logging.getLogger("gateway.nvcf")

FUNCTIONS_URL = "https://api.nvcf.nvidia.com/v2/nvcf/functions"
PEXEC_URL = "https://api.nvcf.nvidia.com/v2/nvcf/pexec/functions"
STATUS_URL = "https://api.nvcf.nvidia.com/v2/nvcf/pexec/status"

# How long one generation may take, polling included. FLUX on free serverless
# can queue; beyond this we report a timeout rather than hang the caller.
INVOKE_TIMEOUT = 150          # seconds total
POLL_SECONDS = 20             # NVCF long-poll window per request

# Function-name fragments this adapter can actually SERVE (text-to-image with
# a {prompt, width, height, steps} body). Grow this list only with a verified
# payload shape — an ingested model that 500s on every call is worse than none.
_IMAGE_FN_TOKENS = ("flux",)

MODEL_PREFIX = "nvcf/"        # litellm_model marker: 'nvcf/<function_id>'


def is_nvcf_model(litellm_model: str) -> bool:
    return litellm_model.startswith(MODEL_PREFIX)


def function_id(litellm_model: str) -> str:
    return litellm_model[len(MODEL_PREFIX):]


def friendly_name(fn_name: str) -> str:
    """'ai-flux_1-schnell' → 'flux.1-schnell' — the name users see and request."""
    name = fn_name[3:] if fn_name.startswith("ai-") else fn_name
    return name.replace("_", ".")


def list_image_functions(api_key: str) -> List[Tuple[str, str]]:
    """
    [(friendly_name, function_id)] of ACTIVE image functions THIS KEY can see.

    The functions endpoint is account-scoped, so this doubles as an access
    check: a function that is not in the list cannot be invoked by this key.
    Returns [] on any failure — discovery degrades, never crashes.
    """
    try:
        resp = httpx.get(
            FUNCTIONS_URL,
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=30,
        )
        resp.raise_for_status()
        out: Dict[str, str] = {}
        for fn in resp.json().get("functions", []):
            name = fn.get("name", "")
            if fn.get("status") == "ACTIVE" and any(t in name for t in _IMAGE_FN_TOKENS):
                out.setdefault(name, fn["id"])   # first ACTIVE version wins
        return sorted((friendly_name(n), fid) for n, fid in out.items())
    except Exception as exc:
        logger.warning("NVCF function listing failed: %s", exc)
        return []


async def _invoke(fid: str, api_key: str, payload: dict) -> dict:
    """POST the function, riding out 202s via the status endpoint."""
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Accept": "application/json",
        "NVCF-POLL-SECONDS": str(POLL_SECONDS),
    }
    async with httpx.AsyncClient(timeout=POLL_SECONDS + 15) as client:
        resp = await client.post(f"{PEXEC_URL}/{fid}", headers=headers, json=payload)
        waited = 0
        while resp.status_code == 202 and waited < INVOKE_TIMEOUT:
            reqid = resp.headers.get("NVCF-REQID")
            resp = await client.get(f"{STATUS_URL}/{reqid}", headers=headers)
            waited += POLL_SECONDS
        resp.raise_for_status()
        return resp.json()


async def generate_image(
    litellm_model: str, api_key: str, prompt: str,
    *, size: Optional[str] = None, steps: Optional[int] = None,
) -> Dict:
    """
    One image, returned OpenAI-shaped: {'data': [{'b64_json': …}]}.

    FLUX responds with {'artifacts': [{'base64': …}]}; we translate so the
    /v1/images/generations response looks the same no matter which provider
    served it.
    """
    width = height = 1024
    if size and "x" in size:
        try:
            w, h = size.lower().split("x", 1)
            width, height = int(w), int(h)
        except ValueError:
            pass

    body = await _invoke(
        function_id(litellm_model), api_key,
        {"prompt": prompt, "width": width, "height": height, "steps": steps or 4},
    )
    artifacts = body.get("artifacts") or []
    if not artifacts or not artifacts[0].get("base64"):
        raise RuntimeError(f"NVCF returned no image artifact: {list(body.keys())}")
    return {"data": [{"b64_json": artifacts[0]["base64"]}]}


async def probe_image(litellm_model: str, api_key: str) -> None:
    """Cheapest real generation — small canvas, minimum steps. Raises on failure."""
    await generate_image(litellm_model, api_key, "a red dot", size="512x512", steps=1)


def decode_b64(data: str) -> bytes:
    return base64.b64decode(data)
