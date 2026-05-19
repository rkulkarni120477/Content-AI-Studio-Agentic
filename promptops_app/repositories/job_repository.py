"""Job Repository — GenerationJob database access.

All functions that touch the generation_jobs table live here.
The generation_jobs.py module (execution logic) delegates DB writes to
job_status.py helpers; this module covers creation and reads.
"""

import json
import uuid
from datetime import datetime, timezone

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


def get_active_job_for_user(db, user_name: str, project_id: int = None):
    """Return the most recent queued-or-running job for this user, or None."""
    q = (
        db.query(GenerationJob)
        .filter(
            GenerationJob.created_by == user_name,
            GenerationJob.status.in_(list(JobStatus.ACTIVE)),
        )
    )
    if project_id:
        q = q.filter(GenerationJob.project_id == project_id)
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
