"""A digest build must survive losing the connection that started it.

Production, 2026-08-13, twice in one morning. A Block 2 build was run inside a single
HTTP request from CAS to DIS:

* 06:25 — failed at 21m52s, ``503 DIS unavailable: timed out``
* 08:06 — failed at 8m07s, same error, **after all 20 days had built successfully**

The second is the one that matters. The work was done, the digests were in the store,
Bedrock had been billed — and because one connection dropped, the report was
discarded and the user was told "Blueprint generation failed". Raising the timeout
does not fix this; it only moves the window. The request must stop being load-bearing.

So ``build_digests_sync`` now starts the build and polls for it. These tests pin the
distinctions that make polling safe: a poll failure is not a build failure, a missing
registry entry is not a finished build, and a build that is genuinely gone must not be
waited out to the deadline.
"""
from __future__ import annotations

import pytest
from fastapi import HTTPException

from app.core import dis_client as dis_mod

REPORT = {"block": "Block 2", "built": 5, "cached": 15, "failed": 0,
          "per_day": [], "strategy": "graph"}


class FakeClock:
    """Monotonic time the test advances, so a 40-minute deadline runs instantly."""

    def __init__(self) -> None:
        self.t = 0.0

    def monotonic(self) -> float:
        return self.t

    def sleep(self, seconds: float) -> None:
        self.t += seconds


def make_client(monkeypatch, *, snapshots, start_reply=None, deadline=600, interval=5,
                start_errors=()):
    """A DISClient whose DIS is a script.

    ``snapshots`` is consumed one per poll; an entry may be a dict (the ``progress``
    payload, or None for "no such build"), or an Exception instance to raise.
    ``start_errors`` is consumed one per POST, raising instead of replying.
    """
    client = dis_mod.DISClient()
    clock = FakeClock()
    monkeypatch.setattr(dis_mod, "time", clock)
    monkeypatch.setattr(dis_mod, "_get_setting", lambda name, default=None: {
        "dis_digest_build_deadline_seconds": deadline,
        "dis_digest_build_poll_seconds": interval,
    }.get(name, default))

    calls = {"start": [], "poll": 0}
    queue = list(snapshots)
    start_queue = list(start_errors)

    def _fake_request(method, path, **kwargs):
        if path.endswith("/digests/build"):
            calls["start"].append(kwargs.get("json") or {})
            if start_queue:
                err = start_queue.pop(0)
                if err is not None:  # None = this POST succeeds
                    raise err
            return start_reply if start_reply is not None else {"started": True}
        if path.endswith("/digests/progress"):
            calls["poll"] += 1
            item = queue.pop(0) if queue else None
            if isinstance(item, Exception):
                raise item
            return {"progress": item}
        raise AssertionError(f"unexpected DIS call: {method} {path}")

    monkeypatch.setattr(client, "request_sync", _fake_request)
    return client, calls, clock


def done(report=REPORT, **kw):
    return {"state": "done", "total": 20, "done": 20, "built": 5, "failed": 0,
            "cached": 15, "remaining": 0, "finished": True, "error": "",
            "report": report, **kw}


def running(built=3):
    return {"state": "running", "total": 20, "done": built, "built": built,
            "failed": 0, "cached": 0, "remaining": 20 - built, "finished": False,
            "error": "", "report": None}


# ── the happy path is now two calls, not one long one ───────────────────────────

def test_the_build_is_started_without_waiting_for_it(monkeypatch):
    client, calls, _ = make_client(monkeypatch, snapshots=[done()])
    client.build_digests_sync("Block 2", client_id="aim")

    assert calls["start"][0]["wait"] is False, (
        "a waiting request is the whole defect — it must not be re-introduced"
    )


def test_the_report_comes_from_the_registry_not_the_starting_request(monkeypatch):
    client, _, _ = make_client(monkeypatch, snapshots=[running(3), running(11), done()])
    assert client.build_digests_sync("Block 2", client_id="aim") == REPORT


def test_an_all_cached_rebuild_pays_no_poll_delay(monkeypatch):
    """The overwhelmingly common case: a retry after a partial build, where every day
    is already cached and the build finishes in under a second. DIS reserves its
    registry slot before the POST returns, so the first poll can read the result
    straight away — sleeping first would tax every retry for nothing.
    """
    client, _, clock = make_client(monkeypatch, snapshots=[done()], interval=5)
    assert client.build_digests_sync("Block 2", client_id="aim") == REPORT
    assert clock.t == 0


