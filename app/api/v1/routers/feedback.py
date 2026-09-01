"""
Feedback router — reviewer-feedback upload, AI extraction, recommendations,
module mapping, and apply-to-regenerate.

A reviewer-feedback document (PPTX / DOCX / PDF / XLSX / TXT) is uploaded
against a course (optionally scoped to a module blueprint), parsed to text,
and analysed by the LLM into structured feedback items shown in a table on
the Feedback workspace tab. Items can receive AI recommendations, be remapped
to a module, and be applied to regenerate a module's content blocks.

  Upload & analyse a document → POST   /api/v1/feedback/analyze
  List a course's feedback     → GET    /api/v1/feedback
  Recommend (AI) for items     → POST   /api/v1/feedback/recommend
  Remap an item's module       → PATCH  /api/v1/feedback/items/{item_id}
  Apply items → regenerate     → POST   /api/v1/feedback/apply
  Delete one item              → DELETE /api/v1/feedback/items/{item_id}
  Bulk-delete items            → POST   /api/v1/feedback/bulk-delete

RBAC permissions used
---------------------
  feedback.view      → List feedback items
  feedback.upload    → Upload, analyse, remap, apply
  feedback.recommend → Generate AI recommendations for feedback items
  feedback.delete    → Delete feedback items
  editor.edit        → Required in addition for apply (regenerates blocks)

The extraction and recommendation both use the model the project already uses —
the course's ``config_model_choice`` (falling back to the catalog default) —
never a hardcoded model. See ``promptops_app.services.feedback_service``.
"""

from __future__ import annotations

import json
import logging

from fastapi import APIRouter, Depends, File, Form, Query, UploadFile
from sqlalchemy.orm import Session

from app.core.dependencies import get_db, get_tenant_context, require_permission
from app.core.exceptions import NotFoundError, PermissionDeniedError, ValidationError
from app.core.permissions import effective_rbac_check
from app.core.tenant_context import apply_tenant_filter, get_scoped_or_404
from app.schemas.common import JobAcceptedResponse
from app.schemas.feedback import (
    FeedbackAnalyzeResponse,
    FeedbackApplyBlockResult,
    FeedbackApplyRequest,
    FeedbackApplyResponse,
    FeedbackBulkDeleteRequest,
    FeedbackBulkDeleteResponse,
    FeedbackDocumentRead,
    FeedbackItemRead,
    FeedbackItemUpdateRequest,
    FeedbackListResponse,
    FeedbackRecommendRequest,
    FeedbackRecommendResponse,
)

_log = logging.getLogger(__name__)
router = APIRouter()


def _parse_refs(raw) -> list[str]:
    """Decode the stored referenced-block labels (JSON array) → list[str]."""
    if not raw:
        return []
    try:
        val = json.loads(raw)
        return [str(x) for x in val] if isinstance(val, list) else []
    except (ValueError, TypeError):
        return []


def _module_label(blueprint_id: int | None, labels: dict[int, str]) -> str:
    if blueprint_id is None:
        return "Entire course"
    return labels.get(blueprint_id, f"Module (id {blueprint_id})")


def _to_item_read(item, labels: dict[int, str] | None = None) -> FeedbackItemRead:
    """Build a row DTO, resolving the source document filename and module label."""
    labels = labels or {}
    return FeedbackItemRead(
        id=item.id,
        document_id=item.document_id,
        course_id=item.course_id,
        blueprint_id=item.blueprint_id,
        module_label=_module_label(item.blueprint_id, labels),
        feedback_text=item.feedback_text,
        source_location=item.source_location,
        theme=item.theme,
        sentiment=item.sentiment,
        priority=item.priority,
        document_name=item.document.filename if item.document else None,
        created_at=item.created_at,
        recommendation=item.recommendation,
        recommendation_refs=_parse_refs(item.recommendation_refs),
        recommendation_model=item.recommendation_model,
        recommendation_status=item.recommendation_status or "none",
        recommended_at=item.recommended_at,
    )


def _validate_blueprint_for_course(db, blueprint_id: int | None, course, tenant) -> object | None:
    """Return ModuleBlueprint if valid for this course, else raise. None → course-wide."""
    if blueprint_id is None:
        return None
    from promptops_app.database import ModuleBlueprint

    tenant_id, is_platform_admin = tenant
    bp = get_scoped_or_404(db, ModuleBlueprint, blueprint_id, tenant_id, is_platform_admin)
    if bp.course_id is not None and bp.course_id != course.id:
        raise ValidationError(
            f"Blueprint {blueprint_id} does not belong to course {course.id}."
        )
    return bp


