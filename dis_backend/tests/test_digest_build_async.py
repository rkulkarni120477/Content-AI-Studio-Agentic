"""The digest build must not be tied to the request that asked for it.

Production, 2026-08-13. A Block 2 build ran for 8m07s, built all 20 days
successfully, and then died with ``503 DIS unavailable: timed out``. The days were in
the store; the report was gone; the user was told generation had failed. Twenty
minutes of Bedrock spend produced nothing because one HTTP connection did not survive
the work it was waiting on.

The same shape caused a second symptom. ``build_block_digests`` was an ``async def``
that called the blocking build directly, so it froze DIS's single event loop for the
whole build — including the progress endpoint, the one thing that could have reported
the build was alive. A ten-minute cold build showed no day counter at all, which is
indistinguishable from a wedged one.

These tests pin the two properties that fix both: the build runs off the event loop,
and its result outlives the connection that started it.
"""
from __future__ import annotations

import asyncio
import threading
import types

import pytest

from services.digests import build as build_mod
from services.digests import progress


@pytest.fixture(autouse=True)
def _clean_registry():
    progress.reset_for_tests()
    yield
    progress.reset_for_tests()


REPORT = {"block": "Block 2", "built": 5, "cached": 15, "failed": 0, "strategy": "graph"}


def _request(client_id="aim", role="admin"):
    """Minimal stand-in for the FastAPI Request that _resolve_block_scope reads.

    Carries the same request.state the auth middleware populates, so the routes are
    exercised through their real scope resolution rather than around it — the
    client_id it produces is what keys the registry.
    """
    from config.settings import get_tenant_config

    cfg = get_tenant_config("aim")
    return types.SimpleNamespace(state=types.SimpleNamespace(
        tenant_config=cfg, tenant_id="aim", client_id=client_id, role=role,
    ))


def _body(block="Block 2", **kw):
    from api.routers.context import DigestBuildRequest

    return DigestBuildRequest(block=block, **kw)


# ── the registry as a single-flight guard ────────────────────────────────────────

def test_only_one_build_per_block_may_be_live():
    """Two users pressing Generate on the same block must not both pay for it.

    Concurrent MAP passes over the same days double the Bedrock bill for one result
    and race each other writing the same content-addressed digest documents.
    """
    assert progress.reserve("aim", "Block 2") is True
    assert progress.reserve("aim", "Block 2") is False


def test_a_different_block_is_not_blocked_by_a_live_build():
    progress.reserve("aim", "Block 2")
    assert progress.reserve("aim", "Block 3") is True


def test_a_different_client_is_not_blocked_by_a_live_build():
    progress.reserve("aim", "Block 2")
    assert progress.reserve("cengage", "Block 2") is True


@pytest.mark.parametrize("finish_with", ["complete", "fail"])
def test_the_slot_is_reusable_once_the_build_is_over(finish_with):
    """Otherwise one failed build locks the block out until DIS is restarted."""
    progress.reserve("aim", "Block 2")
    if finish_with == "complete":
        progress.complete("aim", "Block 2", REPORT)
    else:
        progress.fail("aim", "Block 2", "boom")
    assert progress.reserve("aim", "Block 2") is True


def test_reserving_is_visible_to_a_poll_before_the_day_count_is_known():
    """ENUMERATE is a multi-second round trip, and the poller reads a missing entry as
    "DIS restarted" and re-issues the build. The slot has to be visible during that
    window or every build gets issued twice."""
    progress.reserve("aim", "Block 2")
    snap = progress.snapshot("aim", "Block 2")
    assert snap is not None
    assert snap["state"] == progress.STATE_STARTING
    assert snap["total"] == 0


def test_start_keeps_the_reservations_clock():
    """Elapsed time must cover the ENUMERATE the user actually waited through."""
    progress.reserve("aim", "Block 2")
    reserved_at = progress._builds["aim:Block 2"]["started_at"]
    progress.start("aim", "Block 2", total=20)
    assert progress._builds["aim:Block 2"]["started_at"] == reserved_at
    assert progress.snapshot("aim", "Block 2")["state"] == progress.STATE_RUNNING


# ── the result outlives the connection ──────────────────────────────────────────

