"""CE Review checklist router (Step 1).

Manages the per-client (tenant) CE checklist used by the CE Agent Review:

  Upload a checklist file (async split)  → POST   /api/v1/review-checklists/upload
  List the tenant's checklists           → GET    /api/v1/review-checklists
  Get the active checklist + rules       → GET    /api/v1/review-checklists/active
  Get one checklist + rules              → GET    /api/v1/review-checklists/{id}
  Edit one rule                          → PATCH  /api/v1/review-checklists/{id}/items/{item_id}
  Delete rule(s) / whole checklist       → DELETE /api/v1/review-checklists/{id}[/items/...]

The upload parses the file to text and stores it as a Document, then enqueues a
background job (``review.checklist_import``) that splits it into rules — poll
GET /api/v1/jobs/{job_id} for progress, as with every other async operation.

All routes are tenant-scoped and gated by the ``review.configure`` permission.
This router is only mounted when ``CE_REVIEW_ENABLED`` is true (see router.py),
so when the flag is off the app is behaviourally identical to today.
"""

from __future__ import annotations

import io
import logging

from fastapi import APIRouter, Depends, File, Form, Query, UploadFile
from sqlalchemy.orm import Session

from app.core.dependencies import get_db, get_tenant_context, require_permission
from app.core.exceptions import NotFoundError, ValidationError
from app.core.tenant_context import apply_tenant_filter, get_scoped_or_404
from app.schemas.common import JobAcceptedResponse
from app.schemas.review_checklist import (
    ChecklistItemIdsRequest,
    ChecklistItemUpdateRequest,
    DeleteResult,
    ReviewChecklistItemRead,
    ReviewChecklistRead,
    ReviewChecklistSummary,
)

_log = logging.getLogger(__name__)
router = APIRouter()


class _NamedBytesIO(io.BytesIO):
    """BytesIO with a ``.name`` — file_parser keys extension off the name."""

    def __init__(self, data: bytes, name: str):
        super().__init__(data)
        self.name = name


def _effective_project_id(tenant, explicit_project_id: int | None) -> int:
    """Resolve which project (tenant) the checklist belongs to.

    Regular users are pinned to their own project. A platform admin has no
    tenant of their own, so they must name the project explicitly.
    """
    tenant_id, is_platform_admin = tenant
    if tenant_id is not None:
        return int(tenant_id)
    if is_platform_admin and explicit_project_id is not None:
        return int(explicit_project_id)
    raise ValidationError(
        "No project context. A platform admin must supply project_id."
    )


def _to_read(checklist) -> ReviewChecklistRead:
    """Serialise a checklist with its rules."""
    return ReviewChecklistRead(
        id=checklist.id,
        project_id=checklist.project_id,
        name=checklist.name,
        version=checklist.version,
        status=checklist.status,
        item_count=len(checklist.items),
        source_document_id=checklist.source_document_id,
        created_by=checklist.created_by,
        created_at=checklist.created_at,
        updated_at=checklist.updated_at,
        items=[ReviewChecklistItemRead.model_validate(i) for i in checklist.items],
    )


def _get_checklist_or_404(db, checklist_id, tenant):
    from promptops_app.database import ReviewChecklist
    tenant_id, is_platform_admin = tenant
    return get_scoped_or_404(db, ReviewChecklist, checklist_id, tenant_id, is_platform_admin)


# ---------------------------------------------------------------------------
# Upload (async split)
# ---------------------------------------------------------------------------

