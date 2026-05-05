"""LLM reliability service.

All LLM calls must go through generate_text() or generate_with_metadata().
Direct use of llm_client functions (call_llm, call_openai, call_bedrock) from
pages or services is deprecated — route through this module instead.

Reliability pipeline per call:
  1. Try primary model (selected by model_choice).
  2. On timeout: retry once with the same model.
  3. If still failing: try cross-provider fallback (OpenAI ↔ Bedrock).
  4. If fallback also fails: return a user-safe error string.

Observability — every call returns LLMResult tracking:
  - model actually used
  - prompt/completion token counts (when provided by the API)
  - total wall-clock duration
  - status  : success | retry_success | fallback_success | error
  - error_type: timeout | rate_limit | auth | provider | unknown

Configuration (via .env):
  PROMPTOPS_LLM_MAX_RETRIES       (default: 1)
  PROMPTOPS_LLM_FALLBACK_ENABLED  (default: true)
"""

import logging
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Optional

from promptops_app.database import settings
from promptops_app.core.config import settings as _cfg
from promptops_app.core.llm_client import (
    LLMAuthError,
    LLMProviderError,
    LLMRateLimitError,
    LLMResponse,
    LLMTimeoutError,
    _call_bedrock_raw,
    _call_openai_raw,
)

if TYPE_CHECKING:
    from promptops_app.services.usage_service import UsageLogContext

_log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------

@dataclass
class LLMResult:
    """Observability record for one logical LLM call (may cover multiple attempts)."""
    text: str
    model: str = ""
    prompt_tokens: Optional[int] = None
    completion_tokens: Optional[int] = None
    total_duration_s: float = 0.0
    status: str = "success"           # success | retry_success | fallback_success | error
    error_type: Optional[str] = None  # timeout | rate_limit | auth | provider | unknown

    @property
    def is_error(self) -> bool:
        return self.status == "error"


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _is_bedrock(model_choice: str) -> bool:
    lc = model_choice.lower()
    return "bedrock" in lc or "sonnet" in lc


def _classify(exc: Exception) -> str:
    if isinstance(exc, LLMTimeoutError):
        return "timeout"
    if isinstance(exc, LLMRateLimitError):
        return "rate_limit"
    if isinstance(exc, LLMAuthError):
        return "auth"
    if isinstance(exc, LLMProviderError):
        return "provider"
    return "unknown"


_USER_MESSAGES = {
    "timeout":    "The AI model is taking too long to respond. Please try again in a moment.",
    "rate_limit": "The AI service is currently busy. Please wait a moment and try again.",
    "auth":       "AI service authentication error. Please contact your administrator.",
    "provider":   "The AI service returned an error. Please try again.",
    "unknown":    "Unable to generate content. Please try again or contact support.",
}


def _user_msg(error_type: str) -> str:
    return _USER_MESSAGES.get(error_type, _USER_MESSAGES["unknown"])


def _invoke_primary(model_choice: str, system: str, user: str) -> LLMResponse:
    if _is_bedrock(model_choice):
        return _call_bedrock_raw(system, user)
    return _call_openai_raw(system, user)


def _invoke_fallback(model_choice: str, system: str, user: str) -> LLMResponse:
    if _is_bedrock(model_choice):
        if not settings.openai_api_key:
            raise LLMAuthError("Fallback OpenAI key not configured.")
        return _call_openai_raw(system, user)
    else:
        if not (settings.aws_access_key and settings.aws_secret_key):
            raise LLMAuthError("Fallback Bedrock credentials not configured.")
        return _call_bedrock_raw(system, user)