def test_it_keeps_polling_while_the_build_is_running(monkeypatch):
    client, calls, _ = make_client(monkeypatch,
                                   snapshots=[running(1), running(9), running(19), done()])
    client.build_digests_sync("Block 2", client_id="aim")
    assert calls["poll"] == 4


def test_the_poller_asks_for_the_result_but_the_ui_does_not(monkeypatch):
    """The browser polls progress every 2 seconds through CAS; shipping a build report
    on each of those is waste. The server-to-server poller needs it exactly once."""
    seen = []
    client = dis_mod.DISClient()
    monkeypatch.setattr(client, "request_sync",
                        lambda m, p, **kw: seen.append(kw.get("params")) or {"progress": None})

    client.get_digest_progress_sync("Block 2")
    client.get_digest_progress_sync("Block 2", include_result=True)

    assert "include_result" not in seen[0]
    assert seen[1]["include_result"] == "true"


# ── a poll failure is not a build failure ───────────────────────────────────────

def test_a_transient_poll_error_does_not_lose_the_build(monkeypatch):
    """This is the exact failure that discarded a completed build. A blip while
    watching must cost one poll interval, not twenty minutes of work."""
    client, _, _ = make_client(monkeypatch, snapshots=[
        running(2), OSError("connection reset"), running(14), done(),
    ])
    assert client.build_digests_sync("Block 2", client_id="aim") == REPORT


def test_sustained_unreachability_is_reported_rather_than_waited_out(monkeypatch):
    """Two minutes of continuous failure is an outage, not a blip — waiting out a
    40-minute deadline against a dead DIS helps nobody."""
    client, _, _ = make_client(monkeypatch, snapshots=[OSError("no route")] * 200)

    with pytest.raises(HTTPException) as exc:
        client.build_digests_sync("Block 2", client_id="aim")
    assert exc.value.status_code == 503
    assert "no route" in str(exc.value.detail)


# ── starting is retried, because starting is idempotent ─────────────────────────

def test_a_dropped_packet_on_the_start_call_does_not_fail_the_generation(monkeypatch):
    """The start call takes milliseconds, but it is still a network call. Failing a
    whole generation on one lost packet — before any work has begun — is exactly the
    fragility this rewrite exists to remove. Safe to retry because DIS's single-flight
    reservation answers "already_running" rather than launching a second build."""
    client, calls, _ = make_client(monkeypatch, snapshots=[done()],
                                   start_errors=[OSError("connection reset")])
    assert client.build_digests_sync("Block 2", client_id="aim") == REPORT
    assert len(calls["start"]) == 2


def test_starting_is_not_retried_forever(monkeypatch):
    client, calls, _ = make_client(monkeypatch, snapshots=[],
                                   start_errors=[OSError("down")] * 10)
    with pytest.raises(HTTPException) as exc:
        client.build_digests_sync("Block 2", client_id="aim")
    assert exc.value.status_code == 503
    assert len(calls["start"]) == 3


# ── a missing entry is a restart, not a success ─────────────────────────────────

def test_a_vanished_build_is_re_issued_rather_than_reported_complete(monkeypatch):
    """The registry is in-process, so a DIS restart erases an in-flight build. Reading
    that as "finished" would hand the caller no report; reading it as "failed" would
    throw away days that are already built and cached. Re-issue instead: the cache
    makes it resume."""
    client, calls, _ = make_client(monkeypatch, snapshots=[
        running(4), None, running(12), done(),
    ])
    assert client.build_digests_sync("Block 2", client_id="aim") == REPORT
    assert len(calls["start"]) == 2, "should have re-issued the build exactly once"


def test_a_re_issued_build_keeps_the_original_arguments(monkeypatch):
    """A rebuild that silently dropped force or map_guidance would produce a digest
    tier that disagrees with what was asked for — and the cache key folds in the
    guidance, so it would also quietly reuse the wrong digests."""
    client, calls, _ = make_client(monkeypatch, snapshots=[running(1), None, done()])
    client.build_digests_sync("Block 2", client_id="aim", force=True,
                              map_guidance="Emphasize safety.")

    assert calls["start"][1]["force"] is True
    assert calls["start"][1]["map_guidance"] == "Emphasize safety."


def test_a_re_issue_that_itself_fails_is_treated_as_transient(monkeypatch):
    """A DIS that just restarted may not be accepting requests yet. Letting that
    escape would abandon the build over a blip during recovery."""
    client, calls, _ = make_client(
        monkeypatch, snapshots=[running(3), None, running(8), done()],
        start_errors=[None, ConnectionRefusedError("still booting")])

    # The first start succeeds (None = no error), the re-issue is refused, and polling
    # continues to a successful finish.
    assert client.build_digests_sync("Block 2", client_id="aim") == REPORT
    assert len(calls["start"]) == 2


