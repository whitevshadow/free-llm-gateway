"""
DeepSeek Web executor — serves chat.deepseek.com from a browser session token.

WHY THIS IS NOT A LITELLM PROVIDER
    Everything else in this gateway reaches its upstream through litellm, which
    speaks OpenAI-shaped HTTP. chat.deepseek.com does not: it is the private API
    behind the web app. A request needs a per-message proof-of-work, a server-side
    chat session, and a bespoke SSE dialect that has to be translated back into
    OpenAI chunks. None of that is expressible as a litellm provider, so this
    module talks to DeepSeek directly and returns OpenAI-shaped results, and
    `openai_compat.py` branches to it before it reaches the router.

THE CREDENTIAL
    A `userToken` lifted from a signed-in browser session — not an API key. It
    carries the access of the logged-in account and expires; when it does, calls
    start failing with 401 and the user has to paste a fresh one. It is stored
    encrypted like any other provider key.

WIRE PROTOCOL (derived from OmniRoute's executor, verified against the live API)
    1. POST /api/v0/chat_session/create        -> chat_session_id
    2. POST /api/v0/chat/create_pow_challenge  -> {algorithm, challenge, salt,
                                                  difficulty, expire_at, signature}
    3. solve the challenge (see deepseek_pow.py), base64 the answer envelope,
       send it as `X-Ds-Pow-Response`
    4. POST /api/v0/chat/completion            -> SSE
    5. POST /api/v0/chat_session/delete        (best effort)

    Step 3 is per-request: a challenge cannot be reused.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import random
import re
import time
import uuid
from collections import defaultdict, deque
from typing import Any, AsyncIterator, Deque, Dict, List, Optional, Tuple

import httpx

from app.services.deepseek_pow import solve as solve_pow

logger = logging.getLogger("gateway.deepseek_web")

PROVIDER_SLUG = "deepseek-web"
BASE = "https://chat.deepseek.com"
API = f"{BASE}/api"
COMPLETION_URL = f"{API}/v0/chat/completion"

# The catalogue has no /models endpoint, so the model list is static. Taken from
# OmniRoute's registry entry (registry/deepseek/web/index.ts). `thinking` and
# `search` are not separate upstream models — they are request flags — so the
# ids encode which flags to set (see _resolve_model_options).
MODELS: List[str] = [
    "deepseek-v4-pro",
    "deepseek-v4-pro-think",
    "deepseek-v4-pro-search",
    "deepseek-v4-pro-think-search",
    "deepseek-v4-flash",
    "deepseek-v4-flash-think",
    "deepseek-v4-flash-search",
    "deepseek-v4-flash-think-search",
    "deepseek-chat",
    "deepseek-reasoner",
]

# The chat.deepseek.com web client's own fingerprint. Sending a stale
# X-Client-Version is itself a bot-detection signal, so this mirrors the current
# 2.0.0 build exactly (which dropped X-App-Version and added X-Client-Bundle-Id).
_FAKE_HEADERS: Dict[str, str] = {
    "Accept": "*/*",
    "Accept-Language": "en-US,en;q=0.9",
    "Origin": BASE,
    "Referer": f"{BASE}/",
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36"
    ),
    "X-Client-Bundle-Id": "com.deepseek.chat",
    "X-Client-Locale": "en-US",
    "X-Client-Platform": "web",
    "X-Client-Version": "2.0.0",
}

_TIMEOUT = httpx.Timeout(connect=15.0, read=300.0, write=30.0, pool=15.0)


class DeepSeekWebError(RuntimeError):
    """Upstream refused the request. `status` mirrors the HTTP code when known."""

    def __init__(self, message: str, status: int = 502):
        super().__init__(message)
        self.status = status


def _fake_cookie() -> str:
    ts = int(time.time() * 1000)
    hexs = "".join(random.choice("0123456789abcdef") for _ in range(18))
    return (
        f"intercom-HWWAFSESTIME={ts}; HWWAFSESID={hexs}; "
        f"Hm_lvt_{uuid.uuid4()}={ts // 1000}; _frid={uuid.uuid4()}"
    )


def _headers(token: str, extra: Optional[Dict[str, str]] = None) -> Dict[str, str]:
    h = dict(_FAKE_HEADERS)
    h["Content-Type"] = "application/json"
    h["Authorization"] = f"Bearer {token}"
    if extra:
        h.update(extra)
    return h


def _resolve_model_options(model: str, body: Dict[str, Any]) -> Tuple[str, bool, bool]:
    """
    Map a model id onto DeepSeek's three request knobs.

    Upstream has one model with flags, not ten models. `-think` and `-search`
    suffixes in the id are how a caller selects those flags through an
    OpenAI-shaped `model` field.
    """
    m = (model or "").lower()
    model_type = "expert" if ("pro" in m or "expert" in m) else "default"
    thinking = (
        "r1" in m
        or "think" in m
        or "reason" in m
        or body.get("thinking_enabled") is True
        or bool(body.get("reasoning_effort"))
    )
    search = (
        "search" in m
        or body.get("search_enabled") is True
        or body.get("web_search") is True
    )
    return model_type, thinking, search


def _messages_to_prompt(messages: List[Dict[str, Any]]) -> str:
    """
    Flatten an OpenAI `messages[]` into the single prompt the web app posts.

    DeepSeek Web is a chat UI: it takes one prompt string and keeps history
    server-side per session. Since a session is created per request here, the
    whole conversation has to be replayed inline or the model loses context.
    """
    parts: List[str] = []
    for msg in messages:
        role = msg.get("role", "user")
        content = msg.get("content", "")
        if isinstance(content, list):  # multimodal blocks -> text only
            content = "".join(
                b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text"
            )
        if not content:
            continue
        if role == "system":
            parts.append(str(content))
        elif role == "assistant":
            parts.append(f"Assistant: {content}")
        else:
            parts.append(f"Human: {content}")
    if len(parts) == 1:
        return parts[0]
    return "\n\n".join(parts)


async def _solve_pow_async(ch: Dict[str, Any]) -> str:
    """
    Solve, then base64 the answer envelope for `X-Ds-Pow-Response`.

    Runs in a worker thread: the solve is ~0.8-1.6s of pure CPU and would
    otherwise stall the event loop for every other request in flight.
    """
    answer = await asyncio.to_thread(
        solve_pow,
        ch["algorithm"],
        ch["challenge"],
        ch["salt"],
        int(ch["difficulty"]),
        ch["expire_at"],
    )
    if answer < 0:
        raise DeepSeekWebError("Proof-of-work solver found no answer.", 502)
    envelope = {
        "algorithm": ch["algorithm"],
        "challenge": ch["challenge"],
        "salt": ch["salt"],
        "answer": answer,
        "signature": ch.get("signature"),
        "target_path": ch.get("target_path"),
    }
    return base64.b64encode(json.dumps(envelope).encode()).decode()


def _biz(payload: Dict[str, Any]) -> Dict[str, Any]:
    return (payload.get("data") or {}).get("biz_data") or payload.get("biz_data") or {}


# ── PoW pool — pre-solve challenges so requests do not pay for them ──────────
#  Measured cold request: 1,531 ms of overhead before the first token, 1,075 ms
#  of it pure CPU solving the challenge. Three properties of DeepSeek's scheme,
#  each verified against the live API:
#
#    1. a challenge is valid for ~298 s (its own expire_at), and
#    2. a solution computed ~45 s earlier is still accepted, but
#    3. a solved answer is SINGLE-USE. Replaying one returns HTTP 200 with a
#       well-formed SSE stream containing NO content — the request "succeeds"
#       and the caller gets an empty message. This is the trap: a naive cache
#       keyed on the token looks like a 5x speedup and silently empties every
#       reply after the first. (1) and (2) are only useful via (4):
#
#    4. DISTINCT challenges can be solved AHEAD of time and banked.
#
#  So this keeps a small pool of pre-solved, never-used answers and refills it in
#  the background. A warm request pops one and pays nothing; a cold request (or
#  an exhausted pool) solves inline exactly as before.
_POOL_TARGET = 3          # answers kept ready per token
_POOL_MIN_TTL = 45.0      # discard an answer with less life left than this
_pow_pool: Dict[str, Deque[Tuple[str, float]]] = defaultdict(deque)
_pool_refilling: Dict[str, bool] = {}


def _pool_take(token: str) -> Optional[str]:
    """Pop a still-valid unused answer, discarding any that went stale."""
    pool = _pow_pool.get(token)
    now = time.time()
    while pool:
        header, expires = pool.popleft()
        if expires - now > _POOL_MIN_TTL:
            return header
    return None


def invalidate_pow(token: str) -> None:
    """Drop every banked answer for this token."""
    _pow_pool.pop(token, None)


# ── one generation at a time, per token ──────────────────────────────────────
#  A web session is one browser tab: DeepSeek serialises generation per account,
#  and two completions posted concurrently on the same userToken leave one of
#  them with an empty stream — HTTP 200, no content, no error.
#
#  This was invisible before the PoW pool existed, because every request spent
#  ~1.7 s solving first and that staggered them by accident. Removing the CPU
#  cost removed the accidental spacing and exposed the real constraint, so the
#  serialisation has to become explicit.
#
#  This caps throughput per token at one in-flight generation. That is not a
#  limitation this code imposes — it is what the upstream account supports.
#  Parallelism comes from having more than one provider, which the router
#  already does.
_gen_locks: Dict[str, asyncio.Lock] = {}


def _gen_lock(token: str) -> asyncio.Lock:
    lock = _gen_locks.get(token)
    if lock is None:
        lock = _gen_locks[token] = asyncio.Lock()
    return lock


# Session reuse was tried and reverted — see DeepSeekWebClient.acquire_session
# for why (a reused session returns an empty answer with no error). The cache
# and its invalidator are kept only so the retry path below has something to
# call; _session_cache is intentionally never populated.
_session_cache: Dict[str, Tuple[str, float]] = {}


def invalidate_session(token: str) -> None:
    _session_cache.pop(token, None)


def is_deepseek_web_model(litellm_model: str) -> bool:
    """True for models routed through this executor rather than litellm."""
    return bool(litellm_model) and litellm_model.split("/", 1)[0] == PROVIDER_SLUG


def strip_prefix(litellm_model: str) -> str:
    """`deepseek-web/deepseek-chat` -> `deepseek-chat`."""
    return litellm_model.split("/", 1)[1] if "/" in litellm_model else litellm_model


async def probe(litellm_model: str, token: str) -> None:
    """
    Cheapest real call that proves this token can reach DeepSeek Web.

    Deliberately stops at the PoW challenge rather than running a completion:
    the challenge already requires a valid session token (it 401s otherwise),
    and a full completion would cost a solve plus real model time on every one
    of the ten static models — ~30s of pure CPU for no extra signal.

    Raises on failure; the prober classifies the exception.
    """
    client = DeepSeekWebClient(token)
    async with httpx.AsyncClient(timeout=_TIMEOUT) as http:
        payload = await client._post(
            http, "/v0/chat/create_pow_challenge",
            {"target_path": "/api/v0/chat/completion"},
        )
    if not (_biz(payload).get("challenge") or {}).get("challenge"):
        raise DeepSeekWebError("DeepSeek returned no PoW challenge for this token.", 502)


class DeepSeekWebClient:
    def __init__(self, token: str):
        if not token or not token.strip():
            raise DeepSeekWebError("No DeepSeek Web session token supplied.", 401)
        self.token = token.strip()

    # ── upstream steps ────────────────────────────────────────────────────
    async def _post(
        self, client: httpx.AsyncClient, path: str, body: Dict[str, Any],
        extra_headers: Optional[Dict[str, str]] = None,
    ) -> Dict[str, Any]:
        r = await client.post(
            f"{API}{path}", json=body, headers=_headers(self.token, extra_headers)
        )
        if r.status_code == 401:
            raise DeepSeekWebError(
                "DeepSeek rejected the session token (401). Sign in to "
                "chat.deepseek.com again and paste a fresh userToken.",
                401,
            )
        if r.status_code >= 400:
            raise DeepSeekWebError(f"{path} returned HTTP {r.status_code}.", r.status_code)
        return r.json()

    async def create_session(self, client: httpx.AsyncClient) -> str:
        data = _biz(await self._post(client, "/v0/chat_session/create", {},
                                     {"Cookie": _fake_cookie()}))
        sid = (data.get("chat_session") or {}).get("id")
        if not sid:
            raise DeepSeekWebError("DeepSeek did not return a chat session id.", 502)
        return sid

    async def acquire_session(self, client: httpx.AsyncClient) -> Tuple[str, bool]:
        """
        Always a FRESH session. Returns (session_id, reused=False).

        Session reuse was implemented and measured — it saves the 269 ms create
        plus the 208 ms delete — and then REVERTED, because it silently breaks
        the answer: a second completion posted to an already-used session
        returns a well-formed SSE stream containing no content fragments at all.
        The request succeeds, the caller gets an empty assistant message, and
        nothing anywhere reports an error. Posting `parent_message_id: None` to a
        session that already has a message is evidently not a supported shape.

        Making it work would mean threading real parent_message_ids and letting
        DeepSeek hold the history — a different design from "replay the whole
        prompt each time", and not one worth 477 ms of a ~700 ms request.
        """
        return await self.create_session(client), False

    async def delete_session(self, client: httpx.AsyncClient, session_id: str) -> None:
        try:
            await self._post(client, "/v0/chat_session/delete", {"chat_session_id": session_id})
        except Exception:  # best effort; never fail a completed request on cleanup
            logger.debug("session cleanup failed for %s", session_id, exc_info=True)

    async def _solve_fresh(self, client: httpx.AsyncClient) -> Tuple[str, float]:
        """One new challenge, solved. Returns (header, expires_at_epoch)."""
        data = _biz(await self._post(
            client, "/v0/chat/create_pow_challenge",
            {"target_path": "/api/v0/chat/completion"},
        ))
        ch = data.get("challenge")
        if not ch or not ch.get("challenge"):
            raise DeepSeekWebError("DeepSeek did not return a PoW challenge.", 502)
        header = await _solve_pow_async(ch)
        try:
            expires = float(ch["expire_at"]) / 1000.0   # epoch MILLISECONDS
        except (KeyError, TypeError, ValueError):
            expires = time.time() + 60.0
        return header, expires

    async def _refill_pool(self) -> None:
        """
        Top the pool back up to _POOL_TARGET, in the background.

        Owns its own http client because the request that triggered it will have
        closed its own by the time this runs. Failures are swallowed: a refill
        that cannot complete just means the next request solves inline.
        """
        if _pool_refilling.get(self.token):
            return
        _pool_refilling[self.token] = True
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
                while len(_pow_pool[self.token]) < _POOL_TARGET:
                    header, expires = await self._solve_fresh(client)
                    _pow_pool[self.token].append((header, expires))
        except Exception:
            logger.debug("deepseek-web pow refill failed", exc_info=True)
        finally:
            _pool_refilling[self.token] = False

    async def pow_header(self, client: httpx.AsyncClient, *, force: bool = False) -> str:
        """
        A single-use `X-Ds-Pow-Response`.

        Takes a banked answer when one is ready, otherwise solves inline. Either
        way the answer is consumed exactly once — see the pool comment above for
        why reuse is not an option. A refill is kicked off afterwards so the next
        request finds a warm pool.
        """
        header = None if force else _pool_take(self.token)
        if header is None:
            header, _ = await self._solve_fresh(client)
        # NB: the refill is NOT kicked off here. Solving is ~1 s of CPU, and
        # starting it now runs it concurrently with the completion this header
        # was just fetched for — measured, that pushed a warm request's TTFB from
        # 480 ms back up to ~1,175 ms. The top-up happens after the stream ends
        # (see stream_chat), where there is nothing to contend with.
        return header

    # ── the public call ───────────────────────────────────────────────────
    async def stream_chat(
        self, messages: List[Dict[str, Any]], model: str, body: Dict[str, Any]
    ) -> AsyncIterator[Dict[str, Any]]:
        """Yield OpenAI-shaped chat.completion.chunk dicts."""
        model_type, thinking, search = _resolve_model_options(model, body)
        prompt = _messages_to_prompt(messages)
        cid = f"chatcmpl-{uuid.uuid4().hex[:24]}"
        created = int(time.time())

        # Serialise per token — see _gen_lock. Held across the whole stream, so
        # the next generation starts only once this one has finished reading.
        async with _gen_lock(self.token), httpx.AsyncClient(
            timeout=_TIMEOUT, follow_redirects=True
        ) as client:
            session_id, reused = await self.acquire_session(client)
            try:
                async def open_stream(sid: str, force_pow: bool):
                    pow_value = await self.pow_header(client, force=force_pow)
                    payload = {
                        "chat_session_id": sid,
                        # Always None: every request replays the full conversation
                        # in `prompt`, so each message branches from the session
                        # root. That is what makes session reuse safe.
                        "parent_message_id": None,
                        "model_type": model_type,
                        "prompt": prompt,
                        "ref_file_ids": [],
                        "thinking_enabled": thinking,
                        "search_enabled": search,
                        "preempt": False,
                    }
                    headers = _headers(self.token, {
                        "X-Ds-Pow-Response": pow_value,
                        "X-Client-Timezone-Offset": "0",
                        "Cookie": _fake_cookie(),
                    })
                    ctx = client.stream("POST", COMPLETION_URL, json=payload, headers=headers)
                    return await ctx.__aenter__(), ctx

                resp, ctx = await open_stream(session_id, force_pow=False)

                # Exactly one retry, and only when a CACHED value could be the
                # cause: a stale session (owner deleted the chat) or a PoW answer
                # upstream no longer accepts. Both are self-inflicted by the
                # caches above, so a cold request never pays for this.
                if resp.status_code >= 400 and _pow_pool.get(self.token):
                    detail = (await resp.aread()).decode("utf-8", "replace")[:200]
                    logger.info(
                        "deepseek-web retry after HTTP %s on cached session/pow: %s",
                        resp.status_code, detail,
                    )
                    await ctx.__aexit__(None, None, None)
                    invalidate_session(self.token)
                    invalidate_pow(self.token)
                    session_id, reused = await self.acquire_session(client)
                    resp, ctx = await open_stream(session_id, force_pow=True)

                try:
                    if resp.status_code >= 400:
                        detail = (await resp.aread()).decode("utf-8", "replace")[:300]
                        raise DeepSeekWebError(
                            f"DeepSeek completion returned HTTP {resp.status_code}: {detail}",
                            resp.status_code,
                        )

                    emitted_role = False
                    path = "thinking" if thinking else "content"
                    got_any = False

                    def chunk(delta: Dict[str, Any], finish: Optional[str] = None):
                        return {
                            "id": cid,
                            "object": "chat.completion.chunk",
                            "created": created,
                            "model": model,
                            "choices": [
                                {"index": 0, "delta": delta, "finish_reason": finish}
                            ],
                        }

                    async for raw in resp.aiter_lines():
                        if not raw or not raw.startswith("data:"):
                            continue
                        data_str = raw[5:].strip()
                        if data_str == "[DONE]":
                            break
                        try:
                            evt = json.loads(data_str)
                        except json.JSONDecodeError:
                            continue

                        for text, is_thinking, new_path in _extract(evt, path):
                            path = new_path
                            if not text:
                                continue
                            if not emitted_role:
                                emitted_role = True
                                yield chunk({"role": "assistant", "content": ""})
                            got_any = True
                            key = "reasoning_content" if is_thinking else "content"
                            yield chunk({key: text})

                    if not emitted_role:
                        yield chunk({"role": "assistant", "content": ""})
                    if not got_any:
                        logger.warning("deepseek-web produced no content (session %s)", session_id)
                    yield chunk({}, "stop")
                finally:
                    await ctx.__aexit__(None, None, None)
            finally:
                # Sessions are single-use (see acquire_session), so clean up.
                await self.delete_session(client, session_id)

        # Outside the lock and after the stream: top the pool back up now, while
        # nothing is competing for CPU, so the NEXT request finds a solved answer
        # waiting and pays 0 ms for proof-of-work.
        try:
            asyncio.create_task(self._refill_pool())
        except RuntimeError:      # no running loop (shutdown)
            pass

    async def complete(
        self, messages: List[Dict[str, Any]], model: str, body: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Collect the stream into a single OpenAI chat.completion object."""
        content, reasoning = [], []
        cid = created = None
        async for ck in self.stream_chat(messages, model, body):
            cid = cid or ck["id"]
            created = created or ck["created"]
            delta = ck["choices"][0].get("delta") or {}
            if delta.get("content"):
                content.append(delta["content"])
            if delta.get("reasoning_content"):
                reasoning.append(delta["reasoning_content"])

        message: Dict[str, Any] = {"role": "assistant", "content": "".join(content)}
        if reasoning:
            message["reasoning_content"] = "".join(reasoning)
        return {
            "id": cid or f"chatcmpl-{uuid.uuid4().hex[:24]}",
            "object": "chat.completion",
            "created": created or int(time.time()),
            "model": model,
            "choices": [{"index": 0, "message": message, "finish_reason": "stop"}],
            # DeepSeek Web reports no token counts. Zeros would be a lie the usage
            # dashboard renders as fact, so the field is omitted entirely.
        }


