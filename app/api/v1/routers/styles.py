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
import json
import re

from fastapi import APIRouter, Depends, File, Form, Query, UploadFile
from sqlalchemy.orm import Session

from app.core.dependencies import get_current_user, get_db, require_permission
from app.core.exceptions import LLMGenerationError, NotFoundError
from app.schemas.common import MessageResponse, PaginatedResponse
from app.core.dis_client import dis_client
from app.schemas.style import (
    StyleActivateRequest,
    StyleCreateRequest,
    StyleDocumentUploadResponse,
    StyleListItem,
    StyleRead,
    StyleReferenceDocument,
    StyleUnderstandRequest,
    StyleUnderstandResponse,
    StyleUpdateRequest,
)

_log = logging.getLogger(__name__)
router = APIRouter()


_DIS_IDS_RE = re.compile(r"<!--\s*DIS_SOURCE_DOCUMENT_IDS=(.*?)-->", re.S)


def _split_dis_and_legacy_doc_ids(document_ids: list[str] | None) -> tuple[list[int], list[str]]:
    legacy: list[int] = []
    dis_ids: list[str] = []
    for raw in document_ids or []:
        value = str(raw).strip()
        if not value:
            continue
        if value.isdigit():
            legacy.append(int(value))
        else:
            dis_ids.append(value)
    return legacy, dis_ids


def _encode_dis_ids(ids: list[str]) -> str:
    clean = [str(x).strip() for x in ids if str(x).strip()]
    if not clean:
        return ""
    return f"<!-- DIS_SOURCE_DOCUMENT_IDS={json.dumps(clean)} -->"


def _extract_dis_ids(style, extra_ids: list[str] | None = None) -> list[str]:
    ids: list[str] = []
    text = getattr(style, "custom_instructions", "") or ""
    for match in _DIS_IDS_RE.finditer(text):
        try:
            parsed = json.loads(match.group(1))
            if isinstance(parsed, list):
                ids.extend(str(x).strip() for x in parsed if str(x).strip())
        except Exception:
            pass
    ids.extend(str(x).strip() for x in (extra_ids or []) if str(x).strip())
    # Preserve order, remove duplicates.
    return list(dict.fromkeys(ids))


def _visible_custom_instructions(text: str | None) -> str:
    return _DIS_IDS_RE.sub("", text or "").strip()


def _retrieve_dis_style_context(style, current_user, document_ids: list[str] | None = None, extra_instructions: str = "") -> str:
    ids = _extract_dis_ids(style, document_ids)
    if not ids:
        return ""
    # Style-specific query so semantic (vector) retrieval ranks chunks against
    # THIS style's intent, not only a generic sentence. Capped so the query
    # stays well inside embedding-model input limits.
    query_parts = [
        str(getattr(style, "name", "") or ""),
        str(getattr(style, "description", "") or ""),
        _visible_custom_instructions(getattr(style, "custom_instructions", "")),
        str(extra_instructions or ""),
        "Understand instructional style, authoring standards, copyediting rules, quality standards, tone, structure, and prohibited writing patterns.",
    ]
    query = " ".join(p.strip() for p in query_parts if p and p.strip())[:4000]
    payload = {
        "purpose": "style",
        "document_ids": ids,
        "filters": {"document_ids": ids, "purpose": "style"},
        "retrieval": {"top_k": 20, "token_budget": 14000},
        "query": query,
    }
    try:
        result = dis_client.retrieve_context_sync("style", payload, current_user=current_user)
        ctx = str(result.get("combined_context") or "").strip()
        if ctx:
            return "Use the following processed DIS Source Library documents as the authoritative style reference context. Do not expose internal metadata.\n\n" + ctx
    except Exception as exc:
        _log.warning("dis_style_context_unavailable style_id=%s error=%s", getattr(style, "id", None), exc)
    return ""



