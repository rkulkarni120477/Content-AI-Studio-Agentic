"""A MAP call that produced a rejected reply still cost money — record it.

``build_digest`` increments the token/call budget only after ``_llm_extract``
RETURNS. Every failure path therefore contributed zero calls and zero tokens to
the build report, no matter how much it actually spent.

Observed in production 2026-08-13 (AIM Block 2): all 20 days failed extraction
and the build report read ``map_calls: 0, map_tokens_in: 0, map_tokens_out: 0``.
That is not merely a missing statistic — it made the report actively misleading.
It reads as "the model was never called", which is the opposite of what happened,
and it is exactly the reading that sent diagnosis down the wrong path.

These tests pin that a rejected reply reports its cost and names the model that
produced it (which escalation may have changed from the configured one).
"""
from __future__ import annotations

import types

import pytest

from services.digests import mapper


def _tenant():
    return types.SimpleNamespace(
        pipeline=types.SimpleNamespace(models=types.SimpleNamespace(digest_extraction="stub-model")))


def _day():
    return {"day_number": 7, "topic": "Corrosion", "lesson_title": "L7"}


@pytest.fixture
def rejecting_llm(monkeypatch):
    """A model that answers, is billed, and returns a reply MAP cannot use —
    call_llm's exception stub, which is what an uninvokable model produces."""
    import services.pipeline.common as common

    def fake_call_llm(model, prompt, max_tokens=300):
        return '{"doc_type":"other","confidence":0.0}', 9100, 12
    monkeypatch.setattr(common, "call_llm", fake_call_llm)


def test_a_rejected_reply_reports_the_tokens_it_burned(rejecting_llm):
    budget = {"calls": 0, "tok_in": 0, "tok_out": 0}
    digest = mapper.build_digest(_day(), [], _tenant(), model="stub-model",
                                 client_id="aim", block="Block 2", budget=budget)

    assert digest["digest_status"] == "failed"
    assert budget == {"calls": 1, "tok_in": 9100, "tok_out": 12}, (
        "a billed call reported as zero spend is what made prod's report read "
        "'the model was never called'")


def test_the_failure_is_still_isolated_to_its_own_day(rejecting_llm):
    """Accounting for the failure must not change the per-day isolation contract."""
    digest = mapper.build_digest(_day(), [], _tenant(), model="stub-model",
                                 client_id="aim", block="Block 2", budget=None)
    assert digest["digest_status"] == "failed"
    assert digest["error"]


def test_the_recorded_error_distinguishes_a_stub_from_a_truncated_reply(rejecting_llm):
    """The message is the only artefact that leaves the DIS process, and the two
    causes need completely different fixes."""
    digest = mapper.build_digest(_day(), [], _tenant(), model="stub-model",
                                 client_id="aim", block="Block 2")
    err = digest["error"]
    assert "doc_type" in err, "the raw reply must be visible, not just its keys"
    assert "stub-model" in err, "the model that failed must be named"


def test_the_model_that_actually_ran_is_recorded_on_a_failed_day(monkeypatch):
    """Escalation can swap a content-rich day onto a larger-context model. When that
    model is the one that cannot be invoked, the failed digest is the only place the
    swap is visible — recording the configured model instead hides the real culprit."""
    import services.pipeline.common as common
    monkeypatch.setattr(common, "call_llm",
                        lambda model, prompt, max_tokens=300: ('{"doc_type":"other"}', 5, 1))
    monkeypatch.setattr(mapper, "select_model_for",
                        lambda model, size: ("escalated-big-context-model", "escalated: day too large"))

    digest = mapper.build_digest(_day(), [], _tenant(), model="stub-model",
                                 client_id="aim", block="Block 2")
    assert digest["digest_status"] == "failed"
    assert digest["extractor_model"] == "escalated-big-context-model"
    assert "escalated-big-context-model" in digest["error"]


def test_a_failure_before_the_call_reports_no_spend(monkeypatch):
    """Only MapExtractionError knows a call was made. Anything raised around the
    call has no usable counts, and inventing them would be worse than none."""
    import services.pipeline.common as common

    def exploding_call_llm(model, prompt, max_tokens=300):
        raise RuntimeError("connection reset")
    monkeypatch.setattr(common, "call_llm", exploding_call_llm)

    budget = {"calls": 0, "tok_in": 0, "tok_out": 0}
    digest = mapper.build_digest(_day(), [], _tenant(), model="stub-model",
                                 client_id="aim", block="Block 2", budget=budget)
    assert digest["digest_status"] == "failed"
    assert budget == {"calls": 0, "tok_in": 0, "tok_out": 0}


def test_a_successful_day_still_counts_exactly_once(monkeypatch):
    """The failure accounting must not double-count the success path."""
    import services.pipeline.common as common
    monkeypatch.setattr(common, "call_llm", lambda model, prompt, max_tokens=300: (
        '{"derived_objective":"o","misconceptions":[],"salient_excerpts":[],'
        '"concept_type":"Conceptual"}', 40, 7))

    budget = {"calls": 0, "tok_in": 0, "tok_out": 0}
    digest = mapper.build_digest(_day(), [], _tenant(), model="stub-model",
                                 client_id="aim", block="Block 2", budget=budget)
    assert digest["digest_status"] == "ok"
    assert budget == {"calls": 1, "tok_in": 40, "tok_out": 7}


def test_the_error_carries_its_cost_for_the_caller_to_record():
    exc = mapper.MapExtractionError("bad reply", tokens_in=11, tokens_out=2, model="m")
    assert (exc.tokens_in, exc.tokens_out, exc.model) == (11, 2, "m")


def test_the_error_defaults_are_safe_for_callers_that_omit_them():
    """Constructed without counts elsewhere, it must still behave like a plain error."""
    exc = mapper.MapExtractionError("bad reply")
    assert (exc.tokens_in, exc.tokens_out, exc.model) == (0, 0, "")
    assert isinstance(exc, RuntimeError)