# Paths whose string value is answer text. Matched with a regex because the
# fragment index is embedded: `response/fragments/-1/content`,
# `response/fragments/0/content`, …
_CONTENT_PATH_RE = re.compile(r"^response/fragments/-?\d+/content$")
_THINKING_PATH_RE = re.compile(
    r"^response/(thinking_content|fragments/-?\d+/thinking_content)$"
)

# Paths that carry strings but are NOT text. `response/status` sends the literal
# "FINISHED"; treating any string as content appends it to the answer.
_IGNORED_PATH_PREFIXES = (
    "response/status",
    "response/quasi_status",
    "response/accumulated_token_usage",
    "response/search_status",
    "response/search_results",
)


def _extract(evt: Dict[str, Any], path: str):
    """
    Pull text out of one DeepSeek SSE event.

    The dialect is JSON-patch-ish — {p: path, o: op, v: value} — with two traps
    that are only visible on a real stream:

      STICKY PATHS. Text arrives as one addressed append followed by a run of
      bare values that inherit the last path:

          {"p":"response/fragments/-1/content","o":"APPEND","v":","}
          {"v":" "}  {"v":"2"}  {"v":","}  ...

      So an event with no `p` is not metadata — it is the continuation of the
      current channel, which is why `path` is threaded through rather than
      recomputed per event.

      THE TOP-LEVEL `content` KEY IS NOT THE ANSWER. A completion ends with
      something like {"content":"2026-08-04,Tuesday,India,Web,Enable"} — injected
      context (date, weekday, region, platform), not model output. Emitting it
      appends that string to every reply.

    Yields (text, is_thinking, new_path).
    """
    p = evt.get("p")
    v = evt.get("v")

    def frag_path(frag: Dict[str, Any], cur: str) -> str:
        t = str(frag.get("type") or "").upper()
        if t == "THINK":
            return "thinking"
        if t in ("ANSWER", "RESPONSE"):
            return "content"
        return cur

    # Opening event: the whole response object, possibly with the first fragments.
    if isinstance(v, dict) and isinstance(v.get("response"), dict):
        resp = v["response"]
        if resp.get("thinking_enabled") is True:
            path = "thinking"
        elif resp.get("thinking_enabled") is False:
            path = "content"
        for frag in resp.get("fragments") or []:
            if isinstance(frag, dict) and frag.get("content"):
                path = frag_path(frag, path)
                yield frag["content"], path == "thinking", path
        return

    if p == "response/fragments":
        frags = v if isinstance(v, list) else [v]
        for frag in frags:
            if isinstance(frag, dict) and frag.get("content"):
                path = frag_path(frag, path)
                yield frag["content"], path == "thinking", path
        return

    if isinstance(p, str):
        if p.startswith(_IGNORED_PATH_PREFIXES) or p == "response":
            return
        if _THINKING_PATH_RE.match(p):
            path = "thinking"
        elif _CONTENT_PATH_RE.match(p):
            # NB: this path does NOT mean "answer". `response/fragments/-1/content`
            # appends to whichever fragment is currently open, and that fragment's
            # THINK/RESPONSE type was announced separately — so forcing "content"
            # here dumps a reasoner's entire chain of thought into the answer.
            # Keep the channel the fragment type established.
            pass
        else:
            # An addressed path we do not recognise: do not guess it is text.
            return
        if isinstance(v, str) and v:
            yield v, path == "thinking", path
        return

    # No `p`: a bare continuation of the current channel. Only strings count —
    # the top-level {"content": ...} metadata event has no `v` and is ignored.
    if p is None and isinstance(v, str) and v:
        yield v, path == "thinking", path