def _upsert_generated_style_to_dis(style, current_user, *, active: bool | None = None, source_units: list | None = None) -> None:
    """Save generated Style content body to DIS/S3 and keep CAS as workflow pointer.

    CAS still uses its DB for permissions and active pointers, but DIS/S3 is the
    generated-document store used by Source Library and retrieval.
    """
    content = str(getattr(style, "generated_summary", "") or "").strip()
    if not content:
        return
    payload = {
        "generated_doc_id": f"style_{getattr(style, 'id', '')}",
        "generated_type": "style",
        "title": getattr(style, "name", "Generated Style") or "Generated Style",
        "content": content,
        "summary": content[:500],
        "active": bool(getattr(style, "is_active", False)) if active is None else bool(active),
        "metadata": {
            "description": getattr(style, "description", "") or "",
            "style_id": getattr(style, "id", None),
        },
        "source_documents_used": source_units or _extract_dis_ids(style),
        "cas_ref": {"entity": "style", "id": getattr(style, "id", None)},
        "created_by": getattr(current_user, "username", "") or "cas-user",
    }
    try:
        dis_client.generated_upsert_sync(payload, current_user=current_user)
    except Exception as exc:
        _log.warning("dis_generated_style_upsert_failed style_id=%s error=%s", getattr(style, "id", None), exc)

def _get_style_or_404(db: Session, style_id: int, *, with_documents: bool = False):
    """Fetch a style by ID or raise HTTP 404."""
    from promptops_app.repositories import style_repository
    style = style_repository.get_style_by_id(db, style_id, with_documents=with_documents)
    if style is None:
        raise NotFoundError("Style", style_id)
    return style


