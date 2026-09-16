"""Unit tests for ebook page retag progress registry."""
from __future__ import annotations

from services import ebook_page_retag_progress as progress


def setup_function():
    progress.reset_for_tests()


def test_reserve_single_flight():
    assert progress.reserve("c1", "job-1") is True
    assert progress.reserve("c1", "job-1") is False
    snap = progress.snapshot("c1", "job-1")
    assert snap["state"] == progress.STATE_STARTING


def test_start_update_complete_remaining():
    assert progress.reserve("c1", "job-2") is True
    progress.start("c1", "job-2", total=25)
    progress.update(
        "c1", "job-2",
        done=10, failed=1, last_page=42,
        tagging_failed_count=5, tagging_pending_count=10,
    )
    snap = progress.snapshot("c1", "job-2")
    assert snap["state"] == progress.STATE_RUNNING
    assert snap["total"] == 25
    assert snap["done"] == 10
    assert snap["remaining"] == 15
    assert snap["failed"] == 1
    assert snap["last_page"] == 42
    assert snap["tagging_failed_count"] == 5
    assert snap["tagging_pending_count"] == 10

    progress.complete("c1", "job-2", tagging_failed_count=2, tagging_pending_count=0)
    snap = progress.snapshot("c1", "job-2")
    assert snap["state"] == progress.STATE_DONE
    assert snap["done"] == 25
    assert snap["remaining"] == 0
    assert snap["tagging_failed_count"] == 2


def test_fail_sets_terminal_and_allows_re_reserve():
    assert progress.reserve("c1", "job-3") is True
    progress.start("c1", "job-3", total=5)
    progress.fail("c1", "job-3", "boom")
    snap = progress.snapshot("c1", "job-3")
    assert snap["state"] == progress.STATE_FAILED
    assert snap["error"] == "boom"
    # Terminal → can start again
    assert progress.reserve("c1", "job-3") is True


def test_snapshot_none_when_untracked():
    assert progress.snapshot("c1", "missing") is None
