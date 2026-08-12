"""Startup reaper for background jobs orphaned by a process death.

Why this exists
---------------
Background jobs run in an in-process ``ThreadPoolExecutor`` (``job_runner``)
unless Celery is enabled. Every ``run_*`` job function wraps its body in
``except Exception`` and marks the row failed, so an *error* is always recorded.
What is never recorded is a **process death**: an OOM kill, a container restart,
or a redeploy takes the worker threads with it before any handler can run, and
the ``generation_jobs`` row is left at ``running`` forever.

Observed in production 2026-08-12: a block-wide CDD job sat at
``status=running, current_step="Building day digests..."`` with ``updated_at``
five seconds after creation and no further change. The row was indistinguishable
from a healthy long-running job, so the UI showed a phantom in-flight generation
that could never resolve, and the polling client eventually reported a failure
the database never knew about.

Safety
------
The dangerous mistake here would be failing jobs that are alive in *another*
process. Two guards prevent that:

* **Celery topology is skipped entirely.** When ``settings.use_celery`` is on,
  jobs execute in the worker container, so an API restart says nothing about
  them — and Celery's own ``task_reject_on_worker_lost`` re-queues genuinely
  lost tasks. Reaping here would fight that mechanism.
* **A staleness window.** Only jobs untouched for longer than
  ``stale_after_minutes`` are reaped. Job rows are only written at stage
  transitions, and a cold 20-day digest build can legitimately run minutes
  between them, so the default is deliberately generous. This also keeps the
  reaper safe if the API is ever scaled to multiple replicas, where one
  replica's restart must not disturb another's in-flight work.

The reaper never raises: a cleanup pass must not be the reason the application
fails to start.
"""
from __future__ import annotations

import datetime as _dt
import logging

_log = logging.getLogger(__name__)

# Generous by design — see "Safety" above. A cold block-wide build can go several
# minutes between row writes, and over-eager reaping is worse than a late one.
DEFAULT_STALE_AFTER_MINUTES = 60


def reap_orphaned_jobs(stale_after_minutes: int = DEFAULT_STALE_AFTER_MINUTES) -> int:
    """Mark jobs stranded in an active state by a dead process as failed.

    Returns the number of rows reaped (0 when skipped or nothing was stale).
    """
    try:
        from promptops_app.core.config import settings

        if settings.use_celery:
            _log.info("job reaper: skipped (Celery enabled — jobs are owned by the "
                      "worker, not this process)")
            return 0

        from promptops_app.database import GenerationJob, SessionLocal
        from promptops_app.jobs.job_status import JobStatus, set_failed

        cutoff = _dt.datetime.utcnow() - _dt.timedelta(minutes=stale_after_minutes)
        db = SessionLocal()
        try:
            stranded = (
                db.query(GenerationJob)
                .filter(GenerationJob.status.in_(sorted(JobStatus.ACTIVE)))
                .filter(GenerationJob.updated_at < cutoff)
                .all()
            )
            for job in stranded:
                _log.warning(
                    "job reaper: job %s (%s) stranded at status=%s step=%r since %s "
                    "— the process running it died without recording an error; "
                    "marking failed",
                    job.id, job.job_type, job.status, job.current_step, job.updated_at,
                )
                try:
                    set_failed(db, job,
                               "Generation was interrupted (the server restarted "
                               "while this job was running). Please try again.")
                except Exception:
                    _log.exception("job reaper: could not mark job %s failed", job.id)
                    db.rollback()
            if stranded:
                _log.warning("job reaper: reaped %d stranded job(s)", len(stranded))
            else:
                _log.info("job reaper: no stranded jobs")
            return len(stranded)
        finally:
            db.close()
    except Exception as exc:
        # Never let cleanup block startup. A missing/unmigrated table is an
        # expected, uninteresting condition (a bare test database, or a first boot
        # racing init_db) — say so in one line instead of a traceback, and reserve
        # the full stack for genuinely unexpected failures.
        if _is_schema_not_ready(exc):
            _log.info("job reaper: skipped (generation_jobs not available yet)")
        else:
            _log.exception("job reaper: failed; continuing startup")
        return 0


def _is_schema_not_ready(exc: BaseException) -> bool:
    """True when *exc* means "the jobs table isn't there", not "something broke"."""
    try:
        from sqlalchemy.exc import OperationalError, ProgrammingError
    except Exception:
        return False
    if not isinstance(exc, (OperationalError, ProgrammingError)):
        return False
    text = str(exc).lower()
    return "no such table" in text or "does not exist" in text or "undefinedtable" in text
