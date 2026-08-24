"""A reasoning model's answer must survive the leading `thinking` block.

Anthropic models that reason emit ``content: [{"type": "thinking"...},
{"type": "text"...}]``. The old parser read ``content[0]["text"]``, got "", and
raised "Bedrock returned an empty response body" — so a perfectly good completion
was thrown away, the reliability layer fell back to another model, and the audit
row still named the model that never spoke.

Two properties make this worth pinning rather than trusting to a code comment:

* it is INTERMITTENT — the same model and prompt sometimes emits no thinking
  block at all, so the bug reads as a flaky endpoint, and a fix verified by one
  manual call proves nothing;
* it is SILENT — the fallback succeeds, the document looks right, and the only
  trace is a WARNING line that rotates away.

Measured 2026-08-14 against live Bedrock: sonnet-5, opus-5 and sonnet-4-6 all
return 3/3 when parsed this way, having been recorded as 0/3 (\"cannot invoke\")
in dis_backend/config/clients/aim.yaml under the old read.
"""
from __future__ import annotations

import pytest

from promptops_app.core.llm_client import bedrock_text


def test_a_leading_thinking_block_does_not_hide_the_answer():
    body = {"content": [{"type": "thinking", "thinking": "weighing options"},
                        {"type": "text", "text": "The answer is 42."}]}
    assert bedrock_text(body) == "The answer is 42."


def test_the_plain_single_text_block_still_reads_the_same():
    """The overwhelmingly common shape must be untouched by the fix."""
    assert bedrock_text({"content": [{"type": "text", "text": "hello"}]}) == "hello"


def test_a_block_with_no_declared_type_is_treated_as_text():
    """Older payloads omit `type`; dropping them would break working models."""
    assert bedrock_text({"content": [{"text": "legacy shape"}]}) == "legacy shape"


def test_several_text_blocks_are_joined_not_truncated_to_the_first():
    """Taking only the first text block would silently drop the tail of an answer
    — a subtler version of the bug being fixed, not a fix for it."""
    body = {"content": [{"type": "thinking", "thinking": "..."},
                        {"type": "text", "text": "part one. "},
                        {"type": "text", "text": "part two."}]}
    assert bedrock_text(body) == "part one. part two."


def test_a_genuinely_empty_completion_still_reads_as_empty():
    """The empty-response guard must keep firing for real emptiness; a fix that
    made every response non-empty would hide provider failures instead."""
    assert bedrock_text({"content": [{"type": "thinking", "thinking": "..."}]}) == ""
    assert bedrock_text({"content": []}) == ""
    assert bedrock_text({}) == ""


def _install_bedrock(mp, payload: bytes):
    import promptops_app.core.llm_client as mod

    class _Body:
        @staticmethod
        def read():
            return payload

    class _Client:
        @staticmethod
        def invoke_model(**_kwargs):
            return {"body": _Body()}

    mp.setattr(mod, "_get_bedrock_client", lambda: _Client())
    return mod


def test_a_thinking_only_response_still_raises_rather_than_returning_nothing():
    """End-to-end through the real call path: `content` with no text block must
    still reach the LLMProviderError the reliability layer keys its fallback on."""
    with pytest.MonkeyPatch.context() as mp:
        mod = _install_bedrock(
            mp, b'{"content": [{"type": "thinking", "thinking": "..."}], "stop_reason": "end_turn"}')
        with pytest.raises(mod.LLMProviderError, match="empty response body") as excinfo:
            mod._call_bedrock_raw("sys", "usr", model_id="global.anthropic.claude-sonnet-5")

    # The bare old message sent readers hunting for a provider outage. Naming the
    # stop reason and the block types is what distinguishes "the model went silent"
    # from "it only thought" — and it is the whole reason this case took a day to
    # diagnose the first time.
    msg = str(excinfo.value)
    assert "stop_reason=end_turn" in msg and "thinking" in msg


def test_an_output_cap_consumed_by_thinking_says_to_raise_max_tokens():
    """The one empty-response cause with a concrete, actionable fix must say so
    rather than looking like the same generic provider failure."""
    with pytest.MonkeyPatch.context() as mp:
        mod = _install_bedrock(mp, b'{"content": [], "stop_reason": "max_tokens"}')
        with pytest.raises(mod.LLMProviderError) as excinfo:
            mod._call_bedrock_raw("sys", "usr", model_id="global.anthropic.claude-opus-5")

    assert "raise max_tokens" in str(excinfo.value)


def test_the_thinking_shape_reaches_the_caller_through_the_real_call_path():
    """The unit test above pins the helper; this pins that the helper is the one
    actually wired into _call_bedrock_raw — the fix is worthless if the call path
    still indexes content[0] itself."""
    with pytest.MonkeyPatch.context() as mp:
        mod = _install_bedrock(
            mp,
            b'{"content": [{"type": "thinking", "thinking": "..."},'
            b'{"type": "text", "text": "real answer"}],'
            b'"usage": {"input_tokens": 10, "output_tokens": 3}}')
        result = mod._call_bedrock_raw("sys", "usr", model_id="global.anthropic.claude-opus-5")

    assert result.text == "real answer"
    # The usage that the old path never got far enough to read, so the discarded
    # calls cost real money and recorded NULL tokens.
    assert (result.prompt_tokens, result.completion_tokens) == (10, 3)
