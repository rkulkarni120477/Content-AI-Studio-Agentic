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
    response_model=FeedbackApplyResponse,
    summary="Apply selected feedback items to regenerate a module's blocks",
)
def apply_feedback(
    body: FeedbackApplyRequest,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("editor.edit")),
    tenant=Depends(get_tenant_context),
) -> FeedbackApplyResponse:
    from promptops_app.database import Course, FeedbackItem
    from promptops_app.repositories import feedback_repository
    from promptops_app.services.feedback_service import (
        apply_feedback_to_module,
        resolve_apply_blueprint_id,
    )

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

    instruction, regenerated, skipped = apply_feedback_to_module(
        db,
        items=items,
        blueprint=blueprint,
        course=course,
        created_by=current_user.username,
    )
    db.commit()

    module_label = feedback_repository.format_module_label(blueprint)
    _log.info(
        "feedback_applied user=%s course=%d blueprint_id=%d items=%d regenerated=%d skipped=%d",
        current_user.username, course_id, target_bp_id, len(items),
        len(regenerated), skipped,
    )
    return FeedbackApplyResponse(
        instruction=instruction,
        regenerated=[FeedbackApplyBlockResult(**r) for r in regenerated],
        skipped=skipped,
        blueprint_id=target_bp_id,
        module_label=module_label,
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