@router.post(
    "/upload",
    response_model=JobAcceptedResponse,
    status_code=202,
    summary="Upload a CE checklist and split it into rules (async)",
    description=(
        "Accepts PDF, DOCX, TXT, or XLSX. The file is parsed to text and stored, "
        "then a background job splits it into individual rules using the AI. Poll "
        "GET /api/v1/jobs/{job_id}; on completion ``generation_id`` is the new "
        "checklist id. Rules import as non-mandatory."
    ),
)
def upload_checklist(
    file: UploadFile = File(...),
    name: str | None = Form(default=None, description="Checklist name; defaults to the filename."),
    project_id: int | None = Form(default=None, description="Platform admin only: target project."),
    model_choice: str | None = Form(default=None, description="Override the AI model; optional."),
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("review.configure")),
    tenant=Depends(get_tenant_context),
) -> JobAcceptedResponse:
    from promptops_app.database import Document
    from promptops_app.jobs import dispatch, review_jobs
    from promptops_app.parsers.file_parser import _parse_uploaded_file
    from promptops_app.repositories import job_repository

    pid = _effective_project_id(tenant, project_id)

    raw_bytes = file.file.read()
    if not raw_bytes:
        raise ValidationError(f"Uploaded file '{file.filename}' is empty.")

    parsed_name, content, err = _parse_uploaded_file(
        _NamedBytesIO(raw_bytes, file.filename or "checklist")
    )
    if err:
        raise ValidationError(f"Could not parse file '{file.filename}': {err}")
    if not (content or "").strip():
        raise ValidationError(
            f"No readable text found in '{file.filename}'. The file may be empty or image-only."
        )

    checklist_name = (name or "").strip() or parsed_name or "CE Checklist"

    # Persist the source file as a Document so the rules keep provenance and can
    # be re-split later. doc_tag="checklist" distinguishes it in the registry.
    doc = Document(
        filename=parsed_name or (file.filename or "checklist"),
        file_type=(file.content_type or None),
        doc_tag="checklist",
        content=content,
        uploaded_by=current_user.username,
    )
    db.add(doc)
    db.commit()
    db.refresh(doc)

    job_id = job_repository.create_job(
        db,
        user_name=current_user.username,
        request_params={
            "document_id": doc.id,
            "project_id": pid,
            "name": checklist_name,
            "model_choice": model_choice,
            "user_name": current_user.username,
        },
        project_id=pid,
        job_type="checklist_import",
    )
    dispatch.submit(review_jobs.run_checklist_import_job, job_id)

    _log.info(
        "checklist_upload_queued user=%s project=%d doc_id=%d job=%s",
        current_user.username, pid, doc.id, job_id,
    )
    return JobAcceptedResponse(
        job_id=job_id, status="queued", status_url=f"/api/v1/jobs/{job_id}",
    )


# ---------------------------------------------------------------------------
# Read
# ---------------------------------------------------------------------------

@router.get(
    "",
    response_model=list[ReviewChecklistSummary],
    summary="List the tenant's CE checklists (active first)",
)
def list_checklists(
    include_archived: bool = Query(default=False),
    project_id: int | None = Query(default=None, description="Scope to a project (needed for platform admins)."),
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("review.configure")),
    tenant=Depends(get_tenant_context),
) -> list[ReviewChecklistSummary]:
    from promptops_app.database import ReviewChecklist
    tenant_id, is_platform_admin = tenant

    q = apply_tenant_filter(db.query(ReviewChecklist), ReviewChecklist, tenant_id, is_platform_admin)
    if project_id is not None:
        q = q.filter(ReviewChecklist.project_id == project_id)   # scope to the viewed course's project
    if not include_archived:
        q = q.filter(ReviewChecklist.status == "active")
    rows = q.order_by(ReviewChecklist.status.asc(), ReviewChecklist.version.desc()).all()
    return [
        ReviewChecklistSummary(
            id=c.id, project_id=c.project_id, name=c.name, version=c.version,
            status=c.status, item_count=len(c.items), source_document_id=c.source_document_id,
            created_by=c.created_by, created_at=c.created_at, updated_at=c.updated_at,
        )
        for c in rows
    ]


@router.get(
    "/active",
    response_model=ReviewChecklistRead | None,
    summary="Get the tenant's active checklist and its rules",
    description="Returns the single active checklist for the caller's project, or null if none.",
)
def get_active_checklist(
    project_id: int | None = Query(default=None, description="Scope to a project (needed for platform admins)."),
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("review.configure")),
    tenant=Depends(get_tenant_context),
) -> ReviewChecklistRead | None:
    from promptops_app.database import ReviewChecklist
    tenant_id, is_platform_admin = tenant

    q = apply_tenant_filter(db.query(ReviewChecklist), ReviewChecklist, tenant_id, is_platform_admin)
    if project_id is not None:
        q = q.filter(ReviewChecklist.project_id == project_id)   # scope to the viewed course's project
    checklist = (
        q.filter(ReviewChecklist.status == "active")
         .order_by(ReviewChecklist.version.desc())
         .first()
    )
    return _to_read(checklist) if checklist else None


