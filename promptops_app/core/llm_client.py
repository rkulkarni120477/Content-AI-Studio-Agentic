"""LLM client implementations.

Extracted from core/shared.py (Phase 1 refactoring).

Provides a provider-agnostic call_llm() entry point routing to:
  - OpenAI via HTTP (requests)
  - AWS Bedrock via boto3

Usage:
    from promptops_app.core.llm_client import call_llm, safe_json_loads
    result = call_llm(model_choice, system_prompt, user_prompt)

For new code use the reliability layer instead:
    from promptops_app.services.llm_service import generate_text

Note: module-level singletons are used for connection reuse (thread-safe lazy init).
"""

import json
import logging
import threading
import time
import requests
import boto3
from botocore.config import Config
from dataclasses import dataclass
from typing import Optional
import json_repair
from promptops_app.database import settings
from promptops_app.core.config import settings as _cfg
from promptops_app.services.usage_service import UsageLogContext, log_llm_usage_autocommit, estimate_cost
from promptops_app.services.budget_service import (
    BudgetExceededError,
    check_budget_autocommit,
    reconcile_budget_autocommit,
)

# Resolved once at import time from the central AppSettings.
PROMPTOPS_API_TIMEOUT_SECONDS = _cfg.llm_timeout_seconds

_log = logging.getLogger(__name__)


# =============================================================================
# Structured response & provider exceptions
# =============================================================================

@dataclass
class LLMResponse:
    """Raw provider response with token-usage metadata."""
    text: str
    model: str
    prompt_tokens: Optional[int] = None
    completion_tokens: Optional[int] = None


@dataclass
class LLMResult:
    """Observability record for one logical LLM call (may cover multiple attempts).

    Lives here (not llm_service.py) because this module's raw call functions are
    the ones that construct and log it — llm_service.py imports it back for its
    own return type instead of redefining it, to avoid a two-way circular import.
    """
    text: str
    model: str = ""
    prompt_tokens: Optional[int] = None
    completion_tokens: Optional[int] = None
    total_duration_s: float = 0.0
    status: str = "success"           # success | retry_success | fallback_success | error
    error_type: Optional[str] = None  # timeout | rate_limit | auth | provider | unknown
    langfuse_trace_id: Optional[str] = None  # set by _emit_langfuse_trace before logging (P1)

    @property
    def is_error(self) -> bool:
        return self.status == "error"


def _log_usage(result: "LLMResult", usage_ctx: Optional["UsageLogContext"]) -> None:
    """Write one LLMUsageLog row for this call. log_llm_usage_autocommit never raises.

    A call with no usage_ctx still gets logged, tagged unattributed rather than
    silently dropped — P0.2 replaces this default with real scope at each call site.
    """
    ctx = usage_ctx or UsageLogContext(entity_type="unattributed", entity_id="direct_call")
    log_llm_usage_autocommit(result, ctx)


def _emit_langfuse_trace(
    system_prompt: str,
    user_prompt: str,
    result: "LLMResult",
    usage_ctx: Optional["UsageLogContext"],
) -> Optional[str]:
    """Create one Langfuse generation for this call. Returns its trace_id, or None.

    Same choke point as _log_usage, same scope tags as P0's LLMUsageLog row — this
    is what P1.2's persisted langfuse_trace_id then links back to. Never raises:
    Langfuse being unreachable/unconfigured must never break a real LLM call, only
    silently skip tracing for it (P0's DB logging is unaffected either way).
    """
    try:
        from langfuse import get_client, propagate_attributes

        client = get_client()
        ctx = usage_ctx or UsageLogContext(entity_type="unattributed", entity_id="direct_call")
        usage_details = None
        if result.prompt_tokens is not None or result.completion_tokens is not None:
            usage_details = {
                "input": result.prompt_tokens or 0,
                "output": result.completion_tokens or 0,
            }
        with propagate_attributes(
            user_id=ctx.user_name or None,
            metadata={
                "project_id": ctx.project_id,
                "course_id": ctx.course_id,
                "entity_type": ctx.entity_type,
                "entity_id": ctx.entity_id,
            },
            tags=[ctx.entity_type] if ctx.entity_type else None,
        ):
            generation = client.start_observation(
                name=f"llm_call:{ctx.entity_type or 'unknown'}",
                as_type="generation",
                input={"system_prompt": system_prompt, "user_prompt": user_prompt},
                output=result.text if not result.is_error else None,
                model=result.model,
                usage_details=usage_details,
                level="ERROR" if result.is_error else "DEFAULT",
                status_message=result.text if result.is_error else None,
            )
            trace_id = generation.trace_id
            generation.end()
        return trace_id
    except Exception as exc:
        _log.debug("Langfuse trace emission skipped: %s", exc)
        return None


