"""
Custom HTTP middleware for the Content AI Studio API.

Middleware runs on every request before it reaches a route handler and on
every response before it is sent back to the client.

RequestLoggingMiddleware does three things:
  1. Generates a unique correlation ID (X-Request-ID) for every request.
     This ID is added to the response headers and stored in a ContextVar
     so that log lines emitted deep inside services can include it.
  2. Logs the request method, path, status code, and duration in milliseconds.
  3. Stores the correlation ID in the request state so route handlers and
     services can pass it to audit logs.

Why correlation IDs matter
--------------------------
When a user reports "my generation failed", you can search the log for their
request ID and see every log line — from the HTTP request through the service
layer to the Celery task — as a single trace without needing a full APM tool.
"""

from __future__ import annotations

import logging
import time
import uuid
from contextvars import ContextVar

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

_log = logging.getLogger(__name__)

# ContextVar allows any code running in the same async context (services,
# repositories, Celery tasks via manual pass-through) to read the current
# request ID without it being passed as a function argument everywhere.
correlation_id_ctx: ContextVar[str] = ContextVar("correlation_id", default="")


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    """
    ASGI middleware that adds correlation IDs and request/response logging.

    Every request gets a unique ID either from the incoming X-Request-ID
    header (useful when an API gateway or upstream proxy sets it) or from a
    freshly generated UUID4.
    """

    async def dispatch(self, request: Request, call_next) -> Response:
        """Process the request: assign ID, call the route, log the result."""

        # Use the incoming header if present, otherwise generate a new ID.
        request_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())

        # Store the ID in the ContextVar so downstream code can read it.
        token = correlation_id_ctx.set(request_id)

        # Store on request.state so route handlers can access it directly.
        request.state.request_id = request_id

        start_time = time.perf_counter()

        _log.info(
            "request_started  id=%s  method=%s  path=%s",
            request_id, request.method, request.url.path,
        )

        try:
            response = await call_next(request)
        except Exception as exc:
            duration_ms = int((time.perf_counter() - start_time) * 1000)
            _log.error(
                "request_error  id=%s  method=%s  path=%s  duration_ms=%d  error=%s",
                request_id, request.method, request.url.path, duration_ms, exc,
            )
            raise
        finally:
            # Reset the ContextVar to its previous value to avoid leaking
            # state between requests in the same async task.
            correlation_id_ctx.reset(token)

        duration_ms = int((time.perf_counter() - start_time) * 1000)

        _log.info(
            "request_completed  id=%s  method=%s  path=%s  status=%d  duration_ms=%d",
            request_id, request.method, request.url.path,
            response.status_code, duration_ms,
        )

        # Attach the correlation ID to the response so the frontend/client
        # can include it in bug reports.
        response.headers["X-Request-ID"] = request_id

        return response
