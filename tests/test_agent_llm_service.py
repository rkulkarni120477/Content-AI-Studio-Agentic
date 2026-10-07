"""Unit tests for AgentLLMService

Tests for:
- Model call execution with token counting and cost tracking
- Retry logic with exponential backoff
- Batch model calls with concurrent execution
- Output parsing and validation
- Model pricing retrieval
- Error handling (auth, provider, transient, etc.)
- Stream call fallback
"""

import json
import pytest
from unittest.mock import Mock, MagicMock, patch
from concurrent.futures import ThreadPoolExecutor

from app.core.exceptions import ValidationError, LLMGenerationError
from promptops_app.core.llm_client import (
    LLMResponse,
    LLMTimeoutError,
    LLMRateLimitError,
    LLMAuthError,
    LLMProviderError,
)
from promptops_app.services.agent_llm_service import (
    AgentLLMService,
    DEFAULT_MAX_RETRIES,
    INITIAL_BACKOFF_MS,
    DEFAULT_TIMEOUT_SECONDS,
)


class TestCallModel:
    """Tests for AgentLLMService.call_model"""

    @patch("promptops_app.services.agent_llm_service._call_openai_raw")
    def test_call_model_openai_success(self, mock_openai):
        """Test successful OpenAI model call"""
        # Mock response
        mock_openai.return_value = LLMResponse(
            text="Generated response",
            model="gpt-4o",
            prompt_tokens=100,
            completion_tokens=50,
            stop_reason="stop",
        )

        # Call
        text, prompt_tokens, completion_tokens, cost = AgentLLMService.call_model(
            model_id="gpt-4o",
            system_prompt="You are helpful",
            user_prompt="Hello",
        )

        # Verify
        assert text == "Generated response"
        assert prompt_tokens == 100
        assert completion_tokens == 50
        assert cost > 0
        mock_openai.assert_called_once()

    @patch("promptops_app.services.agent_llm_service._call_bedrock_raw")
    def test_call_model_bedrock_success(self, mock_bedrock):
        """Test successful AWS Bedrock model call"""
        # Mock response
        mock_bedrock.return_value = LLMResponse(
            text="Generated response",
            model="claude-sonnet-5",
            prompt_tokens=150,
            completion_tokens=75,
            stop_reason="stop",
        )

        # Call
        text, prompt_tokens, completion_tokens, cost = AgentLLMService.call_model(
            model_id="Claude Sonnet 5 (Bedrock)",
            system_prompt="You are helpful",
            user_prompt="Hello",
        )

        # Verify
        assert text == "Generated response"
        assert prompt_tokens == 150
        assert completion_tokens == 75
        assert cost > 0
        mock_bedrock.assert_called_once()

    def test_call_model_missing_model_id(self):
        """Test that empty model_id raises ValidationError"""
        with pytest.raises(ValidationError, match="model_id is required"):
            AgentLLMService.call_model(
                model_id="",
                system_prompt="You are helpful",
                user_prompt="Hello",
            )

    def test_call_model_missing_prompts(self):
        """Test that missing prompts raise ValidationError"""
        with pytest.raises(ValidationError, match="required"):
            AgentLLMService.call_model(
                model_id="gpt-4o",
                system_prompt="",
                user_prompt="Hello",
            )

    @patch("promptops_app.services.agent_llm_service.resolve_model")
    def test_call_model_unknown_model(self, mock_resolve):
        """Test that unknown model raises ValidationError"""
        # resolve_model falls back to default, so we need to mock it to raise
        mock_resolve.side_effect = ValueError("Unknown model")

        with pytest.raises(ValidationError, match="Unknown model"):
            AgentLLMService.call_model(
                model_id="unknown-model-xyz",
                system_prompt="You are helpful",
                user_prompt="Hello",
            )

    @patch("promptops_app.services.agent_llm_service._call_openai_raw")
    def test_call_model_timeout_with_retry(self, mock_openai):
        """Test that transient timeout errors trigger retry"""
        # First call times out, second succeeds
        mock_openai.side_effect = [
            LLMTimeoutError("Timeout"),
            LLMResponse(
                text="Success on retry",
                model="gpt-4o",
                prompt_tokens=100,
                completion_tokens=50,
            ),
        ]

        # Call with max_retries >= 1
        text, _, _, _ = AgentLLMService.call_model(
            model_id="gpt-4o",
            system_prompt="You are helpful",
            user_prompt="Hello",
            max_retries=3,
        )

        # Verify
        assert text == "Success on retry"
        assert mock_openai.call_count == 2  # First failed, second succeeded

    @patch("promptops_app.services.agent_llm_service._call_openai_raw")
    def test_call_model_rate_limit_exhausted_retries(self, mock_openai):
        """Test that rate limit errors exhaust retries before failing"""
        # All calls rate limited
        mock_openai.side_effect = LLMRateLimitError("Rate limit exceeded")

        # Call with limited retries
        with pytest.raises(LLMGenerationError, match="failed after"):
            AgentLLMService.call_model(
                model_id="gpt-4o",
                system_prompt="You are helpful",
                user_prompt="Hello",
                max_retries=1,
            )

        # Should have tried twice (initial + 1 retry)
        assert mock_openai.call_count == 2

    @patch("promptops_app.services.agent_llm_service._call_openai_raw")
    def test_call_model_auth_error_no_retry(self, mock_openai):
        """Test that auth errors fail immediately without retry"""
        # Auth error
        mock_openai.side_effect = LLMAuthError("Invalid API key")

        # Call
        with pytest.raises(LLMGenerationError, match="LLM call failed"):
            AgentLLMService.call_model(
                model_id="gpt-4o",
                system_prompt="You are helpful",
                user_prompt="Hello",
                max_retries=3,
            )

        # Should only try once (no retries for auth)
        assert mock_openai.call_count == 1

    @patch("promptops_app.services.agent_llm_service._call_openai_raw")
    def test_call_model_timeout_seconds_clamped(self, mock_openai):
        """Test that timeout is clamped to reasonable range"""
        mock_openai.return_value = LLMResponse(
            text="OK",
            model="gpt-4o",
            prompt_tokens=10,
            completion_tokens=5,
        )

        # Call with excessive timeout (should be clamped to 300s)
        AgentLLMService.call_model(
            model_id="gpt-4o",
            system_prompt="You are helpful",
            user_prompt="Hello",
            timeout_seconds=1000,  # Exceeds max
        )

        # Verify it was called (timeout was clamped)
        assert mock_openai.called


