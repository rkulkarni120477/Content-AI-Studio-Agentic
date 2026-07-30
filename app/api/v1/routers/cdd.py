"""
CDD (Course Design Document) router.

This router handles the complete CDD pipeline:
  - Generating a new CDD with AI
  - Listing and viewing existing CDDs
  - Managing CDD versions (create, list, activate)
  - Pinning a CDD as active for generation
  - Exporting a CDD as DOCX

How this maps to the Streamlit UI
-----------------------------------
  Streamlit page : promptops_app/pages/cdd.py  render_page(db, ctx)
  The entire page becomes these HTTP endpoints — each button/form becomes
  one endpoint.

  "🤖 Generate CDD with AI"        → POST   /api/v1/cdd/generate
  "📂 Your Course Design Documents" → GET    /api/v1/cdd
  "Select CDD to View"             → GET    /api/v1/cdd/{cdd_id}
  "View Version" selector          → GET    /api/v1/cdd/{cdd_id}/versions/{version}
  "Set as Active Version"          → POST   /api/v1/cdd/{cdd_id}/versions/{version}/activate
  "Save as New Version"            → POST   /api/v1/cdd/{cdd_id}/versions
  "📌 Set as Active CDD"           → POST   /api/v1/cdd/{cdd_id}/pin
  "⬇️ Word (.docx)"                → GET    /api/v1/cdd/{cdd_id}/export

RBAC permissions used
---------------------
  cdd.generate  → Generate a new CDD with AI
  cdd.pin       → Pin a CDD for generation
  cdd.version   → Create or activate a CDD version
  export.course → Download a CDD as a file
"""

from __future__ import annotations

import json
import logging

from fastapi import APIRouter, Depends, Path, Query
from fastapi.responses import Response
from sqlalchemy.orm import Session

from app.core.dependencies import get_current_user, get_db, require_permission
from app.core.http import content_disposition
from app.core.exceptions import (
    LLMGenerationError,
    NotFoundError,
    PromptConfigurationError,
    WorkflowError,
)
from app.schemas.cdd import (
    CDDActivateVersionResponse,
    CDDGenerateRequest,
    CDDGenerateResponse,
    CDDListItem,
    CDDPinRequest,
    CDDPinResponse,
    CDDRead,
    CDDRegenerateItemRequest,
    CDDRegenerateItemResponse,
    CDDRegenerateSectionRequest,
    CDDRegenerateSectionResponse,
    CDDVersionCreateRequest,
    CDDVersionListItem,
    CDDVersionRead,
)
from app.schemas.common import PaginatedResponse
from app.api.v1.cdd_response import build_cdd_read
from app.core.dis_client import dis_client
from app.core.dis_access import resolve_course_dis_client

_log = logging.getLogger(__name__)

router = APIRouter()


def _dis_context_block(purpose: str, payload: dict, current_user, label: str, client_id: str = "") -> tuple[str, list]:
    try:
        result = dis_client.retrieve_context_sync(purpose, payload, current_user=current_user, client_id=client_id)
        ctx = str(result.get("combined_context") or "").strip()
        units = result.get("source_units") or result.get("sources") or []
        if ctx:
            return (
                f"\n\n---\n{label} FROM DIS SOURCE LIBRARY\n"
                "Use this as supporting source context only. Follow CAS style, structure, and user instructions first. "
                "Do not expose internal DIS metadata.\n\n"
                f"{ctx}\n---\n",
                units,
            )
    except Exception as exc:
        _log.warning("dis_%s_context_unavailable error=%s", purpose, exc)
    return "", []


def _merge_source_units(primary: list, extra: list) -> list:
    """Append `extra` source units to `primary`, dropping ones already present.

    Units are DIS payloads (usually dicts, occasionally plain ids), so identity is
    keyed off whichever id-ish field is available and falls back to the repr —
    provenance must never raise and break a generation that already succeeded.
    """
    def key(unit):
        if isinstance(unit, dict):
            for field in ("unit_id", "chunk_id", "id", "document_id", "job_id"):
                if unit.get(field):
                    return f"{field}:{unit[field]}"
        return repr(unit)

    merged = list(primary or [])
    seen = {key(u) for u in merged}
    for unit in extra or []:
        unit_key = key(unit)
        if unit_key not in seen:
            seen.add(unit_key)
            merged.append(unit)
    return merged


# ---------------------------------------------------------------------------
# Helper — load CDD or raise 404
# ---------------------------------------------------------------------------

