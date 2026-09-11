"""Day-scoped grounding for the classic single-item Blueprint flow (app.core.dis_day_context).

Offline + deterministic — no DB/DIS/Bedrock calls. Covers the two independently
testable units the router wires together: infer_block_label (pure regex) and
resolve_day_context_block (the three-gate decision: day_number set, digest
pipeline enabled for the client, a parseable block label — any miss is a
no-op, never an error).
"""
from __future__ import annotations

import pytest

from app.core.config import settings
from app.core.dis_day_context import infer_block_label, resolve_day_context_block


def test_course_model_has_name_not_title():
    """Regression: blueprints.py's generate_blueprint called
    resolve_day_context_block(..., course.title if course else None, ...) —
    Course has no `title` attribute, only `name`. Since Python evaluates call
    arguments eagerly, this raised AttributeError on EVERY blueprint-generate
    request that resolved a course, unconditionally — not gated by day_number
    or the digest-pipeline flag at all, defeating the "additive only, never
    breaks an unrelated request" design this whole feature depends on. Caught
    by an adversarial review, not by any test — this locks in the real
    attribute shape so it can't silently regress again."""
    from promptops_app.database import Course

    course = Course(id=1, project_id=1, name="General Science II")
    assert course.name == "General Science II"
    assert not hasattr(course, "title")


def test_blueprints_router_does_not_reference_course_title():
    """Belt-and-suspenders static guard alongside the attribute-shape test
    above: greps the actual call site so a future edit can't reintroduce
    `course.title` even if Course's own shape changes for unrelated reasons."""
    import inspect

    from app.api.v1.routers import blueprints

    source = inspect.getsource(blueprints)
    assert "course.title" not in source


@pytest.fixture
def digest_pipeline_on_for_aim_only():
    """Matches the real default: master switch on, allowlist = 'aim' only.
    Mirrors test_block_wide_coverage.py::test_digest_pipeline_flag_gate's
    save/restore pattern — settings is a pydantic model, so its own
    digest_pipeline_on_for() method can't be monkeypatched directly; toggle
    the real fields it reads instead."""
    orig_enabled, orig_clients = settings.digest_pipeline_enabled, settings.digest_pipeline_clients
    settings.digest_pipeline_enabled = True
    settings.digest_pipeline_clients = "aim"
    try:
        yield
    finally:
        settings.digest_pipeline_enabled, settings.digest_pipeline_clients = orig_enabled, orig_clients


def test_infer_block_label_matches_common_formats():
    assert infer_block_label("Block 2 — General Science II: Aircraft Drawings") == "Block 2"
    assert infer_block_label("BLK 06 Something") == "Block 6"
    assert infer_block_label("block   2 lowercase") == "Block 2"


def test_infer_block_label_tries_titles_in_order_and_falls_back():
    # First title has no match -> falls through to the second.
    assert infer_block_label("Intro to Aviation", "Block 3 — Materials") == "Block 3"


def test_infer_block_label_returns_none_when_nothing_matches():
    assert infer_block_label(None, "", "Module 2 — Pharmacology Basics") is None


def _fail_if_dis_day_context_called(monkeypatch):
    """Regression on the tests themselves, caught by adversarial review: the
    three no-op tests below used to just assert the return value == "" — but
    _dis_day_context_block already degrades to ("", []) on ANY failure
    (including a real DIS call failing because there's no DIS reachable in
    this offline test run), so those assertions would have kept passing even
    with the actual gate (day_number/allowlist/block-label check) deleted
    entirely — they'd just take a longer path to the same "" result instead of
    genuinely short-circuiting before ever reaching the DIS call. A spy that
    raises if called is the only way to prove the gate fires first."""
    from app.core import dis_day_context

    def _fail(*a, **k):
        raise AssertionError("_dis_day_context_block must not be called — the gate should have returned first")

    monkeypatch.setattr(dis_day_context, "_dis_day_context_block", _fail)


def test_resolve_day_context_block_noop_when_day_number_missing(monkeypatch):
    _fail_if_dis_day_context_called(monkeypatch)
    assert resolve_day_context_block(None, "aim", None, "L", "Block 2 — X") == ""


def test_resolve_day_context_block_noop_when_pipeline_disabled_for_client(digest_pipeline_on_for_aim_only, monkeypatch):
    # Fixture pins settings to a known state (enabled=True, allowlist="aim")
    # so this test deterministically exercises the ALLOWLIST gate specifically
    # — not whatever the ambient default/test-order-dependent state happens to
    # be — and "cengage" is excluded by that allowlist.
    _fail_if_dis_day_context_called(monkeypatch)
    assert resolve_day_context_block(3, "cengage", None, "L", "Block 2 — X") == ""


def test_resolve_day_context_block_noop_when_no_block_label_parseable(digest_pipeline_on_for_aim_only, monkeypatch):
    # No title carries a "Block N"/"BLK N" pattern.
    _fail_if_dis_day_context_called(monkeypatch)
    assert resolve_day_context_block(3, "aim", None, "L", "Module 2 — Pharmacology Basics") == ""


def test_resolve_day_context_block_success_path(digest_pipeline_on_for_aim_only, monkeypatch):
    from app.core import dis_day_context

    monkeypatch.setattr(
        dis_day_context, "_dis_day_context_block",
        lambda block, day, current_user, label, client_id="": (f"CTX[{block}/{day}]", ["u1"]),
    )
    result = resolve_day_context_block(3, "aim", None, "BLUEPRINT DAY CONTEXT", "Block 2 — X")
    assert result == "CTX[Block 2/3]"


def test_resolve_day_context_block_falls_through_on_dis_failure(digest_pipeline_on_for_aim_only, monkeypatch):
    from app.core import dis_day_context

    monkeypatch.setattr(
        dis_day_context, "_dis_day_context_block",
        lambda *a, **k: ("", []),  # mirrors _dis_day_context_block's own DIS-failure fallback
    )
    assert resolve_day_context_block(3, "aim", None, "L", "Block 2 — X") == ""


def test_resolve_day_context_block_logs_when_no_block_label_parseable(
    digest_pipeline_on_for_aim_only, monkeypatch, caplog
):
    """B5: no stored course-to-block linkage exists, so this gate can fire for
    a request that genuinely wanted day-scoped grounding — unlike the other
    two gates (day_number missing / client not allowlisted), which are
    deliberate no-ops for a request that never asked for it. Distinguishing
    them means this ONE gate logs; the other two must stay silent."""
    import logging

    _fail_if_dis_day_context_called(monkeypatch)
    with caplog.at_level(logging.WARNING, logger="app.core.dis_day_context"):
        result = resolve_day_context_block(3, "aim", None, "L", "Module 2 — Pharmacology Basics")

    assert result == ""
    assert any("day_scoped_grounding_skipped_no_block_label" in r.message for r in caplog.records)


def test_resolve_day_context_block_does_not_log_for_the_deliberate_noop_gates(
    digest_pipeline_on_for_aim_only, monkeypatch, caplog
):
    """The other two gates are the ordinary case for most requests/tenants —
    logging them would just be noise trained to be ignored."""
    import logging

    _fail_if_dis_day_context_called(monkeypatch)
    with caplog.at_level(logging.WARNING, logger="app.core.dis_day_context"):
        resolve_day_context_block(None, "aim", None, "L", "Block 2 — X")
        resolve_day_context_block(3, "cengage", None, "L", "Block 2 — X")

    assert not any("day_scoped_grounding_skipped_no_block_label" in r.message for r in caplog.records)
