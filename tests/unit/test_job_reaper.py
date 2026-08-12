"""Startup reaping of jobs orphaned by a process death.

Every ``run_*`` job wraps its body in ``except Exception`` and records a failure,
so an *error* always lands in the row. A **process death** never does: an OOM
kill or restart takes the worker threads with it, leaving ``generation_jobs`` at
``running`` forever — indistinguishable from a healthy long job, so the UI shows
an in-flight generation that can never resolve. Observed in prod 2026-08-12.

The risk in fixing it is over-reaping: failing a job that is alive in another
process. These tests pin both guards (Celery topology, staleness window) against
a real session, so the staleness filter is exercised as SQL rather than mocked —
an earlier fake-query version of these tests silently "passed" a fresh job
through the reaper because it could not evaluate the filter at all.
"""
from __future__ import annotations

import datetime as dt

import pytest

from promptops_app.database import GenerationJob
from promptops_app.jobs import reaper
from promptops_app.jobs.job_status import JobStatus


def _mk(db, jid, status, *, age_minutes, job_type="cdd_block", step="Building day digests..."):
    # Naive UTC to match the column type (DateTime without timezone) and the
    # models' own `default=datetime.utcnow`.
    ts = dt.datetime.utcnow() - dt.timedelta(minutes=age_minutes)
    job = GenerationJob(
        id=jid, job_type=job_type, status=status, current_step=step,
        created_by="tester", created_at=ts, updated_at=ts,
    )
    db.add(job)
    db.commit()
    return job


@pytest.fixture(autouse=True)
def _use_test_session(db, monkeypatch):
    """Point the reaper at the test session and default to the threadpool topology."""
    import promptops_app.core.config as cfg
    import promptops_app.database as database
    monkeypatch.setattr(cfg.settings, "use_celery", False)
    monkeypatch.setattr(database, "SessionLocal", lambda: db)
    # The reaper closes the session it opens; keep the fixture's session usable.
    monkeypatch.setattr(db, "close", lambda: None)
    return db


def test_stranded_running_job_is_marked_failed(db):
    job = _mk(db, "stranded-1", JobStatus.RUNNING, age_minutes=360)

    assert reaper.reap_orphaned_jobs() == 1
    db.refresh(job)
    assert job.status == JobStatus.FAILED
    assert job.completed_at is not None


def test_the_user_facing_message_explains_the_interruption(db):
    """error_message is rendered in the UI, so it must read as a retryable
    interruption — not as a content-generation failure, and never as a traceback."""
    job = _mk(db, "stranded-2", JobStatus.RUNNING, age_minutes=360)
    reaper.reap_orphaned_jobs()
    db.refresh(job)

    msg = (job.error_message or "").lower()
    assert "interrupted" in msg and "try again" in msg
    assert "traceback" not in msg and "exception" not in msg


def test_a_fresh_job_is_left_alone(db):
    """Job rows are written only at stage transitions, and a cold 20-day digest
    build legitimately runs minutes between them — so recency must protect a job
    that is mid-stage in a healthy process."""
    fresh = _mk(db, "fresh-1", JobStatus.RUNNING, age_minutes=2)

    assert reaper.reap_orphaned_jobs(stale_after_minutes=60) == 0
    db.refresh(fresh)
    assert fresh.status == JobStatus.RUNNING


def test_only_the_stale_job_is_reaped_when_both_exist(db):
    """The discriminating case: one dead, one healthy, one pass."""
    stale = _mk(db, "mixed-stale", JobStatus.RUNNING, age_minutes=360)
    fresh = _mk(db, "mixed-fresh", JobStatus.RUNNING, age_minutes=1)

    assert reaper.reap_orphaned_jobs(stale_after_minutes=60) == 1
    db.refresh(stale); db.refresh(fresh)
    assert stale.status == JobStatus.FAILED
    assert fresh.status == JobStatus.RUNNING


def test_terminal_jobs_are_never_touched(db):
    completed = _mk(db, "done-1", JobStatus.COMPLETED, age_minutes=999, step="Completed")
    failed = _mk(db, "failed-1", JobStatus.FAILED, age_minutes=999, step="Failed")
    cancelled = _mk(db, "cancelled-1", JobStatus.CANCELLED, age_minutes=999, step="Cancelled")

    assert reaper.reap_orphaned_jobs() == 0
    for job, expected in ((completed, JobStatus.COMPLETED),
                          (failed, JobStatus.FAILED),
                          (cancelled, JobStatus.CANCELLED)):
        db.refresh(job)
        assert job.status == expected


def test_queued_jobs_are_reaped_too(db):
    """A job killed before its thread started is stranded at 'queued', not
    'running' — equally unresolvable, so ACTIVE (not just RUNNING) is the set."""
    assert JobStatus.QUEUED in JobStatus.ACTIVE and JobStatus.RUNNING in JobStatus.ACTIVE
    queued = _mk(db, "q-1", JobStatus.QUEUED, age_minutes=180, step="Queued")

    assert reaper.reap_orphaned_jobs() == 1
    db.refresh(queued)
    assert queued.status == JobStatus.FAILED


def test_celery_topology_is_never_reaped(db, monkeypatch):
    """With Celery enabled the job runs in the worker container, so an API restart
    implies nothing about it — and Celery's own task_reject_on_worker_lost
    re-queues genuinely lost tasks. Reaping here would fail a live job, or fight
    the re-queue."""
    import promptops_app.core.config as cfg
    monkeypatch.setattr(cfg.settings, "use_celery", True)
    job = _mk(db, "celery-owned", JobStatus.RUNNING, age_minutes=999)

    assert reaper.reap_orphaned_jobs() == 0
    db.refresh(job)
    assert job.status == JobStatus.RUNNING, "a live worker's job was failed"


def test_reaper_never_breaks_startup(monkeypatch):
    """It runs inside the FastAPI lifespan hook, so a cleanup pass must never be
    the reason the application fails to boot."""
    import promptops_app.core.config as cfg
    import promptops_app.database as database
    monkeypatch.setattr(cfg.settings, "use_celery", False)

    def boom():
        raise RuntimeError("database unreachable")

    monkeypatch.setattr(database, "SessionLocal", boom)
    assert reaper.reap_orphaned_jobs() == 0  # swallowed, not raised


def test_startup_actually_calls_the_reaper():
    """A reaper nobody invokes fixes nothing — pin the wiring in app.main."""
    from pathlib import Path
    src = Path("app/main.py").read_text()
    assert "reap_orphaned_jobs" in src, "app.main's lifespan no longer reaps jobs"