@router.post(
    "/analyze",
    response_model=FeedbackAnalyzeResponse,
    status_code=201,
    summary="Upload a feedback document and extract feedback with AI",
    description=(
        "Accepts PPTX, DOCX, PDF, XLSX, or TXT. The file is parsed to text, "
        "analysed by the LLM (the course's configured model), and the extracted "
        "feedback items are stored linked to the course. Optional blueprint_id "
        "scopes the upload (and its items) to a module; omit for entire course."
    ),
)
async def analyze_feedback(
    file: UploadFile = File(...),
    course_id: int = Form(..., description="Course the feedback is linked to."),
    blueprint_id: int | None = Form(
        default=None,
        description="Module blueprint id, or omit/null for entire course.",
    ),
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("feedback.upload")),
    tenant=Depends(get_tenant_context),
) -> FeedbackAnalyzeResponse:
    from promptops_app.database import Course
    from promptops_app.repositories import feedback_repository
    from promptops_app.services.feedback_service import (
        FeedbackExtractionError,
        analyze_and_store,
    )

    tenant_id, is_platform_admin = tenant
    course = get_scoped_or_404(db, Course, course_id, tenant_id, is_platform_admin)
    _validate_blueprint_for_course(db, blueprint_id, course, tenant)

    raw_bytes = await file.read()
    if not raw_bytes:
        raise ValidationError(f"Uploaded file '{file.filename}' is empty.")

    try:
        doc, items = analyze_and_store(
            db,
            raw_bytes=raw_bytes,
            filename=file.filename or "feedback",
            project_id=course.project_id,
            course=course,
            created_by=current_user.username,
            blueprint_id=blueprint_id,
        )
    except FeedbackExtractionError as exc:
        db.rollback()
        raise ValidationError(str(exc)) from exc

    db.commit()
    db.refresh(doc)

    labels = feedback_repository.get_blueprint_labels(
        db, {i.blueprint_id for i in items if i.blueprint_id},
    )

    _log.info(
        "feedback_analyzed user=%s course=%d doc_id=%d items=%d blueprint_id=%s",
        current_user.username, course_id, doc.id, len(items), blueprint_id,
    )
    return FeedbackAnalyzeResponse(
        document=FeedbackDocumentRead.model_validate(doc),
        items=[_to_item_read(i, labels) for i in items],
    )


@router.get(
    "",
    response_model=FeedbackListResponse,
    summary="List feedback items",
    description=(
        "Returns active feedback items scoped to the caller's tenant, optionally "
        "filtered by course and/or module blueprint. Pass course_wide=true to "
        "list only entire-course items."
    ),
)
def list_feedback(
    course_id: int | None = Query(default=None, description="Filter to one course."),
    blueprint_id: int | None = Query(
        default=None,
        description="Filter to one module blueprint. Ignored when course_wide=true.",
    ),
    course_wide: bool = Query(
        default=False,
        description="When true, return only items with no module mapping.",
    ),
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("feedback.view")),
    tenant=Depends(get_tenant_context),
) -> FeedbackListResponse:
    from promptops_app.database import FeedbackItem
    from promptops_app.repositories import feedback_repository

    tenant_id, is_platform_admin = tenant
    q = feedback_repository.list_active_items(
        db,
        course_id=course_id,
        blueprint_id=None if course_wide else blueprint_id,
        course_wide_only=course_wide,
    )
    q = apply_tenant_filter(q, FeedbackItem, tenant_id, is_platform_admin)
    items = q.all()
    labels = feedback_repository.get_blueprint_labels(
        db, {i.blueprint_id for i in items if i.blueprint_id},
    )
    return FeedbackListResponse(
        items=[_to_item_read(i, labels) for i in items],
        total=len(items),
    )


