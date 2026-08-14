"""A failed provider call must raise, not return plausible JSON.

``call_llm`` used to swallow every exception and return
``{"doc_type":"other","classification":"internal"}``. That inverted the failure: a
timeout, a throttle, a revoked credential and a missing model all became well-formed
output that every downstream step accepted. On 2026-08-13 a 20-day block was built
entirely from those stubs, every field defaulted, and the run was reported to the user
as a successful generation.

Raising does not make callers fragile — all six call sites already sit inside per-item
isolation. It makes degrading a decision each one takes and records.
"""
from __future__ import annotations

import types

import pytest

from services.digests import mapper
from services.pipeline import common


@pytest.fixture(autouse=True)
def _no_cached_client():
    """These tests swap the boto3 module, so a client cached by an earlier test would
    be reused and the swap silently ignored."""
    common.reset_bedrock_clients()
    yield
    common.reset_bedrock_clients()


def _bedrock_settings():
    return types.SimpleNamespace(
        environment="production", anthropic_api_key=None, aws_access_key_id="k",
        use_bedrock=True, bedrock_client_kwargs=lambda: {"region_name": "ap-south-1"})


def _install_failing_bedrock(monkeypatch, exc):
    class _Client:
        def invoke_model(self, modelId=None, body=None):
            raise exc

    monkeypatch.setattr(common, "get_settings", _bedrock_settings)
    monkeypatch.setitem(__import__("sys").modules, "boto3",
                        types.SimpleNamespace(client=lambda *a, **k: _Client()))


def test_a_provider_error_raises_instead_of_returning_output(monkeypatch):
    _install_failing_bedrock(monkeypatch, RuntimeError("ThrottlingException: slow down"))
    with pytest.raises(common.LLMCallFailed):
        common.call_llm("some-model", "hi")


def test_the_raised_error_names_the_model_and_the_underlying_exception(monkeypatch):
    _install_failing_bedrock(monkeypatch, TimeoutError("Read timeout on endpoint URL"))
    with pytest.raises(common.LLMCallFailed) as excinfo:
        common.call_llm("global.anthropic.claude-sonnet-4-5", "hi")

    exc = excinfo.value
    assert exc.model == "global.anthropic.claude-sonnet-4-5"
    assert exc.cause_type == "TimeoutError"
    assert "TimeoutError" in str(exc)


def test_an_empty_exception_message_still_yields_a_useful_error(monkeypatch):
    """A ReadTimeoutError's str() is frequently blank — the type must carry it."""
    _install_failing_bedrock(monkeypatch, TimeoutError())
    with pytest.raises(common.LLMCallFailed) as excinfo:
        common.call_llm("m", "hi")
    assert "TimeoutError" in str(excinfo.value)
    assert str(excinfo.value).strip()


def test_the_original_exception_is_chained_for_a_traceback(monkeypatch):
    original = RuntimeError("AccessDeniedException")
    _install_failing_bedrock(monkeypatch, original)
    with pytest.raises(common.LLMCallFailed) as excinfo:
        common.call_llm("m", "hi")
    assert excinfo.value.__cause__ is original


def test_a_failed_day_records_the_real_cause_and_counts_the_attempt(monkeypatch):
    """The digest must name e.g. ReadTimeoutError rather than describing a stub's
    shape, and a billed attempt must not report zero calls."""
    def boom(model, prompt, max_tokens=300):
        raise common.LLMCallFailed("escalated-model", TimeoutError("read timeout"))
    monkeypatch.setattr(common, "call_llm", boom)

    tenant = types.SimpleNamespace(
        pipeline=types.SimpleNamespace(models=types.SimpleNamespace(digest_extraction="m")))
    budget = {"calls": 0, "tok_in": 0, "tok_out": 0}
    digest = mapper.build_digest({"day_number": 6, "topic": "t", "lesson_title": "l"},
                                 [], tenant, model="m", client_id="aim", block="Block 2",
                                 budget=budget)

    assert digest["digest_status"] == "failed"
    assert "TimeoutError" in digest["error"]
    assert digest["extractor_model"] == "escalated-model", "the model that failed"
    assert budget["calls"] == 1, "an attempted, billable call must not report zero"