def _log_and_trace(
    system_prompt: str,
    user_prompt: str,
    result: "LLMResult",
    usage_ctx: Optional["UsageLogContext"],
) -> None:
    """Universal choke point for both P0's usage logging and P1's Langfuse tracing.

    Trace first so its id is on `result` before the DB row is written — persists
    the mapping P1.4's trace-detail endpoint looks up by.
    """
    result.langfuse_trace_id = _emit_langfuse_trace(system_prompt, user_prompt, result, usage_ctx)
    _log_usage(result, usage_ctx)


class LLMTimeoutError(Exception):
    """Provider did not respond within the configured timeout."""


class LLMRateLimitError(Exception):
    """Provider returned a rate-limit / throttle response (HTTP 429)."""


class LLMAuthError(Exception):
    """Missing or invalid credentials for the provider."""


class LLMProviderError(Exception):
    """Any other provider-side error (network, 5xx, empty body, etc.)."""


# =============================================================================
# OpenAI API Client
# =============================================================================

# ---------------------------------------------------------------------------
# Module-level singletons — replaces @st.cache_resource from the Streamlit app.
# Initialised once on first use; thread-safe via a lock.
# ---------------------------------------------------------------------------
_openai_session: Optional[requests.Session] = None
_openai_lock = threading.Lock()

_bedrock_client = None
_bedrock_lock = threading.Lock()


def _get_openai_session() -> requests.Session:
    """Return the shared OpenAI HTTP session, creating it on first call."""
    global _openai_session
    if _openai_session is None:
        with _openai_lock:
            if _openai_session is None:
                sess = requests.Session()
                sess.headers.update({"Content-Type": "application/json"})
                _openai_session = sess
    return _openai_session


def call_openai(system_prompt: str, user_prompt: str, usage_ctx: Optional["UsageLogContext"] = None) -> str:
    """Send a prompt to OpenAI and return the response text. No truncation applied.

    Thin wrapper — delegates to _call_openai_raw (which does the real HTTP call,
    logging, and usage-tracking) and converts its raised LLM*Error back to this
    function's existing "ERROR: ..." string-return contract, so callers using
    output.startswith("ERROR") keep working unchanged. Pass usage_ctx to attribute
    this call's cost to a project/course/user in llm_usage_logs.
    """
    if not settings.openai_api_key:
        return "ERROR: OpenAI API Key not configured."
    # display names like "GPT-5.4" should never reach this legacy function —
    # route through generate_text() → _invoke_primary() → _call_openai_raw() instead.
    target_model = settings.openai_model
    try:
        return _call_openai_raw(system_prompt, user_prompt, model=target_model, usage_ctx=usage_ctx).text
    except Exception as e:
        return f"ERROR (OpenAI - {target_model}): {e}"


