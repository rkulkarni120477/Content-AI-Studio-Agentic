"""Compute a review's overall verdict.

Only failed MANDATORY checklist rules block (decision 6). Grammar/style/findings
are warnings, never blockers. Verdict is one of:
  changes_required  — a mandatory rule failed
  warnings          — no blocker, but non-pass rules or open findings remain
  ready             — mandatory checks pass and nothing remains (green badge)
"""
from __future__ import annotations


def compute(db, review) -> dict:
    """Return {verdict, blockers} for a review."""
    from promptops_app.database import ReviewChecklist, ReviewChecklistResult, ReviewFinding

    results = db.query(ReviewChecklistResult).filter(
        ReviewChecklistResult.review_id == review.id).all()

    # Which rules are mandatory (checklist basis only).
    mandatory = set()
    if review.checklist_id:
        cl = db.query(ReviewChecklist).filter(ReviewChecklist.id == review.checklist_id).first()
        if cl:
            mandatory = {i.item_key for i in cl.items if i.is_mandatory}

    blockers = sum(1 for r in results if r.status == "fail" and r.item_key in mandatory)
    if blockers:
        return {"verdict": "changes_required", "blockers": blockers}

    open_findings = db.query(ReviewFinding).filter(
        ReviewFinding.review_id == review.id, ReviewFinding.status == "open").count()
    remaining = open_findings > 0 or any(r.status in ("fail", "warning") for r in results)
    return {"verdict": "warnings" if remaining else "ready", "blockers": 0}
