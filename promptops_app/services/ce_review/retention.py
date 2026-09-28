"""CE review retention — prune old finding/result detail, keep run headers.

Ships OFF (days=0 keeps everything). Not wired to a scheduler; a future
maintenance task can call ``prune_old_review_details``. Headers (content_reviews)
and dismissals are kept forever (audit trail + carry-forward).
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
