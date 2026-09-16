"""Retry-all content tagging must not block on the HTTP request.

Mirrors the digest-build async contract: all_failed starts a background thread,
returns immediately, and progress is polled separately. Single-page retag stays
synchronous.
"""
from __future__ import annotations

import asyncio
import threading
import types

import pytest

from services import ebook_page_retag_progress as retag_progress


@pytest.fixture(autouse=True)
def _clean_registry():
    retag_progress.reset_for_tests()
    yield
    retag_progress.reset_for_tests()


def _request(client_id="aim", role="admin"):
    from config.settings import get_tenant_config

    cfg = get_tenant_config("aim")
    return types.SimpleNamespace(state=types.SimpleNamespace(
        tenant_config=cfg, tenant_id="aim", client_id=client_id, role=role,
    ))


def test_all_failed_returns_immediately_and_reserves(monkeypatch):
    from api.routers import context as ctx

    release = threading.Event()
    started = threading.Event()

    def slow_runner(*_a, **_k):
        started.set()
        release.wait(timeout=5)
        return {"status": "completed", "retagged": 0}

    monkeypatch.setattr("services.ebook_page_retag.run_tracked_retag", slow_runner)

    result = asyncio.run(ctx.source_content_retag(
        "job-ebook", ctx.RetagBody(all_failed=True), _request(),
    ))
    assert result["started"] is True
    assert result["already_running"] is False
    assert result["progress"]["state"] == retag_progress.STATE_STARTING

    assert started.wait(timeout=2)

    result2 = asyncio.run(ctx.source_content_retag(
        "job-ebook", ctx.RetagBody(all_failed=True), _request(),
    ))
    assert result2["started"] is False
    assert result2["already_running"] is True
    release.set()


def test_progress_endpoint_returns_snapshot_or_null():
    from api.routers import context as ctx

    empty = asyncio.run(ctx.source_content_retag_progress("job-none", _request()))
    assert empty == {"progress": None}

    retag_progress.reserve("aim", "job-ebook")
    retag_progress.start("aim", "job-ebook", total=40)
    retag_progress.update("aim", "job-ebook", done=10, failed=0, last_page=11)

    snap = asyncio.run(ctx.source_content_retag_progress("job-ebook", _request()))
    assert snap["progress"]["done"] == 10
    assert snap["progress"]["remaining"] == 30
    assert snap["progress"]["total"] == 40


def test_unit_ids_still_run_synchronously(monkeypatch):
    from api.routers import context as ctx

    def sync_retag(*_a, **_k):
        return {
            "status": "completed",
            "retagged": 1,
            "unit_ids": ["job:page_1"],
            "tagging_failed_count": 0,
            "tagging_pending_count": 0,
            "errors": [],
            "units": [],
        }

    monkeypatch.setattr("services.ebook_page_retag.retag_ebook_pages", sync_retag)

    result = asyncio.run(ctx.source_content_retag(
        "job-ebook",
        ctx.RetagBody(unit_ids=["job:page_1"]),
        _request(),
    ))
    assert result["retagged"] == 1
    assert retag_progress.snapshot("aim", "job-ebook") is None