def test_a_successful_build_publishes_its_report(monkeypatch):
    monkeypatch.setattr(build_mod, "build_digests", lambda *a, **k: REPORT)
    progress.reserve("aim", "Block 2")

    out = build_mod.run_tracked_build(object(), "Block 2", client_id="aim")

    assert out == REPORT
    snap = progress.snapshot("aim", "Block 2", include_result=True)
    assert snap["state"] == progress.STATE_DONE
    assert snap["report"] == REPORT


def test_the_report_is_withheld_from_an_ordinary_poll(monkeypatch):
    """The browser polls this every 2 seconds through CAS and has no use for the
    report; shipping it on every poll is pure waste."""
    monkeypatch.setattr(build_mod, "build_digests", lambda *a, **k: REPORT)
    progress.reserve("aim", "Block 2")
    build_mod.run_tracked_build(object(), "Block 2", client_id="aim")

    assert "report" not in progress.snapshot("aim", "Block 2")


def test_a_failed_build_reaches_a_terminal_state_and_still_raises(monkeypatch):
    """A poller that only ever learns "not done" would wait out its entire deadline on
    a build that died in the first second."""
    def _boom(*a, **k):
        raise RuntimeError("bedrock is unhappy")

    monkeypatch.setattr(build_mod, "build_digests", _boom)
    progress.reserve("aim", "Block 2")

    with pytest.raises(RuntimeError):
        build_mod.run_tracked_build(object(), "Block 2", client_id="aim")

    snap = progress.snapshot("aim", "Block 2")
    assert snap["state"] == progress.STATE_FAILED
    assert "bedrock is unhappy" in snap["error"]


def test_a_build_killed_by_shutdown_still_leaves_a_terminal_state(monkeypatch):
    """BaseException, not Exception: a cancelled build must not look merely slow."""
    def _cancelled(*a, **k):
        raise KeyboardInterrupt()

    monkeypatch.setattr(build_mod, "build_digests", _cancelled)
    progress.reserve("aim", "Block 2")

    with pytest.raises(KeyboardInterrupt):
        build_mod.run_tracked_build(object(), "Block 2", client_id="aim")
    assert progress.snapshot("aim", "Block 2")["state"] == progress.STATE_FAILED


# ── the event loop stays free ───────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_progress_is_readable_while_a_build_is_running(monkeypatch):
    """The regression test for the missing day counter.

    ``build_block_digests`` used to call the blocking build straight from an
    ``async def``, freezing the only event loop for minutes. Nothing else could be
    served — least of all the progress endpoint that exists to report the build.

    Construction: the fake build parks until the progress route has answered. If the
    build were still running on the event loop, the progress route could never run, the
    build would never be released, and the assertions below would fail on the release
    timeout rather than hanging forever.
    """
    from api.routers import context as ctx

    building = threading.Event()
    release = threading.Event()

    def _slow_build(tenant, block, **kwargs):
        progress.start("aim", block, total=20)
        progress.record("aim", block, "built")
        building.set()
        released = release.wait(timeout=10)
        assert released, "the event loop never got a turn — the build is blocking it"
        return REPORT

    monkeypatch.setattr(build_mod, "build_digests", _slow_build)

    async def _poll_once():
        await asyncio.to_thread(building.wait, 10)
        # client_id/include_result passed explicitly: calling the route function
        # directly bypasses FastAPI's dependency resolution, so their Query() defaults
        # would arrive as Query objects rather than as "" and False.
        reply = await ctx.get_block_digest_progress(
            _request(), block="Block 2", client_id="", include_result=False)
        release.set()
        return reply

    build_task = asyncio.create_task(ctx.build_block_digests(_request(), _body()))
    reply = await _poll_once()
    report = await build_task

    assert report == REPORT
    # Served *during* the build — the whole point.
    assert reply["progress"]["total"] == 20
    assert reply["progress"]["done"] == 1
    assert reply["progress"]["state"] == progress.STATE_RUNNING


# ── the non-blocking start ──────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_starting_a_build_returns_before_the_build_finishes(monkeypatch):
    """What removes the long-held request: POST returns once the build is running."""
    from api.routers import context as ctx

    release = threading.Event()
    finished = threading.Event()

    def _slow_build(tenant, block, **kwargs):
        release.wait(timeout=10)
        finished.set()
        return REPORT

    monkeypatch.setattr(build_mod, "build_digests", _slow_build)

    reply = await ctx.build_block_digests(_request(), _body(wait=False))

    assert reply == {"started": True, "block": "Block 2", "client_id": "aim"}
    assert not finished.is_set(), "returned only after the build finished"

    release.set()
    await asyncio.to_thread(finished.wait, 10)
    # And the report is collectable afterwards, from the registry rather than from the
    # response to the request that started it.
    for _ in range(100):
        snap = progress.snapshot("aim", "Block 2", include_result=True)
        if snap and snap["state"] == progress.STATE_DONE:
            break
        await asyncio.sleep(0.02)
    assert snap["report"] == REPORT