@router.post(
    "/recommend",
    response_model=FeedbackRecommendResponse,
    summary="Generate AI recommendations for feedback items",
    description=(
        "For each selected feedback item, the AI reads the item against the "
        "relevant generated content of its course and proposes a concrete "
        "revision. Works for one item or many (one recommendation per item). "
        "Uses the course's configured model, falling back to the catalog default."
    ),
)
def recommend_feedback(
    body: FeedbackRecommendRequest,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("feedback.recommend")),
    tenant=Depends(get_tenant_context),
) -> FeedbackRecommendResponse:
    from promptops_app.database import FeedbackItem
    from promptops_app.repositories import feedback_repository
    from promptops_app.services.feedback_service import recommend_for_items

    tenant_id, is_platform_admin = tenant
    q = feedback_repository.active_items_by_ids(db, body.item_ids)
    q = apply_tenant_filter(q, FeedbackItem, tenant_id, is_platform_admin)
    items = q.all()
    if not items:
        raise NotFoundError("No matching feedback items found for this tenant.")

    try:
        recommend_for_items(
            db, items=items, created_by=current_user.username,
            guidance=body.guidance, model_override=body.model_choice,
        )
    except Exception as exc:
        db.rollback()
        _log.exception("feedback_recommend_failed user=%s", current_user.username)
        raise ValidationError("AI recommendation failed. Please try again.") from exc

    db.commit()
    for item in items:
        db.refresh(item)

    labels = feedback_repository.get_blueprint_labels(
        db, {i.blueprint_id for i in items if i.blueprint_id},
    )
    recommended = sum(1 for i in items if i.recommendation_status == "ready")
    failed = sum(1 for i in items if i.recommendation_status == "error")
    _log.info(
        "feedback_recommended user=%s requested=%d ok=%d failed=%d",
        current_user.username, len(items), recommended, failed,
    )
    return FeedbackRecommendResponse(
        items=[_to_item_read(i, labels) for i in items],
        recommended=recommended,
        failed=failed,
    )


@router.patch(
    "/items/{item_id}",
    response_model=FeedbackItemRead,
    summary="Remap a feedback item to a module (or entire course)",
)
def update_feedback_item(
    item_id: int,
    body: FeedbackItemUpdateRequest,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("feedback.upload")),
    tenant=Depends(get_tenant_context),
) -> FeedbackItemRead:
    from promptops_app.database import Course, FeedbackItem
    from promptops_app.repositories import feedback_repository

    tenant_id, is_platform_admin = tenant
    item = get_scoped_or_404(db, FeedbackItem, item_id, tenant_id, is_platform_admin)
    if item.status != "active":
        raise NotFoundError(f"Feedback item {item_id} not found.")

    if body.blueprint_id is not None:
        if item.course_id is None:
            raise ValidationError("Cannot map feedback to a module without a course.")
        course = get_scoped_or_404(db, Course, item.course_id, tenant_id, is_platform_admin)
        _validate_blueprint_for_course(db, body.blueprint_id, course, tenant)

    item.blueprint_id = body.blueprint_id
    db.commit()
    db.refresh(item)

    labels = feedback_repository.get_blueprint_labels(
        db, {item.blueprint_id} if item.blueprint_id else set(),
    )
    _log.info(
        "feedback_item_remapped user=%s item_id=%d blueprint_id=%s",
        current_user.username, item_id, body.blueprint_id,
    )
    return _to_item_read(item, labels)


@router.post(
    "/apply",
    response_model=JobAcceptedResponse,
    status_code=202,
    summary="Queue applying selected feedback items to regenerate a module's blocks",
    description=(
        "Validates the selection and target module, then queues a background job "
        "that regenerates the module's latest content blocks against the selected "
        "feedback (one LLM call per block). Returns a job_id immediately; poll "
        "GET /jobs/{job_id} to completion, then GET /feedback/apply-result/{job_id} "
        "for the regenerate summary. Running inline previously exceeded the browser "
        "request timeout on modules with several blocks."
    ),
)
def apply_feedback(
    body: FeedbackApplyRequest,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("editor.edit")),
    tenant=Depends(get_tenant_context),
) -> JobAcceptedResponse:
    from promptops_app.database import Course, FeedbackItem
    from promptops_app.jobs import dispatch, regen_jobs
    from promptops_app.repositories import job_repository
    from promptops_app.services.feedback_service import resolve_apply_blueprint_id

    if not effective_rbac_check(current_user, "feedback.upload"):
        raise PermissionDeniedError("feedback.upload", user_role=current_user.role)

    tenant_id, is_platform_admin = tenant
    q = db.query(FeedbackItem).filter(
        FeedbackItem.id.in_(body.item_ids),
        FeedbackItem.status == "active",
    )
    q = apply_tenant_filter(q, FeedbackItem, tenant_id, is_platform_admin)
    items = q.all()
    if not items:
        raise ValidationError("No active feedback items found for the given ids.")
    if len(items) != len(set(body.item_ids)):
        raise ValidationError("One or more feedback item ids were not found or are inactive.")

    course_ids = {i.course_id for i in items}
    if len(course_ids) != 1 or None in course_ids:
        raise ValidationError("All selected feedback items must belong to the same course.")
    course_id = next(iter(course_ids))
    course = get_scoped_or_404(db, Course, course_id, tenant_id, is_platform_admin)

    target_bp_id = resolve_apply_blueprint_id(items, body.blueprint_id)
    if target_bp_id is None:
        raise ValidationError(
            "Select a target module - selected items are course-wide or map to "
            "more than one module."
        )

    blueprint = _validate_blueprint_for_course(db, target_bp_id, course, tenant)
    if blueprint is None:
        raise ValidationError("A target module blueprint is required.")

    # One LLM call per block can run for minutes; running it inline held the
    # request open past the browser's 120s timeout, which then cancelled it
    # while the server kept working. Enqueue and hand back a job to poll.
    job_id = job_repository.create_job(
        db,
        user_name=current_user.username,
        request_params={
            "item_ids": [i.id for i in items],
            "blueprint_id": target_bp_id,
            "course_id": course_id,
            "user_name": current_user.username,
        },
        project_id=getattr(course, "project_id", None),
        course_id=course_id,
        job_type="apply_feedback",
    )
    dispatch.submit(regen_jobs.run_apply_feedback_job, job_id)

    _log.info(
        "feedback_apply_queued user=%s course=%d blueprint_id=%d items=%d job=%s",
        current_user.username, course_id, target_bp_id, len(items), job_id,
    )
    return JobAcceptedResponse(
        job_id=job_id,
        status="queued",
        status_url=f"/api/v1/jobs/{job_id}",
    )