def _call_openai_raw(
    system_prompt: str,
    user_prompt: str,
    model: Optional[str] = None,
    usage_ctx: Optional["UsageLogContext"] = None,
) -> LLMResponse:
    """Call OpenAI and return LLMResponse. Raises LLM*Error on failure.

    Used by llm_service for the retry/fallback reliability layer.
    ``model`` should be the actual OpenAI API model ID (e.g. "gpt-4o"),
    resolved by the model catalog before this call is made.

    This is the universal usage-logging choke point: every attempt writes one
    LLMUsageLog row (success or error), tagged from ``usage_ctx`` when given.
    """
    if not settings.openai_api_key:
        raise LLMAuthError("OpenAI API key not configured.")

    # Use the provided model ID directly; the catalog maps display names to
    # real API IDs before reaching this function — no silent redirect needed.
    target_model = model or settings.openai_model

    # P2.3: pre-flight quota check, same choke point as P0/P1.2's logging/tracing.
    # Raises BudgetExceededError on a confirmed breach — deliberately OUTSIDE the
    # try/except below, so it propagates as itself rather than being logged and
    # re-raised as a provider failure (no provider call was ever attempted).
    check_result = check_budget_autocommit(
        usage_ctx, system_prompt=system_prompt, user_prompt=user_prompt, model=target_model,
    )

    url = "https://api.openai.com/v1/chat/completions"
    data = {
        "model": target_model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": 0.3,
        "max_tokens": 16384,
    }
    _log.info("llm_call_started", extra={
        "event": "llm_call_started", "provider": "openai", "model": target_model,
        "system_prompt": system_prompt, "user_prompt": user_prompt,
    })
    start = time.monotonic()
    try:
        try:
            resp = _get_openai_session().post(
                url,
                headers={"Authorization": f"Bearer {settings.openai_api_key}"},
                json=data,
                timeout=(10, PROMPTOPS_API_TIMEOUT_SECONDS),
            )
        except requests.exceptions.Timeout as exc:
            raise LLMTimeoutError(f"OpenAI request timed out after {PROMPTOPS_API_TIMEOUT_SECONDS}s") from exc
        except requests.exceptions.ConnectionError as exc:
            raise LLMProviderError(f"OpenAI connection error: {exc}") from exc
        except requests.exceptions.RequestException as exc:
            raise LLMProviderError(f"OpenAI request error: {exc}") from exc

        if resp.status_code == 401:
            raise LLMAuthError("OpenAI authentication failed (HTTP 401).")
        if resp.status_code == 429:
            raise LLMRateLimitError("OpenAI rate limit exceeded (HTTP 429).")
        try:
            resp.raise_for_status()
        except requests.exceptions.HTTPError as exc:
            raise LLMProviderError(f"OpenAI HTTP error {resp.status_code}: {exc}") from exc

        body = resp.json()
        usage = body.get("usage", {})
        result = LLMResponse(
            text=body["choices"][0]["message"]["content"],
            model=target_model,
            prompt_tokens=usage.get("prompt_tokens"),
            completion_tokens=usage.get("completion_tokens"),
        )
        duration_s = time.monotonic() - start
        _log.info("llm_call_completed", extra={
            "event": "llm_call_completed", "provider": "openai", "model": target_model,
            "duration_ms": int(duration_s * 1000),
            "prompt_tokens": result.prompt_tokens, "completion_tokens": result.completion_tokens,
            "output": result.text,
        })
        _log_and_trace(system_prompt, user_prompt, LLMResult(
            text=result.text, model=target_model,
            prompt_tokens=result.prompt_tokens, completion_tokens=result.completion_tokens,
            total_duration_s=duration_s, status="success",
        ), usage_ctx)
        # P2.9: true up the worst-case reservation to the real cost now that
        # actual token counts are known.
        reconcile_budget_autocommit(
            check_result.reservations,
            estimate_cost(target_model, result.prompt_tokens or 0, result.completion_tokens or 0),
        )
        return result
    except Exception as exc:
        duration_s = time.monotonic() - start
        _log.error("llm_call_failed", extra={
            "event": "llm_call_failed", "provider": "openai", "model": target_model,
            "duration_ms": int(duration_s * 1000), "error": str(exc),
        })
        _log_and_trace(system_prompt, user_prompt, LLMResult(
            text=str(exc), model=target_model, total_duration_s=duration_s, status="error",
        ), usage_ctx)
        # Release the worst-case reservation — a failed call cost nothing (or
        # near enough); without this every provider error permanently leaks
        # reserved budget until the period rolls over.
        reconcile_budget_autocommit(check_result.reservations, 0.0)
        raise