@pytest.mark.asyncio
async def test_starting_a_build_that_is_already_running_is_not_an_error(monkeypatch):
    """The caller wants the build to happen, and it is happening. It polls the same
    registry entry either way, so refusing would only make it retry pointlessly."""
    from api.routers import context as ctx

    monkeypatch.setattr(build_mod, "build_digests",
                        lambda *a, **k: (threading.Event().wait(0.5), REPORT)[1])
    await ctx.build_block_digests(_request(), _body(wait=False))
    reply = await ctx.build_block_digests(_request(), _body(wait=False))

    assert reply["already_running"] is True
    assert reply["started"] is False


@pytest.mark.asyncio
async def test_a_blocking_caller_is_told_plainly_that_one_is_already_running(monkeypatch):
    """It asked for a report and cannot be given one, so this has to be an error —
    unlike the polling caller above, which can just watch the build in flight."""
    from fastapi import HTTPException

    from api.routers import context as ctx

    monkeypatch.setattr(build_mod, "build_digests", lambda *a, **k: REPORT)
    progress.reserve("aim", "Block 2")

    with pytest.raises(HTTPException) as exc:
        await ctx.build_block_digests(_request(), _body(wait=True))
    assert exc.value.status_code == 409


@pytest.mark.asyncio
async def test_a_background_build_that_raises_is_recorded_not_lost(monkeypatch):
    """Nobody is waiting on the thread, so the registry is the only place its failure
    can surface. Silence here would strand the poller until its deadline."""
    from api.routers import context as ctx

    def _boom(*a, **k):
        raise RuntimeError("no credentials")

    monkeypatch.setattr(build_mod, "build_digests", _boom)
    await ctx.build_block_digests(_request(), _body(wait=False))

    for _ in range(200):
        snap = progress.snapshot("aim", "Block 2")
        if snap and snap["state"] == progress.STATE_FAILED:
            break
        await asyncio.sleep(0.02)
    assert snap["state"] == progress.STATE_FAILED
    assert "no credentials" in snap["error"]


# ── bounded memory, without sabotaging a live build ─────────────────────────────

def test_the_route_and_the_build_agree_on_the_registry_key():
    """A subtle invariant with a silent failure mode.

    The route reserves and completes under the client_id from ``_resolve_block_scope``,
    while ``build_digests`` records days under ``enumerate_block``'s ``en.client_id``.
    Both funnel through ``effective_client_id``, so they agree only because that
    function is idempotent. If it stopped being, reserve and start would write
    different keys: the snapshot would sit at ``starting``/0 days forever, and the
    poller would read that as a lost build and re-issue it — burning a second full MAP
    pass with nothing in the logs to explain why.
    """
    from config.settings import get_tenant_config

    cfg = get_tenant_config("aim")
    once = cfg.effective_client_id("")
    assert cfg.effective_client_id(once) == once
    assert cfg.effective_client_id(cfg.effective_client_id("aim")) == \
        cfg.effective_client_id("aim")


def test_eviction_prefers_builds_that_are_over():
    """Evicting a live entry costs more than losing progress: its poller reads the
    absence as a DIS restart and re-issues, so it can cost a duplicate MAP pass."""
    progress.reserve("aim", "finished-one")
    progress.complete("aim", "finished-one", REPORT)
    for i in range(progress._MAX_TRACKED):
        progress.reserve("aim", f"live-{i}")

    assert progress.snapshot("aim", "finished-one") is None
    assert progress.snapshot("aim", f"live-{progress._MAX_TRACKED - 1}") is not None


def test_the_cap_still_holds_when_every_build_is_live():
    """An unbounded dict in a long-lived process is the worse of the two failures."""
    for i in range(progress._MAX_TRACKED + 10):
        progress.reserve("aim", f"live-{i}")
    assert len(progress._builds) <= progress._MAX_TRACKED