@router.get(
    "/apply-result/{job_id}",
    response_model=FeedbackApplyResponse,
    summary="Read the summary of a completed apply-feedback job",
    description=(
        "Returns the regenerate summary (which blocks were regenerated, how many "
        "were skipped, the target module) recorded by an apply-feedback job. Call "
        "once GET /jobs/{job_id} reports 'completed'."
    ),
)
def get_apply_feedback_result(
    job_id: str,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("feedback.view")),
) -> FeedbackApplyResponse:
    from promptops_app.jobs.job_status import JobStatus
    from promptops_app.repositories import job_repository

    job = job_repository.get_job(db, job_id)
    # Scope to the creator: the summary names this user's regenerated blocks, and
    # the status poll has no ownership check, so a guessed id must not leak them.
    if not job or job.job_type != "apply_feedback" or job.created_by != current_user.username:
        raise NotFoundError("Apply-feedback job", job_id)
    if job.status == JobStatus.FAILED:
        raise ValidationError(job.error_message or "Applying feedback failed.")
    if job.status != JobStatus.COMPLETED or not job.result_json:
        raise ValidationError("Apply-feedback job has not completed yet.")

    data = json.loads(job.result_json)
    return FeedbackApplyResponse(
        instruction=data.get("instruction", ""),
        regenerated=[FeedbackApplyBlockResult(**r) for r in data.get("regenerated", [])],
        skipped=data.get("skipped", 0),
        blueprint_id=data.get("blueprint_id"),
        module_label=data.get("module_label", "module"),
    )


@router.delete(
    "/items/{item_id}",
    status_code=204,
    summary="Delete a feedback item",
)
def delete_feedback_item(
    item_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("feedback.delete")),
    tenant=Depends(get_tenant_context),
) -> None:
    from promptops_app.database import FeedbackItem

    tenant_id, is_platform_admin = tenant
    item = get_scoped_or_404(db, FeedbackItem, item_id, tenant_id, is_platform_admin)
    item.status = "archived"
    if item.document is not None and (item.document.item_count or 0) > 0:
        item.document.item_count -= 1
    db.commit()
    _log.info("feedback_item_deleted user=%s item_id=%d", current_user.username, item_id)


@router.post(
    "/bulk-delete",
    response_model=FeedbackBulkDeleteResponse,
    summary="Delete multiple feedback items",
)
def bulk_delete_feedback(
    body: FeedbackBulkDeleteRequest,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("feedback.delete")),
    tenant=Depends(get_tenant_context),
) -> FeedbackBulkDeleteResponse:
    from promptops_app.database import FeedbackItem

    tenant_id, is_platform_admin = tenant
    q = db.query(FeedbackItem).filter(
        FeedbackItem.id.in_(body.ids),
        FeedbackItem.status == "active",
    )
    q = apply_tenant_filter(q, FeedbackItem, tenant_id, is_platform_admin)
    items = q.all()
    for item in items:
        item.status = "archived"
        if item.document is not None and (item.document.item_count or 0) > 0:
            item.document.item_count -= 1
    db.commit()
    _log.info("feedback_bulk_deleted user=%s count=%d", current_user.username, len(items))
    return FeedbackBulkDeleteResponse(deleted=len(items))
