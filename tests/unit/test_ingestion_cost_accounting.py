"""Cost accounting for the DIS ingestion pipeline's LLM spend.

Ingestion runs four LLM steps per document (classification, metadata extraction,
structure extraction, quality check) on DIS's own Bedrock client, so — like MAP
before it — that spend never passes CAS's usage/budget choke point. These tests
cover the measurement half: DIS now tracks the input/output split and surfaces a
per-run summary that CAS can price and attribute.

Embedding pricing is here too because it is the same class of bug: a model the
pricing table does not recognise falls through to the generic fallback, and for
Titan that fallback is ~250x the real rate.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "dis_backend"))

from promptops_app.services.usage_service import _find_pricing, estimate_cost  # noqa: E402


# --------------------------------------------------------------------------- #
# Pricing
# --------------------------------------------------------------------------- #
def test_titan_embeddings_are_not_priced_at_the_generic_fallback():
    """amazon.titan-embed-text-v2 contains none of the chat-model markers, so it hit
    _default at $5/1M — ~250x Titan's real $0.02/1M. Embedding every chunk of a real
    corpus would have reported cents of spend as tens of dollars, and could trip a
    budget that was never actually approached."""
    pricing = _find_pricing("amazon.titan-embed-text-v2:0")
    assert pricing["input"] == 0.02
    assert pricing["output"] == 0.0, "an embedding call returns a vector, not tokens"
    assert pricing != _find_pricing("some-model-nobody-knows")

    # 5,000 chunks x 500 tokens: cents, not dollars.
    assert estimate_cost("amazon.titan-embed-text-v2:0", 5000 * 500, 0) == pytest.approx(0.05)


def test_cohere_embeddings_are_recognised_too():
    assert _find_pricing("cohere.embed-english-v3")["input"] == 0.10


@pytest.mark.parametrize("model,expected_input", [
    ("global.anthropic.claude-sonnet-5", 3.00),
    ("global.anthropic.claude-opus-5", 15.00),
    ("global.anthropic.claude-haiku-4-5-20251001-v1:0", 0.80),
    ("global.anthropic.claude-fable-5-1", 10.00),
    ("global.anthropic.claude-fable-5", 10.00),
    ("gpt-4o", 5.00),
    ("gpt-5.6-sol", 4.00),
    ("gpt-5.6-terra", 2.00),
    ("gpt-5.6-luna", 0.20),
    ("GPT-5.6 Sol", 4.00),
])
def test_the_embedding_rules_did_not_disturb_chat_pricing(model, expected_input):
    """The embedding checks run first, so this pins that they only match embeddings."""
    assert _find_pricing(model)["input"] == expected_input


@pytest.mark.parametrize("model,expected_output", [
    ("gpt-5.6-sol", 20.00),
    ("gpt-5.6-terra", 12.00),
    ("gpt-5.6-luna", 1.20),
])
def test_gpt56_family_has_distinct_output_prices(model, expected_output):
    """Luna/Terra must not inherit Sol (or gpt-4o) rates via a broad substring."""
    assert _find_pricing(model)["output"] == expected_output


def test_an_unknown_model_still_falls_back_rather_than_costing_zero():
    """Zero would silently hide real spend; the generic rate is the safer error."""
    assert estimate_cost("brand-new-model-v9", 1_000_000, 0) > 0


# --------------------------------------------------------------------------- #
# TokenGuard — the measurement DIS now exposes
# --------------------------------------------------------------------------- #
def _guard():
    from services.token_guard import TokenGuard
    return TokenGuard(None, "tester")


def test_the_split_is_tracked_because_input_and_output_price_differently():
    """The combined total this class always tracked cannot be costed: Sonnet is $3/1M
    in and $15/1M out, so one number cannot be turned back into a dollar figure."""
    g = _guard()
    g.record_usage(1350, "content_classification", tokens_in=1200, tokens_out=150,
                   model="global.anthropic.claude-sonnet-5")
    s = g.usage_summary()
    assert (s["tokens_in"], s["tokens_out"]) == (1200, 150)
    assert s["model"] == "global.anthropic.claude-sonnet-5"
    assert estimate_cost(s["model"], s["tokens_in"], s["tokens_out"]) > 0


def test_historical_single_total_callers_are_unchanged():
    """record_usage's original one-argument form must keep working — this is a
    compatibility layer other agents still call."""
    g = _guard()
    g.record_usage(300, "legacy_step")
    assert g.tokens_used == 300
    assert g.usage_summary()["tokens_in"] == 0, "no split claimed where none was given"


def test_totals_accumulate_across_the_four_ingestion_steps():
    g = _guard()
    for step, i, o in (("content_classification", 1200, 150),
                       ("metadata_extraction", 900, 120),
                       ("structure_extraction", 1500, 400),
                       ("quality_check", 800, 180)):
        g.record_usage(i + o, step, tokens_in=i, tokens_out=o,
                       model="global.anthropic.claude-sonnet-5")
    s = g.usage_summary()
    assert s["calls"] == 4
    assert s["tokens_in"] == 4400 and s["tokens_out"] == 850
    assert s["tokens_total"] == 5250
    assert s["models"] == {"global.anthropic.claude-sonnet-5": 4}


def test_summary_is_safe_on_a_run_that_made_no_llm_calls():
    """A cached or duplicate file skips every LLM step; the summary must not raise on
    an empty model map."""
    s = _guard().usage_summary()
    assert s["tokens_in"] == 0 and s["calls"] == 0 and s["model"] == ""


def test_get_status_still_reports_and_now_carries_the_split():
    g = _guard()
    g.record_usage(100, "s", tokens_in=80, tokens_out=20, model="m")
    status = g.get_status()
    assert status["tokens_used"] == 100
    assert status["tokens_in"] == 80 and status["tokens_out"] == 20


# --------------------------------------------------------------------------- #
# The contract that carries it out of DIS
# --------------------------------------------------------------------------- #
def test_every_ingestion_agent_that_spends_tokens_reports_the_split():
    """A step recording only a combined total cannot be costed, so it would be spend
    that reaches CAS unpriced. Guards all four rather than the ones that happened to
    be wired."""
    import re
    agents = Path("dis_backend/services/agents")
    offenders = []
    for name in ("content_classification_agent.py", "metadata_extraction_agent.py",
                 "structure_extraction_agent.py", "quality_check_agent.py"):
        src = (agents / name).read_text()
        assert "call_llm(" in src, f"{name} no longer calls an LLM — update this test"
        for m in re.finditer(r"record_usage\((.*?)\)", src, re.S):
            if "tokens_in=" not in m.group(1):
                offenders.append(f"{name}: {m.group(1)[:60]}")
    assert offenders == [], f"these record a total with no split: {offenders}"


def test_run_pipeline_attaches_the_usage_summary_to_its_result():
    """The guard is local to run_pipeline, so without this the totals died there and
    ingestion spend could never leave DIS at all."""
    import inspect
    from services.pipeline import graph
    src = inspect.getsource(graph.run_pipeline)
    assert 'state["llm_usage"] = guard.usage_summary()' in src
    assert "return state" in src


def test_the_ingestion_response_exposes_llm_usage():
    """CAS reads job metadata; a summary that stops at the pipeline boundary is
    invisible to it."""
    src = Path("dis_backend/api/routers/ingestion.py").read_text()
    assert '"llm_usage": result.get("llm_usage", {})' in src
