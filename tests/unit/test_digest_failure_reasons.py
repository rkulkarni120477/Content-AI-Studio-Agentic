"""A partially-failed block-wide generation must say WHY the days failed.

A run where MAP fails on most days still completes: it persists, it pins itself
active, and it reports "Done, with gaps". The only thing separating it from real
work is a coverage warning naming the failed days — which on 2026-08-13 sent us
hunting Bedrock credentials, then the block id, then token truncation, while the
actual cause sat in a DIS log line that whoever reads the warning cannot open.

DIS records a real cause per failed day and returns it in the build report. These
tests pin that the cause survives the CAS boundary instead of being summarised
away into counters.
"""
from __future__ import annotations

from promptops_app.jobs import block_wide_jobs
from promptops_app.services import block_wide_service as svc


def _report(*errors, status="failed"):
    return {"per_day": [{"day_number": i, "status": status, "error": e}
                        for i, e in enumerate(errors, start=1)]}


def test_the_reason_survives_the_boundary_that_used_to_drop_it():
    got = svc._digest_failure_reasons(_report("model=sonnet-5 reply[:160]='{\"doc_type\":\"other\"}'"))
    assert got == ["model=sonnet-5 reply[:160]='{\"doc_type\":\"other\"}'"]


def test_twenty_days_failing_the_same_way_is_reported_once():
    """The day numbers already live in coverage.failed_days; repeating one identical
    sentence per day is noise that would bury a second, different cause."""
    assert svc._digest_failure_reasons(_report(*["identical boom"] * 20)) == ["identical boom"]


def test_distinct_causes_are_all_kept_up_to_the_cap():
    got = svc._digest_failure_reasons(_report("a", "b", "c", "d", "e"))
    assert got == ["a", "b", "c"], "distinct reasons kept, in order, capped"
    assert len(got) == svc._MAX_FAILURE_REASONS


def test_days_that_succeeded_contribute_no_reason():
    rep = {"per_day": [{"day_number": 1, "status": "built", "error": None},
                       {"day_number": 2, "status": "cached", "error": None},
                       {"day_number": 3, "status": "failed", "error": "real cause"}]}
    assert svc._digest_failure_reasons(rep) == ["real cause"]


def test_a_failed_day_with_no_recorded_error_does_not_invent_one():
    """Reporting a blank or placeholder cause would be worse than reporting none —
    it reads as though the cause were known."""
    assert svc._digest_failure_reasons(_report("", None)) == []


def test_a_malformed_report_is_not_a_crash():
    """The report is an HTTP body from DIS, so its shape is not guaranteed. Losing
    the reason is acceptable; failing the generation over it is not."""
    for bad in (None, "not a dict", 42, {}, {"per_day": None}, {"per_day": ["junk"]}):
        assert svc._digest_failure_reasons(bad) == []


def test_the_warning_the_user_actually_sees_names_the_cause():
    coverage = {"enumerated_days": 20, "failed_days": list(range(1, 19)),
                "failure_reasons": ["model=global.anthropic.claude-sonnet-5 reply='{\"doc_type\":\"other\"}'"]}
    warning = block_wide_jobs._coverage_warning(coverage)
    assert "18 of 20 days" in warning, "the existing signal must be preserved"
    assert "Cause:" in warning
    assert "sonnet-5" in warning


def test_several_distinct_causes_are_all_shown():
    coverage = {"enumerated_days": 20, "failed_days": [1, 2],
                "failure_reasons": ["truncated mid-JSON", "model could not be invoked"]}
    warning = block_wide_jobs._coverage_warning(coverage)
    assert "Causes:" in warning
    assert "truncated mid-JSON" in warning and "model could not be invoked" in warning


def test_a_clean_run_gains_no_cause_text():
    assert block_wide_jobs._coverage_warning({"enumerated_days": 20, "failed_days": []}) is None


def test_coverage_without_reasons_still_warns_as_before():
    """Older rows, and DIS versions that predate per-day errors, must not regress."""
    warning = block_wide_jobs._coverage_warning({"enumerated_days": 20, "failed_days": [3]})
    assert warning and "1 of 20 days" in warning
    assert "Cause" not in warning