# =============================================================================
# AWS Bedrock API Client
# =============================================================================

def _get_bedrock_client():
    """Return the shared Boto3 Bedrock client, creating it on first call."""
    global _bedrock_client
    if _bedrock_client is None:
        with _bedrock_lock:
            if _bedrock_client is None:
                boto_config = Config(read_timeout=600, connect_timeout=30)
                _bedrock_client = boto3.client(
                    service_name="bedrock-runtime",
                    region_name=settings.aws_region,
                    aws_access_key_id=settings.aws_access_key,
                    aws_secret_access_key=settings.aws_secret_key,
                    config=boto_config,
                )
    return _bedrock_client


def call_bedrock(system_prompt: str, user_prompt: str, model_id: Optional[str] = None, usage_ctx: Optional["UsageLogContext"] = None) -> str:
    """Send a prompt to AWS Bedrock and return the response text.

    ``model_id`` should be the actual Bedrock model ID (e.g.
    "global.anthropic.claude-sonnet-4-6-20250929-v1:0"), resolved by the
    model catalog.  Falls back to ``settings.bedrock_model_id`` when omitted.

    Thin wrapper — delegates to _call_bedrock_raw (real boto3 call, logging,
    usage-tracking) and converts its raised LLM*Error back to this function's
    existing "ERROR: ..." string-return contract. Pass usage_ctx to attribute
    this call's cost to a project/course/user in llm_usage_logs.

    Note: the legacy empty-response case returned the bare string
    "ERROR: Empty response from Bedrock"; _call_bedrock_raw raises
    LLMProviderError for that case instead, so this now returns
    "ERROR (Bedrock - <model>): Bedrock returned an empty response body."
    Both satisfy every real caller's .startswith("ERROR") check; only the
    exact wording differs for this one edge case.
    """
    target_model_id = model_id or settings.bedrock_model_id
    try:
        return _call_bedrock_raw(system_prompt, user_prompt, model_id=target_model_id, usage_ctx=usage_ctx).text
    except Exception as e:
        return f"ERROR (Bedrock - {target_model_id}): {e}"