def _get_cdd_or_404(db: Session, cdd_id: int):
    """
    Fetch a CDD by ID from the database.

    Raises ``NotFoundError`` (HTTP 404) if no CDD with that ID exists.
    Centralising this lookup avoids duplicating the same null-check in every endpoint.
    """
    from promptops_app.repositories import cdd_repository

    cdd = cdd_repository.get_cdd_by_id(db, cdd_id)
    if cdd is None:
        raise NotFoundError("CDD", cdd_id)
    return cdd


# ---------------------------------------------------------------------------
# List CDDs
# ---------------------------------------------------------------------------

@router.get(
    "",
    response_model=PaginatedResponse[CDDListItem],
    summary="List Course Design Documents",
    description=(
        "Returns a paginated list of CDDs scoped to the given project. "
        "Admin users can omit project_id to see all CDDs across all projects."
    ),
)
def list_cdds(
    project_id: int | None = Query(default=None, description="Filter by project ID."),
    course_id: int | None = Query(default=None, description="Optional course ID for legacy row matching."),
    page: int = Query(default=1, ge=1, description="Page number (1-indexed)."),
    page_size: int = Query(default=100, ge=1, le=200, description="Items per page."),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
) -> PaginatedResponse[CDDListItem]:
    """
    Return CDDs the current user has access to.

    Non-admin users are automatically scoped to their assigned project.
    Admin users can pass any project_id or omit it to see all CDDs.
    """
    from promptops_app.repositories import cdd_repository

    effective_project_id = project_id if current_user.role == "admin" else (
        project_id or getattr(current_user, "default_project_id", None)
    )

    if effective_project_id:
        all_cdds = cdd_repository.list_cdds_for_scope(
            db, project_id=effective_project_id, course_id=course_id,
        )
    elif course_id:
        all_cdds = cdd_repository.list_cdds_for_scope(db, course_id=course_id)
    else:
        all_cdds = cdd_repository.list_all_cdds(db)

    total = len(all_cdds)
    start = (page - 1) * page_size
    page_items = all_cdds[start : start + page_size]

    items = []
    for c in page_items:
        items.append(CDDListItem(
            id=c.id,
            title=c.title or "",
            course_title=c.course_title,
            active_version=c.active_version,
            workflow_state=c.workflow_state or "draft",
            created_at=c.created_at,
        ))

    return PaginatedResponse.create(
        items=items,
        total=total,
        page=page,
        page_size=page_size,
    )


# ---------------------------------------------------------------------------
# Generate a new CDD with AI
# ---------------------------------------------------------------------------