def _make_result(resp: LLMResponse, start: float, status: str) -> LLMResult:
    return LLMResult(
        text=resp.text,
        model=resp.model,
        prompt_tokens=resp.prompt_tokens,
        completion_tokens=resp.completion_tokens,
        total_duration_s=time.monotonic() - start,
        status=status,
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def _log_usage_safe(result: LLMResult, usage_ctx: "UsageLogContext") -> None:
    """Write usage log using a fresh session. Never raises."""
    try:
        from promptops_app.services.usage_service import log_llm_usage_autocommit
        log_llm_usage_autocommit(result, usage_ctx)
    except Exception as exc:
        _log.debug("Usage log skipped: %s", exc)


def generate_with_metadata(
    model_choice: str,
    system_prompt: str,
    user_prompt: str,
    usage_ctx: Optional["UsageLogContext"] = None,
) -> LLMResult:
    """Full reliability pipeline. Never raises — always returns an LLMResult.

    On error: is_error=True, text holds a user-safe message, error_type
    describes the failure category for downstream logging or UI display.

    Pass usage_ctx to record token usage, cost, and latency in llm_usage_logs.
    """
    start = time.monotonic()
    last_exc: Optional[Exception] = None
    last_error_type = "unknown"

    # ── Attempt 1: primary model ─────────────────────────────────────────────
    try:
        resp = _invoke_primary(model_choice, system_prompt, user_prompt)
        _log.debug("LLM success [model=%s duration=%.1fs]", resp.model, time.monotonic() - start)
        result = _make_result(resp, start, "success")
        if usage_ctx is not None:
            _log_usage_safe(result, usage_ctx)
        return result
    except Exception as exc:
        last_exc = exc
        last_error_type = _classify(exc)
        _log.warning(
            "LLM primary attempt failed [model=%s type=%s]: %s",
            model_choice, last_error_type, exc,
        )

    # ── Attempt 2: retry on timeout only ────────────────────────────────────
    if last_error_type == "timeout" and _cfg.llm_retry_count >= 1:
        _log.info("Retrying primary model after timeout [model=%s]", model_choice)
        try:
            resp = _invoke_primary(model_choice, system_prompt, user_prompt)
            _log.info(
                "Primary succeeded on retry [model=%s duration=%.1fs]",
                resp.model, time.monotonic() - start,
            )
            result = _make_result(resp, start, "retry_success")
            if usage_ctx is not None:
                _log_usage_safe(result, usage_ctx)
            return result
        except Exception as exc:
            last_exc = exc
            last_error_type = _classify(exc)
            _log.warning(
                "LLM retry also failed [model=%s type=%s]: %s",
                model_choice, last_error_type, exc,
            )

    # ── Attempt 3: cross-provider fallback ───────────────────────────────────
    fallback_label = "OpenAI" if _is_bedrock(model_choice) else "Bedrock"
    if _cfg.llm_fallback_enabled and last_error_type not in ("auth",):
        _log.info(
            "Trying fallback provider [primary=%s fallback=%s]",
            model_choice, fallback_label,
        )
        try:
            resp = _invoke_fallback(model_choice, system_prompt, user_prompt)
            _log.info(
                "Fallback succeeded [provider=%s duration=%.1fs]",
                fallback_label, time.monotonic() - start,
            )
            result = _make_result(resp, start, "fallback_success")
            if usage_ctx is not None:
                _log_usage_safe(result, usage_ctx)
            return result
        except Exception as exc:
            last_exc = exc
            last_error_type = _classify(exc)
            _log.error(
                "LLM fallback also failed [primary=%s fallback=%s type=%s]: %s",
                model_choice, fallback_label, last_error_type, exc,
                exc_info=True,
            )

    # ── All attempts exhausted ───────────────────────────────────────────────
    _log.error(
        "All LLM attempts failed [model=%s type=%s duration=%.1fs last_exc=%s]",
        model_choice, last_error_type, time.monotonic() - start, last_exc,
        exc_info=last_exc,
    )
    result = LLMResult(
        text=_user_msg(last_error_type),
        model=model_choice,
        total_duration_s=time.monotonic() - start,
        status="error",
        error_type=last_error_type,
    )
    if usage_ctx is not None:
        _log_usage_safe(result, usage_ctx)
    return result


def generate_text(
    model_choice: str,
    system_prompt: str,
    user_prompt: str,
    usage_ctx: Optional["UsageLogContext"] = None,
) -> str:
    """Reliability-wrapped LLM call. Returns text on success.

    On error returns a string starting with "ERROR: " so that all existing
    callers using ``output.startswith("ERROR")`` continue to work unchanged.
    Pass usage_ctx to enable automatic usage logging in llm_usage_logs.
    """
    result = generate_with_metadata(model_choice, system_prompt, user_prompt, usage_ctx)
    if result.is_error:
        return f"ERROR: {result.text}"
    return result.text
