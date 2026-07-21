"""
Feedback router — reviewer-feedback upload, AI extraction, and curation.

A reviewer-feedback document (PPTX / DOCX / PDF / XLSX / TXT) is uploaded
against a course, parsed to text, and analysed by the LLM into structured
feedback items shown in a table on the Feedback workspace tab. Items are
linked to the course and stored per-tenant.

  Upload & analyse a document → POST   /api/v1/feedback/analyze
  List a course's feedback     → GET    /api/v1/feedback
  Delete one item              → DELETE /api/v1/feedback/items/{item_id}
  Bulk-delete items            → POST   /api/v1/feedback/bulk-delete

RBAC permissions used
---------------------
  feedback.view    → List feedback items
  feedback.upload  → Upload and AI-analyse a document
  feedback.delete  → Delete feedback items

The extraction uses the model the project already uses — the course's
``config_model_choice`` (falling back to the catalog default) — never a
hardcoded model. See ``promptops_app.services.feedback_service``.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, File, Form, Query, UploadFile
from sqlalchemy.orm import Session

from app.core.dependencies import get_db, get_tenant_context, require_permission
from app.core.exceptions import NotFoundError, ValidationError
from app.core.tenant_context import apply_tenant_filter, get_scoped_or_404
from app.schemas.feedback import (
    FeedbackAnalyzeResponse,
    FeedbackBulkDeleteRequest,
    FeedbackBulkDeleteResponse,
    FeedbackDocumentRead,
    FeedbackItemRead,
    FeedbackListResponse,
)

_log = logging.getLogger(__name__)
router = APIRouter()


def _to_item_read(item) -> FeedbackItemRead:
    """Build a row DTO, resolving the source document filename."""
    return FeedbackItemRead(
        id=item.id,
        document_id=item.document_id,
        course_id=item.course_id,
        feedback_text=item.feedback_text,
        source_location=item.source_location,
        theme=item.theme,
        sentiment=item.sentiment,
        priority=item.priority,
        document_name=item.document.filename if item.document else None,
        created_at=item.created_at,
    )


@router.post(
    "/analyze",
    response_model=FeedbackAnalyzeResponse,
    status_code=201,
    summary="Upload a feedback document and extract feedback with AI",
    description=(
        "Accepts PPTX, DOCX, PDF, XLSX, or TXT. The file is parsed to text, "
        "analysed by the LLM (the course's configured model), and the extracted "
        "feedback items are stored linked to the course."
    ),
)
async def analyze_feedback(
    file: UploadFile = File(...),
    course_id: int = Form(..., description="Course the feedback is linked to."),
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("feedback.upload")),
    tenant=Depends(get_tenant_context),
) -> FeedbackAnalyzeResponse:
    from promptops_app.database import Course
    from promptops_app.services.feedback_service import (
        FeedbackExtractionError,
        analyze_and_store,
    )

    tenant_id, is_platform_admin = tenant
    # Tenant-safe course fetch — 404 if it isn't in the caller's project.
    course = get_scoped_or_404(db, Course, course_id, tenant_id, is_platform_admin)

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
        )
    except FeedbackExtractionError as exc:
        db.rollback()
        raise ValidationError(str(exc)) from exc

    db.commit()
    db.refresh(doc)

    _log.info(
        "feedback_analyzed user=%s course=%d doc_id=%d items=%d",
        current_user.username, course_id, doc.id, len(items),
    )
    return FeedbackAnalyzeResponse(
        document=FeedbackDocumentRead.model_validate(doc),
        items=[_to_item_read(i) for i in items],
    )


@router.get(
    "",
    response_model=FeedbackListResponse,
    summary="List feedback items",
    description="Returns active feedback items scoped to the caller's tenant, optionally filtered by course.",
)
def list_feedback(
    course_id: int | None = Query(default=None, description="Filter to one course."),
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("feedback.view")),
    tenant=Depends(get_tenant_context),
) -> FeedbackListResponse:
    from promptops_app.database import FeedbackItem
    from promptops_app.repositories import feedback_repository

    tenant_id, is_platform_admin = tenant
    q = feedback_repository.list_active_items(db, course_id=course_id)
    q = apply_tenant_filter(q, FeedbackItem, tenant_id, is_platform_admin)
    items = q.all()
    return FeedbackListResponse(
        items=[_to_item_read(i) for i in items],
        total=len(items),
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