@router.post(
    "/generate",
    response_model=CDDGenerateResponse,
    status_code=201,
    summary="Generate a new CDD with AI",
    description=(
        "Runs the full CDD generation pipeline: builds the prompt from the "
        "active style and course metadata, calls the LLM, parses the output "
        "into sections, saves the CDD and version to the database, and "
        "automatically pins the new CDD to the specified course."
    ),
    responses={
        201: {"description": "CDD created and pinned successfully."},
        502: {"description": "All LLM attempts failed (primary + retry + fallback)."},
        403: {"description": "User does not have the cdd.generate permission."},
    },
)
def generate_cdd(
    request_body: CDDGenerateRequest,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("cdd.generate")),
) -> CDDGenerateResponse:
    """
    Generate a Course Design Document using AI.

    This endpoint mirrors the "🤖 Generate CDD with AI" button in the Streamlit app.
    The full pipeline is:
      1. Build the prompt (active style + course metadata + extra instructions)
      2. Call the LLM via llm_service.generate_text() (primary → retry → fallback)
      3. Parse the output into sections using cdd_parser.parse_sections_from_text()
      4. Persist the CDD and version v1 to the database
      5. Auto-pin the new CDD to the course via course_repository.set_active_cdd()

    All steps are identical to the Streamlit implementation — only the delivery
    mechanism (HTTP response vs st.rerun) has changed.
    """
    from promptops_app.database import (
        CDDVersion, CourseDesignDocument, build_style_context,
    )
    from promptops_app.parsers.cdd_parser import parse_cdd_flat, parse_sections_from_text
    from promptops_app.repositories import cdd_repository, style_repository
    from promptops_app.repositories.course_repository import get_course_by_id, set_active_cdd
    from promptops_app.services.audit_service import log_audit_event
    from promptops_app.services.llm_service import generate_with_metadata
    from promptops_app.services.usage_service import UsageLogContext
    from promptops_app.prompts.prompt_builder import PromptVariableError, build_prompt

    _log.info(
        "cdd_generate_start  user=%s  course=%d  title=%s  model=%s",
        current_user.username, request_body.course_id,
        request_body.course_title, request_body.model_choice,
    )

    dis_query = " ".join(str(x or "") for x in [
        request_body.course_title,
        request_body.document_title,
        request_body.target_audience,
        request_body.expert_domain,
        request_body.audience_category,
        request_body.extra_instructions,
    ])
    # Scope retrieval to the COURSE's own Source Library (its project's
    # client), independent of who runs the generation.
    dis_client_id = resolve_course_dis_client(
        db, course_id=request_body.course_id,
        project_id=getattr(request_body, "project_id", None),
    )

    dis_context_block, dis_source_units = _dis_context_block(
        "cdd",
        {
            "purpose": "cdd",
            "query": dis_query,
            "filters": {
                "purpose": "cdd",
                # No hard document_types filter: fixed type names did not match real
                # stored doc types (e.g. Cengage docs are typed "pdf"), silently
                # returning zero. Retrieval relies on purpose + semantic ranking;
                # the security allow-set still applies.
            },
            "retrieval": {"top_k": 12, "token_budget": 12000},
        },
        current_user,
        "CDD CONTEXT",
        client_id=dis_client_id,
    )

    # Documents the user explicitly picked in "Reference Documents" on the Create
    # New CDD form. Retrieved as an ADDITIONAL block pinned to those ids, on top
    # of the automatic purpose=cdd retrieval above — when nothing is selected the
    # pipeline is byte-for-byte what it was before.
    selected_ref_ids = [
        str(x).strip() for x in (request_body.reference_document_ids or []) if str(x).strip()
    ]
    if selected_ref_ids:
        pinned_block, pinned_units = _dis_context_block(
            "cdd",
            {
                "purpose": "cdd",
                "query": dis_query,
                "document_ids": selected_ref_ids,
                # Only pin by id here. The selector lists every processed document,
                # not just purpose=cdd ones, so re-applying the purpose filter would
                # silently drop documents the user deliberately attached.
                "filters": {"document_ids": selected_ref_ids},
                # The pool is already narrowed to the user's picks, so pull a deeper
                # slice than the broad auto-retrieval above.
                "retrieval": {"top_k": 24, "token_budget": 20000},
            },
            current_user,
            "USER-SELECTED REFERENCE DOCUMENTS",
            client_id=dis_client_id,
        )
        if pinned_block:
            dis_context_block = f"{dis_context_block}{pinned_block}"
            dis_source_units = _merge_source_units(dis_source_units, pinned_units)
        _log.info(
            "cdd_generate_reference_docs  user=%s  course=%d  selected=%d  retrieved=%s",
            current_user.username, request_body.course_id,
            len(selected_ref_ids), bool(pinned_block),
        )

    # ── Step 1: Build prompts ──────────────────────────────────────────────────
    # Use the custom override if the user edited the prompt in the UI,
    # otherwise build from the prompt library (falls back to inline constants).
    if request_body.system_prompt_override and request_body.user_prompt_override:
        system_prompt = request_body.system_prompt_override
        user_prompt = request_body.user_prompt_override
        if dis_context_block:
            user_prompt = f"{user_prompt}\n\n{dis_context_block}"
        # Persist the override with the artifact (PL↔CAS sync review, plan
        # Phase 11): a prompt authored inline in CAS must stay recoverable —
        # before this it drove the LLM call and was discarded.
        prompt_provenance = {
            "prompt_source": "override",
            "system_prompt_override": request_body.system_prompt_override,
            "user_prompt_override": request_body.user_prompt_override,
        }
    else:
        course = get_course_by_id(db, request_body.course_id)
        style_context = ""
        if request_body.style_id:
            style = style_repository.get_style_by_id(db, request_body.style_id)
            if style:
                style_context = build_style_context(db, style, cluster_id=course.cluster_id if course else None)

        extra_block = request_body.extra_instructions or ""
        if dis_context_block:
            extra_block = f"{extra_block}\n\n{dis_context_block}".strip()
        if style_context:
            extra_block = f"**ACTIVE STYLE — Apply throughout:**\n{style_context}\n\n{extra_block}"

        variables = {
            "course_title":        request_body.course_title,
            "course_name":         request_body.course_title,
            "target_audience":     request_body.target_audience,
            "expert_domain":       request_body.expert_domain,
            "audience_level":      request_body.audience_category,
            "estimated_duration":  str(request_body.estimated_duration_hours),
            "extra_instructions_block": extra_block,
            "extra_instructions":  extra_block,
            "style_guidelines":    style_context,
            "grade_level":         request_body.target_audience,
        }

        try:
            system_prompt, user_prompt, _tpl_name, _tpl_version = build_prompt(
                "cdd_generation", variables, db=db,
                project_id=course.project_id if course else None,
                cluster_id=course.cluster_id if course else None,
                course_id=request_body.course_id,
                prompt_id=request_body.prompt_id,
            )
            prompt_provenance = {
                "prompt_source": "selected" if request_body.prompt_id else "registry",
                "prompt_name": _tpl_name,
                "prompt_version": _tpl_version,
            }
        except PromptVariableError as exc:
            # A declared-variable violation is a template misconfiguration —
            # surface it to the admin; never silently swap in the constant
            # fallback (that would mask which prompt generation actually used).
            raise PromptConfigurationError(
                str(exc),
                detail={"template": exc.template, "missing": exc.missing},
            ) from exc
        except Exception:
            # Fall back to inline constants if the prompt library fails.
            from promptops_app.prompt_templates import (
                CDD_SYSTEM_PROMPT, CDD_USER_PROMPT_TEMPLATE,
            )
            system_prompt = CDD_SYSTEM_PROMPT
            user_prompt = CDD_USER_PROMPT_TEMPLATE.format(
                course_title=request_body.course_title,
                target_audience=request_body.target_audience,
                expert_domain=request_body.expert_domain,
                audience_level=request_body.audience_category,
                estimated_duration=str(request_body.estimated_duration_hours),
                extra_instructions_block=extra_block,
            )
            prompt_provenance = {"prompt_source": "builtin_fallback"}

    # ── Step 2: Call the LLM ───────────────────────────────────────────────────
    usage_context = UsageLogContext(
        user_name=current_user.username,
        project_id=request_body.project_id,
        course_id=request_body.course_id,
        entity_type="cdd",
    )

    llm_result = generate_with_metadata(
        request_body.model_choice,
        system_prompt,
        user_prompt,
        usage_ctx=usage_context,
    )

    if llm_result.status == "error":
        _log.error(
            "cdd_generate_llm_failed  user=%s  error_type=%s",
            current_user.username, llm_result.error_type,
        )
        raise LLMGenerationError(
            f"Content generation failed after all retries. "
            f"Please try again or switch models. Error type: {llm_result.error_type}"
        )

    # ── Step 3: Parse the AI output into sections ──────────────────────────────
    raw_output = llm_result.text
    sections = parse_sections_from_text(raw_output)

    # Merge any additional flat-parsed keys (handles both parsing strategies).
    flat_parsed = parse_cdd_flat(raw_output)
    for key, value in flat_parsed.items():
        if not key.startswith("_") and value.strip():
            sections[key] = value

    # ── Step 4: Persist the CDD and initial version ────────────────────────────
    document_title = request_body.document_title or f"{request_body.course_title} — CDD"

    new_cdd = CourseDesignDocument(
        title=document_title,
        course_title=request_body.course_title,
        description="",
        active_version="v1",
        workflow_state="draft",
        created_by=current_user.username,
        project_id=request_body.project_id,
        course_id=request_body.course_id,
    )
    db.add(new_cdd)
    db.commit()
    db.refresh(new_cdd)

    generation_params = {
        "course_title":             request_body.course_title,
        "target_audience":          request_body.target_audience,
        "expert_domain":            request_body.expert_domain,
        "estimated_duration_hours": request_body.estimated_duration_hours,
        "extra_instructions":       request_body.extra_instructions,
        "dis_source_units":         dis_source_units,
        # Prompt provenance: which registry template (name+version) produced
        # this version, or the full override text when the user edited the
        # prompt inline — the artifact is reproducible either way.
        **prompt_provenance,
    }

    version_record = CDDVersion(
        cdd_id=new_cdd.id,
        version="v1",
        full_content=raw_output,
        sections=json.dumps(sections),
        generation_params=json.dumps(generation_params),
        change_reason="Initial AI generation",
        is_active=True,
        created_by=current_user.username,
    )
    db.add(version_record)
    db.commit()

    # Copy generated CDD body to DIS/S3. CAS DB keeps workflow pointers and versions;
    # DIS is the generated-document store for retrieval and cross-workflow reuse.
    try:
        dis_client.generated_upsert_sync({
            "generated_doc_id": f"cdd_{new_cdd.id}",
            "generated_type": "cdd",
            "title": document_title,
            "content": raw_output,
            "summary": raw_output[:500],
            "active": True,
            "metadata": {
                "course_title": request_body.course_title,
                "target_audience": request_body.target_audience,
                "expert_domain": request_body.expert_domain,
                "course_id": request_body.course_id,
                "project_id": request_body.project_id,
            },
            "source_documents_used": dis_source_units,
            "cas_ref": {"entity": "cdd", "id": new_cdd.id},
            "created_by": current_user.username,
        }, current_user=current_user)
    except Exception as exc:
        _log.warning("dis_generated_cdd_upsert_failed cdd_id=%s error=%s", new_cdd.id, exc)

    # ── Step 5: Auto-pin the new CDD to the course ────────────────────────────
    set_active_cdd(db, request_body.course_id, new_cdd.id)

    # ── Audit log ─────────────────────────────────────────────────────────────
    log_audit_event(
        db,
        current_user.username,
        "cdd.created",
        entity_type="cdd",
        entity_id=new_cdd.id,
        project_id=request_body.project_id,
        course_id=request_body.course_id,
        metadata={
            "title": document_title, "sections": len(sections),
            "model_choice": request_body.model_choice,
            "course_title": request_body.course_title,
            "target_audience": request_body.target_audience,
            "expert_domain": request_body.expert_domain,
            "extra_instructions": request_body.extra_instructions,
            "dis_source_units_count": len(dis_source_units) if dis_source_units else 0,
            "input_mode": "full",
            "system_prompt": system_prompt,
            "user_prompt": user_prompt,
            "output": raw_output,
        },
    )

    _log.info(
        "cdd_generate_complete  user=%s  cdd_id=%d  sections=%d  model=%s",
        current_user.username, new_cdd.id, len(sections), llm_result.model,
    )

    return CDDGenerateResponse(
        cdd_id=new_cdd.id,
        title=document_title,
        version="v1",
        sections_count=len(sections),
        full_content=raw_output,
        sections=sections,
        model_used=llm_result.model or request_body.model_choice,
        tokens_used=(
            (llm_result.prompt_tokens or 0) + (llm_result.completion_tokens or 0)
            if llm_result.prompt_tokens else None
        ),
        auto_pinned=True,
    )


