"""CE review run router (Step 2 — run skeleton).

  Start (or reuse) a review of a lesson  → POST /api/v1/reviews
  Get one review's status                → GET  /api/v1/reviews/{review_id}
  A generation's review history          → GET  /api/v1/reviews?generation_id=

Starting a review assembles the generation's blocks, fingerprints them, and —
if an identical completed run already exists — returns it instead of launching a
new one. Otherwise a background job (``review.run``) runs the review; poll
GET /api/v1/jobs/{job_id} for progress.

Gated by ``editor.edit`` and tenant-scoped. Mounted only when ``CE_REVIEW_ENABLED``
is true (see router.py).
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.dependencies import get_db, get_tenant_context, require_permission
from app.core.exceptions import NotFoundError, ValidationError
from app.core.tenant_context import apply_tenant_filter, get_scoped_or_404
from app.schemas.review import (
    ApplyManyRequest,
    ApplySummary,
    ChecklistResultRead,
    ContentReviewRead,
    DismissRequest,
    FindingRead,
    ReviewStartRequest,
    ReviewStartResponse,
)

_log = logging.getLogger(__name__)
router = APIRouter()


@router.post(
    "",
    response_model=ReviewStartResponse,
    status_code=202,
    summary="Start (or reuse) a CE review of a lesson",
)
def start_review(
    body: ReviewStartRequest,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("editor.edit")),
    tenant=Depends(get_tenant_context),
) -> ReviewStartResponse:
    from promptops_app.database import Generation
    from promptops_app.jobs import dispatch, review_jobs
    from promptops_app.repositories import job_repository
    from promptops_app.services.ce_review.review_runner import ReviewError, prepare_review

    tenant_id, is_platform_admin = tenant
    generation = get_scoped_or_404(db, Generation, body.generation_id, tenant_id, is_platform_admin)

    try:
        review, reused = prepare_review(
            db, generation, actor=current_user.username, model_choice=body.model_choice,
        )
    except ReviewError as exc:
        raise ValidationError(str(exc)) from exc

    if reused:
        _log.info("review_reused user=%s generation=%d review=%d",
                  current_user.username, generation.id, review.id)
        return ReviewStartResponse(
            review_id=review.id, run_status=review.run_status, reused=True,
        )

    job_id = job_repository.create_job(
        db,
        user_name=current_user.username,
        request_params={"review_id": review.id, "user_name": current_user.username},
        project_id=review.project_id,
        course_id=review.course_id,
        job_type="ce_review",
    )
    review.job_id = job_id
    db.commit()
    dispatch.submit(review_jobs.run_ce_review_job, job_id)

    _log.info("review_started user=%s generation=%d review=%d job=%s",
              current_user.username, generation.id, review.id, job_id)
    return ReviewStartResponse(
        review_id=review.id, run_status=review.run_status, reused=False,
        job_id=job_id, status_url=f"/api/v1/jobs/{job_id}",
    )


@router.get(
    "/{review_id}",
    response_model=ContentReviewRead,
    summary="Get one review run's status and results",
)
def get_review(
    review_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("editor.edit")),
    tenant=Depends(get_tenant_context),
) -> ContentReviewRead:
    from promptops_app.database import ContentReview
    tenant_id, is_platform_admin = tenant
    review = get_scoped_or_404(db, ContentReview, review_id, tenant_id, is_platform_admin)
    return ContentReviewRead.model_validate(review)


@router.get(
    "/{review_id}/checklist-results",
    response_model=list[ChecklistResultRead],
    summary="Non-pass checklist rule outcomes for a review",
)
def get_checklist_results(
    review_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("editor.edit")),
    tenant=Depends(get_tenant_context),
) -> list[ChecklistResultRead]:
    from promptops_app.database import ContentReview, ReviewChecklistResult
    tenant_id, is_platform_admin = tenant
    get_scoped_or_404(db, ContentReview, review_id, tenant_id, is_platform_admin)  # tenant gate
    rows = (
        db.query(ReviewChecklistResult)
        .filter(ReviewChecklistResult.review_id == review_id)
        .order_by(ReviewChecklistResult.status.asc(), ReviewChecklistResult.id.asc())
        .all()
    )
    return [ChecklistResultRead.model_validate(r) for r in rows]


@router.get(
    "/{review_id}/findings",
    response_model=list[FindingRead],
    summary="Detected issues for a review (open first, most severe first)",
)
def get_findings(
    review_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("editor.edit")),
    tenant=Depends(get_tenant_context),
) -> list[FindingRead]:
    from promptops_app.database import ContentReview, ReviewFinding
    tenant_id, is_platform_admin = tenant
    get_scoped_or_404(db, ContentReview, review_id, tenant_id, is_platform_admin)  # tenant gate
    _rank = {"blocker": 0, "major": 1, "minor": 2}
    rows = db.query(ReviewFinding).filter(ReviewFinding.review_id == review_id).all()
    # open first, then by severity — done in Python to keep the query index-simple.
    rows.sort(key=lambda r: (r.status != "open", _rank.get(r.severity, 3), r.id))
    return [FindingRead.model_validate(r) for r in rows]


def _get_review_finding(db, review_id, finding_id, tenant):
    """Load a finding scoped to a tenant-visible review, or 404."""
    from promptops_app.database import ContentReview, ReviewFinding
    tenant_id, is_platform_admin = tenant
    get_scoped_or_404(db, ContentReview, review_id, tenant_id, is_platform_admin)  # tenant gate
    finding = db.query(ReviewFinding).filter(
        ReviewFinding.id == finding_id, ReviewFinding.review_id == review_id,
    ).first()
    if finding is None:
        raise NotFoundError("ReviewFinding", finding_id)
    return finding


@router.post(
    "/{review_id}/findings/{finding_id}/apply",
    response_model=FindingRead,
    summary="Apply one finding's fix (deterministic swap, snapshots the block)",
)
def apply_finding(
    review_id: int,
    finding_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("editor.edit")),
    tenant=Depends(get_tenant_context),
) -> FindingRead:
    from promptops_app.database import ContentReview
    from promptops_app.services.ce_review.fix_service import apply_one
    finding = _get_review_finding(db, review_id, finding_id, tenant)
    review = db.query(ContentReview).filter(ContentReview.id == review_id).first()
    apply_one(db, review, finding, current_user.username)  # status carries the outcome
    db.refresh(finding)
    return FindingRead.model_validate(finding)


@router.post(
    "/{review_id}/findings/apply",
    response_model=ApplySummary,
    summary="Apply selected findings, or all eligible (one restore point per block)",
)
def apply_findings(
    review_id: int,
    body: ApplyManyRequest,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("editor.edit")),
    tenant=Depends(get_tenant_context),
) -> ApplySummary:
    from promptops_app.database import ContentReview
    from promptops_app.services.ce_review.fix_service import apply_many
    tenant_id, is_platform_admin = tenant
    review = get_scoped_or_404(db, ContentReview, review_id, tenant_id, is_platform_admin)
    summary = apply_many(db, review, body.finding_ids, current_user.username)
    return ApplySummary(**summary)


@router.post(
    "/{review_id}/findings/{finding_id}/dismiss",
    response_model=FindingRead,
    summary="Dismiss a finding (content unchanged)",
)
def dismiss_finding(
    review_id: int,
    finding_id: int,
    body: DismissRequest,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("editor.edit")),
    tenant=Depends(get_tenant_context),
) -> FindingRead:
    from promptops_app.database import ContentReview
    from promptops_app.services.ce_review.fix_service import dismiss
    finding = _get_review_finding(db, review_id, finding_id, tenant)
    review = db.query(ContentReview).filter(ContentReview.id == review_id).first()
    dismiss(db, review, finding, current_user.username, body.reason)
    db.refresh(finding)
    return FindingRead.model_validate(finding)


@router.get(
    "",
    response_model=list[ContentReviewRead],
    summary="List a generation's review history (newest first)",
)
def list_reviews(
    generation_id: int = Query(..., description="The generation whose reviews to list."),
    limit: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("editor.edit")),
    tenant=Depends(get_tenant_context),
) -> list[ContentReviewRead]:
    from promptops_app.database import ContentReview
    tenant_id, is_platform_admin = tenant

    q = apply_tenant_filter(db.query(ContentReview), ContentReview, tenant_id, is_platform_admin)
    rows = (
        q.filter(ContentReview.generation_id == generation_id)
         .order_by(ContentReview.created_at.desc())
         .limit(limit)
         .all()
    )
    return [ContentReviewRead.model_validate(r) for r in rows]
