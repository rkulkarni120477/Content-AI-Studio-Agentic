"""Structured logging and duration timing utilities.

Configure once at startup (app.py or the Streamlit entry point):

    from promptops_app.core.logging import configure_logging
    configure_logging()

Use the standard logger in each module:

    import logging
    _log = logging.getLogger(__name__)

Use log_duration to time any operation:

    from promptops_app.core.logging import log_duration

    with log_duration("generation_service.generate_content",
                      extra={"user": user_name, "course_id": course_id}):
        ...

Environment variables
---------------------
PROMPTOPS_LOG_LEVEL        DEBUG | INFO | WARNING | ERROR   (default: INFO)
PROMPTOPS_LOG_FULL_PROMPTS 1 | true                         (default: off)
PROMPTOPS_LOG_COLOR        1 | true  — force color even when stdout is not a TTY
"""

from __future__ import annotations

import contextlib
import json
import logging
import logging.handlers
import re
import sys
import threading
import time
from contextvars import ContextVar
from pathlib import Path
from typing import Any, Dict, Generator, Optional

from promptops_app.core.config import settings as _cfg

# ---------------------------------------------------------------------------
# Public feature flag — re-exported so callers don't need to import config
# ---------------------------------------------------------------------------

LOG_FULL_PROMPTS: bool = _cfg.enable_prompt_logging

_CONFIGURED = False
_timing_log = logging.getLogger("promptops_app.timing")

# ---------------------------------------------------------------------------
# Per-login session log files
#
# Every successful login gets its own fresh log file under logs/sessions/,
# even a repeat login by the same username. get_current_user() sets
# session_username_ctx on every authenticated request, so all of that user's
# subsequent activity (requests, LLM calls) is mirrored into their active
# session file in addition to the main logs/app.log.
# ---------------------------------------------------------------------------

session_username_ctx: ContextVar[str] = ContextVar("session_username", default="")
_session_handlers: Dict[str, logging.Handler] = {}
_session_lock = threading.Lock()


class _SessionFilter(logging.Filter):
    """Only pass log records emitted while this username is the active request user."""

    def __init__(self, username: str) -> None:
        super().__init__()
        self._username = username

    def filter(self, record: logging.LogRecord) -> bool:  # noqa: A003
        return session_username_ctx.get() == self._username


def _safe_filename(text: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_-]", "_", text) or "user"


def start_session_log(username: str) -> None:
    """Create a brand-new per-login log file for this username and make it active.

    Call this once, right after a successful login. Any previous session
    file for the same username is detached (left on disk as history) and
    this new one takes over as the live target.

    Runs in a background thread to avoid blocking login responses.
    """
    def _setup_session_log():
        try:
            configure_logging()
            Path("logs/sessions").mkdir(parents=True, exist_ok=True)
            path = f"logs/sessions/{_safe_filename(username)}_{time.strftime('%Y%m%d_%H%M%S')}.log"

            handler = logging.FileHandler(path, encoding="utf-8")
            handler.setFormatter(_JsonFormatter())
            handler.addFilter(_SessionFilter(username))

            root = logging.getLogger()
            with _session_lock:
                old = _session_handlers.get(username)
                if old is not None:
                    root.removeHandler(old)
                    old.close()
                root.addHandler(handler)
                _session_handlers[username] = handler

            session_username_ctx.set(username)
            logging.getLogger(__name__).info(
                "session_log_started", extra={"event": "session_log_started", "username": username, "file": path},
            )
        except Exception as e:
            logging.getLogger(__name__).warning(
                "session_log_setup_failed", extra={"error": str(e), "username": username}
            )

    threading.Thread(target=_setup_session_log, daemon=True).start()


# ---------------------------------------------------------------------------
# Formatter
# ---------------------------------------------------------------------------

# Standard LogRecord attributes — anything else on the record came from a
# caller's `extra={...}` dict and gets surfaced as its own JSON field.
_STANDARD_RECORD_KEYS = frozenset({
    "name", "msg", "args", "levelname", "levelno", "pathname", "filename",
    "module", "exc_info", "exc_text", "stack_info", "lineno", "funcName",
    "created", "msecs", "relativeCreated", "thread", "threadName",
    "processName", "process", "message", "taskName",
})


