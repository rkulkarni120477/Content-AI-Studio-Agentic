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
import logging
import sys
import time
from typing import Any, Dict, Generator, Optional

from promptops_app.core.config import settings as _cfg

# ---------------------------------------------------------------------------
# Public feature flag — re-exported so callers don't need to import config
# ---------------------------------------------------------------------------

LOG_FULL_PROMPTS: bool = _cfg.enable_prompt_logging

_CONFIGURED = False
_timing_log = logging.getLogger("promptops_app.timing")


# ---------------------------------------------------------------------------
# Formatter
# ---------------------------------------------------------------------------

class _LocalFormatter(logging.Formatter):
    """Readable single-line formatter — optional ANSI color for TTY output."""

    _COLORS: dict[str, str] = {
        "DEBUG":    "\033[36m",    # cyan
        "INFO":     "\033[32m",    # green
        "WARNING":  "\033[33m",    # yellow
        "ERROR":    "\033[31m",    # red
        "CRITICAL": "\033[1;31m",  # bold red
    }
    _RESET = "\033[0m"
    _DIM   = "\033[2m"

    def __init__(self, use_color: bool = False) -> None:
        super().__init__()
        self._use_color = use_color

    @staticmethod
    def _shorten(name: str) -> str:
        if name.startswith("promptops_app."):
            return name[len("promptops_app."):]
        return name

    def format(self, record: logging.LogRecord) -> str:  # noqa: A003
        ts   = self.formatTime(record, datefmt="%H:%M:%S")
        lvl  = record.levelname
        name = self._shorten(record.name)
        msg  = record.getMessage()

        if record.exc_info:
            msg = f"{msg}\n{self.formatException(record.exc_info)}"

        if self._use_color:
            c, r, d = self._COLORS.get(lvl, ""), self._RESET, self._DIM
            return f"{d}{ts}{r}  {c}{lvl:<8}{r}  {d}{name}{r}  {msg}"

        return f"{ts}  {lvl:<8}  {name}  {msg}"


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

    use_color = sys.stdout.isatty() or _cfg.log_color

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(_LocalFormatter(use_color=use_color))

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(log_level)

    # Quiet chatty third-party libraries that flood logs at DEBUG/INFO
    for noisy in (
        "httpx", "httpcore", "urllib3", "botocore", "boto3",
        "openai", "watchdog", "asyncio",
    ):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    logging.getLogger(__name__).debug(
        "logging configured  level=%s  color=%s  full_prompts=%s",
        raw_level, use_color, LOG_FULL_PROMPTS,
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
    _logger     = logger or _timing_log
    _extra_str  = _fmt_extra(extra or {})
    start       = time.monotonic()
    try:
        yield
    except Exception:
        elapsed = time.monotonic() - start
        _logger.error(
            "FAILED  %s  duration=%.3fs%s",
            operation, elapsed, _extra_str,
            exc_info=True,
        )
        raise
    else:
        elapsed = time.monotonic() - start
        _logger.log(
            level,
            "%s  duration=%.3fs%s",
            operation, elapsed, _extra_str,
        )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _fmt_extra(extra: Dict[str, Any]) -> str:
    if not extra:
        return ""
    return "  " + "  ".join(f"{k}={v!r}" for k, v in extra.items())