# ---------------------------------------------------------------------------
# Get a single CDD
# ---------------------------------------------------------------------------

@router.get(
    "/{cdd_id}",
    response_model=CDDRead,
    summary="Get a single CDD with its active version content",
    description=(
        "**cdd_id** is the primary key of `course_design_documents` — not the course id. "
        "To load the CDD pinned on a course, call `GET /api/v1/courses/{course_id}` "
        "and use the returned `active_cdd_id`, or use "
        "`GET /api/v1/courses/{course_id}/active-cdd`."
    ),
    responses={404: {"description": "CDD not found."}},
)
def get_cdd(
    cdd_id: int = Path(
        ...,
        description="CDD record id (course_design_documents.id). Not the course id.",
        examples=[12],
    ),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
) -> CDDRead:
    """
    Return full CDD metadata and the content of its currently active version.

    The active version content is embedded in the response to avoid a second
    API call, matching the Streamlit pattern of loading both simultaneously.
    """
    cdd = _get_cdd_or_404(db, cdd_id)
    return build_cdd_read(db, cdd)


# ---------------------------------------------------------------------------
# List CDD versions
# ---------------------------------------------------------------------------

@router.get(
    "/{cdd_id}/versions",
    response_model=list[CDDVersionListItem],
    summary="List all versions of a CDD",
    responses={404: {"description": "CDD not found."}},
)
def list_cdd_versions(
    cdd_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
) -> list[CDDVersionListItem]:
    """Return all saved versions for a CDD, newest first."""
    from promptops_app.repositories import cdd_repository

    _get_cdd_or_404(db, cdd_id)  # validates existence
    versions = cdd_repository.list_cdd_versions(db, cdd_id)
    return [CDDVersionListItem.model_validate(v) for v in versions]


