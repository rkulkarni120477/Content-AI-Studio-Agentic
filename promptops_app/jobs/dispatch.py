"""Background-job dispatch: Celery when enabled, ThreadPoolExecutor otherwise.

Single entry point (:func:`submit`) that the API routers call instead of
``job_runner.submit`` directly. It decides *where* a job runs:

* ``settings.use_celery`` is False (default) → submit to the in-process
  ``job_runner`` ThreadPoolExecutor, exactly as before. Zero behaviour change.
* ``settings.use_celery`` is True → enqueue the matching Celery task so the job
  survives restarts and scales across worker replicas.

Fail-safe: if Celery is enabled but the broker is unreachable at submit time
(or the task can't be routed), we log loudly and fall back to the threadpool
rather than dropping the job or failing the request. Turning the flag off is a
zero-deploy kill switch back to the historical behaviour.

The public signature mirrors ``job_runner.submit(fn, *args)`` — callers pass the
same plain ``run_*`` function reference regardless of backend.
"""
from __future__ import annotations

import logging

from promptops_app.core.config import settings
from promptops_app.jobs import job_runner

_log = logging.getLogger(__name__)


def submit(fn, job_id: str):
    """Submit ``fn(job_id)`` to the active background backend.

    Returns whatever the chosen backend returns (a ``Future`` for the
    threadpool, an ``AsyncResult`` for Celery) — callers poll the
    ``GenerationJob`` row for status, so the return value is informational only.
    """
    if settings.use_celery:
        try:
            # Imported lazily so environments that never enable Celery don't pay
            # the import cost or touch the broker at all.
            from promptops_app.jobs.celery_tasks import TASK_FOR_FUNC

            task = TASK_FOR_FUNC.get(fn)
            if task is None:
                _log.error(
                    "dispatch: no Celery task registered for %s — falling back "
                    "to threadpool", getattr(fn, "__name__", fn),
                )
            else:
                result = task.delay(job_id)
                _log.info(
                    "dispatch: enqueued %s job_id=%s via Celery (task_id=%s)",
                    getattr(fn, "__name__", fn), job_id, result.id,
                )
                return result
        except Exception:  # broker down, routing error, import failure, ...
            # Never let a Celery/broker problem take down job submission: fall
            # back to the in-process executor and alert loudly.
            _log.exception(
                "dispatch: Celery submit failed for %s job_id=%s — falling back "
                "to in-process threadpool", getattr(fn, "__name__", fn), job_id,
            )

    return job_runner.submit(fn, job_id)