def _style_to_read(style, current_user=None) -> StyleRead:
    """Build StyleRead and expose DIS-linked reference documents cleanly.

    DIS document ids are stored in a hidden HTML marker inside custom_instructions
    for backward-compatible CAS DB storage. They are shown as reference documents,
    while the marker is hidden from users.
    """
    ref_docs: list[StyleReferenceDocument] = []
    for sd in getattr(style, "style_documents", None) or []:
        doc = getattr(sd, "document", None)
        if doc is None:
            continue
        ref_docs.append(
            StyleReferenceDocument(
                id=doc.id,
                name=doc.filename,
                source_type=doc.doc_tag or "general",
                file_type=doc.file_type,
            )
        )

    dis_ids = _extract_dis_ids(style)
    dis_name_map: dict[str, str] = {}
    if dis_ids and current_user is not None:
        try:
            data = dis_client.documents_library_sync({"purpose": "style", "limit": 500}, current_user=current_user)
            for item in (data.get("documents") or data.get("sources") or []):
                key_values = [
                    str(item.get("document_id") or ""),
                    str(item.get("job_id") or ""),
                    str(item.get("id") or ""),
                ]
                title = item.get("source_file_name") or item.get("title") or item.get("filename")
                for key in key_values:
                    if key:
                        dis_name_map[key] = str(title or key)
        except Exception as exc:
            _log.debug("dis_reference_name_lookup_failed style_id=%s error=%s", getattr(style, "id", None), exc)

    for dis_id in dis_ids:
        ref_docs.append(
            StyleReferenceDocument(
                id=dis_id,
                name=dis_name_map.get(dis_id, f"DIS Source: {dis_id}"),
                source_type="dis_source_library",
                file_type="dis",
            )
        )

    base = StyleRead.model_validate(style)
    return base.model_copy(update={
        "custom_instructions": _visible_custom_instructions(getattr(style, "custom_instructions", "") or ""),
        "reference_documents": ref_docs,
    })


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
    """Return styles for the workspace scope (project/course) when provided."""
    from promptops_app.database import get_active_style, get_styles

    styles = get_styles(db, project_id=project_id, course_id=course_id)
    active = get_active_style(db, project_id=project_id, course_id=course_id)
    active_id = active.id if active else None

    total = len(styles)
    start = (page - 1) * page_size
    items = []
    for s in styles[start: start + page_size]:
        item = StyleListItem.model_validate(s)
        # Surface course/project activation, not the legacy global Style.is_active flag.
        item.is_active = bool(active_id is not None and s.id == active_id)
        if s.generated_summary:
            item.understanding_preview = s.generated_summary[:200]
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
    """Create a style with optional reference documents and course activation."""
    from promptops_app.database import create_style as db_create_style, set_active_style

    legacy_doc_ids, dis_doc_ids = _split_dis_and_legacy_doc_ids(request_body.document_ids or [])
    visible_instructions = (request_body.custom_instructions or "").strip()
    hidden_dis_marker = _encode_dis_ids(dis_doc_ids)
    stored_instructions = "\n".join(x for x in [visible_instructions, hidden_dis_marker] if x).strip()

    style = db_create_style(
        db,
        request_body.name.strip(),
        (request_body.description or "").strip(),
        stored_instructions,
        legacy_doc_ids,
        current_user.username,
    )

    if request_body.activate:
        scope = "course" if request_body.course_id else (
            "project" if request_body.project_id else "global"
        )
        set_active_style(
            db,
            style.id,
            scope=scope,
            project_id=request_body.project_id,
            course_id=request_body.course_id,
        )
        db.refresh(style)

    _log.info(
        "style_created  user=%s  style_id=%d  name=%s  docs=%d  activated=%s",
        current_user.username,
        style.id,
        style.name,
        len(request_body.document_ids or []),
        request_body.activate,
    )
    return _style_to_read(_get_style_or_404(db, style.id, with_documents=True), current_user)


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
    """Return full style details including linked documents (Streamlit view panel)."""
    style = _get_style_or_404(db, style_id, with_documents=True)
    return _style_to_read(style, current_user)


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
    return _style_to_read(style, current_user)


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
    summary="Append reference documents to a style",
    description=(
        "Link documents from the library and/or upload new files. "
        "Matches Streamlit 'Append Files' on the Style tab."
    ),
)
async def append_style_documents(
    style_id: int,
    files: list[UploadFile] = File(default=None),
    document_ids: str = Form(
        default="",
        description="JSON array of document library IDs to link, e.g. [1, 2, 3]",
    ),
    additional_instructions: str = Form(
        default="",
        description="Optional instructions appended to the style (marks understanding stale).",
    ),
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("style.upload")),
) -> StyleDocumentUploadResponse:
    """Append library documents and/or newly uploaded files to a style."""
    import io
    import json
    from datetime import datetime, timezone

    from promptops_app.database import Document, add_files_to_style
    from promptops_app.parsers.file_parser import _parse_uploaded_file
    from promptops_app.repositories import document_repository

    style = _get_style_or_404(db, style_id, with_documents=True)
    uploaded: list[str] = []
    errors: list[str] = []
    new_doc_ids: list[int] = []

    if document_ids.strip():
        try:
            parsed = json.loads(document_ids)
            if isinstance(parsed, list):
                new_doc_ids.extend(int(x) for x in parsed)
        except (json.JSONDecodeError, TypeError, ValueError):
            from app.core.exceptions import ValidationError
            raise ValidationError("document_ids must be a JSON array of integers.")

    for upload in files or []:
        raw_bytes = await upload.read()

        file_obj = io.BytesIO(raw_bytes)
        file_obj.name = upload.filename

        name, content, err = _parse_uploaded_file(file_obj)

        if err:
            errors.append(f"{upload.filename}: {err}")
            continue

        if not content:
            continue

        existing = document_repository.get_document_by_filename(db, name)
        if existing:
            new_doc_ids.append(existing.id)
            uploaded.append(name)
            continue

        doc = Document(
            filename=name,
            content=content,
            file_type=(upload.filename or "").rsplit(".", 1)[-1].lower(),
            doc_tag="style_reference",
            status="active",
            uploaded_by=current_user.username,
            uploaded_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )
        db.add(doc)
        db.flush()
        new_doc_ids.append(doc.id)
        uploaded.append(name)

    unique_ids = list(dict.fromkeys(new_doc_ids))
    if not unique_ids:
        from app.core.exceptions import ValidationError
        raise ValidationError("No new files selected or uploaded.")

    added = add_files_to_style(db, style, unique_ids)

    if additional_instructions.strip():
        prev = (style.custom_instructions or "").strip()
        extra = additional_instructions.strip()
        style.custom_instructions = f"{prev}\n{extra}".strip() if prev else extra

    db.commit()

    _log.info(
        "style_documents_appended  user=%s  style_id=%d  added=%d  uploaded=%d",
        current_user.username,
        style_id,
        added,
        len(uploaded),
    )
    return StyleDocumentUploadResponse(uploaded=uploaded, errors=errors, added=added)


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

    dis_context = _retrieve_dis_style_context(
        style, current_user, request_body.document_ids,
        extra_instructions=request_body.extra_instructions,
    )
    extra_parts = []
    if dis_context:
        extra_parts.append(dis_context)
    if request_body.extra_instructions.strip():
        extra_parts.append(request_body.extra_instructions.strip())
    effective_extra = "\n\n".join(extra_parts).strip()

    # Resolve the prompt selected in the "Prompt Template" dropdown (if any).
    # Only pipeline prompts are injectable (Decision 1); a system_prompt_override
    # from an AI "Use Now" still takes priority for the system prompt.
    _sel_sys = request_body.system_prompt_override
    _sel_usr = None
    if request_body.prompt_id:
        from promptops_app.database import Prompt, PromptVersion
        _sel_p = (
            db.query(Prompt)
            .filter(Prompt.id == request_body.prompt_id,
                    Prompt.prompt_kind == "pipeline",
                    Prompt.deleted_at.is_(None))
            .first()
        )
        if _sel_p:
            _sel_pv = (
                db.query(PromptVersion)
                .filter(PromptVersion.prompt_id == _sel_p.id,
                        PromptVersion.is_active == True)
                .first()
            )
            if _sel_pv:
                if not _sel_sys:
                    _sel_sys = _sel_pv.system_prompt or None
                _sel_usr = _sel_pv.user_prompt_template or None

    # Use regenerate if understanding already exists, otherwise generate fresh.
    _audit_capture = {}
    if style.generated_summary:
        result = regenerate_style_understanding(
            db,
            style,
            request_body.model_choice,
            effective_extra,
            system_prompt=_sel_sys,
            audit_capture=_audit_capture,
        )
    else:
        result = generate_style_understanding(
            db,
            style,
            request_body.model_choice,
            effective_extra,
            system_prompt=_sel_sys,
            user_prompt_template=_sel_usr,
            audit_capture=_audit_capture,
        )

    if isinstance(result, str) and result.startswith("ERROR: No documents or instructions"):
        from app.core.exceptions import ValidationError
        raise ValidationError(
            "Add at least one document or custom instruction before generating Style "
            "Understanding."
        )

    if not result or (isinstance(result, str) and result.startswith("ERROR")):
        # The service layer already produced a user-safe reason (auth/timeout/
        # rate-limit/provider) — surface it instead of a generic message.
        reason = result[len("ERROR:"):].strip() if isinstance(result, str) else ""
        raise LLMGenerationError(
            reason or "Style intelligence generation failed. Check LLM connectivity and retry."
        )

    # Persist the result.
    understanding_text = result if isinstance(result, str) else str(result)
    style.generated_summary = understanding_text
    db.commit()
    _upsert_generated_style_to_dis(style, current_user)

    from promptops_app.services.audit_service import log_audit_event

    log_audit_event(
        db, current_user.username, "style.upgraded",
        entity_type="style", entity_id=style.style_id,
        project_id=request_body.project_id, course_id=request_body.course_id,
        metadata={
            "name": style.name,
            "model_choice": request_body.model_choice,
            "extra_instructions": request_body.extra_instructions,
            "document_ids": request_body.document_ids,
            "input_mode": "full",
            "system_prompt": _audit_capture.get("system_prompt"),
            "user_prompt": _audit_capture.get("user_prompt"),
            "output": understanding_text,
        },
    )

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
    _upsert_generated_style_to_dis(style, current_user, active=True)

    _log.info("style_activated  user=%s  style_id=%d", current_user.username, style_id)
    return _style_to_read(style, current_user)


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
    """Remove active status from a style."""
    style = _get_style_or_404(db, style_id)
    style.is_active = False
    db.commit()
    db.refresh(style)
    _upsert_generated_style_to_dis(style, current_user, active=False)

    _log.info("style_deactivated  user=%s  style_id=%d", current_user.username, style_id)
    return _style_to_read(style, current_user)
