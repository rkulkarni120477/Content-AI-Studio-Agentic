"""A reply cut off by the output cap must never pass as a finished one.

The model's own output limit is the one bound content is allowed to hit. What was
missing was any *notice* that it had been hit: OpenAI's ``finish_reason`` was read
nowhere in the codebase, and Bedrock's ``stop_reason`` was read only to explain an
EMPTY reply — the less damaging case, because an empty reply is obviously wrong.

A non-empty reply that spent the whole cap was returned as if complete. The tail
was lost, the caller stored the fragment, and the last markdown construct on the
cut line was left unclosed — which is one of the three ways a regenerated label
ends up rendering as ``*Label:**``.
"""

import json
from types import SimpleNamespace

import pytest

from promptops_app.core.llm_client import LLMResponse, LLMResult

# ---------------------------------------------------------------------------
# The vocabulary
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("reason,expected", [
    ("length", True),        # OpenAI
    ("max_tokens", True),    # Bedrock / Anthropic
    ("stop", False),
    ("end_turn", False),
    ("tool_use", False),
    ("", False),
    (None, False),
])
def test_only_a_cap_stop_counts_as_truncated(reason, expected):
    assert LLMResponse("text", "model", stop_reason=reason).truncated is expected
    assert LLMResult("text", stop_reason=reason).truncated is expected


def test_stop_reason_defaults_to_none_so_an_unpatched_caller_is_never_wrongly_blocked():
    """Absent metadata must read as "not truncated". A provider or a test double
    that supplies no stop_reason keeps its previous behaviour exactly."""
    assert LLMResponse("text", "model").stop_reason is None
    assert LLMResponse("text", "model").truncated is False


# ---------------------------------------------------------------------------
# The providers actually read it
# ---------------------------------------------------------------------------

def _openai_body(finish_reason):
    return {
        "choices": [{"message": {"content": "half a table"}, "finish_reason": finish_reason}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 16384},
    }


def _patch_openai(monkeypatch, body):
    import promptops_app.core.llm_client as lc

    monkeypatch.setattr(lc.settings, "openai_api_key", "sk-test", raising=False)
    monkeypatch.setattr(lc, "check_budget_autocommit",
                        lambda *a, **k: SimpleNamespace(reservations=[]))
    monkeypatch.setattr(lc, "reconcile_budget_autocommit", lambda *a, **k: None)
    monkeypatch.setattr(lc, "_log_and_trace", lambda *a, **k: None)

    resp = SimpleNamespace(status_code=200, json=lambda: body,
                           raise_for_status=lambda: None)
    monkeypatch.setattr(lc, "_get_openai_session",
                        lambda: SimpleNamespace(post=lambda *a, **k: resp))
    return lc


@pytest.mark.parametrize("finish_reason,truncated", [("length", True), ("stop", False)])
def test_openai_finish_reason_reaches_the_response(monkeypatch, finish_reason, truncated):
    lc = _patch_openai(monkeypatch, _openai_body(finish_reason))
    out = lc._call_openai_raw("sys", "user", model="gpt-4o")
    assert out.stop_reason == finish_reason
    assert out.truncated is truncated
    assert out.text == "half a table"          # the text still comes back intact


def test_openai_truncation_is_logged_loudly(monkeypatch, caplog):
    import logging

    lc = _patch_openai(monkeypatch, _openai_body("length"))
    with caplog.at_level(logging.WARNING):
        lc._call_openai_raw("sys", "user", model="gpt-4o")
    hits = [r for r in caplog.records if "llm_output_truncated" in r.getMessage()]
    assert hits and hits[0].levelno >= logging.WARNING


