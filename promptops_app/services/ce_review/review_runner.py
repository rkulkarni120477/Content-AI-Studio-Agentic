"""CE review run orchestration.

``prepare_review`` is called from the request thread: it resolves the review
basis, assembles + fingerprints the lesson, returns an identical prior run when
nothing changed (skip-unchanged), or creates a fresh queued ``ContentReview``.

``execute_review`` is called from the background job: it runs the review passes
and finalises the row. Step 2 is the skeleton — the passes are added in Steps
3-4, so for now it simply completes the run with empty counts. The lifecycle,
skip-unchanged, and one-active-run guard are all live.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional, Tuple

from sqlalchemy.exc import IntegrityError

from promptops_app.services.ce_review.assembly import assemble_generation, fingerprint
from promptops_app.services.ce_review.review_basis import resolve_basis

_log = logging.getLogger(__name__)


class ReviewError(Exception):
    """A review cannot be started (no basis, empty content, already running)."""


_MAX_REVIEW_CHARS = 200_000   # lesson size ceiling for a single-pass review (#2)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _clear_orphaned_active(db, generation_id: int) -> None:
    """Mark active reviews whose job is terminal/missing as failed, so a crash or
    restart can't leave a generation permanently blocked by the one-active index."""
    from promptops_app.database import ContentReview, GenerationJob
    actives = db.query(ContentReview).filter(
        ContentReview.generation_id == generation_id,
        ContentReview.run_status.in_(("queued", "running")),
    ).all()
    for r in actives:
        job = db.query(GenerationJob).filter(GenerationJob.id == r.job_id).first() if r.job_id else None
        if job is None or job.status in ("completed", "failed", "cancelled"):
            r.run_status = "failed"
            r.error_message = "Interrupted (server restart or lost worker). Please run the review again."
            r.completed_at = _now()
    db.flush()


def prepare_review(
    db,
    generation,
    *,
    actor: str,
    model_choice: Optional[str] = None,
) -> Tuple["object", bool]:
    """Resolve basis, assemble + fingerprint, and return ``(review, reused)``.

    ``reused=True`` means an identical completed run already existed and is
    returned unchanged (no new row, no job). ``reused=False`` means a fresh
    queued ``ContentReview`` was created (the caller launches the job).

    Raises ``ReviewError`` when there is nothing to review against, no content,
    or a run is already active for this generation.
    """
    from promptops_app.database import ContentReview

    project_id = getattr(generation, "project_id", None)
    course_id = getattr(generation, "course_id", None)

    basis = resolve_basis(db, project_id=project_id, course_id=course_id)
    if not basis.is_reviewable:
        raise ReviewError(
            "No checklist or style rules to review against. Upload a CE checklist "
            "(or activate a style with writing rules) first."
        )

    text, parts = assemble_generation(generation)
    # "Empty" means no real body content — block labels alone are not reviewable.
    if not any((p.get("content") or "").strip() for p in parts):
        raise ReviewError("This lesson has no content to review yet.")

    fp = fingerprint(text, basis_version=basis.version)

    # Clear orphaned "active" reviews whose job has finished/vanished (e.g. server
    # restart) so the one-active-run index doesn't block every future Start (#4).
    _clear_orphaned_active(db, generation.id)

    # Skip-unchanged: an identical completed run → return it, no new work.
    existing = (
        db.query(ContentReview)
        .filter(
            ContentReview.generation_id == generation.id,
            ContentReview.content_fingerprint == fp,
            ContentReview.checklist_version == basis.version,
            ContentReview.run_status == "completed",
        )
        .order_by(ContentReview.created_at.desc())
        .first()
    )
    if existing is not None:
        _log.info("ce_review_reused generation=%s review=%s", generation.id, existing.id)
        return existing, True

    # Chain to the latest prior completed run for before/after delta (re-review).
    prior = (db.query(ContentReview)
             .filter(ContentReview.generation_id == generation.id,
                     ContentReview.run_status == "completed")
             .order_by(ContentReview.created_at.desc()).first())

    review = ContentReview(
        rerun_of_id=prior.id if prior else None,
        generation_id=generation.id,
        project_id=project_id,
        course_id=course_id,
        checklist_id=basis.checklist_id,
        review_basis=basis.basis,
        checklist_version=basis.version,
        content_fingerprint=fp,
        run_status="queued",
        model_used=model_choice,
        created_by=actor,
        created_at=_now(),
    )
    db.add(review)
    try:
        db.flush()   # assign id; the partial unique index fires here if a run is active
    except IntegrityError as exc:
        db.rollback()
        raise ReviewError(
            "A review is already running for this lesson. Wait for it to finish."
        ) from exc
    return review, False


