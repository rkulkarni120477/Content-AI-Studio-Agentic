"""A failed block-wide job must say *why* it failed.

Every digest-pipeline failure used to reach the user as one fixed sentence —
"Digest pipeline unavailable (no enumerated days or DIS error)" — which merges
causes with completely different fixes:

  * DIS unreachable / erroring        -> infrastructure or credentials
  * DIS answered with zero days       -> nothing ingested, or wrong block id
  * REDUCE blew up after the build    -> CAS-side model/prompt/parse bug

In prod the job row is the only place a failure surfaces, so collapsing them
meant diagnosis required container logs that whoever sees the error cannot read.
These tests pin that each cause is distinguishable from the others.
"""
from __future__ import annotations

import pytest

from promptops_app.services import block_wide_service as svc


class _Req:
    block = "02"
    project_id = None
    course_id = None
    prompt_id = None
    quality_tier = None


class _User:
    username = "tester"


def _build(monkeypatch, *, build=None, bundle=None):
    """Run _build_and_reduce with DIS stubbed; return (result, report, directives)."""
    class _Stub:
        def build_digests_sync(self, block, **kw):
            if callable(build):
                return build()
            return build or {}

        def get_digests_bundle_sync(self, block, **kw):
            if callable(bundle):
                return bundle()
            return bundle or {}

    monkeypatch.setattr(svc, "dis_client", _Stub())
    # Budget/usage plumbing is not under test and needs a DB; neutralise it.
    monkeypatch.setattr(svc, "_map_usage_ctx", lambda *a, **k: None)
    monkeypatch.setattr(svc, "_reserve_map_budget", lambda *a, **k: [])
    monkeypatch.setattr(svc, "_settle_map_usage", lambda *a, **k: None)
    return svc._build_and_reduce("cdd", "02", None, _User(), "aim",
                                 db=None, request_body=_Req())


def test_a_dis_outage_names_the_exception_rather_than_the_generic_sentence(monkeypatch):
    def boom():
        raise ConnectionError("connection to server at 'dis-dev-postgres' failed")

    assert _build(monkeypatch, build=boom)[:2] == (None, None)
    reason = svc.last_failure_reason()
    assert "ConnectionError" in reason
    assert "dis-dev-postgres" in reason
    assert "no enumerated days" not in reason, "must not read as a content problem"


def test_an_exception_with_an_empty_message_still_reports_something_useful(monkeypatch):
    """str() on many connection errors is "" — reporting a blank reason would be
    no better than the generic sentence it replaces."""
    def boom():
        raise TimeoutError()

    _build(monkeypatch, build=boom)
    reason = svc.last_failure_reason()
    assert "TimeoutError" in reason and reason.strip()


def test_zero_enumerated_days_reads_as_a_content_problem_not_an_outage(monkeypatch):
    """DIS answered — so pointing an operator at infrastructure would waste the
    call. This is a wrong block id / nothing-ingested problem."""
    assert _build(monkeypatch, bundle={"enumerate": {"days": []}})[:2] == (None, None)
    reason = svc.last_failure_reason()
    assert "no days" in reason.lower()
    assert "aim" in reason, "the DIS client is what scopes the lookup"
    assert "Error" not in reason, "nothing threw; do not imply an exception"


def test_the_two_causes_do_not_produce_the_same_message(monkeypatch):
    def boom():
        raise ConnectionError("refused")

    _build(monkeypatch, build=boom)
    outage = svc.last_failure_reason()
    _build(monkeypatch, bundle={"enumerate": {"days": []}})
    empty = svc.last_failure_reason()
    assert outage != empty


def test_a_reduce_failure_is_distinguished_from_a_build_failure(monkeypatch):
    """The digests were built and paid for here, so the fault is CAS-side. Blaming
    DIS would send someone to the wrong service."""
    monkeypatch.setattr(
        svc, "_build_and_reduce", svc._build_and_reduce, raising=False)

    class _Gen:
        def __init__(self, **kw): pass
        def reduce(self, *a, **k): raise ValueError("bad reduce payload")

    import promptops_app.services.block_wide_generator as bwg
    monkeypatch.setattr(bwg, "BlockWideGenerator", _Gen)

    assert _build(monkeypatch, bundle={"enumerate": {"days": [1]}})[:2] == (None, None)
    reason = svc.last_failure_reason()
    assert "REDUCE" in reason and "ValueError" in reason
    assert "after digests were built" in reason


def test_a_stale_reason_is_not_reported_by_a_later_attempt(monkeypatch):
    """A retry in the same context must not inherit the previous cause — that would
    be actively misleading, unlike a generic message."""
    def boom():
        raise ConnectionError("refused")

    _build(monkeypatch, build=boom)
    assert svc.last_failure_reason()

    ok_bundle = {"enumerate": {"days": [1]}, "digests": []}

    class _Gen:
        def __init__(self, **kw): pass
        def reduce(self, *a, **k): return {"ok": True}

    import promptops_app.services.block_wide_generator as bwg
    monkeypatch.setattr(bwg, "BlockWideGenerator", _Gen)

    result, _, _ = _build(monkeypatch, bundle=ok_bundle)
    assert result == {"ok": True}
    assert svc.last_failure_reason() == "", "a success must clear the reason"


def test_a_budget_breach_is_still_raised_and_not_turned_into_a_reason(monkeypatch):
    """BudgetExceededError must keep reaching the HTTP layer as a real 402 rather
    than being folded into the None/fallback path."""
    from promptops_app.services.budget_service import BudgetExceededError

    def boom():
        raise BudgetExceededError("project", "23", 10.0, 12.5)

    with pytest.raises(BudgetExceededError):
        _build(monkeypatch, build=boom)


def test_the_job_layer_prefers_the_specific_reason_over_the_generic_one():
    """The service can record a cause, but it only reaches the user if the job layer
    actually uses it in set_failed."""
    import inspect
    from promptops_app.jobs import block_wide_jobs

    src = inspect.getsource(block_wide_jobs.run_block_wide_job)
    assert "last_failure_reason()" in src
    generic = "Digest pipeline unavailable"
    idx_reason = src.index("last_failure_reason()")
    idx_generic = src.index(generic)
    assert idx_reason < idx_generic, "the generic string must be the fallback, not the default"
