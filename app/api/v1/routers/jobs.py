"""
Jobs router — background job status polling.

Streamlit equivalent: ``ui/job_progress.py`` render_active_job()

The React frontend polls GET /api/v1/jobs/{job_id} every 2 seconds
after launching a generation job, displaying a progress bar until the
job reaches a terminal state (completed | failed | cancelled).

The job record is written by the Celery worker via job_status.py helpers.
This router only reads and cancels jobs — it never executes them.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.dependencies import get_current_user, get_db
from app.core.exceptions import JobNotFoundError
from app.schemas.common import JobStatusResponse

_log = logging.getLogger(__name__)
router = APIRouter()


@router.get(
    "/{job_id}",
    response_model=JobStatusResponse,
    summary="Poll background job status",
    description=(
        "Returns the current status, progress percentage, and step label for a job. "
        "Poll this endpoint every 2 seconds after launching a generation job. "
        "Stop polling when status is 'completed', 'failed', or 'cancelled'."
    ),
)
def get_job_status(
    job_id: str,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
) -> JobStatusResponse:
    """
    Return the current state of a background job.

    Reads the GenerationJob row, which the Celery worker updates via
    job_status.set_running(), set_completed(), and set_failed().
    """
    from promptops_app.repositories import job_repository

    job = job_repository.get_job(db, int(job_id))
    if not job:
        raise JobNotFoundError(job_id)

    return JobStatusResponse(
        job_id=str(job.id),
        status=job.status,
        progress=job.progress or 0,
        current_step=job.current_step,
        generation_id=job.result_entity_id,
        error_message=job.error_message,
        created_at=job.created_at.isoformat() if job.created_at else None,
        updated_at=job.updated_at.isoformat() if job.updated_at else None,
    )


@router.delete(
    "/{job_id}",
    response_model=JobStatusResponse,
    summary="Cancel a queued or running job",
    description="Marks the job as cancelled. The Celery worker will stop processing if it hasn't started.",
)
def cancel_job(
    job_id: str,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
) -> JobStatusResponse:
    """
    Cancel a generation job.

    Updates the GenerationJob row to status='cancelled'.
    The Celery task may already be running; cancellation is best-effort.
    """
    from promptops_app.jobs.job_status import set_cancelled
    from promptops_app.repositories import job_repository

    job = job_repository.get_job(db, int(job_id))
    if not job:
        raise JobNotFoundError(job_id)

    from promptops_app.jobs.job_status import JobStatus
    if job.status not in JobStatus.TERMINAL:
        set_cancelled(db, job)

    _log.info("job_cancelled  user=%s  job_id=%s", current_user.username, job_id)

    return JobStatusResponse(
        job_id=str(job.id),
        status=job.status,
        progress=job.progress or 0,
        current_step=job.current_step,
    )
