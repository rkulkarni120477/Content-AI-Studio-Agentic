"""Background jobs for the CE Agent Review feature.

Step 1: ``run_checklist_import_job`` splits an uploaded CE checklist document
into individual rules (one LLM call, with truncation escalation) and persists a
new active ``ReviewChecklist`` version. The heavy LLM work runs here rather than
on the request thread so a large checklist cannot hit the browser's request
timeout mid-split.

Follows the same durable-job contract as the design/generation jobs: load the
GenerationJob row, drive progress through the shared ``job_status`` helpers, and
store a clean user-facing message on failure. On success the new checklist id is
recorded as the job's result entity.
"""
from __future__ import annotations

import json
import logging

from promptops_app.database import GenerationJob, SessionLocal
from promptops_app.jobs.job_status import JobStatus, set_completed, set_failed, set_running

_log = logging.getLogger(__name__)


def _load_job(db, job_id: str):
    job = db.query(GenerationJob).filter(GenerationJob.id == job_id).first()
    if not job:
        _log.error("review job %s not found", job_id)
        return None
    if job.status == JobStatus.CANCELLED:
        _log.info("review job %s cancelled before start", job_id)
        return None
    if job.status == JobStatus.COMPLETED:
        _log.info("review job %s already completed — skipping", job_id)
        return None
    return job


def run_checklist_import_job(job_id: str) -> None:
    """Split an uploaded checklist document into rules and store a new version."""
    from promptops_app.services.ce_review.checklist_import import (
        ChecklistImportError,
        split_and_store,
    )

    db = SessionLocal()
    job = None
    try:
        job = _load_job(db, job_id)
        if job is None:
            return

        params = json.loads(job.request_json or "{}")
        document_id = int(params["document_id"])
        project_id = int(params["project_id"])
        name = str(params.get("name") or "CE Checklist")
        created_by = str(params.get("user_name") or job.created_by or "cas-user")
        model_choice = params.get("model_choice") or None

        set_running(db, job, 25, "Splitting checklist into rules...")
        checklist = split_and_store(
            db,
            document_id=document_id,
            project_id=project_id,
            name=name,
            created_by=created_by,
            model_choice=model_choice,
        )

        job.result_json = json.dumps(
            {"checklist_id": checklist.id, "version": checklist.version,
             "rule_count": len(checklist.items)},
            ensure_ascii=False,
        )
        db.commit()
        set_completed(db, job, checklist.id)
        _log.info("checklist_import_job_done job=%s checklist_id=%s", job_id, checklist.id)

    except ChecklistImportError as exc:
        # Clean, user-facing reason — safe to show in the activity bell.
        _log.warning("checklist_import_job %s failed: %s", job_id, exc)
        if job is not None:
            db.rollback()
            set_failed(db, job, str(exc)[:2000])
    except Exception as exc:  # noqa: BLE001
        _log.exception("checklist_import_job %s crashed: %s", job_id, exc)
        if job is not None:
            db.rollback()
            set_failed(db, job, "Checklist import failed. Please try again.")
    finally:
        db.close()


def run_ce_review_job(job_id: str) -> None:
    """Run a CE review for one generation (lesson).

    The ``ContentReview`` row is created queued in the request thread; this job
    runs its passes and finalises it. Both the GenerationJob (for polling) and
    the ContentReview (the domain row) are advanced together.
    """
    from promptops_app.database import ContentReview
    from promptops_app.services.ce_review.review_runner import execute_review

    db = SessionLocal()
    job = None
    try:
        job = _load_job(db, job_id)
        if job is None:
            return

        params = json.loads(job.request_json or "{}")
        review_id = int(params["review_id"])
        review = db.query(ContentReview).filter(ContentReview.id == review_id).first()
        if review is None:
            _log.error("run_ce_review_job %s: ContentReview %s not found", job_id, review_id)
            set_failed(db, job, "Review record not found.")
            return

        set_running(db, job, 30, "Reviewing content...")
        execute_review(db, review)

        job.result_json = json.dumps(
            {"review_id": review.id, "run_status": review.run_status, "counts": review.counts},
            ensure_ascii=False, default=str,
        )
        db.commit()
        set_completed(db, job, review.id)
        _log.info("ce_review_job_done job=%s review_id=%s", job_id, review.id)

    except Exception as exc:  # noqa: BLE001
        _log.exception("run_ce_review_job %s crashed: %s", job_id, exc)
        if job is not None:
            db.rollback()
            set_failed(db, job, "The review could not be completed. Please try again.")
        # Best-effort: mark the domain row failed too, so the panel doesn't hang.
        try:
            params = json.loads((job.request_json if job else "") or "{}")
            rid = params.get("review_id")
            if rid is not None:
                from promptops_app.database import ContentReview
                rev = db.query(ContentReview).filter(ContentReview.id == int(rid)).first()
                if rev is not None and rev.run_status in ("queued", "running"):
                    rev.run_status = "failed"
                    rev.error_message = "The review could not be completed."
                    db.commit()
        except Exception:  # pragma: no cover
            db.rollback()
    finally:
        db.close()