# ---------------------------------------------------------------------------
# Get a specific CDD version
# ---------------------------------------------------------------------------

@router.get(
    "/{cdd_id}/versions/{version}",
    response_model=CDDVersionRead,
    summary="Get the full content of a specific CDD version",
    responses={
        404: {"description": "CDD or version not found."},
    },
)
def get_cdd_version(
    cdd_id: int,
    version: str,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
) -> CDDVersionRead:
    """
    Return the full Markdown content and parsed sections for a specific CDD version.

    Used when the user selects a version from the version dropdown in the CDD tab.
    """
    from promptops_app.repositories import cdd_repository

    _get_cdd_or_404(db, cdd_id)
    version_record = cdd_repository.get_cdd_version(db, cdd_id, version)

    if version_record is None:
        raise NotFoundError(f"CDD version '{version}'", cdd_id)

    return CDDVersionRead.model_validate(version_record)


# ---------------------------------------------------------------------------
# Activate a specific CDD version
# ---------------------------------------------------------------------------

@router.post(
    "/{cdd_id}/versions/{version}/activate",
    response_model=CDDActivateVersionResponse,
    summary="Set a specific version as the active version",
    description="Marks the given version as active. All other versions are deactivated.",
    responses={
        404: {"description": "CDD or version not found."},
        403: {"description": "Requires cdd.version permission."},
    },
)
def activate_cdd_version(
    cdd_id: int,
    version: str,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("cdd.version")),
) -> CDDActivateVersionResponse:
    """
    Set a CDD version as the active version.

    Replicates the "Set as Active Version" button in the Streamlit CDD tab.
    All other versions for this CDD are deactivated in the same transaction.
    """
    from promptops_app.database import CDDVersion

    cdd = _get_cdd_or_404(db, cdd_id)

    version_record = db.query(CDDVersion).filter(
        CDDVersion.cdd_id == cdd_id,
        CDDVersion.version == version,
    ).first()

    if version_record is None:
        raise NotFoundError(f"CDD version '{version}'", cdd_id)

    # Deactivate all versions, then activate the selected one.
    db.query(CDDVersion).filter(CDDVersion.cdd_id == cdd_id).update(
        {CDDVersion.is_active: False}
    )
    version_record.is_active = True
    cdd.active_version = version
    db.commit()

    _log.info(
        "cdd_version_activated  user=%s  cdd_id=%d  version=%s",
        current_user.username, cdd_id, version,
    )

    return CDDActivateVersionResponse(cdd_id=cdd_id, active_version=version)


