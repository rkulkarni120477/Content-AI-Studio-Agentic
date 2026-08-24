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


# --------------------------------------------------------------------------- #
# Every enqueue site must route through dispatch, and every job function it can
# be handed must be mappable to a Celery task.
# --------------------------------------------------------------------------- #
def test_block_wide_job_is_mapped_to_a_celery_task():
    """Block-wide CDD/Blueprint is the longest, most memory-hungry job in the
    product (a cold 20-day build: ENUMERATE + one MAP per day + REDUCE). It was
    absent from TASK_FOR_FUNC, so even with Celery enabled it silently ran in the
    API container's threadpool — where a restart or OOM killed it mid-run with no
    handler, stranding the job row at status=running forever."""
    from promptops_app.jobs import block_wide_jobs
    from promptops_app.jobs.celery_tasks import TASK_FOR_FUNC

    assert block_wide_jobs.run_block_wide_job in TASK_FOR_FUNC


def test_no_router_enqueues_a_job_bypassing_dispatch():
    """Calling job_runner.submit directly pins a job to the in-process pool and
    silently defeats the use_celery flag — the exact defect that left block-wide
    generation (and import reverse-gen) running in the web container. Guard the
    whole router package rather than the two files that happened to be wrong."""
    import pathlib

    routers = pathlib.Path("app/api/v1/routers")
    offenders = [
        f"{path.name}:{i}"
        for path in sorted(routers.glob("*.py"))
        for i, line in enumerate(path.read_text().splitlines(), 1)
        if "job_runner.submit(" in line and not line.lstrip().startswith("#")
    ]
    assert offenders == [], (
        "these routers enqueue via job_runner.submit instead of dispatch.submit, "
        f"so Celery can never run them: {offenders}"
    )


def test_every_mapped_task_delegates_to_its_plain_function(monkeypatch):
    """The wrappers must stay thin: each Celery task calls the same run_* function
    the threadpool path calls, so behaviour cannot drift between backends."""
    from promptops_app.jobs.celery_tasks import TASK_FOR_FUNC

    for plain_fn, task in TASK_FOR_FUNC.items():
        module = __import__(plain_fn.__module__, fromlist=["x"])
        seen = {}
        monkeypatch.setattr(module, plain_fn.__name__,
                            lambda job_id, _n=plain_fn.__name__: seen.setdefault(_n, job_id))
        task.run("job-xyz")
        assert seen == {plain_fn.__name__: "job-xyz"}, (
            f"{task.name} did not delegate to {plain_fn.__module__}.{plain_fn.__name__}"
        )
