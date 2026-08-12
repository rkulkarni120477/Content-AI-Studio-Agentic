"""Universal usage-logging choke point (Phase P0 of claude_plan_platform_hardening).

Regression tests for the fix where every LLM call now logs exactly once, from
inside _call_openai_raw/_call_bedrock_raw, instead of the previous design where
generate_with_metadata ALSO logged — which would have double-counted every
call's cost once logging moved down into the raw functions (see
IMPLEMENTATION_P0-P3.txt correction C1). Mocks the HTTP/boto3 boundary and the
usage-log sink per this repo's unit-test convention (tests/unit/ never touches
a real database — see tests/conftest.py's "Design rules").
"""

from __future__ import annotations

import promptops_app.core.llm_client as llm_client
import promptops_app.services.llm_service as llm_service
from promptops_app.services.usage_service import UsageLogContext


class _FakeResponse:
    def __init__(self, status_code=200, json_body=None):
        self.status_code = status_code
        self._json = json_body or {}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise Exception(f"HTTP {self.status_code}")

    def json(self):
        return self._json


def _ok_openai_body(text="OK"):
    return {
        "choices": [{"message": {"content": text}}],
        "usage": {"prompt_tokens": 5, "completion_tokens": 2},
    }


class _UsageRecorder:
    """Stand-in for log_llm_usage_autocommit — records calls, never touches a DB."""
    def __init__(self):
        self.calls = []

    def __call__(self, result, ctx):
        self.calls.append((result, ctx))


def _patch_openai_session(monkeypatch, response):
    session = type("S", (), {"post": lambda self, *a, **k: response})()
    monkeypatch.setattr(llm_client, "_get_openai_session", lambda: session)


def test_call_openai_wrapper_preserves_missing_key_string(monkeypatch):
    """C6: the exact pre-existing 'no API key' string must survive delegation."""
    monkeypatch.setattr(llm_client.settings, "openai_api_key", "")
    assert llm_client.call_openai("sys", "usr") == "ERROR: OpenAI API Key not configured."


def test_call_openai_wrapper_preserves_error_string_format(monkeypatch):
    """C6: call_openai must still return 'ERROR (OpenAI - <model>): <exc>' on failure."""
    monkeypatch.setattr(llm_client.settings, "openai_api_key", "sk-fake")
    monkeypatch.setattr(llm_client.settings, "openai_model", "gpt-4o")
    _patch_openai_session(monkeypatch, _FakeResponse(status_code=500))
    recorder = _UsageRecorder()
    monkeypatch.setattr(llm_client, "log_llm_usage_autocommit", recorder)

    result = llm_client.call_openai("sys", "usr")

    assert result.startswith("ERROR (OpenAI - gpt-4o):")
    # The failure path must still log — no call goes unlogged, even the legacy wrapper's.
    assert len(recorder.calls) == 1
    assert recorder.calls[0][0].status == "error"


def test_raw_openai_success_logs_exactly_one_attributed_row(monkeypatch):
    monkeypatch.setattr(llm_client.settings, "openai_api_key", "sk-fake")
    _patch_openai_session(monkeypatch, _FakeResponse(json_body=_ok_openai_body("Hello")))
    recorder = _UsageRecorder()
    monkeypatch.setattr(llm_client, "log_llm_usage_autocommit", recorder)
    ctx = UsageLogContext(user_name="alice", project_id=1, course_id=2, entity_type="test")

    resp = llm_client._call_openai_raw("sys", "usr", model="gpt-4o", usage_ctx=ctx)

    assert resp.text == "Hello"
    assert len(recorder.calls) == 1
    logged_result, logged_ctx = recorder.calls[0]
    assert logged_result.status == "success"
    assert logged_result.model == "gpt-4o"
    assert logged_result.prompt_tokens == 5
    assert logged_result.completion_tokens == 2
    assert logged_ctx is ctx


