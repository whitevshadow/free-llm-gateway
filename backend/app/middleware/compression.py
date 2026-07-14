"""
Conditional gzip — compress the UI, never the API.

When nginx sat in front, it gzipped the ~660 KB JS bundle (→ ~190 KB) but left
the proxied API responses alone, with buffering explicitly OFF so streaming chat
completions (Server-Sent Events) flowed token-by-token. Now that uvicorn serves
both, we must reproduce BOTH halves of that.

A blanket GZipMiddleware would break the streaming half: it compresses an SSE
response incrementally, and gzip holds bytes back until a block fills — so tokens
arrive in bursts instead of as they are generated. That is the single most common
way an LLM proxy breaks, and it is worth avoiding by construction.

So: gzip everything EXCEPT /v1. The SPA, /assets, /docs and /openapi.json get
compressed; the whole API — streaming and not — is passed through untouched.
"""

from starlette.middleware.gzip import GZipMiddleware


class ConditionalGZipMiddleware:
    """Delegate to Starlette's GZip for non-/v1 requests; pass /v1 through raw."""

    def __init__(self, app, minimum_size: int = 1024):
        self.app = app
        self._gzip = GZipMiddleware(app, minimum_size=minimum_size)

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http" and not scope["path"].startswith("/v1"):
            await self._gzip(scope, receive, send)
        else:
            await self.app(scope, receive, send)
