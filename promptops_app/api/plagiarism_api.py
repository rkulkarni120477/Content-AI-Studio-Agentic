"""FastAPI routes for the plagiarism check API.

Endpoints
---------
POST /api/plagiarism/check
    Trigger an async Copyleaks scan for a block.
    Returns report_id + task_id immediately (non-blocking).

GET /api/plagiarism/status/{block_id}
    Poll the scan status and results for a given block.

DELETE /api/plagiarism/{block_id}
    Cancel an in-progress scan (marks as failed, cancels Celery task).
"""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

_log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/plagiarism", tags=["plagiarism"])


# ---------------------------------------------------------------------------
# DB dependency
# ---------------------------------------------------------------------------

def _get_db():
    from promptops_app.database import SessionLocal
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Request / response schemas
# ---------------------------------------------------------------------------

class CheckRequest(BaseModel):
    block_id:   int
    project_id: Optional[int] = None
    course_id:  Optional[int] = None


class CheckResponse(BaseModel):
    report_id: int
    task_id:   str
    status:    str
    message:   str


# ---------------------------------------------------------------------------
# POST /api/plagiarism/check
# ---------------------------------------------------------------------------

@router.post("/check", response_model=CheckResponse, status_code=status.HTTP_202_ACCEPTED)
def trigger_check(req: CheckRequest, db: Session = Depends(_get_db)):
    """Enqueue an async Copyleaks plagiarism scan for *block_id*.

    Returns 202 Accepted immediately.  Poll ``GET /api/plagiarism/status/{block_id}``
    for results.  Returns 409 if a scan is already in progress.
    """
    from promptops_app.database import Block, PlagiarismReport
    from promptops_app.services.plagiarism_service import (
        generate_scan_id, CopyleaksNotConfigured,
    )

    block = db.query(Block).filter(Block.id == req.block_id).first()
    if not block:
        raise HTTPException(status_code=404, detail=f"Block {req.block_id} not found.")
    if not block.content or not block.content.strip():
        raise HTTPException(status_code=400, detail="Block has no content to scan.")

    # Check for an existing in-progress scan
    existing = (
        db.query(PlagiarismReport)
        .filter(PlagiarismReport.block_id == req.block_id)
        .order_by(PlagiarismReport.created_at.desc())
        .first()
    )
    if existing and existing.status in ("pending", "processing"):
        raise HTTPException(
            status_code=409,
            detail=f"Scan already in progress (report_id={existing.id}, status={existing.status}).",
        )

    # Create the report row
    report = PlagiarismReport(
        block_id=req.block_id,
        project_id=req.project_id,
        course_id=req.course_id,
        scan_id=generate_scan_id(),
        status="pending",
    )
    db.add(report)
    db.commit()
    db.refresh(report)

    # Enqueue the Celery task
    from promptops_app.jobs.plagiarism_jobs import run_plagiarism_scan
    task = run_plagiarism_scan.delay(report.id, block.content)

    report.celery_task_id = task.id
    db.commit()

    _log.info(
        "Plagiarism check triggered: block_id=%d report_id=%d task_id=%s",
        req.block_id, report.id, task.id,
    )
    return CheckResponse(
        report_id=report.id,
        task_id=task.id,
        status="pending",
        message="Plagiarism scan queued. Poll /status/{block_id} for results.",
    )


# ---------------------------------------------------------------------------
# GET /api/plagiarism/status/{block_id}
# ---------------------------------------------------------------------------

@router.get("/status/{block_id}")
def get_status(block_id: int, db: Session = Depends(_get_db)):
    """Return the latest plagiarism scan status and results for *block_id*."""
    from promptops_app.database import PlagiarismReport

    report = (
        db.query(PlagiarismReport)
        .filter(PlagiarismReport.block_id == block_id)
        .order_by(PlagiarismReport.created_at.desc())
        .first()
    )
    if not report:
        return {"status": "not_checked", "block_id": block_id}

    payload: dict = {
        "report_id":        report.id,
        "block_id":         block_id,
        "scan_id":          report.scan_id,
        "status":           report.status,
        "created_at":       report.created_at.isoformat() if report.created_at else None,
        "submitted_at":     report.submitted_at.isoformat() if report.submitted_at else None,
        "completed_at":     report.completed_at.isoformat() if report.completed_at else None,
        "similarity_score": report.similarity_score,
        "ai_score":         report.ai_score,
        "error_message":    report.error_message,
    }
    if report.status == "completed":
        payload["source_urls"] = report.source_urls or []
        payload["highlights"]  = report.highlights  or []
    return payload


# ---------------------------------------------------------------------------
# DELETE /api/plagiarism/{block_id}
# ---------------------------------------------------------------------------

@router.delete("/{block_id}", status_code=status.HTTP_204_NO_CONTENT)
def cancel_scan(block_id: int, db: Session = Depends(_get_db)):
    """Cancel an in-progress scan and revoke the Celery task."""
    from promptops_app.database import PlagiarismReport

    report = (
        db.query(PlagiarismReport)
        .filter(PlagiarismReport.block_id == block_id)
        .order_by(PlagiarismReport.created_at.desc())
        .first()
    )
    if not report or report.status not in ("pending", "processing"):
        raise HTTPException(status_code=404, detail="No active scan found for this block.")

    if report.celery_task_id:
        try:
            from promptops_app.celery_app import celery_app
            celery_app.control.revoke(report.celery_task_id, terminate=True)
        except Exception as exc:
            _log.warning("Could not revoke task %s: %s", report.celery_task_id, exc)

    report.status        = "failed"
    report.error_message = "Cancelled by user."
    db.commit()
