"""MAP-stage cost accounting for the block-wide digest pipeline.

MAP (per-day digest extraction) runs inside DIS on DIS's own Bedrock client, so it
never passes through CAS's usage/budget choke point in core/llm_client.py. Measured
on a real 20-day AIM block: MAP spent ~176k input / 15k output tokens while REDUCE
spent ~6k / 2.5k — so ~96% of a block-wide generation was invisible to
llm_usage_logs, the cost dashboards, and the token-cap budgets, and no budget could
stop a run however large it got.

These tests pin the two halves that close it: a pre-flight reservation that can
refuse an over-budget build, and a post-build record + reconcile that uses the token
counts DIS actually reports rather than the estimate.
"""
from __future__ import annotations

import types

import pytest

from promptops_app.services import block_wide_service as bws
from promptops_app.services.budget_service import BudgetExceededError, BudgetReservation


def _req(**kw):
    base = dict(project_id=23, course_id=48, block="Block 2")
    base.update(kw)
    return types.SimpleNamespace(**base)


_USER = types.SimpleNamespace(username="platformadmin")
_REPORT = {"map_calls": 20, "map_tokens_in": 176_155, "map_tokens_out": 15_033,
           "map_model": "global.anthropic.claude-sonnet-4-5-20250929-v1:0",
           "prompt_version": "map-v6+abc123"}


# --------------------------------------------------------------------------- #
# Context
# --------------------------------------------------------------------------- #
def test_usage_context_attributes_map_spend_to_the_right_scopes():
    """Budgets are enforced per project/course/user, so all three must be present or
    the spend lands in no bucket at all."""
    ctx = bws._map_usage_ctx("cdd", _req(), _USER)
    assert (ctx.project_id, ctx.course_id, ctx.user_name) == (23, 48, "platformadmin")
    assert ctx.entity_type == "cdd"
    assert ctx.entity_id == "Block 2"
    assert ctx.prompt_template == "digest_map", "must be distinguishable from REDUCE rows"


def test_context_failure_degrades_to_none_rather_than_raising(monkeypatch):
    """Cost accounting must never be the reason a generation fails."""
    monkeypatch.setattr(bws, "_log", bws._log)
    bad = types.SimpleNamespace()          # no project_id/course_id attributes at all
    ctx = bws._map_usage_ctx("cdd", bad, None)
    assert ctx is not None and ctx.project_id is None    # getattr defaults, no raise


# --------------------------------------------------------------------------- #
# Reservation
# --------------------------------------------------------------------------- #
def test_no_db_or_context_is_a_documented_no_op():
    assert bws._reserve_map_budget(None, None) == []
    assert bws._reserve_map_budget(None, bws._map_usage_ctx("cdd", _req(), _USER)) == []


def test_reservation_uses_an_explicit_token_estimate_not_prompt_lengths(monkeypatch):
    """CAS has no MAP prompts to size a reservation from — they are built per-day
    inside DIS. Passing empty prompts without the explicit estimate would reserve
    ~0 tokens and make the whole check vacuous."""
    seen = {}

    def fake_check(db, ctx, **kw):
        seen.update(kw)
        return types.SimpleNamespace(reservations=["r"], warnings=[])

    monkeypatch.setattr("promptops_app.services.budget_service.check_budget", fake_check)
    out = bws._reserve_map_budget(object(), bws._map_usage_ctx("cdd", _req(), _USER))

    assert out == ["r"]
    assert seen["estimated_input_tokens"] == bws._MAP_ESTIMATE_DAYS * bws._MAP_EST_INPUT_TOKENS_PER_DAY
    assert seen["estimated_output_tokens"] == bws._MAP_ESTIMATE_DAYS * bws._MAP_EST_OUTPUT_TOKENS_PER_DAY
    assert seen["estimated_input_tokens"] > 100_000, "estimate must reflect a real block"


def test_a_breach_propagates_so_the_build_is_refused(monkeypatch):
    """The point of reserving before the build: an over-budget run must not spend
    ~176k tokens and only then be noticed."""
    def boom(db, ctx, **kw):
        raise BudgetExceededError("project", "23", 10.0, 11.0, "usd")

    monkeypatch.setattr("promptops_app.services.budget_service.check_budget", boom)
    with pytest.raises(BudgetExceededError):
        bws._reserve_map_budget(object(), bws._map_usage_ctx("cdd", _req(), _USER))


def test_any_other_reservation_failure_lets_the_build_proceed(monkeypatch):
    """Fail OPEN, matching check_budget's own locked decision: a bug in cost code
    must never take down generation."""
    def boom(db, ctx, **kw):
        raise RuntimeError("budget table missing")

    monkeypatch.setattr("promptops_app.services.budget_service.check_budget", boom)
    assert bws._reserve_map_budget(object(), bws._map_usage_ctx("cdd", _req(), _USER)) == []


# --------------------------------------------------------------------------- #
# Recording + reconciliation
# --------------------------------------------------------------------------- #
def test_reported_tokens_are_recorded_with_the_model_dis_actually_used(monkeypatch):
    logged = {}
    monkeypatch.setattr("promptops_app.services.usage_service.log_llm_usage",
                        lambda db, result, ctx: logged.update(result=result, ctx=ctx))
    monkeypatch.setattr("promptops_app.services.budget_service.reconcile_budget",
                        lambda *a, **k: None)

    bws._settle_map_usage(object(), bws._map_usage_ctx("cdd", _req(), _USER), _REPORT, [])

    res = logged["result"]
    assert (res.prompt_tokens, res.completion_tokens) == (176_155, 15_033)
    assert res.model == _REPORT["map_model"], "cost must be priced on the real model"
    assert logged["ctx"].prompt_version == "map-v6+abc123", "ties the row to the MAP prompt"