def execute_review(db, review) -> None:
    """Run the review passes and finalise the row.

    Step 3: the checklist pass (per-rule Pass/Fail/Warning/NA). Issue detection
    (Step 4) plugs in alongside. Findings tallies stay zero until then.
    """
    from promptops_app.database import ContentReview, Generation, ReviewChecklist

    review.run_status = "running"
    review.started_at = _now()
    db.commit()

    # Re-assemble the lesson (job runs in its own session).
    generation = db.query(Generation).filter(Generation.id == review.generation_id).first()
    lesson, parts = assemble_generation(generation) if generation else ("", [])

    # Guard against silently reviewing only part of a very large lesson (#2): fail
    # with a clear message rather than truncate. Tunable; models are 1M-context so
    # this bounds cost, not capability. (Chunked grading is a possible follow-up.)
    if len(lesson) > _MAX_REVIEW_CHARS:
        review.run_status = "failed"
        review.error_message = (
            f"This lesson is too large to review in one pass ({len(lesson):,} characters, "
            f"limit {_MAX_REVIEW_CHARS:,}). Please split it into smaller lessons."
        )
        review.completed_at = _now()
        db.commit()
        _log.warning("ce_review_too_large review=%s chars=%d", review.id, len(lesson))
        return

    # Pass 1 — checklist evaluation (checklist basis only).
    checklist_tally = {"pass": 0, "fail": 0, "warning": 0, "na": 0}
    if review.review_basis == "checklist" and review.checklist_id and lesson.strip():
        checklist = db.query(ReviewChecklist).filter(ReviewChecklist.id == review.checklist_id).first()
        items = list(checklist.items) if checklist else []
        if items:
            from promptops_app.services.ce_review.checklist_pass import evaluate_checklist
            checklist_tally = evaluate_checklist(db, review, lesson, items, model_choice=review.model_used)

    # Pass 2 — issue detection (both bases).
    findings_tally = {"total": 0, "more": 0, "applied": 0, "dismissed": 0}
    if parts:
        from promptops_app.services.ce_review.issue_pass import detect_issues
        findings_tally = detect_issues(db, review, parts, model_choice=review.model_used)

    # If any AI check failed, the review is incomplete — mark it failed rather than
    # showing a false "Ready" from an empty result (#3).
    failed_calls = checklist_tally.get("failed_calls", 0) + findings_tally.get("failed_calls", 0)
    if failed_calls:
        review.counts = {"checklist": checklist_tally, "findings": findings_tally}
        review.run_status = "failed"
        review.error_message = "Some AI checks could not complete. Please run the review again."
        review.completed_at = _now()
        db.commit()
        _log.warning("ce_review_failed review=%s failed_calls=%d", review.id, failed_calls)
        return

    # Verdict from mandatory-rule fails (only these block "Ready for Approval").
    from promptops_app.services.ce_review.verdict import compute as compute_verdict
    v = compute_verdict(db, review)
    review.verdict = v["verdict"]
    counts = {"checklist": checklist_tally, "findings": findings_tally, "blockers": v["blockers"]}
    # Before/after delta vs the run this re-review chained from.
    if review.rerun_of_id:
        prior = db.query(ContentReview).filter(ContentReview.id == review.rerun_of_id).first()
        pc = (prior.counts or {}) if prior else {}
        counts["prev"] = {"findings": (pc.get("findings") or {}).get("total", 0),
                          "blockers": pc.get("blockers", 0)}
    review.counts = counts
    review.run_status = "completed"
    review.completed_at = _now()
    db.commit()

    # Audit trail (AC-11) — fail-safe, never breaks the run.
    from promptops_app.services.audit_service import log_audit_event
    log_audit_event(db, review.created_by or "cas-user", "content.reviewed",
                    entity_type="generation", entity_id=review.generation_id,
                    project_id=review.project_id, course_id=review.course_id,
                    metadata={"verdict": review.verdict, "counts": review.counts,
                              "review_id": review.id})
    _log.info("ce_review_completed review=%s basis=%s verdict=%s checklist=%s findings=%s",
              review.id, review.review_basis, review.verdict, checklist_tally, findings_tally)
