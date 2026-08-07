"""Regression tests for two chained LLM failures caught live on a real block-wide
Blueprint generation: (1) a newer Bedrock model rejecting an explicit
`temperature` outright, and (2) the OpenAI fallback getting a `max_tokens`
sized for the primary Bedrock model and being rejected by OpenAI for exceeding
its own real limit — both together sank the whole call with no surviving
fallback (see promptops_app/core/llm_client.py, promptops_app/services/llm_service.py)."""
from unittest.mock import MagicMock, patch

import pytest


def test_bedrock_retries_without_temperature_when_model_rejects_it():
    from promptops_app.core.llm_client import _invoke_bedrock_with_retry

    client = MagicMock()
    reject = Exception("An error occurred (ValidationException) when calling the "
                       "InvokeModel operation: `temperature` is deprecated for this model.")
    ok_response = MagicMock()
    client.invoke_model.side_effect = [reject, ok_response]

    result = _invoke_bedrock_with_retry(client, "global.anthropic.claude-opus-4-8",
                                        "sys", "user", 32000)

    assert result is ok_response
    assert client.invoke_model.call_count == 2
    first_body, second_body = (c.kwargs["body"] for c in client.invoke_model.call_args_list)
    assert '"temperature"' in first_body
    assert '"temperature"' not in second_body


def test_bedrock_retry_does_not_mask_unrelated_errors():
    from promptops_app.core.llm_client import _invoke_bedrock_with_retry

    client = MagicMock()
    client.invoke_model.side_effect = Exception("Bedrock throttled: too many requests")

    with pytest.raises(Exception, match="throttled"):
        _invoke_bedrock_with_retry(client, "some-model", "sys", "user", 1000)
    assert client.invoke_model.call_count == 1


def test_fallback_caps_max_tokens_to_fallback_models_own_ceiling():
    """Regression: a real run had reduce_model=Claude Opus 4.8 (max_output_tokens
    32000). When Bedrock failed, the OpenAI fallback (gpt-4o, ceiling 16384) was
    called with max_tokens=32000 forwarded unchanged and OpenAI rejected the
    request outright (HTTP 400) before generating anything — losing the
    fallback entirely rather than returning its own fullest possible result."""
    from promptops_app.services import llm_service

    with patch("promptops_app.services.llm_service._call_openai_raw") as mock_call, \
         patch("promptops_app.services.llm_service.settings") as mock_settings:
        mock_settings.openai_api_key = "test-key"
        mock_call.return_value = MagicMock()
        llm_service._invoke_fallback("Claude Opus 4.8 (Bedrock)", "sys", "user", max_tokens=32000)

    assert mock_call.called
    _, kwargs = mock_call.call_args
    assert kwargs["max_tokens"] == 16384  # gpt-4o's own catalog ceiling, not Opus's 32000


def test_fallback_leaves_max_tokens_untouched_when_already_within_ceiling():
    from promptops_app.services import llm_service

    with patch("promptops_app.services.llm_service._call_openai_raw") as mock_call, \
         patch("promptops_app.services.llm_service.settings") as mock_settings:
        mock_settings.openai_api_key = "test-key"
        mock_call.return_value = MagicMock()
        llm_service._invoke_fallback("Claude Opus 4.8 (Bedrock)", "sys", "user", max_tokens=2000)

    _, kwargs = mock_call.call_args
    assert kwargs["max_tokens"] == 2000
