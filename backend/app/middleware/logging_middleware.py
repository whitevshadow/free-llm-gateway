"""
Logging Middleware

Logs every incoming HTTP request with method, path, status code, and
processing time. This is the most basic observability layer — it tells you
exactly what your server is doing at any moment.

PRODUCTION NOTE:
  In a real deployment you'd ship these logs to ELK Stack, Datadog, or
  CloudWatch. The structured format makes them easily parseable by log
  aggregation tools.
"""

import time
import logging
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

logger = logging.getLogger("gateway.access")


class LoggingMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        start = time.perf_counter()

        # Extract request ID set by the RequestIDMiddleware
        request_id = getattr(request.state, "request_id", "unknown")

        response = await call_next(request)

        duration_ms = round((time.perf_counter() - start) * 1000, 2)

        logger.info(
            "request_completed",
            extra={
                "request_id": request_id,
                "method": request.method,
                "path": request.url.path,
                "status_code": response.status_code,
                "duration_ms": duration_ms,
                "client_ip": request.client.host if request.client else "unknown",
            },
        )

        # Also expose processing time as a response header (useful for debugging)
        response.headers["X-Process-Time-Ms"] = str(duration_ms)
        return response