# ---------------------------------------------------------------------------
# Commit a new manual CDD version
# ---------------------------------------------------------------------------

@router.post(
    "/{cdd_id}/versions",
    response_model=CDDVersionRead,
    status_code=201,
    summary="Commit edited content as a new CDD version",
    description=(
        "Saves the current editor content as a new named version. "
        "The new version is immediately set as active."
    ),
    responses={
        404: {"description": "CDD not found."},
        403: {"description": "Requires cdd.version permission."},
    },
)
def create_cdd_version(
    cdd_id: int,
    request_body: CDDVersionCreateRequest,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("cdd.version")),
) -> CDDVersionRead:
    """
    Commit a new CDD version from manually edited content.

    Replicates the "Save as New Version" expander in the Streamlit CDD tab.
    The new version is set as active and the CDD's ``active_version`` field
    is updated accordingly.
    """
    from promptops_app.database import CDDVersion
    from promptops_app.services.audit_service import log_audit_event

    cdd = _get_cdd_or_404(db, cdd_id)

    # Deactivate all existing versions before creating the new one.
    db.query(CDDVersion).filter(CDDVersion.cdd_id == cdd_id).update(
        {CDDVersion.is_active: False}
    )

    new_version = CDDVersion(
        cdd_id=cdd_id,
        version=request_body.version_tag,
        full_content=request_body.full_content,
        sections=json.dumps(request_body.sections),
        change_reason=request_body.change_reason,
        is_active=True,
        created_by=current_user.username,
    )
    db.add(new_version)
    cdd.active_version = request_body.version_tag
    db.commit()
    db.refresh(new_version)

    log_audit_event(
        db,
        current_user.username,
        "cdd.version_committed",
        entity_type="cdd",
        entity_id=cdd_id,
        metadata={"version": request_body.version_tag, "reason": request_body.change_reason},
    )

    _log.info(
        "cdd_version_committed  user=%s  cdd_id=%d  version=%s",
        current_user.username, cdd_id, request_body.version_tag,
    )

    return CDDVersionRead.model_validate(new_version)


# ---------------------------------------------------------------------------
# Regeneration (AI) — ports the Streamlit CDD regenerate controls
# ---------------------------------------------------------------------------

