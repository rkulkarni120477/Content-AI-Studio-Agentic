"""Apply / dismiss CE review findings.

Applying a fix is a deterministic in-place text swap — no LLM (AC-5). Each apply
snapshots the block first (restore point); a batch snapshots once per block. After
a block changes, the other open findings in it are re-anchored (marked stale if
their quote is gone). Only single-line inline fixes auto-apply, so a swap can
never shatter a table row or code fence.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from promptops_app.repositories.block_repo import save_block_version
from promptops_app.services.ce_review.anchoring import locate

_log = logging.getLogger(__name__)
_FIX_SOURCE = "ce_review_fix"


def _now():
    return datetime.now(timezone.utc)


def _audit(db, actor: str, action: str, review, metadata: dict) -> None:
    """Fail-safe audit write for a review action (logged against the generation;
    any specific block id is carried in metadata)."""
    from promptops_app.services.audit_service import log_audit_event
    log_audit_event(db, actor or "cas-user", action, entity_type="generation",
                    entity_id=review.generation_id, project_id=review.project_id,
                    course_id=review.course_id, metadata={**metadata, "review_id": review.id})


def _restale(db, review_id: int, block_id: int, content: str) -> None:
    """Mark any still-open finding in this block whose quote no longer resolves."""
    from promptops_app.database import ReviewFinding
    db.flush()   # persist just-applied statuses so they're excluded below (autoflush-independent)
    rows = db.query(ReviewFinding).filter(
        ReviewFinding.review_id == review_id,
        ReviewFinding.block_id == block_id,
        ReviewFinding.status == "open",
    ).all()
    for f in rows:
        if locate(content, f.anchor_quote)[0] < 0:
            f.status = "stale"


def _swap(block, finding, actor, version_id) -> tuple[bool, str | None]:
    """Locate + swap one finding's quote in *block.content*. No snapshot/commit.

    Marks the finding stale/apply_failed on a miss. Returns (ok, reason).
    """
    s, e = locate(block.content or "", finding.anchor_quote)
    if s < 0:
        finding.status = "stale"
        return False, "The content changed since the review — re-review needed."
    if "\n" in (block.content[s:e]) or "\n" in (finding.suggested_replacement or ""):
        finding.status = "apply_failed"
        return False, "This fix spans multiple lines — edit it manually."
    block.content = block.content[:s] + finding.suggested_replacement + block.content[e:]
    block.updated_at = _now()
    finding.status = "applied"
    finding.applied_by = actor
    finding.applied_at = _now()
    finding.applied_version_id = version_id
    return True, None


def _eligible(finding) -> bool:
    return finding.status == "open" and finding.auto_applicable and bool(finding.suggested_replacement)


def _recompute_verdict(db, review) -> None:
    """Refresh the stored verdict after findings change (cheap; no re-review needed)."""
    from promptops_app.services.ce_review.verdict import compute
    db.flush()   # persist pending finding-status changes so compute() counts them
    v = compute(db, review)
    review.verdict = v["verdict"]
    if isinstance(review.counts, dict):
        review.counts = {**review.counts, "blockers": v["blockers"]}   # reassign so JSON persists


def apply_one(db, review, finding, actor: str) -> tuple[bool, str | None]:
    """Apply a single finding (snapshots the block first). Returns (ok, reason)."""
    from promptops_app.database import Block
    if not _eligible(finding):
        return False, "This finding has no one-click fix."
    block = db.query(Block).filter(Block.id == finding.block_id).first()
    if not block:
        return False, "Block not found."
    # Snapshot first so the pre-fix content is restorable (AC-8).
    ver = save_block_version(db, block, _FIX_SOURCE, f"CE fix #{finding.id}", actor, commit=False)
    ok, reason = _swap(block, finding, actor, ver.id)
    if ok:
        _restale(db, review.id, finding.block_id, block.content)
    _recompute_verdict(db, review)
    db.commit()
    if ok:
        _audit(db, actor, "review.fix_applied", review, {"finding_id": finding.id, "block_id": finding.block_id})
    return ok, reason


def apply_many(db, review, finding_ids: list[int] | None, actor: str) -> dict:
    """Apply selected findings (or all eligible when *finding_ids* is None).

    One restore point per block. Returns {applied, skipped, stale}.
    """
    from promptops_app.database import Block, ReviewFinding
    q = db.query(ReviewFinding).filter(ReviewFinding.review_id == review.id)
    if finding_ids is not None:
        q = q.filter(ReviewFinding.id.in_(finding_ids))
    findings = [f for f in q.all() if _eligible(f)]

    summary = {"applied": 0, "skipped": 0, "stale": 0}
    by_block: dict[int, list] = {}
    for f in findings:
        by_block.setdefault(f.block_id, []).append(f)

    for block_id, group in by_block.items():
        block = db.query(Block).filter(Block.id == block_id).first()
        if not block:
            summary["skipped"] += len(group)
            continue
        # One snapshot per block before its batch of swaps.
        ver = save_block_version(db, block, _FIX_SOURCE, "CE fixes (batch)", actor, commit=False)
        for f in group:
            ok, _ = _swap(block, f, actor, ver.id)   # fresh locate each time (offsets shift)
            if ok:
                summary["applied"] += 1
            elif f.status == "stale":
                summary["stale"] += 1
            else:
                summary["skipped"] += 1
        _restale(db, review.id, block_id, block.content)
        db.commit()

    _recompute_verdict(db, review)
    db.commit()
    if summary["applied"]:
        _audit(db, actor, "review.fix_applied", review, {"batch": summary})
    _log.info("ce_fixes_applied review=%s %s", review.id, summary)
    return summary


def dismiss(db, review, finding, actor: str, reason: str | None = None) -> tuple[bool, str | None]:
    """Dismiss a finding — content unchanged (AC-7)."""
    if finding.status in ("applied", "dismissed"):
        return False, "This finding is already resolved."
    from promptops_app.database import ReviewDismissal
    finding.status = "dismissed"
    finding.dismissed_by = actor
    finding.dismissed_at = _now()
    finding.dismiss_reason = reason or None
    # Persist per-lesson so a re-review keeps it dismissed (carry-forward).
    if finding.fingerprint and not db.query(ReviewDismissal).filter_by(
            generation_id=review.generation_id, fingerprint=finding.fingerprint).first():
        db.add(ReviewDismissal(generation_id=review.generation_id, project_id=review.project_id,
                               fingerprint=finding.fingerprint, dismissed_by=actor,
                               dismissed_at=_now(), reason=reason or None))
    _recompute_verdict(db, review)
    db.commit()
    _audit(db, actor, "review.finding_dismissed", review, {"finding_id": finding.id})
    return True, None
