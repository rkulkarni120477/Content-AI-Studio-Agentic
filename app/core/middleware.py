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

_log = logging.getLogger(__name__)

_LOGGABLE_CONTENT_TYPES = ("application/json", "text/")
_REDACT_KEYS = {
    "password", "admin_password", "new_password", "password_hash",
    "azure_client_secret", "token", "access_token",
}
# Upper bound on how many request/response body bytes we ever buffer for
# logging. File uploads (multipart/form-data, application/octet-stream, …) are
# skipped entirely by the content-type guard below.
_MAX_LOGGED_BODY_BYTES = 64 * 1024  # 64 KB


def _redact(body_bytes: bytes):
    """Mask sensitive fields in JSON logs; decode other text bodies as-is."""
    try:
        data = json.loads(body_bytes)

        def scrub(obj):
            if isinstance(obj, dict):
                return {
                    key: "***" if key in _REDACT_KEYS else scrub(value)
                    for key, value in obj.items()
                }
            if isinstance(obj, list):
                return [scrub(value) for value in obj]
            return obj

        return scrub(data)
    except (ValueError, UnicodeDecodeError):
        return body_bytes.decode("utf-8", errors="replace")


# ContextVar allows any code running in the same async context (services,
# repositories, Celery tasks via manual pass-through) to read the current
# request ID without it being passed as a function argument everywhere.
correlation_id_ctx: ContextVar[str] = ContextVar("correlation_id", default="")


class RequestLoggingMiddleware:
    """Pure ASGI middleware for correlation IDs and request/response logging."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = dict(scope.get("headers", ()))
        request_id = headers.get(b"x-request-id", b"").decode("latin-1") or str(uuid.uuid4())
        token = correlation_id_ctx.set(request_id)
        scope.setdefault("state", {})["request_id"] = request_id

        # Tag logs with the caller, but never use the unvalidated token for auth.
        from app.core.security import decode_access_token
        from promptops_app.core.logging import session_username_ctx

        auth_header = headers.get(b"authorization", b"").decode("latin-1")
        if auth_header.lower().startswith("bearer "):
            try:
                username = decode_access_token(auth_header[7:]).get("sub")
                if username:
                    session_username_ctx.set(username)
            except Exception:
                pass

        start_time = time.perf_counter()
        content_type = headers.get(b"content-type", b"").decode("latin-1")
        if any(content_type.startswith(ct) for ct in _LOGGABLE_CONTENT_TYPES):
            # Read then replay loggable bodies. BaseHTTPMiddleware can consume a
            # POST body here and leave the downstream handler waiting forever.
            body_messages = []
            body_length = 0
            while True:
                message = await receive()
                body_messages.append(message)
                if message["type"] != "http.request":
                    break
                body_length += len(message.get("body", b""))
                if not message.get("more_body", False):
                    break

            req_body = b"".join(
                message.get("body", b"")
                for message in body_messages
                if message["type"] == "http.request"
            )
            req_body_val = (
                f"<{body_length} bytes, not logged>"
                if body_length > _MAX_LOGGED_BODY_BYTES
                else (_redact(req_body) if req_body else "")
            )
            body_pending = True

            async def replay_receive():
                nonlocal body_pending
                if body_pending:
                    body_pending = False
                    return {"type": "http.request", "body": req_body, "more_body": False}
                return await receive()
        else:
            req_body_val = (
                f"<{content_type or 'unknown'} body, not logged>"
                if scope["method"] not in ("GET", "HEAD", "DELETE", "OPTIONS")
                else ""
            )
            replay_receive = receive

        _log.info(
            "request_started",
            extra={
                "event": "request_started", "request_id": request_id,
                "method": scope["method"], "path": scope["path"],
                "request_body": req_body_val,
            },
        )

        response_status = 500
        response_loggable = False
        response_length = 0
        response_parts = []
        response_completed = False

        async def send_with_logging(message):
            nonlocal response_status, response_loggable, response_length, response_completed

            if message["type"] == "http.response.start":
                response_status = message["status"]
                response_headers = dict(message.get("headers", ()))
                response_type = response_headers.get(b"content-type", b"").decode("latin-1")
                response_loggable = any(
                    response_type.startswith(ct) for ct in _LOGGABLE_CONTENT_TYPES
                )
                outgoing = dict(message)
                outgoing["headers"] = [
                    (key, value) for key, value in message.get("headers", ())
                    if key.lower() != b"x-request-id"
                ] + [(b"x-request-id", request_id.encode("latin-1"))]
                message = outgoing
            elif message["type"] == "http.response.body" and response_loggable:
                part = message.get("body", b"")
                response_length += len(part)
                if response_length <= _MAX_LOGGED_BODY_BYTES:
                    response_parts.append(part)
                else:
                    response_parts.clear()

            await send(message)

            if (
                message["type"] == "http.response.body"
                and not message.get("more_body", False)
                and not response_completed
            ):
                response_completed = True
                resp_body_val = ""
                if response_loggable:
                    resp_body_val = (
                        f"<{response_length} bytes, not logged>"
                        if response_length > _MAX_LOGGED_BODY_BYTES
                        else (_redact(b"".join(response_parts)) if response_parts else "")
                    )
                duration_ms = int((time.perf_counter() - start_time) * 1000)
                _log.info(
                    "request_completed",
                    extra={
                        "event": "request_completed", "request_id": request_id,
                        "method": scope["method"], "path": scope["path"],
                        "status": response_status, "duration_ms": duration_ms,
                        "response_body": resp_body_val,
                    },
                )

        try:
            await self.app(scope, replay_receive, send_with_logging)
        except Exception as exc:
            duration_ms = int((time.perf_counter() - start_time) * 1000)
            _log.error(
                "request_error",
                extra={
                    "event": "request_error", "request_id": request_id,
                    "method": scope["method"], "path": scope["path"],
                    "duration_ms": duration_ms, "error": str(exc),
                },
            )
            raise
        finally:
            correlation_id_ctx.reset(token)
