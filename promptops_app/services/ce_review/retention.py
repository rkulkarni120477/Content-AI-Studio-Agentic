"""CE review retention — prune old finding/result detail, keep run headers.

Runs at app startup (like the job reaper). Off by default: only prunes when
``CE_REVIEW_RETENTION_DAYS > 0``. Only the bulky detail rows (findings + checklist
results) of reviews completed longer ago than the retention window are removed —
the ``content_reviews`` headers (audit trail) and ``review_dismissals``
(carry-forward) are kept forever.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

_log = logging.getLogger(__name__)


def prune_old_review_details(db, days: int) -> int:
    """Delete findings + checklist results for reviews completed over *days* ago.
    Returns rows removed. 0/None days → no-op."""
    if not days or days <= 0:
        return 0
    from promptops_app.database import ContentReview, ReviewChecklistResult, ReviewFinding

    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    old_ids = [r.id for r in db.query(ContentReview.id)
               .filter(ContentReview.completed_at < cutoff).all()]
    if not old_ids:
        return 0
    f = db.query(ReviewFinding).filter(ReviewFinding.review_id.in_(old_ids)).delete(synchronize_session=False)
    c = db.query(ReviewChecklistResult).filter(ReviewChecklistResult.review_id.in_(old_ids)).delete(synchronize_session=False)
    db.commit()
    _log.info("ce_review_pruned reviews=%d findings=%d results=%d", len(old_ids), f, c)
    return f + c


def run_startup_prune() -> None:
    """Called once at app startup. Off (days<=0) → does nothing; on → prunes.
    Fail-safe: never blocks startup."""
    try:
        from app.core.config import settings
        if not getattr(settings, "ce_review_enabled", False):
            return
        days = getattr(settings, "ce_review_retention_days", 0) or 0
        if days <= 0:
            return                      # retention off → stays off
        from promptops_app.database import SessionLocal
        db = SessionLocal()
        try:
            prune_old_review_details(db, days)
        finally:
            db.close()
    except Exception as exc:  # pragma: no cover - never break startup
        _log.warning("ce_review retention prune skipped: %s", exc)