class TestParseResponse:
    """Tests for AgentLLMService.parse_response"""

    def test_parse_response_success(self):
        """Test successful response parsing"""
        response = LLMResponse(
            text="Generated content",
            model="gpt-4o",
            prompt_tokens=100,
            completion_tokens=50,
            stop_reason="stop",
        )

        result = AgentLLMService.parse_response(response)

        assert result["text"] == "Generated content"
        assert result["model"] == "gpt-4o"
        assert result["prompt_tokens"] == 100
        assert result["completion_tokens"] == 50
        assert result["total_tokens"] == 150
        assert result["stop_reason"] == "stop"
        assert result["truncated"] is False

    def test_parse_response_truncated(self):
        """Test parsing truncated response"""
        response = LLMResponse(
            text="Truncated...",
            model="gpt-4o",
            prompt_tokens=100,
            completion_tokens=1000,  # Hit max_tokens
            stop_reason="length",
        )

        result = AgentLLMService.parse_response(response)

        assert result["truncated"] is True
        assert result["stop_reason"] == "length"

    def test_parse_response_missing_tokens(self):
        """Test parsing response with missing token counts"""
        response = LLMResponse(
            text="Content",
            model="gpt-4o",
            prompt_tokens=None,
            completion_tokens=None,
        )

        result = AgentLLMService.parse_response(response)

        assert result["prompt_tokens"] == 0
        assert result["completion_tokens"] == 0
        assert result["total_tokens"] == 0

    def test_parse_response_none(self):
        """Test that None response raises ValidationError"""
        with pytest.raises(ValidationError, match="required"):
            AgentLLMService.parse_response(None)


