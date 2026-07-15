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

import json
import logging
import time
import uuid
from contextvars import ContextVar

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

_log = logging.getLogger(__name__)

_LOGGABLE_CONTENT_TYPES = ("application/json", "text/")
_REDACT_KEYS = {"password", "admin_password", "new_password", "password_hash",
                 "azure_client_secret", "token", "access_token"}


def _redact(body_bytes: bytes):
    """Best-effort: parse as JSON and mask sensitive fields (as a real nested
    object, not a re-stringified blob, so the log line stays proper JSON).
    Falls back to the raw decoded text for non-JSON bodies. No size limit —
    full request/response content is always logged.
    """
    try:
        data = json.loads(body_bytes)
        def scrub(obj):
            if isinstance(obj, dict):
                return {k: ("***" if k in _REDACT_KEYS else scrub(v)) for k, v in obj.items()}
            if isinstance(obj, list):
                return [scrub(v) for v in obj]
            return obj
        return scrub(data)
    except (ValueError, UnicodeDecodeError):
        return body_bytes.decode("utf-8", errors="replace")

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

        # Best-effort: tag this request's logs with the calling username, so
        # they land in that user's per-login session log file. This must
        # happen here (before call_next spawns the route's task) rather than
        # inside get_current_user() — BaseHTTPMiddleware runs the downstream
        # app in a separate child task, so a ContextVar set there never
        # becomes visible back in this middleware's own request_completed
        # log. Setting it here, before call_next, means the child task's
        # copied context already includes it. Never used for authorization —
        # get_current_user() still does the real, validated auth.
        from promptops_app.core.logging import session_username_ctx
        from app.core.security import decode_access_token

        auth_header = request.headers.get("authorization", "")
        if auth_header.lower().startswith("bearer "):
            try:
                claims = decode_access_token(auth_header[7:])
                username = claims.get("sub")
                if username:
                    session_username_ctx.set(username)
            except Exception:
                pass

        start_time = time.perf_counter()

        # Reading body() caches it on the request, so downstream handlers can
        # still read it normally — this does not consume the stream for them.
        req_body = await request.body()
        req_body_val = _redact(req_body) if req_body else ""

        _log.info(
            "request_started",
            extra={
                "event": "request_started", "request_id": request_id,
                "method": request.method, "path": request.url.path,
                "request_body": req_body_val,
            },
        )

        try:
            response = await call_next(request)
        except Exception as exc:
            duration_ms = int((time.perf_counter() - start_time) * 1000)
            _log.error(
                "request_error",
                extra={
                    "event": "request_error", "request_id": request_id,
                    "method": request.method, "path": request.url.path,
                    "duration_ms": duration_ms, "error": str(exc),
                },
            )
            raise
        finally:
            # Reset the ContextVar to its previous value to avoid leaking
            # state between requests in the same async task.
            correlation_id_ctx.reset(token)

        duration_ms = int((time.perf_counter() - start_time) * 1000)

        # Capture the response body for logging, then rebuild an identical
        # response so the client still receives it untouched.
        resp_body_val = ""
        content_type = response.headers.get("content-type", "")
        if any(content_type.startswith(ct) for ct in _LOGGABLE_CONTENT_TYPES):
            resp_bytes = b"".join([chunk async for chunk in response.body_iterator])
            resp_body_val = _redact(resp_bytes) if resp_bytes else ""
            response = Response(
                content=resp_bytes,
                status_code=response.status_code,
                headers=dict(response.headers),
                media_type=response.media_type,
            )

        _log.info(
            "request_completed",
            extra={
                "event": "request_completed", "request_id": request_id,
                "method": request.method, "path": request.url.path,
                "status": response.status_code, "duration_ms": duration_ms,
                "response_body": resp_body_val,
            },
        )

        # Attach the correlation ID to the response so the frontend/client
        # can include it in bug reports.
        response.headers["X-Request-ID"] = request_id

        return response
