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

Note: @st.cache_resource is used here for connection reuse.
      Replace with functools.lru_cache or DI pattern before FastAPI migration.
"""

import json
import requests
import boto3
from botocore.config import Config
from dataclasses import dataclass
from typing import Optional
import streamlit as st
import json_repair
from promptops_app.database import settings
from promptops_app.core.config import settings as _cfg

# Resolved once at import time from the central AppSettings.
PROMPTOPS_API_TIMEOUT_SECONDS = _cfg.llm_timeout_seconds


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

@st.cache_resource(show_spinner=False)
def _get_openai_session() -> requests.Session:
    sess = requests.Session()
    sess.headers.update({"Content-Type": "application/json"})
    return sess


def call_openai(system_prompt: str, user_prompt: str) -> str:
    """Send a prompt to OpenAI and return the response text. No truncation applied."""
    if not settings.openai_api_key:
        return "ERROR: OpenAI API Key not configured."
    url = "https://api.openai.com/v1/chat/completions"

    # Model Mapping: Redirect non-existent gpt-5.4 to gpt-4o
    target_model = settings.openai_model
    if target_model == "gpt-5.4":
        target_model = "gpt-4o"

    # max_tokens=16384 prevents truncation on long CDD/Blueprint outputs
    data = {
        "model": target_model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": 0.3,
        "max_tokens": 16384,
    }
    try:
        resp = _get_openai_session().post(
            url,
            headers={"Authorization": f"Bearer {settings.openai_api_key}"},
            json=data,
            timeout=(10, PROMPTOPS_API_TIMEOUT_SECONDS),
        )
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"]
    except Exception as e:
        return f"ERROR (OpenAI - {target_model}): {e}"


def _call_openai_raw(
    system_prompt: str,
    user_prompt: str,
    model: Optional[str] = None,
) -> LLMResponse:
    """Call OpenAI and return LLMResponse. Raises LLM*Error on failure.

    Used by llm_service for the retry/fallback reliability layer.
    """
    if not settings.openai_api_key:
        raise LLMAuthError("OpenAI API key not configured.")

    target_model = model or settings.openai_model
    if target_model == "gpt-5.4":
        target_model = "gpt-4o"

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
    return LLMResponse(
        text=body["choices"][0]["message"]["content"],
        model=target_model,
        prompt_tokens=usage.get("prompt_tokens"),
        completion_tokens=usage.get("completion_tokens"),
    )


# =============================================================================
# AWS Bedrock API Client
# =============================================================================

@st.cache_resource(show_spinner=False)
def _get_bedrock_client():
    """Create the Boto3 Bedrock client once and reuse it across all calls."""
    boto_config = Config(read_timeout=600, connect_timeout=30)
    return boto3.client(
        service_name="bedrock-runtime",
        region_name=settings.aws_region,
        aws_access_key_id=settings.aws_access_key,
        aws_secret_access_key=settings.aws_secret_key,
        config=boto_config,
    )


def call_bedrock(system_prompt: str, user_prompt: str) -> str:
    """Send a prompt to AWS Bedrock and return the response text."""
    try:
        client = _get_bedrock_client()

        body = json.dumps({
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": 16384,
            "system": system_prompt,
            "messages": [{"role": "user", "content": user_prompt}],
            "temperature": 0.3,
        })

        # Model Mapping: Redirect non-existent Sonnet 4.5 to Claude 3.5 Sonnet
        target_model_id = settings.bedrock_model_id
        if "sonnet-4-5" in target_model_id.lower():
            target_model_id = "global.anthropic.claude-sonnet-4-5-20250929-v1:0"

        response = client.invoke_model(modelId=target_model_id, body=body)
        response_body = json.loads(response.get("body").read())
        return response_body.get("content", [{}])[0].get("text", "ERROR: Empty response from Bedrock")
    except Exception as e:
        return f"ERROR (Bedrock - {settings.bedrock_model_id}): {e}"


def _call_bedrock_raw(system_prompt: str, user_prompt: str) -> LLMResponse:
    """Call AWS Bedrock and return LLMResponse. Raises LLM*Error on failure.

    Used by llm_service for the retry/fallback reliability layer.
    """
    try:
        client = _get_bedrock_client()
    except Exception as exc:
        raise LLMProviderError(f"Bedrock client init failed: {exc}") from exc

    target_model_id = settings.bedrock_model_id
    if "sonnet-4-5" in target_model_id.lower():
        target_model_id = "global.anthropic.claude-sonnet-4-5-20250929-v1:0"

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
    return LLMResponse(
        text=text,
        model=target_model_id,
        prompt_tokens=usage.get("input_tokens"),
        completion_tokens=usage.get("output_tokens"),
    )


# =============================================================================
# Multi-Model Router
# =============================================================================

def call_llm(model_choice: str, system_prompt: str, user_prompt: str) -> str:
    """Route the LLM call to the appropriate provider based on model_choice."""
    if model_choice == "Sonnet 4.5 (Bedrock)":
        return call_bedrock(system_prompt, user_prompt)
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