def test_raw_openai_failure_logs_exactly_one_error_row(monkeypatch):
    monkeypatch.setattr(llm_client.settings, "openai_api_key", "sk-fake")
    _patch_openai_session(monkeypatch, _FakeResponse(status_code=500))
    recorder = _UsageRecorder()
    monkeypatch.setattr(llm_client, "log_llm_usage_autocommit", recorder)
    ctx = UsageLogContext(user_name="alice", project_id=1, entity_type="test")

    try:
        llm_client._call_openai_raw("sys", "usr", model="gpt-4o", usage_ctx=ctx)
    except Exception:
        pass

    assert len(recorder.calls) == 1
    assert recorder.calls[0][0].status == "error"


def test_no_usage_ctx_still_logs_as_unattributed(monkeypatch):
    """No call's cost goes unlogged, even with no usage_ctx supplied."""
    monkeypatch.setattr(llm_client.settings, "openai_api_key", "sk-fake")
    _patch_openai_session(monkeypatch, _FakeResponse(json_body=_ok_openai_body()))
    recorder = _UsageRecorder()
    monkeypatch.setattr(llm_client, "log_llm_usage_autocommit", recorder)

    llm_client._call_openai_raw("sys", "usr", model="gpt-4o")

    assert len(recorder.calls) == 1
    _, ctx = recorder.calls[0]
    assert ctx.entity_type == "unattributed"
    assert ctx.entity_id == "direct_call"


def test_generate_with_metadata_does_not_double_log(monkeypatch):
    """THE regression this phase exists to prevent (correction C1).

    Exercises the real production path — generate_with_metadata ->
    _invoke_primary -> _call_openai_raw (only HTTP + the usage-log sink are
    mocked) — and asserts the sink fires exactly once, not twice. Before the
    fix, generate_with_metadata logged once AND the raw function logged again,
    double-counting every call's cost (which would silently over-charge budget
    enforcement in Phase P2 by 2x).
    """
    monkeypatch.setattr(llm_client.settings, "openai_api_key", "sk-fake")
    _patch_openai_session(monkeypatch, _FakeResponse(json_body=_ok_openai_body("Hello")))
    recorder = _UsageRecorder()
    monkeypatch.setattr(llm_client, "log_llm_usage_autocommit", recorder)
    ctx = UsageLogContext(user_name="alice", project_id=1, entity_type="test")

    result = llm_service.generate_with_metadata("GPT-5.4", "sys", "usr", ctx)

    assert result.status == "success"
    assert len(recorder.calls) == 1, (
        f"expected exactly 1 usage-log call, got {len(recorder.calls)} — "
        "double logging would over-charge P2 budget enforcement by 2x"
    )


def test_generate_with_metadata_retry_logs_one_row_per_attempt(monkeypatch):
    """A timeout-then-retry-success now logs 2 rows (one per attempt), not 1.

    Documented, intentional semantic change (see IMPLEMENTATION_P0-P3.txt P0.1
    step 4) — each provider attempt is its own billable event.
    """
    monkeypatch.setattr(llm_client.settings, "openai_api_key", "sk-fake")
    monkeypatch.setattr(llm_service._cfg, "llm_retry_count", 1)
    recorder = _UsageRecorder()
    monkeypatch.setattr(llm_client, "log_llm_usage_autocommit", recorder)

    calls = {"n": 0}

    def flaky_post(*a, **k):
        calls["n"] += 1
        if calls["n"] == 1:
            raise llm_client.LLMTimeoutError("timed out")
        return _FakeResponse(json_body=_ok_openai_body("Hello"))

    session = type("S", (), {"post": lambda self, *a, **k: flaky_post()})()
    monkeypatch.setattr(llm_client, "_get_openai_session", lambda: session)
    ctx = UsageLogContext(user_name="alice", project_id=1, entity_type="test")

    result = llm_service.generate_with_metadata("GPT-5.4", "sys", "usr", ctx)

    assert result.status == "retry_success"
    assert len(recorder.calls) == 2
    assert recorder.calls[0][0].status == "error"
    assert recorder.calls[1][0].status == "success"


if __name__ == "__main__":
    import pytest, sys
    sys.exit(pytest.main([__file__, "-v"]))