def _patch_bedrock(monkeypatch, body):
    import promptops_app.core.llm_client as lc

    monkeypatch.setattr(lc, "check_budget_autocommit",
                        lambda *a, **k: SimpleNamespace(reservations=[]))
    monkeypatch.setattr(lc, "reconcile_budget_autocommit", lambda *a, **k: None)
    monkeypatch.setattr(lc, "_log_and_trace", lambda *a, **k: None)
    monkeypatch.setattr(lc, "_get_bedrock_client", lambda: object(), raising=False)

    payload = json.dumps(body).encode()
    monkeypatch.setattr(lc, "_invoke_bedrock_with_retry",
                        lambda *a, **k: {"body": SimpleNamespace(read=lambda: payload)})
    return lc


@pytest.mark.parametrize("stop_reason,truncated", [("max_tokens", True), ("end_turn", False)])
def test_bedrock_stop_reason_reaches_the_response_on_the_success_path(
        monkeypatch, stop_reason, truncated):
    """It was already read when the reply was empty. This is the non-empty case."""
    lc = _patch_bedrock(monkeypatch, {
        "content": [{"type": "text", "text": "half a table"}],
        "stop_reason": stop_reason,
        "usage": {"input_tokens": 10, "output_tokens": 64000},
    })
    out = lc._call_bedrock_raw("sys", "user", model_id="global.anthropic.claude-opus-5")
    assert out.stop_reason == stop_reason
    assert out.truncated is truncated


def test_an_empty_bedrock_reply_still_raises_with_its_old_diagnosis(monkeypatch):
    """The pre-existing behaviour for the empty case is unchanged."""
    from promptops_app.core.llm_client import LLMProviderError

    lc = _patch_bedrock(monkeypatch, {
        "content": [{"type": "thinking"}],
        "stop_reason": "max_tokens",
        "usage": {},
    })
    with pytest.raises(LLMProviderError, match="raise max_tokens"):
        lc._call_bedrock_raw("sys", "user", model_id="global.anthropic.claude-opus-5")


# ---------------------------------------------------------------------------
# The reliability wrapper carries it through
# ---------------------------------------------------------------------------

def test_generate_with_metadata_propagates_the_stop_reason(monkeypatch):
    """Without this the detection is stranded in llm_client — every CDD caller
    goes through the retry/fallback wrapper, not the raw provider function."""
    import promptops_app.services.llm_service as svc

    monkeypatch.setattr(svc, "_invoke_primary",
                        lambda *a, **k: LLMResponse("half", "m", 1, 2, stop_reason="length"))
    result = svc.generate_with_metadata("GPT-5.4", "sys", "user")
    assert result.stop_reason == "length"
    assert result.truncated is True
    assert result.is_error is False       # it succeeded; it is just incomplete


# ---------------------------------------------------------------------------
# The CDD routes refuse it
# ---------------------------------------------------------------------------

def test_a_truncated_reply_is_refused_rather_than_stored():
    from app.api.v1.routers.cdd import _reject_if_truncated
    from app.core.exceptions import LLMGenerationError

    with pytest.raises(LLMGenerationError) as exc:
        _reject_if_truncated(LLMResult("half a table", stop_reason="length"),
                             "regenerating these rows")
    message = str(exc.value)
    assert "nothing was changed" in message         # says the state is safe
    assert "larger output range" in message         # says what to do about it


def test_a_complete_reply_passes_through_untouched():
    from app.api.v1.routers.cdd import _reject_if_truncated

    assert _reject_if_truncated(LLMResult("whole table", stop_reason="end_turn"), "x") is None
    assert _reject_if_truncated(LLMResult("whole table"), "x") is None


def test_every_cdd_route_that_overwrites_content_checks_before_committing():
    """Ordering matters more than presence: the check has to run before the merge
    or the splice, or the fragment is already in the section by the time it
    fires."""
    from pathlib import Path

    src = Path("app/api/v1/routers/cdd.py").read_text(encoding="utf-8")
    assert src.count("_reject_if_truncated(") == 4      # 1 definition + 3 call sites

    rows_check = src.index('_reject_if_truncated(result, "regenerating these rows")')
    merge = src.index("merged = scoped_regen.merge_rows(plan, result.text)")
    assert rows_check < merge, "the truncated rows would already be merged"