@router.get(
    "/{checklist_id}",
    response_model=ReviewChecklistRead,
    summary="Get one checklist and its rules",
)
def get_checklist(
    checklist_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("review.configure")),
    tenant=Depends(get_tenant_context),
) -> ReviewChecklistRead:
    checklist = _get_checklist_or_404(db, checklist_id, tenant)
    return _to_read(checklist)


# ---------------------------------------------------------------------------
# Edit rules
# ---------------------------------------------------------------------------

@router.patch(
    "/{checklist_id}/items/{item_id}",
    response_model=ReviewChecklistItemRead,
    summary="Edit one rule (text, section, guidance, mandatory flag, order)",
)
def update_item(
    checklist_id: int,
    item_id: int,
    body: ChecklistItemUpdateRequest,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("review.configure")),
    tenant=Depends(get_tenant_context),
) -> ReviewChecklistItemRead:
    from promptops_app.database import ReviewChecklistItem

    checklist = _get_checklist_or_404(db, checklist_id, tenant)  # tenant gate
    item = (
        db.query(ReviewChecklistItem)
        .filter(
            ReviewChecklistItem.id == item_id,
            ReviewChecklistItem.checklist_id == checklist.id,
        )
        .first()
    )
    if item is None:
        raise NotFoundError("ReviewChecklistItem", item_id)

    data = body.model_dump(exclude_unset=True)
    for field in ("rule_text", "section", "guidance", "is_mandatory", "applies_to", "position"):
        if field in data:
            setattr(item, field, data[field])
    db.commit()
    db.refresh(item)
    _log.info("checklist_item_updated user=%s checklist=%d item=%d fields=%s",
              current_user.username, checklist.id, item.id, list(data.keys()))
    return ReviewChecklistItemRead.model_validate(item)


@router.delete(
    "/{checklist_id}/items/{item_id}",
    response_model=DeleteResult,
    summary="Delete one rule (hard delete)",
)
def delete_item(
    checklist_id: int,
    item_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("review.configure")),
    tenant=Depends(get_tenant_context),
) -> DeleteResult:
    from promptops_app.database import ReviewChecklistItem
    checklist = _get_checklist_or_404(db, checklist_id, tenant)  # tenant gate
    n = (db.query(ReviewChecklistItem)
         .filter(ReviewChecklistItem.id == item_id,
                 ReviewChecklistItem.checklist_id == checklist.id)
         .delete(synchronize_session=False))
    db.commit()
    _log.info("checklist_item_deleted user=%s checklist=%d item=%d", current_user.username, checklist.id, item_id)
    return DeleteResult(deleted=n)


@router.post(
    "/{checklist_id}/items/bulk-delete",
    response_model=DeleteResult,
    summary="Delete several rules (hard delete)",
)
def bulk_delete_items(
    checklist_id: int,
    body: ChecklistItemIdsRequest,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("review.configure")),
    tenant=Depends(get_tenant_context),
) -> DeleteResult:
    from promptops_app.database import ReviewChecklistItem
    checklist = _get_checklist_or_404(db, checklist_id, tenant)  # tenant gate
    n = (db.query(ReviewChecklistItem)
         .filter(ReviewChecklistItem.checklist_id == checklist.id,
                 ReviewChecklistItem.id.in_(body.item_ids))
         .delete(synchronize_session=False))
    db.commit()
    _log.info("checklist_items_bulk_deleted user=%s checklist=%d n=%d", current_user.username, checklist.id, n)
    return DeleteResult(deleted=n)


@router.delete(
    "/{checklist_id}",
    response_model=DeleteResult,
    summary="Delete a whole checklist (hard delete — rules + source file)",
)
def delete_checklist(
    checklist_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("review.configure")),
    tenant=Depends(get_tenant_context),
) -> DeleteResult:
    from promptops_app.database import Document, ReviewChecklist
    checklist = _get_checklist_or_404(db, checklist_id, tenant)  # tenant gate
    doc_id = checklist.source_document_id
    db.delete(checklist)          # items cascade (FK ondelete=CASCADE + relationship)
    db.flush()
    # Remove the uploaded source file too, unless another checklist still uses it.
    if doc_id and not db.query(ReviewChecklist).filter(ReviewChecklist.source_document_id == doc_id).first():
        db.query(Document).filter(Document.id == doc_id).delete(synchronize_session=False)
    db.commit()
    _log.info("checklist_deleted user=%s checklist=%d", current_user.username, checklist_id)
    return DeleteResult(deleted=1)
