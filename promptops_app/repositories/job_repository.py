"""Job Repository — GenerationJob database access.

All functions that touch the generation_jobs table live here.
The generation_jobs.py module (execution logic) delegates DB writes to
job_status.py helpers; this module covers creation and reads.
"""

import json
import uuid
from datetime import datetime, timedelta, timezone

from promptops_app.database import GenerationJob
from promptops_app.jobs.job_status import JobStatus


# ── Create ────────────────────────────────────────────────────────────────────

def create_job(
    db,
    *,
    user_name: str,
    request_params: dict,
    project_id: int = None,
    course_id: int = None,
    job_type: str = "generation",
) -> str:
    """Insert a new GenerationJob row in *queued* state.

    Returns the new job ID (32-char hex UUID).
    Call this from the UI thread *before* submitting to the executor.
    """
    job_id = uuid.uuid4().hex
    now    = datetime.now(timezone.utc)
    job    = GenerationJob(
        id                 = job_id,
        job_type           = job_type,
        status             = JobStatus.QUEUED,
        progress           = 0,
        current_step       = "Queued",
        input_payload_json = json.dumps(request_params, ensure_ascii=False),
        project_id         = project_id,
        course_id          = course_id,
        created_by         = user_name,
        created_at         = now,
        updated_at         = now,
    )
    db.add(job)
    db.commit()
    return job_id


# ── Read ──────────────────────────────────────────────────────────────────────

def get_job(db, job_id: str):
    """Return a single GenerationJob or None."""
    return db.query(GenerationJob).filter(GenerationJob.id == job_id).first()


def count_active_jobs_ahead(db, job) -> int:
    """Number of still-active jobs queued/running ahead of *job* (P4.6/F19).

    Counts GenerationJob rows in ``queued`` or ``running`` state created before
    *job* (FIFO by ``created_at``). A running job counts as "ahead" because it is
    occupying a worker the queued job is waiting on. Backend-agnostic: both the
    ThreadPoolExecutor and Celery paths write GenerationJob rows, so this gives a
    real queue position for either. Approximate under exact ``created_at`` ties,
    which is fine for a position indicator.
    """
    from sqlalchemy import func

    created_at = job.created_at
    if created_at is None:
        return 0
    count = (
        db.query(func.count(GenerationJob.id))
        .filter(
            GenerationJob.status.in_([JobStatus.QUEUED, JobStatus.RUNNING]),
            GenerationJob.id != job.id,
            GenerationJob.created_at < created_at,
        )
        .scalar()
    )
    return int(count or 0)


def list_jobs(
    db,
    *,
    user_name: str = None,
    project_id: int = None,
    is_admin: bool = False,
    status_filter: str = None,
    limit: int = 20,
    offset: int = 0,
) -> list:
    """Return recent jobs with optional scope + status filtering."""
    q = db.query(GenerationJob)
    if not is_admin and user_name:
        q = q.filter(GenerationJob.created_by == user_name)
    if project_id:
        q = q.filter(GenerationJob.project_id == project_id)
    if status_filter:
        q = q.filter(GenerationJob.status == status_filter)
    return (
        q.order_by(GenerationJob.created_at.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )


def count_jobs(
    db,
    *,
    user_name: str = None,
    project_id: int = None,
    is_admin: bool = False,
    status_filter: str = None,
) -> int:
    q = db.query(GenerationJob)
    if not is_admin and user_name:
        q = q.filter(GenerationJob.created_by == user_name)
    if project_id:
        q = q.filter(GenerationJob.project_id == project_id)
    if status_filter:
        q = q.filter(GenerationJob.status == status_filter)
    return q.count()


def get_active_job_for_user(db, user_name: str, project_id: int = None,
                            course_id: int = None, job_types: list[str] = None,
                            max_age_minutes: int = None):
    """Return the most recent queued-or-running job for this user, or None.

    Scoped to ``created_by`` deliberately. This backs the frontend's reattach-after-
    refresh flow (GET /api/v1/jobs/active), and a job row carries a user's own cost
    and error detail — so "resume MY in-flight job" is the safe semantic. Widening it
    to everyone working on a course would need a real course-access check, which this
    codebase does not yet have anywhere.

    ``course_id`` and ``job_types`` narrow it further so a CDD page reattaches to a CDD
    build rather than to whatever unrelated job the user happens to be running
    elsewhere in the app.

    ``max_age_minutes`` bounds how stale a row may be and defaults to the reaper's own
    threshold, so the two agree by construction: a row the reaper would call stranded
    must never be adopted as live. This matters because a process death leaves a row at
    ``running`` forever (the reaper runs only at startup), and adopting such a row
    re-disables the Generate button on every page load — the panel has no cancel
    control, so before this bound a single zombie row could lock a user out of a course
    permanently, where previously a page reload cleared the phantom. ``updated_at``
    does not advance during a build (job rows are written only at stage transitions),
    so this must stay generous enough to cover a real cold build; the reaper's 60
    minutes is both, since a build running longer than that is one the reaper will end.
    """
    from promptops_app.jobs.reaper import DEFAULT_STALE_AFTER_MINUTES

    q = (
        db.query(GenerationJob)
        .filter(
            GenerationJob.created_by == user_name,
            GenerationJob.status.in_(list(JobStatus.ACTIVE)),
        )
    )
    if project_id:
        q = q.filter(GenerationJob.project_id == project_id)
    if course_id:
        q = q.filter(GenerationJob.course_id == course_id)
    if job_types:
        q = q.filter(GenerationJob.job_type.in_(list(job_types)))
    window = DEFAULT_STALE_AFTER_MINUTES if max_age_minutes is None else max_age_minutes
    if window:
        cutoff = datetime.utcnow() - timedelta(minutes=int(window))
        q = q.filter(GenerationJob.updated_at >= cutoff)
    return q.order_by(GenerationJob.created_at.desc()).first()


# ── Mutations ─────────────────────────────────────────────────────────────────

def cancel_job(db, job_id: str, requesting_user: str, is_admin: bool = False) -> tuple[bool, str]:
    """Cancel a queued or running job if the requester owns it (or is admin).

    Returns (success, message).
    """
    from promptops_app.jobs.job_status import set_cancelled

    job = get_job(db, job_id)
    if not job:
        return False, "Job not found."
    if not is_admin and job.created_by != requesting_user:
        return False, "You can only cancel your own jobs."
    if job.status not in JobStatus.ACTIVE:
        return False, f"Job is already {job.status} — cannot cancel."
    set_cancelled(db, job)
    return True, "Job cancelled."
