"""
Styles router — instructional style management.

Streamlit equivalent: ``pages/style.py`` render_page()

Styles define the tone, vocabulary, and structure injected into every
CDD, Blueprint, and content generation prompt. This router handles:
  - Creating and editing styles
  - Uploading reference documents (PDF, DOCX, XLSX)
  - Generating AI style intelligence from those documents
  - Activating/deactivating a style for a project/course scope

RBAC:
  style.create     → Admin, Lead
  style.edit       → Admin, Lead
  style.delete     → Admin only
  style.upload     → Admin, Lead
  style.understand → Admin, Lead
  style.activate   → Admin, Lead
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, File, Form, Query, UploadFile
from sqlalchemy.orm import Session

from app.core.dependencies import get_current_user, get_db, require_permission
from app.core.exceptions import LLMGenerationError, NotFoundError
from app.schemas.common import MessageResponse, PaginatedResponse
from app.schemas.style import (
    StyleActivateRequest,
    StyleCreateRequest,
    StyleDocumentUploadResponse,
    StyleListItem,
    StyleRead,
    StyleUnderstandRequest,
    StyleUnderstandResponse,
    StyleUpdateRequest,
)

_log = logging.getLogger(__name__)
router = APIRouter()


def _get_style_or_404(db: Session, style_id: int):
    """Fetch a style by ID or raise HTTP 404."""
    from promptops_app.repositories import style_repository
    style = style_repository.get_style_by_id(db, style_id)
    if style is None:
        raise NotFoundError("Style", style_id)
    return style


@router.get(
    "",
    response_model=PaginatedResponse[StyleListItem],
    summary="List all styles",
    description="Ordered by most recently updated. Active styles are tagged.",
)
def list_styles(
    project_id: int | None = Query(default=None),
    course_id: int | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
) -> PaginatedResponse[StyleListItem]:
    """Return all styles ordered by updated_at desc. Matches get_styles() from database.py."""
    from promptops_app.database import get_styles

    styles = get_styles(db)
    total = len(styles)
    start = (page - 1) * page_size
    items = []
    for s in styles[start: start + page_size]:
        item = StyleListItem.model_validate(s)
        # Truncate understanding to a short preview for list responses.
        if s.understanding:
            item.understanding_preview = s.understanding[:200]
        items.append(item)

    return PaginatedResponse.create(items=items, total=total, page=page, page_size=page_size)


@router.post(
    "",
    response_model=StyleRead,
    status_code=201,
    summary="Create a new style",
)
def create_style(
    request_body: StyleCreateRequest,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("style.create")),
) -> StyleRead:
    """Create a style metadata shell. Documents are uploaded separately."""
    from promptops_app.database import Style

    style = Style(
        name=request_body.name,
        description=request_body.description,
        is_active=False,
        created_by=current_user.username,
    )
    db.add(style)
    db.commit()
    db.refresh(style)

    _log.info("style_created  user=%s  style_id=%d  name=%s",
              current_user.username, style.id, style.name)
    return StyleRead.model_validate(style)


@router.get(
    "/{style_id}",
    response_model=StyleRead,
    summary="Get a single style with full understanding text",
)
def get_style(
    style_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
) -> StyleRead:
    """Return full style details including the AI-generated understanding text."""
    return StyleRead.model_validate(_get_style_or_404(db, style_id))


@router.put(
    "/{style_id}",
    response_model=StyleRead,
    summary="Update style name or description",
)
def update_style(
    style_id: int,
    request_body: StyleUpdateRequest,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("style.edit")),
) -> StyleRead:
    """Update style metadata. Does not regenerate AI understanding."""
    style = _get_style_or_404(db, style_id)

    if request_body.name is not None:
        style.name = request_body.name
    if request_body.description is not None:
        style.description = request_body.description

    db.commit()
    db.refresh(style)
    _log.info("style_updated  user=%s  style_id=%d", current_user.username, style_id)
    return StyleRead.model_validate(style)


@router.delete(
    "/{style_id}",
    status_code=204,
    summary="Delete a style permanently",
)
def delete_style(
    style_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("style.delete")),
) -> None:
    """Hard-delete a style. Admin only."""
    style = _get_style_or_404(db, style_id)
    db.delete(style)
    db.commit()
    _log.info("style_deleted  user=%s  style_id=%d", current_user.username, style_id)


@router.post(
    "/{style_id}/documents",
    response_model=StyleDocumentUploadResponse,
    summary="Upload reference documents to a style",
    description="Accepts PDF, DOCX, XLSX, or TXT files. Files are parsed and stored for style intelligence generation.",
)
async def upload_style_documents(
    style_id: int,
    files: list[UploadFile] = File(...),
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("style.upload")),
) -> StyleDocumentUploadResponse:
    """
    Parse and attach reference documents to a style.

    Replicates the file uploader in the Streamlit Style tab.
    Uses the existing file_parser.py to extract text from each file.
    """
    from promptops_app.database import add_files_to_style
    from promptops_app.parsers.file_parser import _parse_uploaded_file

    _get_style_or_404(db, style_id)

    uploaded, errors = [], []

    for upload in files:
        raw_bytes = await upload.read()

        # Wrap in a file-like object that matches what _parse_uploaded_file expects.
        class _FakeST:
            name = upload.filename
            def read(self): return raw_bytes
            def getvalue(self): return raw_bytes

        name, content, err = _parse_uploaded_file(_FakeST())

        if err:
            errors.append(f"{upload.filename}: {err}")
            continue

        if content:
            from promptops_app.database import Document
            doc = Document(
                name=name,
                content=content,
                file_type=(upload.filename or "").rsplit(".", 1)[-1].lower(),
                source_type="style",
                char_count=len(content),
                is_active=True,
                created_by=current_user.username,
            )
            db.add(doc)
            db.flush()
            add_files_to_style(db, style_id, [doc])
            uploaded.append(name)

    db.commit()
    _log.info("style_documents_uploaded  user=%s  style_id=%d  count=%d",
              current_user.username, style_id, len(uploaded))
    return StyleDocumentUploadResponse(uploaded=uploaded, errors=errors)


@router.post(
    "/{style_id}/understand",
    response_model=StyleUnderstandResponse,
    summary="Generate AI style intelligence from uploaded documents",
    description=(
        "Calls the LLM to analyse the style's reference documents and produce "
        "a structured style guide. Equivalent to the '✨ Generate Style Intelligence' button."
    ),
)
def generate_style_intelligence(
    style_id: int,
    request_body: StyleUnderstandRequest,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("style.understand")),
) -> StyleUnderstandResponse:
    """
    Generate or regenerate style intelligence using AI.

    Calls style_service.generate_style_understanding() which is already
    framework-agnostic and moves unchanged from the Streamlit app.
    """
    from promptops_app.services.style_service import (
        generate_style_understanding,
        regenerate_style_understanding,
    )

    style = _get_style_or_404(db, style_id)

    # Use regenerate if understanding already exists, otherwise generate fresh.
    if style.understanding:
        result = regenerate_style_understanding(
            db,
            style,
            model_choice=request_body.model_choice,
            extra_instructions=request_body.extra_instructions,
            system_prompt_override=request_body.system_prompt_override,
            user_name=current_user.username,
        )
    else:
        result = generate_style_understanding(
            db,
            style,
            model_choice=request_body.model_choice,
            extra_instructions=request_body.extra_instructions,
            system_prompt_override=request_body.system_prompt_override,
            user_name=current_user.username,
        )

    if not result or (isinstance(result, str) and result.startswith("ERROR")):
        raise LLMGenerationError(
            "Style intelligence generation failed. Check LLM connectivity and retry."
        )

    # Persist the result.
    understanding_text = result if isinstance(result, str) else str(result)
    style.understanding = understanding_text
    db.commit()

    _log.info("style_understood  user=%s  style_id=%d  model=%s",
              current_user.username, style_id, request_body.model_choice)

    return StyleUnderstandResponse(
        style_id=style_id,
        understanding=understanding_text,
        model_used=request_body.model_choice,
    )


@router.post(
    "/{style_id}/activate",
    response_model=StyleRead,
    summary="Set this style as active for a project/course",
)
def activate_style(
    style_id: int,
    request_body: StyleActivateRequest,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("style.activate")),
) -> StyleRead:
    """
    Mark a style as active for the given scope.

    Replicates the "Set as Active Style" button in the Streamlit Style tab.
    Calls the existing set_active_style() function from database.py.
    """
    from promptops_app.database import set_active_style

    _get_style_or_404(db, style_id)
    set_active_style(
        db,
        style_id,
        project_id=request_body.project_id,
        course_id=request_body.course_id,
    )
    style = _get_style_or_404(db, style_id)
    db.refresh(style)

    _log.info("style_activated  user=%s  style_id=%d", current_user.username, style_id)
    return StyleRead.model_validate(style)


@router.post(
    "/{style_id}/deactivate",
    response_model=StyleRead,
    summary="Deactivate a style",
)
def deactivate_style(
    style_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("style.deactivate")),
) -> StyleRead:
    """Remove active status from a style. Calls deactivate_style() from database.py."""
    from promptops_app.database import deactivate_style as _deactivate

    _get_style_or_404(db, style_id)
    _deactivate(db, style_id)
    style = _get_style_or_404(db, style_id)

    _log.info("style_deactivated  user=%s  style_id=%d", current_user.username, style_id)
    return StyleRead.model_validate(style)