def test_one_failed_day_does_not_sink_the_block(monkeypatch):
    """Per-day isolation (§8.3) is what makes raising safe."""
    def boom(model, prompt, max_tokens=300):
        raise common.LLMCallFailed("m", RuntimeError("nope"))
    monkeypatch.setattr(common, "call_llm", boom)

    tenant = types.SimpleNamespace(
        pipeline=types.SimpleNamespace(models=types.SimpleNamespace(digest_extraction="m")))
    # Must return a digest rather than propagate.
    digest = mapper.build_digest({"day_number": 1, "topic": "t", "lesson_title": "l"},
                                 [], tenant, model="m", client_id="aim", block="B")
    assert digest["day_number"] == 1 and digest["digest_status"] == "failed"


@pytest.mark.parametrize("agent_module,step", [
    ("services.agents.content_classification_agent", "content_classification"),
    ("services.agents.metadata_extraction_agent", "metadata_extraction"),
    ("services.agents.structure_extraction_agent", "structure_extraction"),
    ("services.agents.quality_check_agent", "quality_check"),
])
def test_every_ingestion_agent_handles_the_new_exception(agent_module, step):
    """Each of the four agents previously guarded only TokenLimitError, so a raising
    call_llm would have crashed an ingestion run. Each must now catch LLMCallFailed
    explicitly AND record it — degrading silently is the bug being fixed."""
    import importlib
    import inspect

    src = inspect.getsource(importlib.import_module(agent_module))
    assert "except LLMCallFailed" in src, f"{agent_module} would crash on a provider error"
    assert step in src
    # The recorded message must reach state['errors'], not just a log.
    assert "errors" in src


# ---------------------------------------------------------------------------
# Reasoning models put a `thinking` block first (2026-08-14)
# ---------------------------------------------------------------------------

def _install_bedrock_returning(monkeypatch, payload: str):
    class _Body:
        @staticmethod
        def read():
            return payload.encode()

    class _Client:
        def invoke_model(self, modelId=None, body=None):
            return {"body": _Body()}

    monkeypatch.setattr(common, "get_settings", _bedrock_settings)
    monkeypatch.setitem(__import__("sys").modules, "boto3",
                        types.SimpleNamespace(client=lambda *a, **k: _Client()))


def test_a_leading_thinking_block_does_not_destroy_a_good_answer(monkeypatch):
    """``result["content"][0]["text"]`` raised KeyError('text') on a reasoning
    model's reply, and the broad except turned that into LLMCallFailed — so a
    perfectly good extraction became a failed day.

    DIS has not been bitten only because aim.yaml pins Sonnet 4.5. The moment any
    client is pointed at Sonnet 5 or Opus 5 this fires, and INTERMITTENTLY: the
    same model emits the thinking block only sometimes, so it would read as a
    flaky provider rather than a parse bug. CAS lost every REDUCE call of a
    block-wide build to the identical read on 2026-08-14.
    """
    _install_bedrock_returning(monkeypatch, (
        '{"content": [{"type": "thinking", "thinking": "considering the source"},'
        '{"type": "text", "text": "{\\"day\\": 1}"}],'
        '"usage": {"input_tokens": 900, "output_tokens": 40}}'
    ))
    text, tin, tout = common.call_llm("global.anthropic.claude-sonnet-5", "extract")
    assert text == '{"day": 1}'
    # Real usage, not the 500/100 placeholders the old path fell back to.
    assert (tin, tout) == (900, 40)


def test_the_ordinary_single_text_block_is_unchanged(monkeypatch):
    _install_bedrock_returning(monkeypatch, (
        '{"content": [{"type": "text", "text": "plain answer"}],'
        '"usage": {"input_tokens": 10, "output_tokens": 2}}'
    ))
    assert common.call_llm("global.anthropic.claude-sonnet-4-5", "hi")[0] == "plain answer"


def test_several_text_blocks_are_joined_rather_than_truncated(monkeypatch):
    """Taking only the FIRST text block would be a quieter version of the same bug."""
    _install_bedrock_returning(monkeypatch, (
        '{"content": [{"type": "thinking", "thinking": "..."},'
        '{"type": "text", "text": "first half "},'
        '{"type": "text", "text": "second half"}]}'
    ))
    assert common.call_llm("m", "hi")[0] == "first half second half"


def test_a_reply_with_no_text_block_at_all_still_fails_loudly(monkeypatch):
    """The fix must not convert a genuinely contentless reply into empty output that
    downstream steps accept — that is exactly the stub behaviour this module exists
    to prevent."""
    _install_bedrock_returning(monkeypatch,
                               '{"content": [{"type": "thinking", "thinking": "..."}]}')
    with pytest.raises(common.LLMCallFailed):
        common.call_llm("global.anthropic.claude-opus-5", "extract")