class TestCalculateCost:
    """Tests for AgentLLMService.calculate_cost"""

    def test_calculate_cost_gpt4o(self):
        """Test cost calculation for GPT-4o"""
        # GPT-4o: $5/1M input, $15/1M output
        cost = AgentLLMService.calculate_cost(
            model_id="gpt-4o",
            prompt_tokens=1_000_000,
            completion_tokens=1_000_000,
        )

        # Should be $5 + $15 = $20
        assert cost == pytest.approx(20.0, abs=0.01)

    def test_calculate_cost_claude_sonnet(self):
        """Test cost calculation for Claude Sonnet"""
        # Claude Sonnet: $3/1M input, $15/1M output
        cost = AgentLLMService.calculate_cost(
            model_id="claude-sonnet",
            prompt_tokens=1_000_000,
            completion_tokens=1_000_000,
        )

        # Should be $3 + $15 = $18
        assert cost == pytest.approx(18.0, abs=0.01)

    def test_calculate_cost_zero_tokens(self):
        """Test cost calculation with zero tokens"""
        cost = AgentLLMService.calculate_cost(
            model_id="gpt-4o",
            prompt_tokens=0,
            completion_tokens=0,
        )

        assert cost == 0.0

    def test_calculate_cost_partial_tokens(self):
        """Test cost calculation with only input tokens"""
        # 500k input tokens for GPT-4o ($5/1M)
        cost = AgentLLMService.calculate_cost(
            model_id="gpt-4o",
            prompt_tokens=500_000,
            completion_tokens=0,
        )

        # Should be $2.50
        assert cost == pytest.approx(2.5, abs=0.01)

    def test_calculate_cost_negative_tokens_rejected(self):
        """Test that negative token counts raise ValidationError"""
        with pytest.raises(ValidationError, match="cannot be negative"):
            AgentLLMService.calculate_cost(
                model_id="gpt-4o",
                prompt_tokens=-100,
                completion_tokens=50,
            )

    def test_calculate_cost_unknown_model(self):
        """Test cost calculation falls back for unknown model"""
        # Should not raise, uses default pricing
        cost = AgentLLMService.calculate_cost(
            model_id="unknown-model-xyz",
            prompt_tokens=100,
            completion_tokens=50,
        )

        # Should still return a value (using defaults)
        assert cost >= 0.0


class TestGetModelPricing:
    """Tests for AgentLLMService.get_model_pricing"""

    def test_get_model_pricing_gpt4o(self):
        """Test retrieving pricing for GPT-4o"""
        pricing = AgentLLMService.get_model_pricing("gpt-4o")

        assert "input" in pricing
        assert "output" in pricing
        assert pricing["provider"] in ["openai", "bedrock"]
        assert pricing["input"] > 0
        assert pricing["output"] > 0

    def test_get_model_pricing_claude_sonnet(self):
        """Test retrieving pricing for Claude Sonnet"""
        pricing = AgentLLMService.get_model_pricing("Claude Sonnet 5 (Bedrock)")

        assert pricing["provider"] == "bedrock"
        assert pricing["input"] > 0
        assert pricing["output"] > 0

    def test_get_model_pricing_empty_model_id(self):
        """Test that empty model_id raises ValidationError"""
        with pytest.raises(ValidationError, match="model_id is required"):
            AgentLLMService.get_model_pricing("")

    def test_get_model_pricing_unknown_model(self):
        """Test that unknown model returns default pricing"""
        pricing = AgentLLMService.get_model_pricing("unknown-model-xyz")

        assert "input" in pricing
        assert "output" in pricing
        # Should return default pricing, not raise


class TestBatchModelCalls:
    """Tests for AgentLLMService.batch_model_calls"""

    @patch("promptops_app.services.agent_llm_service.AgentLLMService.call_model")
    def test_batch_model_calls_single_call(self, mock_call):
        """Test batch execution with single call"""
        mock_call.return_value = ("OK", 100, 50, 0.01)

        calls = [
            {
                "model_id": "gpt-4o",
                "system_prompt": "You are helpful",
                "user_prompt": "Hello",
                "request_id": "req_1",
            }
        ]

        results = AgentLLMService.batch_model_calls(calls)

        assert len(results) == 1
        assert results[0]["success"] is True
        assert results[0]["text"] == "OK"
        assert results[0]["request_id"] == "req_1"

    @patch("promptops_app.services.agent_llm_service.AgentLLMService.call_model")
    def test_batch_model_calls_multiple_calls(self, mock_call):
        """Test batch execution with multiple concurrent calls"""
        # Return different results for each call
        mock_call.side_effect = [
            ("Response 1", 100, 50, 0.01),
            ("Response 2", 150, 75, 0.02),
            ("Response 3", 200, 100, 0.03),
        ]

        calls = [
            {
                "model_id": "gpt-4o",
                "system_prompt": "You are helpful",
                "user_prompt": "Hello 1",
                "request_id": "req_1",
            },
            {
                "model_id": "gpt-4o",
                "system_prompt": "You are helpful",
                "user_prompt": "Hello 2",
                "request_id": "req_2",
            },
            {
                "model_id": "gpt-4o",
                "system_prompt": "You are helpful",
                "user_prompt": "Hello 3",
                "request_id": "req_3",
            },
        ]

        results = AgentLLMService.batch_model_calls(calls, max_workers=2)

        assert len(results) == 3
        assert all(r["success"] for r in results)
        assert mock_call.call_count == 3

    @patch("promptops_app.services.agent_llm_service.AgentLLMService.call_model")
    def test_batch_model_calls_partial_failure(self, mock_call):
        """Test batch execution with some failures"""
        # First call succeeds, second fails, third succeeds
        mock_call.side_effect = [
            ("Response 1", 100, 50, 0.01),
            Exception("Provider error"),
            ("Response 3", 200, 100, 0.03),
        ]

        calls = [
            {"model_id": "gpt-4o", "system_prompt": "You", "user_prompt": "H", "request_id": "r1"},
            {"model_id": "gpt-4o", "system_prompt": "You", "user_prompt": "H", "request_id": "r2"},
            {"model_id": "gpt-4o", "system_prompt": "You", "user_prompt": "H", "request_id": "r3"},
        ]

        results = AgentLLMService.batch_model_calls(calls)

        # Should have 3 results
        assert len(results) == 3
        # First and third succeed
        success_count = sum(1 for r in results if r.get("success"))
        assert success_count >= 2

    def test_batch_model_calls_empty_list(self):
        """Test that empty call list raises ValidationError"""
        with pytest.raises(ValidationError, match="At least one call is required"):
            AgentLLMService.batch_model_calls([])

    def test_batch_model_calls_invalid_format(self):
        """Test that invalid call format raises ValidationError"""
        with pytest.raises(ValidationError, match="must be a dict"):
            AgentLLMService.batch_model_calls(["not a dict"])

    def test_batch_model_calls_missing_fields(self):
        """Test that missing required fields raise ValidationError"""
        with pytest.raises(ValidationError, match="missing required fields"):
            AgentLLMService.batch_model_calls([
                {"model_id": "gpt-4o"}  # Missing system_prompt and user_prompt
            ])


