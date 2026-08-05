"""P4.1 — background-job dispatch routing and fail-safe fallback.

``dispatch.submit`` chooses where a job runs based on ``settings.use_celery``:
  * off (default) → in-process ThreadPoolExecutor (job_runner), unchanged;
  * on → the matching Celery task;
  * on but broker/routing fails → fall back to the threadpool, never drop the
    job (the flag is also a zero-deploy kill switch back to the threadpool).
"""

from __future__ import annotations

import promptops_app.jobs.dispatch as dispatch


def _noop_job(job_id):  # a stand-in job function
    return job_id


def test_defaults_to_threadpool(monkeypatch):
    """With use_celery False, submit goes straight to job_runner.submit."""
    monkeypatch.setattr(dispatch.settings, "use_celery", False)
    called = {}
    monkeypatch.setattr(
        dispatch.job_runner, "submit",
        lambda fn, job_id: called.update(fn=fn, job_id=job_id) or "future",
    )

    result = dispatch.submit(_noop_job, "job-123")

    assert result == "future"
    assert called == {"fn": _noop_job, "job_id": "job-123"}


def test_routes_to_celery_when_enabled(monkeypatch):
    """With use_celery True, submit enqueues the mapped Celery task."""
    monkeypatch.setattr(dispatch.settings, "use_celery", True)

    class _Result:
        id = "celery-task-id"

    class _Task:
        @staticmethod
        def delay(job_id):
            _Task.seen = job_id
            return _Result()

    import promptops_app.jobs.celery_tasks as celery_tasks
    monkeypatch.setattr(celery_tasks, "TASK_FOR_FUNC", {_noop_job: _Task})

    # job_runner must NOT be used on the happy Celery path.
    monkeypatch.setattr(
        dispatch.job_runner, "submit",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("threadpool used")),
    )

    result = dispatch.submit(_noop_job, "job-xyz")

    assert result.id == "celery-task-id"
    assert _Task.seen == "job-xyz"


def test_falls_back_to_threadpool_when_celery_raises(monkeypatch):
    """If the Celery enqueue raises (broker down), the job still runs via the
    in-process threadpool rather than being dropped."""
    monkeypatch.setattr(dispatch.settings, "use_celery", True)

    class _Task:
        @staticmethod
        def delay(job_id):
            raise ConnectionError("broker unreachable")

    import promptops_app.jobs.celery_tasks as celery_tasks
    monkeypatch.setattr(celery_tasks, "TASK_FOR_FUNC", {_noop_job: _Task})

    fell_back = {}
    monkeypatch.setattr(
        dispatch.job_runner, "submit",
        lambda fn, job_id: fell_back.update(fn=fn, job_id=job_id) or "threadpool-future",
    )

    result = dispatch.submit(_noop_job, "job-fallback")

    assert result == "threadpool-future"
    assert fell_back == {"fn": _noop_job, "job_id": "job-fallback"}


def test_falls_back_when_no_task_mapped(monkeypatch):
    """An unmapped job function falls back to the threadpool, not an exception."""
    monkeypatch.setattr(dispatch.settings, "use_celery", True)

    import promptops_app.jobs.celery_tasks as celery_tasks
    monkeypatch.setattr(celery_tasks, "TASK_FOR_FUNC", {})  # nothing mapped

    fell_back = {}
    monkeypatch.setattr(
        dispatch.job_runner, "submit",
        lambda fn, job_id: fell_back.update(fn=fn, job_id=job_id) or "threadpool-future",
    )

    result = dispatch.submit(_noop_job, "job-unmapped")

    assert result == "threadpool-future"
    assert fell_back == {"fn": _noop_job, "job_id": "job-unmapped"}
