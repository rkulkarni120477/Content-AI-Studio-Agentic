"""Celery task for async plagiarism scanning via Copyleaks.

Task flow
---------
1. Mark PlagiarismReport as "processing".
2. Call plagiarism_service.run_full_scan() — submit + poll + parse.
3. Store parsed results and update Block.plagiarism_score.
4. On failure, mark as "failed" and retry up to MAX_RETRIES times.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from celery.exceptions import MaxRetriesExceededError

from promptops_app.celery_app import celery_app

_log = logging.getLogger(__name__)


@celery_app.task(
    bind=True,
    name="plagiarism.run_scan",
    max_retries=3,
    default_retry_delay=120,    # 2 min between retries
    acks_late=True,
    reject_on_worker_lost=True,
)
def run_plagiarism_scan(self, report_id: int, block_content: str) -> dict:
    """Submit *block_content* to Copyleaks, wait for results, store them.

    Parameters
    ----------
    report_id:
        PlagiarismReport.id — row must already exist with status="pending".
    block_content:
        Raw text content of the block (passed by value so the task is self-contained).
    """
    from promptops_app.database import SessionLocal, PlagiarismReport, Block
    from promptops_app.services.plagiarism_service import (
        run_full_scan, CopyleaksError, CopyleaksNotConfigured,
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

        # ── Mark as processing ─────────────────────────────────────────────
        report.status       = "processing"
        report.submitted_at = datetime.now(timezone.utc)
        db.commit()

        scan_id = report.scan_id
        _log.info(
            "Starting Copyleaks scan: report_id=%d scan_id=%s block_id=%d",
            report_id, scan_id, report.block_id,
        )

        # ── Run the full scan (submit → poll → parse) ──────────────────────
        result = run_full_scan(block_content, scan_id)
        parsed = result["parsed"]

        # ── Store results ──────────────────────────────────────────────────
        report.similarity_score = parsed["similarity_score"]
        report.ai_score         = parsed.get("ai_score")
        report.source_urls      = parsed["source_urls"]     # stored as JSON by SQLAlchemy
        report.highlights       = parsed["highlights"]
        report.raw_response     = json.dumps(result["raw"])
        report.status           = "completed"
        report.completed_at     = datetime.now(timezone.utc)
        db.commit()

        # ── Back-fill Block.plagiarism_score for the legacy dashboard ──────
        block = db.query(Block).filter(Block.id == report.block_id).first()
        if block:
            block.plagiarism_score  = int(round(parsed["similarity_score"]))
            block.plagiarism_report = (
                f"Copyleaks similarity: {parsed['similarity_score']}%"
                + (f" | AI score: {parsed['ai_score']}%" if parsed.get("ai_score") is not None else "")
            )
            db.commit()

        _log.info(
            "Plagiarism scan completed: report_id=%d sim=%.1f%% ai=%s",
            report_id,
            parsed["similarity_score"],
            f"{parsed['ai_score']}%" if parsed.get("ai_score") is not None else "n/a",
        )
        return {"status": "completed", "report_id": report_id}

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
                report.error_message = f"Max retries exceeded: {exc}"
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
        raise   # Let Celery handle the exception (will be retried if configured)

    finally:
        db.close()
