"""
Request-ID Middleware

WHY THIS MATTERS:
  In production, when a user reports a bug, the support team says:
  "What's your request ID?" and traces the entire journey of that request
  through logs, databases, and downstream services.

  Every serious API (AWS, GCP, Stripe) returns an X-Request-ID header.
  This middleware:
    1. Reads an incoming X-Request-ID header (if the client sends one).
    2. Generates a UUID if none was provided.
    3. Attaches it to `request.state.request_id` so handlers can use it.
    4. Includes it in the response headers.
"""

import uuid
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response


class RequestIDMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        # Use client-provided ID or generate one
        request_id = request.headers.get("X-Request-ID", str(uuid.uuid4()))
        request.state.request_id = request_id

        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        return response
