"""
NVIDIA Riva ASR — real speech-to-text NVCF functions, spoken through litellm's
native `nvidia_riva` integration (extra: litellm[stt-nvidia-riva], gRPC to
grpc.nvcf.nvidia.com).

These functions live on the SAME NVCF surface as the FLUX image functions (see
nvcf.py), but unlike FLUX, litellm already speaks the wire protocol — we only
need to supply the bare model id and the function's NVCF function id per call,
so these rows ride the normal litellm.Router, unlike the images which bypass
it entirely.

litellm_model carries the function id as a SEP-delimited suffix (verified
working end-to-end against a real account, both via litellm.atranscription
directly and via litellm.Router) so no schema change is needed — the same
trick nvcf.py already uses on this API, just with a different prefix
('nvidia_riva/…' vs 'nvcf/…') because THIS surface is spoken by litellm
itself, not bypassed.

TTS (magpie-tts, …) is deliberately NOT ingested here: litellm's nvidia_riva
integration implements speech-to-text only — verified live, `aspeech()`
raises "Unable to map the custom llm provider=nvidia_riva". Ingesting a TTS
function would only manufacture a row that can never work.
"""

import logging
from typing import Dict, List, Tuple

import httpx

logger = logging.getLogger("gateway.nvidia_riva")

FUNCTIONS_URL = "https://api.nvcf.nvidia.com/v2/nvcf/functions"
GRPC_ENDPOINT = "grpc.nvcf.nvidia.com:443"

MODEL_PREFIX = "nvidia_riva/"
SEP = "#"   # litellm_model = '<litellm model string>#<nvcf-function-id>'

# Friendly names of ASR functions with a PROVEN working call (verified live
# against a real account with litellm's default probe params — language_code
# 'en', sample_rate 16000, type 'online'). This is deliberately an ALLOWLIST,
# not a token match: siblings that look alike do NOT all work the same way.
# Verified live and REJECTED, do not add without also passing the right
# non-default params:
#   whisper-large-v3, canary-1b-asr, parakeet-tdt-0.6b-v2
#     -> gRPC INVALID_ARGUMENT "Unavailable model requested given these
#        parameters" (need a language_code/sample_rate/type this adapter
#        does not send)
#   conformer-ctc-asr, parakeet-ctc-riva, parakeet-ctc-1.1b-asr-inworld
#     -> gRPC DEADLINE_EXCEEDED "failed to establish link to worker"
# Grow this set only after a NEW verified-live probe — an ingested function
# that fails every call is worse than no row at all.
_VERIFIED_ASR_FUNCTIONS = frozenset((
    "parakeet-ctc-0.6b-asr",
    "parakeet-ctc-1.1b-asr",
    "parakeet-1.1b-rnnt-multilingual-asr",
    "nemotron-asr-streaming",
))


def is_riva_model(litellm_model: str) -> bool:
    return litellm_model.startswith(MODEL_PREFIX) and SEP in litellm_model


def split_function_id(litellm_model: str) -> Tuple[str, str]:
    """'nvidia_riva/nvidia/parakeet-ctc-0.6b-asr#<fid>' -> (model, fid)."""
    model, fid = litellm_model.rsplit(SEP, 1)
    return model, fid


def friendly_name(fn_name: str) -> str:
    """'ai-parakeet-ctc-0_6b-asr' -> 'parakeet-ctc-0.6b-asr'."""
    name = fn_name[3:] if fn_name.startswith("ai-") else fn_name
    return name.replace("_", ".")


def list_asr_functions(api_key: str) -> List[Tuple[str, str]]:
    """
    [(friendly_name, function_id)] of ACTIVE ASR functions THIS KEY can see.

    The functions endpoint is account-scoped, so this doubles as an access
    check, same as nvcf.list_image_functions. Returns [] on any failure —
    discovery degrades, never crashes.
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
            if fn.get("status") != "ACTIVE":
                continue
            friendly = friendly_name(name)
            if friendly in _VERIFIED_ASR_FUNCTIONS:
                out.setdefault(friendly, fn["id"])   # first ACTIVE version wins
        return sorted(out.items())
    except Exception as exc:
        logger.warning("Riva ASR function listing failed: %s", exc)
        return []
