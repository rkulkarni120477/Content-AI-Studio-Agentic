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

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.dependencies import get_current_user, get_db
from app.core.exceptions import JobNotFoundError
from app.schemas.common import ActiveJobResponse, JobListResponse, JobStatusResponse

_log = logging.getLogger(__name__)
router = APIRouter()


def _utc_iso(value) -> Optional[str]:
    """ISO-8601 with an explicit UTC marker, or None.

    ``created_at``/``updated_at`` are written with ``datetime.utcnow()``, i.e. naive
    but UTC. A bare ``.isoformat()`` therefore emits "2026-08-13T06:25:22" with no
    offset, and JavaScript's ``new Date(...)`` parses an offset-less timestamp as
    LOCAL time — so a browser in IST reads a build that started 5.5 hours in the
    future, and any elapsed-time calculation comes out negative. Appending the marker
    is the fix; guessing in the client would only move the bug.
    """
    if value is None:
        return None
    text = value.isoformat()
    # Only stamp genuinely naive values: a tz-aware column would already carry an
    # offset, and appending Z to that would produce an invalid timestamp.
    if value.tzinfo is None and not text.endswith("Z"):
        return text + "Z"
    return text


def _active_block(job) -> Optional[str]:
    """The block a block-wide job is building, if it is one.

    Returned so a reattached page can say WHICH block is running. ``/active`` matches
    on user + course + job_type, and a course can hold several blocks — so a page
    showing "Block 3" could otherwise adopt a running Block 2 build and, on
    completion, report "Done — pinned as active" for a block the user was not looking
    at. Naming it makes that visible instead of silently wrong.
    """
    raw = getattr(job, "request_json", None)
    if not raw:
        return None
    try:
        params = json.loads(raw)
    except (TypeError, ValueError):
        return None
    block = params.get("block") if isinstance(params, dict) else None
    return str(block) if block else None


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


def _result_payload(job) -> Optional[dict]:
    """Parse result_json for clients that need the job's stored output (e.g. regen)."""
    raw = getattr(job, "result_json", None)
    if not raw:
        return None
    try:
        payload = json.loads(raw)
    except (TypeError, ValueError):
        return None
    return payload if isinstance(payload, dict) else None


def _build_status_response(job, db, current_user) -> JobStatusResponse:
    """Full status for a single-job poll (usage + queue + result)."""
    from promptops_app.jobs.job_status import JobStatus
    from promptops_app.repositories import job_repository

    usage_summary = None
    if job.status == "completed" and job.created_by == current_user.username:
        from promptops_app.services.budget_service import build_usage_summary
        from promptops_app.services.usage_service import UsageLogContext

        usage_ctx = UsageLogContext(
            user_name=job.created_by, project_id=job.project_id, course_id=job.course_id,
        )
        if job.job_type == "regenerate_item":
            usage_summary = build_usage_summary(
                db, usage_ctx, "block_item_regen", str(job.result_entity_id),
            )
        else:
            usage_summary = build_usage_summary(db, usage_ctx, "generation", str(job.id))

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
        job_type=getattr(job, "job_type", None) or None,
        course_id=getattr(job, "course_id", None),
        generation_id=job.result_entity_id,
        error_message=job.error_message,
        warning=_result_warning(job),
        queue_position=queue_position,
        created_at=_utc_iso(job.created_at),
        updated_at=_utc_iso(job.updated_at),
        block=_active_block(job),
        usage_summary=usage_summary,
        result=_result_payload(job) if job.status == "completed" else None,
    )


