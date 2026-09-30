"""Day-level progress for a block-wide build, exposed per job.

The job row only moves at stage boundaries — 20% "Building day digests..." then 90%
"Saving output..." — so a cold 20-day build shows one label for its whole duration and
looks identical to a wedged one. The day counts live in the DIS process doing the work.

The property these tests protect: progress is a nicety, and it must never break the
status poll the UI relies on to notice completion. Every failure degrades to null.
"""
from __future__ import annotations

import json
import types

import pytest

from app.api.v1.routers import jobs as jobs_router
from app.core.exceptions import JobNotFoundError
from promptops_app.repositories import job_repository


def _job(job_type="cdd_block", block="Block 2", dis_client_id="aim"):
    payload = {"block": block, "dis_client_id": dis_client_id, "deliverable": "cdd"}
    return types.SimpleNamespace(
        id="j1", job_type=job_type, request_json=json.dumps(payload),
    )


def _user():
    return types.SimpleNamespace(username="sami", id="sami", email="s@x.y", role="user")


def _call(monkeypatch, job, dis_reply=None, dis_error=None):
    monkeypatch.setattr(job_repository, "get_job", lambda db, jid: job)

    captured = {}

    class _Client:
        def get_digest_progress_sync(self, block, current_user=None, client_id="",
                                     include_result=False, timeout=10.0):
            captured.update(block=block, client_id=client_id,
                            include_result=include_result)
            if dis_error:
                raise dis_error
            return dis_reply

    import app.core.dis_client as dis_mod
    # The singleton, not the class: the route deliberately reuses the shared client
    # rather than constructing one per poll (see test_the_shared_client_is_reused).
    monkeypatch.setattr(dis_mod, "dis_client", _Client())
    return jobs_router.get_block_job_progress(job_id="j1", db=object(), tenant=(None, True),
                                              current_user=_user()), captured


SNAPSHOT = {"total": 20, "done": 7, "built": 6, "failed": 1, "cached": 0,
            "remaining": 13, "finished": False, "elapsed_seconds": 91.4}


def test_day_counts_reach_the_caller(monkeypatch):
    out, _ = _call(monkeypatch, _job(), dis_reply={"progress": SNAPSHOT})
    assert out["progress"]["done"] == 7
    assert out["progress"]["total"] == 20
    assert out["progress"]["remaining"] == 13


def test_the_build_is_looked_up_by_the_jobs_own_block_and_client(monkeypatch):
    """Reading the block from the job row, not from a query parameter, is what stops
    one job's progress bar showing another block's build."""
    _, captured = _call(monkeypatch, _job(block="Block 7", dis_client_id="aim"),
                        dis_reply={"progress": SNAPSHOT})
    assert captured["block"] == "Block 7"
    assert captured["client_id"] == "aim"
    assert captured["include_result"] is False, (
        "the browser polls this every 2 seconds and has no use for the build report"
    )


@pytest.mark.parametrize("job_type", ["generation", "regenerate_item", "import_course"])
def test_jobs_that_have_no_days_report_nothing(monkeypatch, job_type):
    """Only block-wide builds have per-day progress. Asking DIS about anything else
    would be a pointless cross-service call on every poll."""
    out, captured = _call(monkeypatch, _job(job_type=job_type), dis_reply={"progress": SNAPSHOT})
    assert out == {"progress": None}
    assert captured == {}, "DIS must not be called at all"


def test_a_dis_outage_degrades_to_null_rather_than_failing_the_poll(monkeypatch):
    out, _ = _call(monkeypatch, _job(), dis_error=RuntimeError("DIS unavailable"))
    assert out == {"progress": None}


def test_a_malformed_dis_reply_degrades_to_null(monkeypatch):
    for reply in (None, "not a dict", 42, [], {}):
        out, _ = _call(monkeypatch, _job(), dis_reply=reply)
        assert out == {"progress": None}, reply


def test_a_job_without_a_block_reports_nothing(monkeypatch):
    out, captured = _call(monkeypatch, _job(block=None), dis_reply={"progress": SNAPSHOT})
    assert out == {"progress": None}
    assert captured == {}


def test_unparseable_job_params_degrade_to_null(monkeypatch):
    job = types.SimpleNamespace(id="j1", job_type="cdd_block", request_json="{not json")
    out, _ = _call(monkeypatch, job, dis_reply={"progress": SNAPSHOT})
    assert out == {"progress": None}


def test_a_missing_job_is_still_a_real_404(monkeypatch):
    """Degrading everything to null must not swallow a genuinely wrong job id."""
    monkeypatch.setattr(job_repository, "get_job", lambda db, jid: None)
    with pytest.raises(JobNotFoundError):
        jobs_router.get_block_job_progress(job_id="nope", db=object(), current_user=_user(), tenant=(None, True))


def test_the_shared_client_is_reused_rather_than_one_built_per_poll(monkeypatch):
    """This route fires every 2 seconds for the length of a build.

    ``DISClient()`` lazily creates its own ``httpx.Client`` and nothing closes it, so
    constructing one per poll leaked a client and its connection pool every 2 seconds
    — roughly 240 over the 8-minute production build on 2026-08-13 — while defeating
    the connection reuse the pooling exists to provide.
    """
    import app.core.dis_client as dis_mod

    monkeypatch.setattr(job_repository, "get_job", lambda db, jid: _job())
    constructed = []
    real_init = dis_mod.DISClient.__init__

    def _counting_init(self, *a, **kw):
        constructed.append(1)
        real_init(self, *a, **kw)

    monkeypatch.setattr(dis_mod.DISClient, "__init__", _counting_init)
    monkeypatch.setattr(dis_mod.dis_client, "get_digest_progress_sync",
                        lambda *a, **kw: {"progress": SNAPSHOT})

    for _ in range(5):
        jobs_router.get_block_job_progress(job_id="j1", db=object(), current_user=_user(), tenant=(None, True))

    assert constructed == [], "a DISClient was constructed inside the poll path"


def test_progress_is_a_separate_route_from_the_status_poll():
    """Folding this into the status endpoint would put a cross-service call on every
    job poll in the app; it must stay a single fast DB read."""
    paths = [r.path for r in jobs_router.router.routes]
    assert "/{job_id}/progress" in paths
    assert "/{job_id}" in paths