def test_re_issuing_is_bounded(monkeypatch):
    """A build that cannot survive being started twice will not survive a third
    time; looping on it burns money on every attempt."""
    client, calls, _ = make_client(monkeypatch, snapshots=[running(1)] + [None] * 50)

    with pytest.raises(HTTPException) as exc:
        client.build_digests_sync("Block 2", client_id="aim")
    assert exc.value.status_code == 503
    assert len(calls["start"]) == 3  # the original + 2 re-issues


def test_a_missing_entry_before_the_build_registers_is_just_waited_out(monkeypatch):
    """DIS reserves its slot then spawns a thread; a poll landing in that window sees
    nothing yet. Treating that as a restart would re-issue every build."""
    client, calls, _ = make_client(monkeypatch, snapshots=[None, None, running(5), done()])
    assert client.build_digests_sync("Block 2", client_id="aim") == REPORT
    assert len(calls["start"]) == 1


# ── terminal states ─────────────────────────────────────────────────────────────

def test_a_failed_build_surfaces_the_reason_dis_gave(monkeypatch):
    client, _, _ = make_client(monkeypatch, snapshots=[
        running(1),
        {**running(1), "state": "failed", "error": "ExtractorUnavailable: mock LLM active"},
    ])
    with pytest.raises(HTTPException) as exc:
        client.build_digests_sync("Block 2", client_id="aim")
    assert exc.value.status_code == 502
    assert "mock LLM active" in str(exc.value.detail)


def test_a_failed_build_is_not_waited_out(monkeypatch):
    """It must stop at the failure, not at the deadline."""
    client, calls, clock = make_client(monkeypatch, snapshots=[
        {**running(0), "state": "failed", "error": "boom"}], deadline=2400)
    with pytest.raises(HTTPException):
        client.build_digests_sync("Block 2", client_id="aim")
    assert clock.t < 60


def test_complete_but_reportless_is_an_error_not_an_empty_success(monkeypatch):
    """Returning None here would let the caller build a deliverable out of nothing —
    which is how a 20-day Blueprint of empty cells shipped once already."""
    client, _, _ = make_client(monkeypatch, snapshots=[done(report=None)])
    with pytest.raises(HTTPException) as exc:
        client.build_digests_sync("Block 2", client_id="aim")
    assert exc.value.status_code == 502


def test_the_deadline_names_the_cheap_recovery(monkeypatch):
    """Days already built are cached, so the honest advice is "retry", and the message
    should say so rather than leaving the user to guess."""
    client, _, _ = make_client(monkeypatch, snapshots=[running(7)] * 500, deadline=600)
    with pytest.raises(HTTPException) as exc:
        client.build_digests_sync("Block 2", client_id="aim")
    assert exc.value.status_code == 504
    assert "retry" in str(exc.value.detail).lower()


def test_a_build_already_running_is_polled_not_refused(monkeypatch):
    """DIS's single-flight guard answers "already_running" when two callers race. The
    build the caller wants IS happening, so it should watch it."""
    client, calls, _ = make_client(
        monkeypatch, snapshots=[running(6), done()],
        start_reply={"started": False, "already_running": True, "block": "Block 2"})
    assert client.build_digests_sync("Block 2", client_id="aim") == REPORT
    assert len(calls["start"]) == 1


# ── deploy-order safety ─────────────────────────────────────────────────────────

def test_a_dis_that_predates_this_protocol_still_works(monkeypatch):
    """CAS and DIS are separate images. During a rebuild, a CAS that polls can meet a
    DIS that still builds inline and returns the report from the POST — recognised
    here so the mixed-version window degrades instead of breaking."""
    client, calls, _ = make_client(monkeypatch, snapshots=[], start_reply=REPORT)
    assert client.build_digests_sync("Block 2", client_id="aim") == REPORT
    assert calls["poll"] == 0


# ── the deadline has to coexist with the job reaper ─────────────────────────────

def test_the_build_deadline_stays_under_the_reaper_window():
    """The reaper marks a job stranded on ``updated_at`` age, and updated_at does not
    advance during a build. A deadline at or past that window would have the reaper
    fail jobs whose builds are still legitimately running."""
    from app.core.config import settings
    from promptops_app.jobs.reaper import DEFAULT_STALE_AFTER_MINUTES

    assert settings.dis_digest_build_deadline_seconds < DEFAULT_STALE_AFTER_MINUTES * 60
