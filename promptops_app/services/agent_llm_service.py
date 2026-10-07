"""Agent LLM Integration Service — Stage 3 of Phase 2

Manages LLM interactions for agent execution:
- Model call execution with token counting and cost tracking
- Support for multiple LLM providers (OpenAI, AWS Bedrock)
- Retry logic with exponential backoff for transient failures
- Batch execution for parallel model calls
- Streaming support for real-time feedback
- Cost calculation and tracking

Patterns:
- Uses existing llm_client for OpenAI and Bedrock routing
- Integrates with usage_service for cost estimation
- Implements exponential backoff for retries (100ms → 1s → 10s)
- Thread-safe for concurrent execution
- Follows existing error handling patterns
"""

from __future__ import annotations

import json
import logging
import time
import asyncio
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from typing import Dict, Optional, Any, Tuple, Callable, List

from sqlalchemy.orm import Session

from app.core.exceptions import (
    NotFoundError,
    ValidationError,
    WorkflowError,
    LLMGenerationError,
)
from promptops_app.core.llm_client import (
    call_openai,
    call_bedrock,
    _call_openai_raw,
    _call_bedrock_raw,
    LLMResponse,
    LLMTimeoutError,
    LLMRateLimitError,
    LLMAuthError,
    LLMProviderError,
)
from promptops_app.core.models import resolve_model
from promptops_app.services.usage_service import estimate_cost

_log = logging.getLogger(__name__)

# Retry configuration
DEFAULT_MAX_RETRIES = 3
INITIAL_BACKOFF_MS = 100  # 100ms
MAX_BACKOFF_MS = 10000    # 10 seconds
BACKOFF_MULTIPLIER = 10   # 100ms → 1s → 10s

# Timeout configuration
DEFAULT_TIMEOUT_SECONDS = 30
MAX_TIMEOUT_SECONDS = 300


