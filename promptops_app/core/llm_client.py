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


def call_openai(system_prompt: str, user_prompt: str) -> str:
    """Send a prompt to OpenAI and return the response text. No truncation applied."""
    if not settings.openai_api_key:
        return "ERROR: OpenAI API Key not configured."
    url     = "https://api.openai.com/v1/chat/completions"
    # Use the config's openai_model (defaults to "gpt-4o"); display names like
    # "GPT-5.4" should never reach this legacy function — route through
    # generate_text() → _invoke_primary() → _call_openai_raw() instead.
    target_model = settings.openai_model
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
        resp = _get_openai_session().post(
            url,
            headers={"Authorization": f"Bearer {settings.openai_api_key}"},
            json=data,
            timeout=(10, PROMPTOPS_API_TIMEOUT_SECONDS),
        )
        resp.raise_for_status()
        text = resp.json()["choices"][0]["message"]["content"]
        _log.info("llm_call_completed", extra={
            "event": "llm_call_completed", "provider": "openai", "model": target_model,
            "duration_ms": int((time.monotonic() - start) * 1000), "output": text,
        })
        return text
    except Exception as e:
        _log.error("llm_call_failed", extra={
            "event": "llm_call_failed", "provider": "openai", "model": target_model,
            "duration_ms": int((time.monotonic() - start) * 1000), "error": str(e),
        })
        return f"ERROR (OpenAI - {target_model}): {e}"


def _call_openai_raw(
    system_prompt: str,
    user_prompt: str,
    model: Optional[str] = None,
) -> LLMResponse:
    """Call OpenAI and return LLMResponse. Raises LLM*Error on failure.

    Used by llm_service for the retry/fallback reliability layer.
    ``model`` should be the actual OpenAI API model ID (e.g. "gpt-4o"),
    resolved by the model catalog before this call is made.
    """
    if not settings.openai_api_key:
        raise LLMAuthError("OpenAI API key not configured.")

    # Use the provided model ID directly; the catalog maps display names to
    # real API IDs before reaching this function — no silent redirect needed.
    target_model = model or settings.openai_model

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
        _log.info("llm_call_completed", extra={
            "event": "llm_call_completed", "provider": "openai", "model": target_model,
            "duration_ms": int((time.monotonic() - start) * 1000),
            "prompt_tokens": result.prompt_tokens, "completion_tokens": result.completion_tokens,
            "output": result.text,
        })
        return result
    except Exception as exc:
        _log.error("llm_call_failed", extra={
            "event": "llm_call_failed", "provider": "openai", "model": target_model,
            "duration_ms": int((time.monotonic() - start) * 1000), "error": str(exc),
        })
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


def call_bedrock(system_prompt: str, user_prompt: str, model_id: Optional[str] = None) -> str:
    """Send a prompt to AWS Bedrock and return the response text.

    ``model_id`` should be the actual Bedrock model ID (e.g.
    "global.anthropic.claude-sonnet-4-6-20250929-v1:0"), resolved by the
    model catalog.  Falls back to ``settings.bedrock_model_id`` when omitted.
    """
    target_model_id = model_id or settings.bedrock_model_id
    _log.info("llm_call_started", extra={
        "event": "llm_call_started", "provider": "bedrock", "model": target_model_id,
        "system_prompt": system_prompt, "user_prompt": user_prompt,
    })
    start = time.monotonic()
    try:
        client = _get_bedrock_client()
        body = json.dumps({
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": 16384,
            "system": system_prompt,
            "messages": [{"role": "user", "content": user_prompt}],
            "temperature": 0.3,
        })
        response = client.invoke_model(modelId=target_model_id, body=body)
        response_body = json.loads(response.get("body").read())
        text = response_body.get("content", [{}])[0].get("text", "ERROR: Empty response from Bedrock")
        _log.info("llm_call_completed", extra={
            "event": "llm_call_completed", "provider": "bedrock", "model": target_model_id,
            "duration_ms": int((time.monotonic() - start) * 1000), "output": text,
        })
        return text
    except Exception as e:
        _log.error("llm_call_failed", extra={
            "event": "llm_call_failed", "provider": "bedrock", "model": target_model_id,
            "duration_ms": int((time.monotonic() - start) * 1000), "error": str(e),
        })
        return f"ERROR (Bedrock - {target_model_id}): {e}"


def _call_bedrock_raw(
    system_prompt: str,
    user_prompt: str,
    model_id: Optional[str] = None,
) -> LLMResponse:
    """Call AWS Bedrock and return LLMResponse. Raises LLM*Error on failure.

    Used by llm_service for the retry/fallback reliability layer.
    ``model_id`` should be the actual Bedrock model ID resolved by the catalog;
    falls back to ``settings.bedrock_model_id`` when omitted.
    """
    target_model_id = model_id or settings.bedrock_model_id
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
        _log.info("llm_call_completed", extra={
            "event": "llm_call_completed", "provider": "bedrock", "model": target_model_id,
            "duration_ms": int((time.monotonic() - start) * 1000),
            "prompt_tokens": result.prompt_tokens, "completion_tokens": result.completion_tokens,
            "output": result.text,
        })
        return result
    except Exception as exc:
        _log.error("llm_call_failed", extra={
            "event": "llm_call_failed", "provider": "bedrock", "model": target_model_id,
            "duration_ms": int((time.monotonic() - start) * 1000), "error": str(exc),
        })
        raise


# =============================================================================
# Multi-Model Router
# =============================================================================

def call_llm(model_choice: str, system_prompt: str, user_prompt: str) -> str:
    """Route the LLM call to the appropriate provider based on model_choice.

    Uses the model catalog for routing; falls back to OpenAI for unrecognised
    model names so that legacy call sites are not broken.
    """
    from promptops_app.core.models import resolve_model
    m = resolve_model(model_choice)
    if m.provider == "bedrock":
        return call_bedrock(system_prompt, user_prompt, model_id=m.api_model_id)
    return call_openai(system_prompt, user_prompt)


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
