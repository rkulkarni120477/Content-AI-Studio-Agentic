"""Per-day progress for an in-flight block build.

A cold build holds one HTTP request open for minutes and the job row only moves at
stage boundaries, so the UI could show "Building day digests..." and nothing else for
the entire run — indistinguishable from a wedged build.

The trap these tests exist to pin: progress must be reported BY the build, never
inferred from the digest store. Digests persist between attempts, so a block already
holding 20 documents (15 ok + 5 failed from an earlier run — exactly the state prod
was left in on 2026-08-13) would report 20/20 the instant a retry started.
"""
from __future__ import annotations

import pytest

from services.digests import progress


@pytest.fixture(autouse=True)
def _clean():
    progress.reset_for_tests()
    yield
    progress.reset_for_tests()


def test_an_untracked_block_reports_nothing_rather_than_zero():
    """None means "no build running"; 0/0 would render as a real, stalled build."""
    assert progress.snapshot("aim", "Block 2") is None


def test_a_build_reports_days_as_they_complete():
    progress.start("aim", "Block 2", total=20)
    assert progress.snapshot("aim", "Block 2")["done"] == 0

    for _ in range(7):
        progress.record("aim", "Block 2", "built")
    snap = progress.snapshot("aim", "Block 2")
    assert (snap["done"], snap["built"], snap["remaining"]) == (7, 7, 13)
    assert snap["finished"] is False


def test_reused_days_count_immediately_so_a_retry_reads_honestly():
    """A retry that reuses 15 of 20 days should jump to 15/20, not crawl from zero —
    the remaining work is the 5 days that actually need building."""
    progress.start("aim", "Block 2", total=20)
    for _ in range(15):
        progress.record("aim", "Block 2", "cached")
    snap = progress.snapshot("aim", "Block 2")
    assert (snap["done"], snap["cached"], snap["remaining"]) == (15, 15, 5)


def test_failed_days_count_as_done():
    """A failed day is finished work — it is not coming back — so it must advance the
    bar. Counting only successes would stall a build that is actually complete."""
    progress.start("aim", "Block 2", total=3)
    progress.record("aim", "Block 2", "built")
    progress.record("aim", "Block 2", "failed")
    progress.record("aim", "Block 2", "failed")
    snap = progress.snapshot("aim", "Block 2")
    assert (snap["done"], snap["built"], snap["failed"], snap["remaining"]) == (3, 1, 2, 0)


def test_finishing_keeps_the_final_counts_readable():
    """The frontend polls on an interval; the poll landing just after the last day must
    see 20/20 rather than "no build", which would read as a build that vanished."""
    progress.start("aim", "Block 2", total=2)
    progress.record("aim", "Block 2", "built")
    progress.record("aim", "Block 2", "built")
    progress.finish("aim", "Block 2")
    snap = progress.snapshot("aim", "Block 2")
    assert snap["finished"] is True
    assert snap["done"] == 2


def test_a_new_attempt_discards_the_previous_ones_counts():
    progress.start("aim", "Block 2", total=20)
    for _ in range(20):
        progress.record("aim", "Block 2", "built")
    progress.finish("aim", "Block 2")

    progress.start("aim", "Block 2", total=20)
    snap = progress.snapshot("aim", "Block 2")
    assert (snap["done"], snap["finished"]) == (0, False)


def test_blocks_and_clients_are_tracked_independently():
    progress.start("aim", "Block 2", total=20)
    progress.start("aim", "Block 3", total=10)
    progress.start("other", "Block 2", total=5)
    progress.record("aim", "Block 2", "built")

    assert progress.snapshot("aim", "Block 2")["done"] == 1
    assert progress.snapshot("aim", "Block 3")["done"] == 0
    assert progress.snapshot("other", "Block 2")["done"] == 0
    assert progress.snapshot("other", "Block 2")["total"] == 5


def test_recording_against_an_untracked_block_is_ignored_not_an_error():
    """Reporting progress must never be the reason a build fails."""
    progress.record("aim", "nope", "built")      # no start() first
    progress.finish("aim", "nope")
    assert progress.snapshot("aim", "nope") is None


def test_an_unknown_status_does_not_corrupt_the_counts():
    progress.start("aim", "Block 2", total=2)
    progress.record("aim", "Block 2", "something-else")
    assert progress.snapshot("aim", "Block 2")["done"] == 0


def test_remaining_never_goes_negative():
    """Over-reporting would otherwise render as a nonsensical progress bar."""
    progress.start("aim", "Block 2", total=1)
    for _ in range(5):
        progress.record("aim", "Block 2", "built")
    assert progress.snapshot("aim", "Block 2")["remaining"] == 0


def test_tracking_is_bounded_so_a_long_lived_process_cannot_leak():
    for i in range(progress._MAX_TRACKED + 10):
        progress.start("aim", f"Block {i}", total=1)
    assert len(progress._builds) <= progress._MAX_TRACKED
    # The most recent build must survive the eviction of older ones.
    assert progress.snapshot("aim", f"Block {progress._MAX_TRACKED + 9}") is not None


def test_concurrent_recording_from_the_fanout_loses_nothing():
    """The fan-out records from N worker threads at once (max_concurrency=5)."""
    import threading

    progress.start("aim", "Block 2", total=200)

    def worker():
        for _ in range(40):
            progress.record("aim", "Block 2", "built")

    threads = [threading.Thread(target=worker) for _ in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert progress.snapshot("aim", "Block 2")["done"] == 200