class _JsonFormatter(logging.Formatter):
    """One JSON object per line. No truncation — full field values always included.

    Any keys passed via `extra={...}` on the logging call are merged in as
    top-level fields (e.g. extra={"method": "GET", "body": "..."} becomes
    {"method": "GET", "body": "..."} in the JSON line), so the file stays
    directly greppable/parseable per field instead of one opaque string.
    """

    @staticmethod
    def _shorten(name: str) -> str:
        if name.startswith("promptops_app."):
            return name[len("promptops_app."):]
        return name

    def format(self, record: logging.LogRecord) -> str:  # noqa: A003
        payload: Dict[str, Any] = {
            "timestamp": self.formatTime(record, datefmt="%Y-%m-%d %H:%M:%S"),
            "level": record.levelname,
            "logger": self._shorten(record.name),
            "message": record.getMessage(),
        }
        for key, value in record.__dict__.items():
            if key not in _STANDARD_RECORD_KEYS and not key.startswith("_"):
                payload[key] = value
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str, ensure_ascii=False)


# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------

def configure_logging(level: Optional[str] = None) -> None:
    """Configure the root logger. Safe to call multiple times (idempotent).

    Parameters
    ----------
    level:
        Override log level string. Falls back to PROMPTOPS_LOG_LEVEL env var,
        then INFO.
    """
    global _CONFIGURED
    if _CONFIGURED:
        return
    _CONFIGURED = True

    raw_level = (level or _cfg.log_level).upper()
    log_level  = getattr(logging, raw_level, logging.INFO)

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(_JsonFormatter())

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(log_level)

    # Also write everything to a rotating file so logs survive past the
    # console scrollback and can be grepped/tailed directly (repo root is
    # bind-mounted into the container, so this shows up on the host too).
    #
    # Best-effort: the path is CWD-relative and the directory is a bind mount, so
    # whether it is writable depends on who last created the file and which uid the
    # process runs as. Because configure_logging() is called at import time from
    # app.main, an unwritable logs/app.log used to raise PermissionError and take
    # down the ENTIRE application (and every test collection) — losing stdout logs
    # too, over a convenience sink. Degrade to the stdout handler instead and say so.
    try:
        Path("logs").mkdir(exist_ok=True)
        file_handler = logging.handlers.RotatingFileHandler(
            "logs/app.log", maxBytes=10 * 1024 * 1024, backupCount=3, encoding="utf-8",
        )
    except OSError as exc:
        root.warning("File logging disabled (%s: %s) — logging to stdout only",
                     type(exc).__name__, exc)
    else:
        file_handler.setFormatter(_JsonFormatter())
        root.addHandler(file_handler)

    # Quiet chatty third-party libraries that flood logs at DEBUG/INFO
    for noisy in (
        "httpx", "httpcore", "urllib3", "botocore", "boto3",
        "openai", "watchdog", "asyncio",
    ):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    logging.getLogger(__name__).debug(
        "logging configured  level=%s  full_prompts=%s",
        raw_level, LOG_FULL_PROMPTS,
    )


# ---------------------------------------------------------------------------
# Duration context manager
# ---------------------------------------------------------------------------

@contextlib.contextmanager
def log_duration(
    operation: str,
    logger: Optional[logging.Logger] = None,
    extra: Optional[Dict[str, Any]] = None,
    level: int = logging.INFO,
) -> Generator[None, None, None]:
    """Context manager that logs wall-clock duration of the wrapped block.

    Parameters
    ----------
    operation:
        Dot-namespaced label, e.g. ``"generation_service.generate_content"``.
    logger:
        Logger to use. Defaults to ``promptops_app.timing``.
    extra:
        Key-value pairs appended to the log line for structured filtering.
        Do NOT include API keys, passwords, or raw prompt text.
    level:
        Log level for the success line (default INFO).
        Failures always log at ERROR with full exc_info.

    Example
    -------
        with log_duration("export_service.build_zip",
                          extra={"fmt": "zip", "user": user_name}):
            data = _build_zip(request)
    """
    _logger = logger or _timing_log
    _extra  = dict(extra or {})
    start   = time.monotonic()
    try:
        yield
    except Exception:
        elapsed = time.monotonic() - start
        _logger.error(
            "operation_failed",
            extra={"event": "operation_failed", "operation": operation,
                   "duration_seconds": round(elapsed, 3), **_extra},
            exc_info=True,
        )
        raise
    else:
        elapsed = time.monotonic() - start
        _logger.log(
            level,
            "operation_completed",
            extra={"event": "operation_completed", "operation": operation,
                   "duration_seconds": round(elapsed, 3), **_extra},
        )