@router.post(
    "/{cdd_id}/regenerate-item",
    response_model=CDDRegenerateItemResponse,
    summary="Regenerate a single item within a CDD section",
    responses={
        404: {"description": "CDD or item not found."},
        403: {"description": "Requires cdd.version permission."},
    },
)
def regenerate_cdd_item(
    cdd_id: int,
    request_body: CDDRegenerateItemRequest,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("cdd.version")),
) -> CDDRegenerateItemResponse:
    """
    Regenerate one bullet/line/paragraph inside a CDD section, preserving all
    siblings. Replicates the per-item "⟳" button in the Streamlit CDD tab.

    Stateless: returns the patched section content. The frontend commits a new
    CDD version, matching the existing "Save Edit" flow.
    """
    from promptops_app.parsers.blueprint_parser import (
        parse_items_from_section,
        patch_item_in_section,
        regen_single_item,
    )

    _get_cdd_or_404(db, cdd_id)

    original = request_body.section_content or ""
    item_index = request_body.item_index
    items = parse_items_from_section(original)
    if not items or item_index < 0 or item_index >= len(items):
        raise NotFoundError("CDD item", item_index)

    target = items[item_index]
    new_item_text = regen_single_item(
        section_title=request_body.section_key,
        section_content=original,
        item_index=item_index,
        item_text=target["text"],
        custom_instruction=request_body.feedback or "",
        model_choice=request_body.model_choice,
    )
    updated_content = patch_item_in_section(original, item_index, new_item_text)

    _log.info("cdd_item_regenerated  user=%s  cdd_id=%d  section=%s  item=%d",
              current_user.username, cdd_id, request_body.section_key, item_index)

    return CDDRegenerateItemResponse(
        updated_content=updated_content,
        patched_item=new_item_text or "",
    )


@router.post(
    "/{cdd_id}/regenerate-section",
    response_model=CDDRegenerateSectionResponse,
    summary="Regenerate an entire CDD section with AI",
    responses={
        404: {"description": "CDD not found."},
        403: {"description": "Requires cdd.version permission."},
    },
)
def regenerate_cdd_section(
    cdd_id: int,
    request_body: CDDRegenerateSectionRequest,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("cdd.version")),
) -> CDDRegenerateSectionResponse:
    """
    Regenerate a whole CDD section using the section-regeneration prompt.
    Replicates the "🔄 Regenerate Section" button in the Streamlit CDD tab.

    Stateless: returns the new section content; the frontend commits a version.
    """
    from promptops_app.prompt_templates import (
        CDD_SECTION_REGENERATE_PROMPT,
        CDD_SYSTEM_PROMPT,
    )
    from promptops_app.services.llm_service import generate_text as call_llm

    cdd = _get_cdd_or_404(db, cdd_id)
    course_title = getattr(cdd, "course_title", None) or request_body.section_key

    regen_prompt = CDD_SECTION_REGENERATE_PROMPT.format(
        section_title=request_body.section_key,
        course_title=course_title,
        custom_instruction=request_body.feedback or "Improve and expand this section.",
    )
    new_content = call_llm(request_body.model_choice, CDD_SYSTEM_PROMPT, regen_prompt)
    if not new_content or new_content.startswith("ERROR"):
        raise LLMGenerationError("Section regeneration failed. Please try again.")

    _log.info("cdd_section_regenerated  user=%s  cdd_id=%d  section=%s",
              current_user.username, cdd_id, request_body.section_key)

    return CDDRegenerateSectionResponse(updated_content=new_content.strip())


# ---------------------------------------------------------------------------
# Pin a CDD to a course
# ---------------------------------------------------------------------------

@router.post(
    "/{cdd_id}/pin",
    response_model=CDDPinResponse,
    summary="Set this CDD as the active CDD for generation",
    description=(
        "Persists the selection so the Generate page knows which CDD to inject "
        "into prompts. Equivalent to the '📌 Set as Active CDD' button."
    ),
    responses={
        404: {"description": "CDD not found."},
        403: {"description": "Requires cdd.pin permission."},
    },
)
def pin_cdd(
    cdd_id: int,
    request_body: CDDPinRequest,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("cdd.pin")),
) -> CDDPinResponse:
    """
    Pin a CDD as the active CDD for a course.

    Calls ``course_repository.set_active_cdd()`` — the same function used by
    the auto-pin after generation and the manual pin button in the Streamlit UI.
    """
    from promptops_app.repositories.course_repository import set_active_cdd

    _get_cdd_or_404(db, cdd_id)
    set_active_cdd(db, request_body.course_id, cdd_id)

    _log.info(
        "cdd_pinned  user=%s  cdd_id=%d  course_id=%d",
        current_user.username, cdd_id, request_body.course_id,
    )

    return CDDPinResponse(cdd_id=cdd_id, course_id=request_body.course_id, pinned=True)


