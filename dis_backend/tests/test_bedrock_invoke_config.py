"""Bedrock InvokeModel needs explicit timeouts, and a failed call must say so.

botocore's default read timeout is 60 seconds. A content-rich MAP day generates for
longer than that, so the socket raises ReadTimeoutError, ``call_llm`` swallows it into
a valid-JSON stub, and the day becomes filler that is indistinguishable from real
extraction downstream.

Measured 2026-08-13: prod's 20-day Block 2 build lost exactly the five heaviest days
(3, 6, 7, 8, 9) while the other fifteen succeeded, each recording
``{"doc_type":"other","classification":"internal"}`` — the signature of that swallowed
exception. CAS's Bedrock client had already set read_timeout=600 for the same reason;
DIS duplicates the helper and never inherited it.
"""
from __future__ import annotations

import types

import pytest

from services.digests import mapper
from services.pipeline import common


def test_the_read_timeout_is_far_above_botocores_default():
    """60s is the default and is below what a real MAP day takes. This is the whole
    bug — a number, not a code path."""
    cfg = common.bedrock_invoke_config()
    assert cfg.read_timeout >= 300, "a content-rich day generates for minutes"
    assert cfg.connect_timeout and cfg.connect_timeout <= 60


def test_retries_are_configured_for_a_call_per_day_workload():
    cfg = common.bedrock_invoke_config()
    assert cfg.retries["max_attempts"] > 1
    assert cfg.retries["mode"] == "adaptive"


def test_operators_can_override_the_timeout(monkeypatch):
    monkeypatch.setenv("DIS_BEDROCK_READ_TIMEOUT", "900")
    monkeypatch.setenv("DIS_BEDROCK_MAX_ATTEMPTS", "7")
    cfg = common.bedrock_invoke_config()
    assert cfg.read_timeout == 900
    assert cfg.retries["max_attempts"] == 7


@pytest.mark.parametrize("junk", ["", "   ", "abc", "0", "-5", "12.5"])
def test_a_malformed_override_falls_back_and_never_becomes_zero(monkeypatch, junk):
    """A zero read timeout or zero attempts would be a far worse reading of a typo
    than ignoring it."""
    monkeypatch.setenv("DIS_BEDROCK_READ_TIMEOUT", junk)
    cfg = common.bedrock_invoke_config()
    assert cfg.read_timeout == 600


def test_the_failure_stub_is_recognisable():
    assert common.is_llm_failure_stub(common.LLM_FAILURE_STUB) is True
    # Reformatted / reordered JSON must still match — it arrives via safe_json.
    assert common.is_llm_failure_stub('{"classification": "internal", "doc_type": "other"}') is True


def test_real_model_output_is_not_mistaken_for_the_failure_stub():
    for text in ('{"derived_objective":"o","concept_type":"Conceptual"}',
                 '{"doc_type":"study_material","classification":"internal"}',  # the dev MOCK
                 '{"doc_type":"other"}',
                 'not json at all', '', '[]', 'null'):
        assert common.is_llm_failure_stub(text) is False, text


def test_a_provider_failure_is_reported_as_such_not_as_a_bad_reply(monkeypatch):
    """The message must send an operator to credentials/timeouts, not to the prompt."""
    monkeypatch.setattr(common, "call_llm",
                        lambda model, prompt, max_tokens=300: (common.LLM_FAILURE_STUB, 0, 0))

    tenant = types.SimpleNamespace(
        pipeline=types.SimpleNamespace(models=types.SimpleNamespace(digest_extraction="m")))
    digest = mapper.build_digest({"day_number": 3, "topic": "t", "lesson_title": "l"},
                                 [], tenant, model="m", client_id="aim", block="Block 2")

    assert digest["digest_status"] == "failed"
    err = digest["error"]
    assert "FAILED at the provider" in err
    assert "not a prompt or schema one" in err
    assert "lacks the required keys" not in err


def test_a_genuinely_malformed_reply_still_reports_the_schema_problem(monkeypatch):
    """The opposite case must keep its own distinct message and quote the reply."""
    monkeypatch.setattr(common, "call_llm",
                        lambda model, prompt, max_tokens=300: ('{"unexpected":"shape"}', 12, 3))

    tenant = types.SimpleNamespace(
        pipeline=types.SimpleNamespace(models=types.SimpleNamespace(digest_extraction="m")))
    digest = mapper.build_digest({"day_number": 4, "topic": "t", "lesson_title": "l"},
                                 [], tenant, model="m", client_id="aim", block="Block 2")

    err = digest["error"]
    assert "lacks the required keys" in err
    assert "FAILED at the provider" not in err
    assert "unexpected" in err, "the actual reply must be quoted"