def test_reconciliation_replaces_the_estimate_with_the_real_cost(monkeypatch):
    calls = []
    monkeypatch.setattr("promptops_app.services.usage_service.log_llm_usage", lambda *a, **k: None)
    monkeypatch.setattr("promptops_app.services.budget_service.reconcile_budget",
                        lambda db, res, cost, tokens: calls.append((res, cost, tokens)))
    reservation = [BudgetReservation("project", "23", "2026-08", 0.94, 238_750)]

    bws._settle_map_usage(object(), bws._map_usage_ctx("cdd", _req(), _USER), _REPORT, reservation)

    assert len(calls) == 1
    _res, cost, tokens = calls[0]
    assert tokens == 176_155 + 15_033
    assert 0.7 < cost < 0.8, f"expected the measured ~$0.75, got {cost}"
    assert cost < reservation[0].reserved_usd, "reconciliation must release the excess"


def test_a_failed_build_still_releases_its_reservation(monkeypatch):
    """Without this a failure leaks its worst-case hold until the period rolls over,
    suppressing every later generation in that budget scope."""
    calls = []
    monkeypatch.setattr("promptops_app.services.usage_service.log_llm_usage", lambda *a, **k: None)
    monkeypatch.setattr("promptops_app.services.budget_service.reconcile_budget",
                        lambda db, res, cost, tokens: calls.append((cost, tokens)))
    reservation = [BudgetReservation("project", "23", "2026-08", 0.94, 238_750)]

    bws._settle_map_usage(object(), bws._map_usage_ctx("cdd", _req(), _USER), None, reservation)

    assert calls == [(0.0, 0)], "a build that reported nothing must settle at zero"


def test_a_zero_token_report_writes_no_usage_row(monkeypatch):
    """A fully cached rebuild makes no MAP calls; a $0 row would be noise that makes
    the dashboards look busier than the spend was."""
    logged = []
    monkeypatch.setattr("promptops_app.services.usage_service.log_llm_usage",
                        lambda *a, **k: logged.append(1))
    monkeypatch.setattr("promptops_app.services.budget_service.reconcile_budget", lambda *a, **k: None)

    bws._settle_map_usage(object(), bws._map_usage_ctx("cdd", _req(), _USER),
                          {"map_calls": 0, "map_tokens_in": 0, "map_tokens_out": 0}, [])
    assert logged == []


def test_settling_never_raises_even_when_both_halves_fail(monkeypatch):
    """It runs in a finally on the generation path — an exception here would replace
    a real result (or a real error) with a bookkeeping crash."""
    monkeypatch.setattr("promptops_app.services.usage_service.log_llm_usage",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("db gone")))
    monkeypatch.setattr("promptops_app.services.budget_service.reconcile_budget",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("also gone")))
    bws._settle_map_usage(object(), bws._map_usage_ctx("cdd", _req(), _USER), _REPORT,
                          [BudgetReservation("project", "23", "2026-08", 0.94, 1)])


def test_an_older_dis_without_map_model_still_prices_in_the_right_family(monkeypatch):
    """map_model is new; a report from a DIS that predates it must not price at $0."""
    logged = {}
    monkeypatch.setattr("promptops_app.services.usage_service.log_llm_usage",
                        lambda db, result, ctx: logged.update(result=result))
    monkeypatch.setattr("promptops_app.services.budget_service.reconcile_budget", lambda *a, **k: None)

    bws._settle_map_usage(object(), bws._map_usage_ctx("cdd", _req(), _USER),
                          {"map_tokens_in": 1000, "map_tokens_out": 100}, [])

    from promptops_app.services.usage_service import estimate_cost
    assert logged["result"].model == bws._MAP_PRICING_MODEL
    assert estimate_cost(logged["result"].model, 1000, 100) > 0


# --------------------------------------------------------------------------- #
# The DIS contract this depends on
# --------------------------------------------------------------------------- #
def test_dis_build_report_carries_the_model_and_token_counts():
    """Token counts cannot be costed without the model, so the report must send it."""
    import sys
    from pathlib import Path
    dis = str(Path(__file__).resolve().parents[2] / "dis_backend")
    if dis not in sys.path:
        sys.path.insert(0, dis)
    import inspect
    from services.digests import build as dis_build

    src = inspect.getsource(dis_build._finalize_report)
    for key in ('"map_calls"', '"map_tokens_in"', '"map_tokens_out"', '"map_model"'):
        assert key in src, f"DIS build report no longer reports {key}"


def test_a_malformed_dis_report_settles_at_zero_rather_than_raising(monkeypatch):
    """The report is an HTTP body from DIS, so its shape is not guaranteed. Found by
    an existing eval whose stub returns a string: a non-dict raised AttributeError out
    of the finally block, which would have replaced the generation's real result (or
    its real error) with a bookkeeping crash."""
    calls = []
    monkeypatch.setattr("promptops_app.services.usage_service.log_llm_usage",
                        lambda *a, **k: calls.append("logged"))
    monkeypatch.setattr("promptops_app.services.budget_service.reconcile_budget",
                        lambda db, res, cost, tokens: calls.append((cost, tokens)))
    reservation = [BudgetReservation("project", "23", "2026-08", 0.94, 238_750)]

    for bad in ("an error string", 42, ["a", "list"], {"map_tokens_in": "lots"}):
        calls.clear()
        bws._settle_map_usage(object(), bws._map_usage_ctx("cdd", _req(), _USER),
                              bad, reservation)
        assert calls == [(0.0, 0)], f"{bad!r} did not settle cleanly at zero"