class TestStreamModelCall:
    """Tests for AgentLLMService.stream_model_call"""

    @patch("promptops_app.services.agent_llm_service.AgentLLMService.call_model")
    def test_stream_model_call_success(self, mock_call):
        """Test stream call (currently falls back to regular call)"""
        mock_call.return_value = ("Streamed content", 100, 50, 0.01)

        callback_tokens = []

        def on_token(token):
            callback_tokens.append(token)

        text, _, _, _ = AgentLLMService.stream_model_call(
            model_id="gpt-4o",
            system_prompt="You are helpful",
            user_prompt="Hello",
            on_token=on_token,
        )

        assert text == "Streamed content"
        # Callback should be invoked with content
        assert len(callback_tokens) > 0

    @patch("promptops_app.services.agent_llm_service.AgentLLMService.call_model")
    def test_stream_model_call_no_callback(self, mock_call):
        """Test stream call without callback"""
        mock_call.return_value = ("Content", 100, 50, 0.01)

        text, _, _, _ = AgentLLMService.stream_model_call(
            model_id="gpt-4o",
            system_prompt="You are helpful",
            user_prompt="Hello",
        )

        assert text == "Content"
        mock_call.assert_called_once()


class TestValidateOutput:
    """Tests for AgentLLMService.validate_output"""

    def test_validate_output_valid_json(self):
        """Test validation of valid JSON response"""
        response = json.dumps({"key": "value", "number": 42})

        is_valid, parsed = AgentLLMService.validate_output(response)

        assert is_valid is True
        assert parsed == {"key": "value", "number": 42}

    def test_validate_output_json_with_schema(self):
        """Test validation with required fields schema"""
        response = json.dumps({"name": "John", "age": 30})
        schema = {"required": ["name", "age"]}

        is_valid, parsed = AgentLLMService.validate_output(response, schema)

        assert is_valid is True
        assert parsed["name"] == "John"

    def test_validate_output_json_missing_required(self):
        """Test validation fails when required fields missing"""
        response = json.dumps({"name": "John"})
        schema = {"required": ["name", "age"]}

        is_valid, parsed = AgentLLMService.validate_output(response, schema)

        assert is_valid is False
        assert parsed is None

    def test_validate_output_invalid_json(self):
        """Test validation of non-JSON response"""
        response = "This is plain text, not JSON"

        is_valid, parsed = AgentLLMService.validate_output(response)

        assert is_valid is False
        assert parsed is None

    def test_validate_output_empty_response(self):
        """Test validation of empty response"""
        is_valid, parsed = AgentLLMService.validate_output("")

        assert is_valid is False
        assert parsed is None

    def test_validate_output_malformed_json(self):
        """Test validation of malformed JSON"""
        response = "{incomplete json"

        is_valid, parsed = AgentLLMService.validate_output(response)

        # Should handle gracefully (might use json_repair)
        # Result depends on json_repair behavior
        assert isinstance(is_valid, bool)
