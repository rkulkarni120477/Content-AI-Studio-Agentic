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

import json
import logging
from typing import Optional

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.dependencies import get_current_user, get_db
from app.core.exceptions import JobNotFoundError
from app.schemas.common import ActiveJobResponse, JobStatusResponse

_log = logging.getLogger(__name__)
router = APIRouter()


def _result_warning(job) -> Optional[str]:
    """Read the ``warning`` a completed job recorded in result_json, if any.

    Kept tolerant: result_json is free-form and written by several job types, so a
    missing key or unparseable payload means "no warning", never an error on a
    status poll.
    """
    raw = getattr(job, "result_json", None)
    if not raw:
        return None
    try:
        payload = json.loads(raw)
    except (TypeError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None
    warning = payload.get("warning")
    return warning.strip() if isinstance(warning, str) and warning.strip() else None


@router.get(
    "/active",
    response_model=ActiveJobResponse,
    summary="Find the caller's in-flight job for a course",
    description=(
        "Returns the caller's most recent queued-or-running job for a course, or "
        "`{\"job\": null}` when there is none. The frontend calls this on mount so a "
        "page refresh reattaches to a build already in progress instead of losing it."
    ),
)
def get_active_job(
    course_id: Optional[int] = None,
    job_type: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
) -> ActiveJobResponse:
    """Let a reloaded page find the job it was already watching.

    Block-wide generation runs for minutes, and the poll chain lived only in browser
    memory: a refresh (or a closed laptop, or the network blip that surfaced as
    "Network error. Check your connection.") orphaned the UI while the job kept
    running server-side. Users then either sat on a dead spinner or re-submitted,
    paying for a second concurrent build. Observed 2026-08-13 on a 22-minute Block 2
    build.

    Deliberately server-authoritative rather than a job id kept in localStorage: it
    survives a cleared cache, a different tab, and a different machine, and it cannot
    disagree with the database about whether the job is still alive.

    ``job_type`` accepts a comma-separated list so a page watching one deliverable
    ("cdd_block") is not reattached to an unrelated job the same user started
    elsewhere.
    """
    from promptops_app.repositories import job_repository

    job_types = [t.strip() for t in (job_type or "").split(",") if t.strip()] or None
    job = job_repository.get_active_job_for_user(
        db, current_user.username, course_id=course_id, job_types=job_types,
    )
    if not job:
        return ActiveJobResponse(job=None)
    # Reuses the same shape the poller already consumes, so reattaching needs no
    # second response format. usage_summary/warning are absent by construction: this
    # only ever returns queued or running jobs.
    return ActiveJobResponse(job=JobStatusResponse(
        job_id=str(job.id),
        status=job.status,
        progress=job.progress or 0,
        current_step=job.current_step,
        generation_id=job.result_entity_id,
        error_message=job.error_message,
        created_at=job.created_at.isoformat() if job.created_at else None,
        updated_at=job.updated_at.isoformat() if job.updated_at else None,
    ))


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
    from promptops_app.jobs.job_status import JobStatus
    from promptops_app.repositories import job_repository

    job = job_repository.get_job(db, job_id)
    if not job:
        raise JobNotFoundError(job_id)

    usage_summary = None
    # Scoped to the job's own creator, not whoever happens to be polling —
    # get_job_status has no ownership check (pre-existing), so without this a
    # shared/guessed job_id would leak a stranger's personal budget headroom
    # under someone else's cost/token numbers. In the normal flow the poller
    # already is the creator, so this changes nothing for legitimate use.
    if job.status == "completed" and job.created_by == current_user.username:
        from promptops_app.services.budget_service import build_usage_summary
        from promptops_app.services.usage_service import UsageLogContext

        usage_ctx = UsageLogContext(
            user_name=job.created_by, project_id=job.project_id, course_id=job.course_id,
        )
        # entity_id differs per job type: the full-generation job logs its LLM
        # call under its own job id, but the regenerate-item job logs under the
        # block it regenerated (result_entity_id) — matches how each job type
        # constructs its own UsageLogContext at the actual LLM call site.
        if job.job_type == "regenerate_item":
            usage_summary = build_usage_summary(db, usage_ctx, "block_item_regen", str(job.result_entity_id))
        else:
            usage_summary = build_usage_summary(db, usage_ctx, "generation", str(job.id))

    # Queue-depth visibility (P4.6/F19): for a still-queued job, tell the user how
    # many jobs are ahead of it instead of showing a bare, position-less spinner.
    # Folded into current_step too, so the existing progress UI shows it with no
    # frontend change; also exposed as a structured field for richer UIs.
    queue_position = None
    current_step = job.current_step
    if job.status == JobStatus.QUEUED:
        queue_position = job_repository.count_active_jobs_ahead(db, job)
        current_step = (
            f"Queued — {queue_position} job(s) ahead"
            if queue_position
            else "Queued — starting shortly"
        )

    return JobStatusResponse(
        job_id=str(job.id),
        status=job.status,
        progress=job.progress or 0,
        current_step=current_step,
        generation_id=job.result_entity_id,
        error_message=job.error_message,
        warning=_result_warning(job),
        queue_position=queue_position,
        created_at=job.created_at.isoformat() if job.created_at else None,
        updated_at=job.updated_at.isoformat() if job.updated_at else None,
        usage_summary=usage_summary,
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

    job = job_repository.get_job(db, job_id)
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
