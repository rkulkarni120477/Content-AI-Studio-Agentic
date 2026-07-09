"""Celery task for async plagiarism scanning via Copyleaks.

Task flow (webhook-based)
--------------------------
1. Mark PlagiarismReport as "processing".
2. Call plagiarism_service.submit_scan() — PUT to Copyleaks.
3. Return immediately.  Copyleaks delivers results to the webhook endpoint
   (POST /api/plagiarism/webhook/{status}/{scan_id}), which stores them.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from celery.exceptions import MaxRetriesExceededError

from promptops_app.celery_app import celery_app

_log = logging.getLogger(__name__)


@celery_app.task(
    bind=True,
    name="plagiarism.run_scan",
    max_retries=3,
    default_retry_delay=60,
    acks_late=True,
    reject_on_worker_lost=True,
)
def run_plagiarism_scan(self, report_id: int, block_content: str) -> dict:
    """Submit *block_content* to Copyleaks.  Results arrive via webhook.

    Parameters
    ----------
    report_id:
        PlagiarismReport.id — row must already exist with status="pending".
    block_content:
        Raw text content of the block.
    """
    from promptops_app.database import SessionLocal, PlagiarismReport
    from promptops_app.services.plagiarism_service import (
        submit_scan, CopyleaksError, CopyleaksNotConfigured,
    )

    db     = SessionLocal()
    report = None
    try:
        report = db.query(PlagiarismReport).filter(
            PlagiarismReport.id == report_id
        ).first()
        if not report:
            _log.error("run_plagiarism_scan: PlagiarismReport id=%d not found", report_id)
            return {"error": "report_not_found"}

        report.status       = "processing"
        report.submitted_at = datetime.now(timezone.utc)
        db.commit()

        scan_id = report.scan_id
        _log.info(
            "Submitting Copyleaks scan: report_id=%d scan_id=%s block_id=%d",
            report_id, scan_id, report.block_id,
        )

        submit_scan(block_content, scan_id)

        _log.info(
            "Copyleaks scan submitted: report_id=%d scan_id=%s — awaiting webhook",
            report_id, scan_id,
        )
        return {"status": "processing", "report_id": report_id, "scan_id": scan_id}

    except CopyleaksNotConfigured as exc:
        _log.error("Copyleaks not configured — will NOT retry: %s", exc)
        if report:
            report.status        = "failed"
            report.error_message = str(exc)
            db.commit()
        return {"status": "failed", "error": str(exc)}

    except CopyleaksError as exc:
        _log.warning(
            "Copyleaks error for report %d (attempt %d/%d): %s",
            report_id, self.request.retries + 1, self.max_retries + 1, exc,
        )
        try:
            raise self.retry(exc=exc)
        except MaxRetriesExceededError:
            if report:
                report.status        = "failed"
                report.error_message = f"Submit failed after retries: {exc}"
                db.commit()
            _log.error("Max retries exceeded for report %d: %s", report_id, exc)
            return {"status": "failed", "error": str(exc)}

    except Exception as exc:
        _log.error(
            "Unexpected error in plagiarism scan report_id=%d: %s",
            report_id, exc, exc_info=True,
        )
        if report:
            report.status        = "failed"
            report.error_message = f"Unexpected error: {exc}"
            db.commit()
        raise

    finally:
        db.close()