def _call_bedrock_raw(
    system_prompt: str,
    user_prompt: str,
    model_id: Optional[str] = None,
    usage_ctx: Optional["UsageLogContext"] = None,
) -> LLMResponse:
    """Call AWS Bedrock and return LLMResponse. Raises LLM*Error on failure.

    Used by llm_service for the retry/fallback reliability layer.
    ``model_id`` should be the actual Bedrock model ID resolved by the catalog;
    falls back to ``settings.bedrock_model_id`` when omitted.

    This is the universal usage-logging choke point: every attempt writes one
    LLMUsageLog row (success or error), tagged from ``usage_ctx`` when given.
    """
    target_model_id = model_id or settings.bedrock_model_id

    # P2.3: pre-flight quota check — see _call_openai_raw's identical comment.
    check_result = check_budget_autocommit(
        usage_ctx, system_prompt=system_prompt, user_prompt=user_prompt, model=target_model_id,
    )

    _log.info("llm_call_started", extra={
        "event": "llm_call_started", "provider": "bedrock", "model": target_model_id,
        "system_prompt": system_prompt, "user_prompt": user_prompt,
    })
    start = time.monotonic()
    try:
        try:
            client = _get_bedrock_client()
        except Exception as exc:
            raise LLMProviderError(f"Bedrock client init failed: {exc}") from exc

        body = json.dumps({
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": 16384,
            "system": system_prompt,
            "messages": [{"role": "user", "content": user_prompt}],
            "temperature": 0.3,
        })

        try:
            response = client.invoke_model(modelId=target_model_id, body=body)
        except Exception as exc:
            err_lower = str(exc).lower()
            if any(k in err_lower for k in ("timeout", "timed out", "read timeout", "connect timeout")):
                raise LLMTimeoutError(f"Bedrock request timed out: {exc}") from exc
            if any(k in err_lower for k in ("throttl", "rate", "too many")):
                raise LLMRateLimitError(f"Bedrock throttled: {exc}") from exc
            if any(k in err_lower for k in ("access denied", "not authorized", "credential", "auth")):
                raise LLMAuthError(f"Bedrock auth error: {exc}") from exc
            raise LLMProviderError(f"Bedrock invoke error: {exc}") from exc

        response_body = json.loads(response.get("body").read())
        text = response_body.get("content", [{}])[0].get("text", "")
        if not text:
            raise LLMProviderError("Bedrock returned an empty response body.")

        usage = response_body.get("usage", {})
        result = LLMResponse(
            text=text,
            model=target_model_id,
            prompt_tokens=usage.get("input_tokens"),
            completion_tokens=usage.get("output_tokens"),
        )
        duration_s = time.monotonic() - start
        _log.info("llm_call_completed", extra={
            "event": "llm_call_completed", "provider": "bedrock", "model": target_model_id,
            "duration_ms": int(duration_s * 1000),
            "prompt_tokens": result.prompt_tokens, "completion_tokens": result.completion_tokens,
            "output": result.text,
        })
        _log_and_trace(system_prompt, user_prompt, LLMResult(
            text=result.text, model=target_model_id,
            prompt_tokens=result.prompt_tokens, completion_tokens=result.completion_tokens,
            total_duration_s=duration_s, status="success",
        ), usage_ctx)
        reconcile_budget_autocommit(
            check_result.reservations,
            estimate_cost(target_model_id, result.prompt_tokens or 0, result.completion_tokens or 0),
        )
        return result
    except Exception as exc:
        duration_s = time.monotonic() - start
        _log.error("llm_call_failed", extra={
            "event": "llm_call_failed", "provider": "bedrock", "model": target_model_id,
            "duration_ms": int(duration_s * 1000), "error": str(exc),
        })
        _log_and_trace(system_prompt, user_prompt, LLMResult(
            text=str(exc), model=target_model_id, total_duration_s=duration_s, status="error",
        ), usage_ctx)
        reconcile_budget_autocommit(check_result.reservations, 0.0)
        raise


# =============================================================================
# Multi-Model Router
# =============================================================================

def call_llm(model_choice: str, system_prompt: str, user_prompt: str, usage_ctx: Optional["UsageLogContext"] = None) -> str:
    """Route the LLM call to the appropriate provider based on model_choice.

    Uses the model catalog for routing; falls back to OpenAI for unrecognised
    model names so that legacy call sites are not broken.
    """
    from promptops_app.core.models import resolve_model
    m = resolve_model(model_choice)
    if m.provider == "bedrock":
        return call_bedrock(system_prompt, user_prompt, model_id=m.api_model_id, usage_ctx=usage_ctx)
    return call_openai(system_prompt, user_prompt, usage_ctx=usage_ctx)


def safe_json_loads(text: str) -> dict:
    """Robust JSON parsing using json_repair to handle markdown fences and artifacts."""
    if not text:
        return {}
    try:
        return json_repair.loads(text)
    except Exception:
        # Fallback cleaning if json_repair also fails
        text = (
            text.strip()
            .replace("```json", "")
            .replace("```", "")
            .replace("'''json", "")
            .replace("'''", "")
            .strip()
        )
        try:
            return json.loads(text)
        except Exception:
            return {}
