"""Job status constants and DB update helpers.

All helpers receive the SQLAlchemy session from the background thread — they
must NOT call st.anything().

Celery migration: nothing changes here; these helpers work with any session.
"""

from datetime import datetime, timezone


class JobStatus:
    QUEUED    = "queued"
    RUNNING   = "running"
    COMPLETED = "completed"
    FAILED    = "failed"
    CANCELLED = "cancelled"

    TERMINAL = {COMPLETED, FAILED, CANCELLED}
    ACTIVE   = {QUEUED, RUNNING}


# (progress_pct, human-readable label) — mirrors the % shown in the UI
STAGE_CONTEXT       = (10,  "Preparing context...")
STAGE_PROMPT        = (25,  "Building prompt...")
STAGE_LLM           = (45,  "Calling LLM...")
STAGE_CE_VALIDATION = (65,  "CE Validation...")
STAGE_SPLIT         = (80,  "Splitting content blocks...")
STAGE_SAVE          = (92,  "Saving output...")
STAGE_DONE          = (100, "Completed")

# Ordered list used by the progress bar stepper in the UI
ALL_STAGES = [STAGE_CONTEXT, STAGE_PROMPT, STAGE_LLM, STAGE_CE_VALIDATION, STAGE_SPLIT, STAGE_SAVE, STAGE_DONE]


def _now() -> datetime:
    return datetime.now(timezone.utc)


def set_running(db, job, progress: int, stage: str) -> None:
    """Advance a job to *running* at the given progress/stage."""
    job.status       = JobStatus.RUNNING
    job.progress     = progress
    job.current_step = stage
    job.updated_at   = _now()
    db.commit()


def set_completed(db, job, generation_id: int) -> None:
    """Mark the job as successfully completed and record the result entity."""
    now = _now()
    job.status          = JobStatus.COMPLETED
    job.progress        = 100
    job.current_step    = "Completed"
    job.result_entity_id = generation_id
    job.updated_at      = now
    job.completed_at    = now
    db.commit()


def set_failed(db, job, error: str) -> None:
    """Mark the job as failed.

    Only a clean, user-friendly message is stored in ``error_message``.
    Full tracebacks must be written to the application log *before* calling
    this helper so they are never exposed in the UI.
    """
    now = _now()
    job.status        = JobStatus.FAILED
    job.current_step  = "Failed"
    job.error_message = str(error)[:2000]
    job.updated_at    = now
    job.completed_at  = now
    db.commit()


def set_cancelled(db, job) -> None:
    """Mark the job as cancelled by the user."""
    now = _now()
    job.status       = JobStatus.CANCELLED
    job.current_step = "Cancelled"
    job.updated_at   = now
    job.completed_at = now
    db.commit()