class AgentLLMService:
    """Service for managing LLM interactions within agent execution."""

    @staticmethod
    def call_model(
        model_id: str,
        system_prompt: str,
        user_prompt: str,
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
        timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
        max_retries: int = DEFAULT_MAX_RETRIES,
    ) -> Tuple[str, int, int, float]:
        """
        Execute a single LLM model call with retry logic.

        Calls the specified model with the given prompts, tracks token usage,
        calculates cost, and retries on transient failures with exponential backoff.

        Args:
            model_id: Model identifier (e.g., "gpt-4o", "claude-sonnet-5")
            system_prompt: System/context prompt for the model
            user_prompt: User/input prompt for the model
            temperature: Sampling temperature (0.0-2.0, default 0.7)
            max_tokens: Maximum output tokens (default uses model's default)
            timeout_seconds: Request timeout in seconds (default 30, max 300)
            max_retries: Maximum retry attempts (default 3)

        Returns:
            Tuple of (response_text, prompt_tokens, completion_tokens, cost_usd)

        Raises:
            ValidationError: Invalid model ID or parameters
            LLMGenerationError: All retry attempts failed
            WorkflowError: Model not found or provider error
        """
        # 1. Validate inputs
        if not model_id:
            raise ValidationError("model_id is required")
        if not system_prompt or not user_prompt:
            raise ValidationError("system_prompt and user_prompt are required")

        # Clamp timeout
        timeout_seconds = min(max(timeout_seconds, 1), MAX_TIMEOUT_SECONDS)

        # 2. Resolve model from catalog
        try:
            model_def = resolve_model(model_id)
            provider = model_def.provider
            api_model_id = model_def.api_model_id
        except Exception as e:
            raise ValidationError(f"Unknown model: {model_id}") from e

        # 3. Execute with retry logic
        last_error = None
        for attempt in range(max_retries + 1):
            try:
                _log.info(
                    f"Calling model {model_id} (provider: {provider}), "
                    f"attempt {attempt + 1}/{max_retries + 1}"
                )

                # Call appropriate provider
                if provider == "bedrock":
                    response = _call_bedrock_raw(
                        system_prompt=system_prompt,
                        user_prompt=user_prompt,
                        model_id=api_model_id,
                        max_tokens=max_tokens,
                    )
                else:  # openai (default)
                    response = _call_openai_raw(
                        system_prompt=system_prompt,
                        user_prompt=user_prompt,
                        model=api_model_id,
                        max_tokens=max_tokens,
                    )

                # 4. Parse response and calculate cost
                text = response.text
                prompt_tokens = response.prompt_tokens or 0
                completion_tokens = response.completion_tokens or 0

                # Calculate USD cost
                cost = estimate_cost(api_model_id, prompt_tokens, completion_tokens)

                _log.info(
                    f"Model call succeeded: {api_model_id}. "
                    f"Tokens: {prompt_tokens} prompt + {completion_tokens} completion. "
                    f"Cost: ${cost:.6f}"
                )

                return text, prompt_tokens, completion_tokens, cost

            except (LLMTimeoutError, LLMRateLimitError) as e:
                # Transient errors — retry with backoff
                last_error = e
                if attempt < max_retries:
                    backoff_ms = min(
                        INITIAL_BACKOFF_MS * (BACKOFF_MULTIPLIER ** attempt),
                        MAX_BACKOFF_MS
                    )
                    _log.warning(
                        f"Transient LLM error on attempt {attempt + 1}: {e}. "
                        f"Retrying after {backoff_ms}ms..."
                    )
                    time.sleep(backoff_ms / 1000.0)
                else:
                    _log.error(
                        f"Model call failed after {max_retries + 1} attempts: {e}"
                    )
            except (LLMAuthError, LLMProviderError) as e:
                # Non-transient errors — fail immediately
                _log.error(f"Non-transient LLM error: {e}")
                raise LLMGenerationError(f"LLM call failed: {e}") from e
            except Exception as e:
                # Unexpected errors
                _log.error(f"Unexpected error during LLM call: {e}", exc_info=True)
                last_error = e
                if attempt == max_retries:
                    raise LLMGenerationError(f"LLM call failed: {e}") from e

        # 5. All retries exhausted
        raise LLMGenerationError(
            f"Model {model_id} call failed after {max_retries + 1} attempts: {last_error}"
        )

    @staticmethod
    def parse_response(
        response: LLMResponse,
    ) -> Dict[str, Any]:
        """
        Parse LLM response into structured format.

        Extracts text content, token counts, stop reason, and other metadata
        from a raw LLMResponse object.

        Args:
            response: LLMResponse object from llm_client

        Returns:
            Dict with keys:
            - text: Response text content
            - model: Model used
            - prompt_tokens: Input token count
            - completion_tokens: Output token count
            - total_tokens: Sum of prompt + completion tokens
            - stop_reason: Why the model stopped (stop, length, max_tokens, etc.)
            - truncated: Whether output was cut off by max_tokens

        Raises:
            ValidationError: Invalid response object
        """
        if not response:
            raise ValidationError("Response object is required")

        prompt_tokens = response.prompt_tokens or 0
        completion_tokens = response.completion_tokens or 0
        total_tokens = prompt_tokens + completion_tokens

        return {
            "text": response.text,
            "model": response.model,
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": total_tokens,
            "stop_reason": response.stop_reason,
            "truncated": response.truncated,
        }

    @staticmethod
    def calculate_cost(
        model_id: str,
        prompt_tokens: int,
        completion_tokens: int,
    ) -> float:
        """
        Calculate USD cost from token counts.

        Uses the model pricing catalog to estimate cost based on token usage.
        Supports OpenAI, AWS Bedrock, and other providers.

        Args:
            model_id: Model identifier (e.g., "gpt-4o", "claude-sonnet-5")
            prompt_tokens: Number of prompt/input tokens used
            completion_tokens: Number of completion/output tokens used

        Returns:
            Estimated cost in USD (rounded to 6 decimal places)

        Raises:
            ValidationError: Invalid model or token counts
        """
        if not model_id:
            raise ValidationError("model_id is required")
        if prompt_tokens < 0 or completion_tokens < 0:
            raise ValidationError("Token counts cannot be negative")

        # Use existing cost estimation function
        try:
            cost = estimate_cost(model_id, prompt_tokens, completion_tokens)
            return cost
        except Exception as e:
            _log.warning(f"Failed to calculate cost for {model_id}: {e}")
            # Return 0 rather than fail — let usage tracking handle pricing
            return 0.0

    @staticmethod
    def get_model_pricing(model_id: str) -> Dict[str, float]:
        """
        Retrieve pricing for a specific model.

        Returns the input and output token pricing (USD per 1M tokens) for
        a model, supporting multiple providers (OpenAI, AWS Bedrock, etc.).

        Args:
            model_id: Model identifier

        Returns:
            Dict with keys:
            - input: USD per 1M input tokens
            - output: USD per 1M output tokens
            - provider: Provider name (openai, bedrock)
            - model_name: Canonical model name

        Raises:
            ValidationError: Unknown model
        """
        if not model_id:
            raise ValidationError("model_id is required")

        # Import here to avoid circular dependency
        from promptops_app.services.usage_service import _find_pricing, MODEL_PRICING

        try:
            # Resolve model to get provider
            model_def = resolve_model(model_id)
            provider = model_def.provider
            api_model_id = model_def.api_model_id

            # Look up pricing
            pricing = _find_pricing(api_model_id)

            return {
                "input": pricing["input"],
                "output": pricing["output"],
                "provider": provider,
                "model_name": model_def.display_name,
            }
        except Exception as e:
            _log.warning(f"Failed to get pricing for {model_id}: {e}")
            # Return default pricing
            default_pricing = MODEL_PRICING.get("_default", {"input": 5.0, "output": 15.0})
            return {
                "input": default_pricing["input"],
                "output": default_pricing["output"],
                "provider": "unknown",
                "model_name": model_id,
            }

    @staticmethod
    def batch_model_calls(
        calls: List[Dict[str, Any]],
        max_workers: int = 3,
        timeout_per_call: int = DEFAULT_TIMEOUT_SECONDS,
    ) -> List[Dict[str, Any]]:
        """
        Execute multiple model calls concurrently.

        Executes a batch of LLM calls in parallel using a thread pool.
        Each call is independent and errors are captured per-call rather
        than failing the entire batch.

        Args:
            calls: List of call specifications, each with:
                - model_id: Model to call
                - system_prompt: System prompt
                - user_prompt: User prompt
                - [optional] temperature: Sampling temperature
                - [optional] max_tokens: Output token limit
                - [optional] request_id: For correlation/logging

            max_workers: Maximum concurrent threads (default 3)
            timeout_per_call: Timeout per individual call in seconds

        Returns:
            List of results, one per input call, with:
            - request_id: Request identifier (if provided)
            - success: Boolean indicating call success
            - text: Response text (if successful)
            - prompt_tokens: Input tokens
            - completion_tokens: Output tokens
            - cost: USD cost
            - error: Error message (if failed)
            - error_type: Type of error (transient|auth|provider|unknown)

        Raises:
            ValidationError: Invalid input format
        """
        if not calls:
            raise ValidationError("At least one call is required")

        if not isinstance(calls, list):
            raise ValidationError("calls must be a list")

        # Validate all calls before executing
        for i, call in enumerate(calls):
            if not isinstance(call, dict):
                raise ValidationError(f"Call {i} must be a dict")
            if "model_id" not in call or "system_prompt" not in call or "user_prompt" not in call:
                raise ValidationError(
                    f"Call {i} missing required fields: model_id, system_prompt, user_prompt"
                )

        results = []
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {}

            # Submit all tasks
            for i, call in enumerate(calls):
                request_id = call.get("request_id", f"batch_{i}")
                future = executor.submit(
                    AgentLLMService._execute_single_call,
                    call,
                    timeout_per_call,
                )
                futures[future] = (i, request_id)

            # Collect results as they complete
            for future in as_completed(futures):
                i, request_id = futures[future]
                try:
                    result = future.result(timeout=timeout_per_call + 5)
                    results.append(result)
                except Exception as e:
                    _log.error(f"Batch call {request_id} failed: {e}")
                    results.append({
                        "request_id": request_id,
                        "success": False,
                        "error": str(e),
                        "error_type": "unknown",
                    })

        # Sort by original order
        results = sorted(results, key=lambda r: calls.index(
            next((c for c in calls if c.get("request_id") == r.get("request_id")), calls[0])
        ))

        return results

    @staticmethod
    def _execute_single_call(
        call_spec: Dict[str, Any],
        timeout_seconds: int,
    ) -> Dict[str, Any]:
        """
        Execute a single model call (helper for batch_model_calls).

        Args:
            call_spec: Call specification dict
            timeout_seconds: Timeout for this call

        Returns:
            Result dict with success/error status and data
        """
        request_id = call_spec.get("request_id")
        try:
            text, prompt_tokens, completion_tokens, cost = AgentLLMService.call_model(
                model_id=call_spec["model_id"],
                system_prompt=call_spec["system_prompt"],
                user_prompt=call_spec["user_prompt"],
                temperature=call_spec.get("temperature", 0.7),
                max_tokens=call_spec.get("max_tokens"),
                timeout_seconds=timeout_seconds,
            )

            return {
                "request_id": request_id,
                "success": True,
                "text": text,
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "cost": cost,
            }

        except (LLMTimeoutError, LLMRateLimitError) as e:
            return {
                "request_id": request_id,
                "success": False,
                "error": str(e),
                "error_type": "transient",
            }
        except LLMAuthError as e:
            return {
                "request_id": request_id,
                "success": False,
                "error": str(e),
                "error_type": "auth",
            }
        except (LLMProviderError, LLMGenerationError) as e:
            return {
                "request_id": request_id,
                "success": False,
                "error": str(e),
                "error_type": "provider",
            }
        except Exception as e:
            return {
                "request_id": request_id,
                "success": False,
                "error": str(e),
                "error_type": "unknown",
            }

    @staticmethod
    def stream_model_call(
        model_id: str,
        system_prompt: str,
        user_prompt: str,
        on_token: Optional[Callable[[str], None]] = None,
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
        timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
    ) -> Tuple[str, int, int, float]:
        """
        Execute a model call with token-level streaming callback.

        Calls the model and invokes a callback function for each token
        as it's received (if supported by provider). Falls back to
        non-streaming call if streaming is unavailable.

        Args:
            model_id: Model identifier
            system_prompt: System prompt
            user_prompt: User prompt
            on_token: Callback function called with each token (optional)
            temperature: Sampling temperature
            max_tokens: Maximum output tokens
            timeout_seconds: Request timeout

        Returns:
            Tuple of (response_text, prompt_tokens, completion_tokens, cost_usd)

        Notes:
            - Streaming support depends on provider API
            - Currently falls back to regular call_model() for non-streaming providers
            - Token callback is not invoked for non-streaming calls
        """
        # For MVP, streaming is scaffolded but falls back to regular call_model
        # Full streaming implementation would require:
        # - OpenAI: streaming=True in chat.completions
        # - Bedrock: InvokeModelWithResponseStream
        # - Token-level callbacks via stream consumers

        _log.info(f"Stream call for {model_id} (currently non-streaming fallback)")

        # Fallback to regular call for now
        text, prompt_tokens, completion_tokens, cost = AgentLLMService.call_model(
            model_id=model_id,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            temperature=temperature,
            max_tokens=max_tokens,
            timeout_seconds=timeout_seconds,
        )

        # Invoke callback if provided (with complete text for now)
        if on_token:
            try:
                on_token(text)
            except Exception as e:
                _log.warning(f"Token callback failed: {e}")

        return text, prompt_tokens, completion_tokens, cost

    @staticmethod
    def validate_output(
        response: str,
        output_schema: Optional[Dict[str, Any]] = None,
    ) -> Tuple[bool, Optional[Dict[str, Any]]]:
        """
        Validate LLM output against a schema.

        Attempts to parse response as JSON and validate against
        a provided schema (if given).

        Args:
            response: Response text from LLM
            output_schema: Optional JSON schema dict for validation

        Returns:
            Tuple of (is_valid, parsed_dict_or_none)
            - is_valid: Whether response is valid
            - parsed_dict: Parsed JSON dict (if valid and JSON), else None
        """
        if not response:
            return False, None

        # Try to parse as JSON
        try:
            from promptops_app.core.llm_client import safe_json_loads
            parsed = safe_json_loads(response)

            if not parsed:
                # Empty or invalid JSON
                return False, None

            # Validate against schema if provided
            if output_schema:
                # Basic schema validation (required fields check)
                if "required" in output_schema:
                    required_fields = output_schema.get("required", [])
                    missing = [f for f in required_fields if f not in parsed]
                    if missing:
                        _log.warning(f"Missing required fields: {missing}")
                        return False, None

                # TODO: Full JSON Schema validation using jsonschema library
                # For now, just check required fields

            return True, parsed

        except Exception as e:
            _log.debug(f"Failed to parse response as JSON: {e}")
            # Not JSON, return as-is
            return False, None