@router.get(
    "",
    response_model=JobListResponse,
    summary="List the caller's jobs for a course",
    description=(
        "Returns the caller's in-flight jobs for a course plus recently finished "
        "ones (completed/failed/cancelled within ``since_minutes``). Powers the "
        "workspace header bell so leaving a generation page does not orphan progress."
    ),
)
def list_jobs(
    course_id: Optional[int] = Query(default=None),
    statuses: Optional[str] = Query(
        default="queued,running,completed,failed",
        description="Comma-separated status filter.",
    ),
    since_minutes: int = Query(
        default=30,
        ge=1,
        le=24 * 60,
        description="Only include jobs updated within this many minutes.",
    ),
    limit: int = Query(default=40, ge=1, le=100),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
) -> JobListResponse:
    from promptops_app.repositories import job_repository

    status_list = [s.strip() for s in (statuses or "").split(",") if s.strip()] or None
    rows = job_repository.list_jobs(
        db,
        user_name=current_user.username,
        course_id=course_id,
        statuses=status_list,
        since_minutes=since_minutes,
        limit=limit,
    )
    return JobListResponse(
        jobs=[
            JobStatusResponse(
                job_id=str(job.id),
                status=job.status,
                progress=job.progress or 0,
                current_step=job.current_step,
                job_type=getattr(job, "job_type", None) or None,
                course_id=getattr(job, "course_id", None),
                generation_id=job.result_entity_id,
                error_message=job.error_message,
                warning=_result_warning(job),
                created_at=_utc_iso(job.created_at),
                updated_at=_utc_iso(job.updated_at),
                block=_active_block(job),
                result=_result_payload(job) if job.status == "completed" else None,
            )
            for job in rows
        ]
    )


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
        job_type=getattr(job, "job_type", None) or None,
        course_id=getattr(job, "course_id", None),
        generation_id=job.result_entity_id,
        error_message=job.error_message,
        created_at=_utc_iso(job.created_at),
        updated_at=_utc_iso(job.updated_at),
        block=_active_block(job),
    ))


@router.get(
    "/{job_id}/progress",
    summary="Per-day progress of a block-wide build",
    description=(
        "Day-level progress for a block-wide CDD/Blueprint job — how many of the "
        "block's days are done. Returns `{\"progress\": null}` for any job that is "
        "not a block-wide build, or when the build is not reporting."
    ),
)
def get_block_job_progress(
    job_id: str,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
) -> dict:
    """Let the UI show "day 7 of 20" instead of a bar frozen at 20%.

    The job row only moves at stage boundaries (20% "Building day digests..." → 90%
    "Saving output..."), so a cold 20-day build shows one label for its entire
    duration and cannot be told apart from a wedged one. The day counts live in the
    DIS process actually doing the work, so this reads them from there.

    Separate from the status poll rather than folded into it: that endpoint is hit by
    every job type in the app and must stay a single fast DB read, not gain a
    cross-service call. Failures here degrade to ``null`` — progress is a nicety, and
    it must never be able to break the poll the UI depends on to detect completion.
    """
    import json as _json

    from promptops_app.repositories import job_repository

    job = job_repository.get_job(db, job_id)
    if not job:
        raise JobNotFoundError(job_id)
    if job.job_type not in ("cdd_block", "blueprint_block"):
        return {"progress": None}

    try:
        params = _json.loads(job.request_json or "{}")
        block = params.get("block")
        if not block:
            return {"progress": None}
        # The module singleton, NOT DISClient(): each instance lazily builds its own
        # httpx.Client and nothing closes it, so constructing one per poll leaked a
        # client (and its connection pool) every 2 seconds for the whole build —
        # roughly 240 of them over an 8-minute run — while defeating the reuse the
        # pooling in DISClient exists to provide.
        from app.core.dis_client import dis_client

        reply = dis_client.get_digest_progress_sync(
            block, current_user=current_user, client_id=params.get("dis_client_id") or "",
        )
        progress = reply.get("progress") if isinstance(reply, dict) else None
        return {"progress": progress}
    except Exception:
        # Debug, not warning: DIS being momentarily unreachable during a 2-second poll
        # is unremarkable, and logging it at warning would bury real problems under one
        # line per poll per user.
        _log.debug("block job %s: day progress unavailable", job_id, exc_info=True)
        return {"progress": None}


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

    job = job_repository.get_job(db, job_id)
    if not job:
        raise JobNotFoundError(job_id)

    return _build_status_response(job, db, current_user)


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
        job_type=getattr(job, "job_type", None) or None,
        course_id=getattr(job, "course_id", None),
    )