# ---------------------------------------------------------------------------
# Export a CDD as a file
# ---------------------------------------------------------------------------

@router.get(
    "/{cdd_id}/export",
    summary="Download a CDD as a file (DOCX or Markdown)",
    description=(
        "Generates a formatted document from the CDD's active version content. "
        "Supports 'docx' (default) and 'md' formats."
    ),
    responses={
        200: {"description": "File download."},
        404: {"description": "CDD not found or has no active version."},
        403: {"description": "Requires export.course permission."},
    },
)
def export_cdd(
    cdd_id: int,
    format: str = Query(default="docx", description="Export format: docx | md"),
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("export.course")),
) -> Response:
    """
    Export the active CDD version as a downloadable file.

    Calls ``export_service.export_content()`` with the same parameters as the
    Streamlit "⬇️ Word (.docx)" download button.
    """
    from promptops_app.parsers.cdd_parser import (
        _strip_ui_hidden_text, parse_cdd_flat, is_dlu_cdd, split_cdd_worksheets,
    )
    from promptops_app.repositories import cdd_repository
    from promptops_app.services.export_service import ExportRequest, export_content

    cdd = _get_cdd_or_404(db, cdd_id)

    if not cdd.active_version:
        raise WorkflowError("This CDD has no active version to export.")

    version_record = cdd_repository.get_cdd_version(db, cdd_id, cdd.active_version)
    if not version_record:
        raise NotFoundError(f"CDD active version '{cdd.active_version}'", cdd_id)

    # Build export blocks — same logic as the Streamlit download button.
    parsed = parse_cdd_flat(version_record.full_content or "")

    # DLU CDD + XLSX → one sheet per worksheet (Overview + Worksheet 1..N).
    # Standard CDDs and every other format fall through to the shared export path
    # below, unchanged.
    if format == "xlsx" and is_dlu_cdd(version_record.full_content or ""):
        from promptops_app.exporters.xlsx_exporter import build_xlsx_worksheets

        cs_text = parsed.get("Course Structure", "") or (version_record.full_content or "")
        sheets = []
        for label, content in split_cdd_worksheets(cs_text):
            clean = _strip_ui_hidden_text(content)
            if (clean or "").strip():
                sheets.append((label, clean))
        if sheets:
            buf = build_xlsx_worksheets(cdd.title, sheets)
            fname = f"CDD_{cdd.title.replace(' ', '_')}_{cdd.active_version}.xlsx"
            _log.info(
                "cdd_exported_dlu_xlsx  user=%s  cdd_id=%d  sheets=%d",
                current_user.username, cdd_id, len(sheets),
            )
            return Response(
                content=buf.read(),
                media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                headers={"Content-Disposition": content_disposition(fname)},
            )
    export_blocks = []
    for section_key, section_label in [
        ("Course Details",          "Course Details"),
        ("Course Structure",        "Course Structure & Module Assessments"),
        ("Course Level Assessment", "Course Level Assessment"),
    ]:
        content = parsed.get(section_key, "").strip()
        if content:
            content = _strip_ui_hidden_text(content)
            if content:
                export_blocks.append((section_label, content))

    if not export_blocks:
        export_blocks = [("CDD Content", version_record.full_content or "")]

    export_request = ExportRequest(
        fmt=format,
        topic=cdd.title,
        blocks=export_blocks,
        user_name=current_user.username,
        is_admin=(current_user.role == "admin"),
        entity_type="cdd",
        entity_id=cdd.id,
        project_id=cdd.project_id,
        course_id=cdd.course_id,
        file_name=f"CDD_{cdd.title.replace(' ', '_')}_{cdd.active_version}.{format}",
    )

    result = export_content(db, export_request)

    if not result.success:
        raise WorkflowError(f"Export failed: {result.error_message}")

    _log.info(
        "cdd_exported  user=%s  cdd_id=%d  format=%s",
        current_user.username, cdd_id, format,
    )

    return Response(
        content=result.data,
        media_type=result.mime_type,
        headers={"Content-Disposition": content_disposition(result.file_name)},
    )
