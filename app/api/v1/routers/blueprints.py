"""
Blueprints router — Module Blueprint pipeline.

Streamlit equivalent: ``pages/blueprint.py`` render_page()

Mirrors the CDD router in structure. The blueprint is generated from the
pinned CDD's content and produces a structured lesson plan with parseable
components (lessons, assessments) that drive the Generate page dropdown.

Endpoints:
  GET    /blueprints                     List blueprints
  POST   /blueprints/generate            AI generation
  GET    /blueprints/{id}                Get blueprint + active version
  GET    /blueprints/{id}/versions       List versions
  GET    /blueprints/{id}/versions/{v}   Get specific version
  POST   /blueprints/{id}/versions/{v}/activate
  POST   /blueprints/{id}/versions       Commit manual version
  POST   /blueprints/{id}/pin            Pin to course
  GET    /blueprints/{id}/components     Parsed component list (for Generate dropdown)
  GET    /blueprints/{id}/export         Download as file
"""

from __future__ import annotations

import json
import logging
import re

from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response
from sqlalchemy.orm import Session

from app.core.dependencies import get_current_user, get_db, require_permission
from app.core.exceptions import LLMGenerationError, NotFoundError, WorkflowError
from app.schemas.blueprint import (
    BlueprintActivateVersionResponse,
    BlueprintComponent,
    BlueprintComponentsResponse,
    BlueprintGenerateRequest,
    BlueprintGenerateResponse,
    BlueprintListItem,
    BlueprintPinRequest,
    BlueprintPinResponse,
    BlueprintRead,
    BlueprintVersionCreateRequest,
    BlueprintVersionListItem,
    BlueprintVersionRead,
)
from app.schemas.common import PaginatedResponse

_log = logging.getLogger(__name__)
router = APIRouter()


def _get_blueprint_or_404(db: Session, blueprint_id: int):
    """Fetch a blueprint by ID or raise HTTP 404."""
    from promptops_app.repositories import blueprint_repository
    bp = blueprint_repository.get_blueprint_by_id(db, blueprint_id)
    if bp is None:
        raise NotFoundError("Blueprint", blueprint_id)
    return bp


@router.get(
    "",
    response_model=PaginatedResponse[BlueprintListItem],
    summary="List blueprints",
)
def list_blueprints(
    project_id: int | None = Query(default=None),
    course_id: int | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
) -> PaginatedResponse[BlueprintListItem]:
    """Return blueprints scoped to the given project or course."""
    from promptops_app.repositories import blueprint_repository

    if course_id:
        bps = blueprint_repository.list_blueprints_for_course(db, course_id=course_id, project_id=project_id)
    else:
        bps = blueprint_repository.list_all_blueprints(db)

    total = len(bps)
    start = (page - 1) * page_size
    return PaginatedResponse.create(
        items=[BlueprintListItem.model_validate(b) for b in bps[start: start + page_size]],
        total=total, page=page, page_size=page_size,
    )


@router.post(
    "/generate",
    response_model=BlueprintGenerateResponse,
    status_code=201,
    summary="Generate a module blueprint with AI",
    description=(
        "Builds a module blueprint from the active CDD content. "
        "Auto-parses the output into components (lessons, assessments) "
        "and auto-pins the new blueprint to the course."
    ),
)
def generate_blueprint(
    request_body: BlueprintGenerateRequest,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("blueprint.generate")),
) -> BlueprintGenerateResponse:
    """
    Generate a Module Blueprint using AI.

    Mirrors the Streamlit "🤖 Generate Blueprint" button.
    Pipeline: build prompt → call LLM → parse sections → save → auto-pin.
    """
    from promptops_app.database import (
        BlueprintVersion, ModuleBlueprint, build_style_context,
        get_active_cdd_version,
    )
    from promptops_app.parsers.blueprint_parser import (
        parse_blueprint_components, get_blueprint_prompts,
    )
    from promptops_app.parsers.cdd_parser import extract_cdd_summary
    from promptops_app.prompts.prompt_builder import build_prompt
    from promptops_app.repositories import blueprint_repository, cdd_repository, style_repository
    from promptops_app.repositories.course_repository import set_active_blueprint
    from promptops_app.services.audit_service import log_audit_event
    from promptops_app.services.llm_service import generate_with_metadata
    from promptops_app.services.usage_service import UsageLogContext
    from promptops_app.core.llm_client import safe_json_loads

    _log.info("blueprint_generate_start  user=%s  course=%d  module=%s",
              current_user.username, request_body.course_id, request_body.selected_module)

    # Resolve CDD context.
    cdd_id = request_body.cdd_id
    if not cdd_id:
        from promptops_app.repositories import course_repository
        course = course_repository.get_course_by_id(db, request_body.course_id)
        cdd_id = course.active_cdd_id if course else None

    cdd_context = ""
    if cdd_id:
        cdd_version = get_active_cdd_version(db, cdd_id)
        if cdd_version:
            cdd_context = extract_cdd_summary(cdd_version.full_content or "")

    # Build prompt.
    style_context = ""
    if request_body.style_id:
        style = style_repository.get_style_by_id(db, request_body.style_id)
        if style:
            style_context = build_style_context(db, style, cluster_id=None)

    extra_block = request_body.extra_instructions or ""
    if style_context:
        extra_block = f"**ACTIVE STYLE:**\n{style_context}\n\n{extra_block}"

    if request_body.system_prompt_override and request_body.user_prompt_override:
        system_prompt = request_body.system_prompt_override
        user_prompt = request_body.user_prompt_override
    else:
        variables = {
            "cdd_context":        cdd_context or "No CDD linked.",
            "selected_module":    request_body.selected_module,
            "extra_instructions": extra_block,
            "teacher_mode":       "Yes" if request_body.teacher_mode else "No",
            "student_mode":       "No" if request_body.teacher_mode else "Yes",
            "style_guidelines":   style_context,
        }
        try:
            system_prompt, user_prompt, _, _ = build_prompt("blueprint_generation", variables, db=db)
        except Exception:
            mode = "teacher" if request_body.teacher_mode else "student"
            system_prompt, user_prompt_tmpl, _ = get_blueprint_prompts(mode)
            user_prompt = user_prompt_tmpl.format(
                cdd_context=cdd_context or "No CDD linked.",
                selected_module=request_body.selected_module,
                extra_instructions_block=extra_block,
            )

    # Call LLM.
    llm_result = generate_with_metadata(
        request_body.model_choice, system_prompt, user_prompt,
        usage_ctx=UsageLogContext(
            user_name=current_user.username,
            project_id=request_body.project_id,
            course_id=request_body.course_id,
            entity_type="blueprint",
        ),
    )

    if llm_result.status == "error":
        raise LLMGenerationError(
            f"Blueprint generation failed. Error type: {llm_result.error_type}"
        )

    raw_output = llm_result.text

    # Parse sections.
    from promptops_app.parsers.cdd_parser import parse_sections_from_text
    sections = parse_sections_from_text(raw_output)

    # Persist blueprint.
    bp_title = f"{request_body.selected_module} Blueprint"
    mod_match = re.search(r"\d+", request_body.selected_module or "")
    module_number = int(mod_match.group()) if mod_match else 1
    new_bp = ModuleBlueprint(
        title=bp_title,
        module_number=module_number,
        active_version="v1",
        project_id=request_body.project_id,
        course_id=request_body.course_id,
        created_by=current_user.username,
    )
    db.add(new_bp)
    db.commit()
    db.refresh(new_bp)

    version_record = BlueprintVersion(
        blueprint_id=new_bp.id,
        version="v1",
        full_content=raw_output,
        sections=json.dumps(sections),
        change_reason="Initial AI generation",
        is_active=True,
        created_by=current_user.username,
    )
    db.add(version_record)
    db.commit()

    # Auto-pin.
    set_active_blueprint(db, request_body.course_id, new_bp.id)

    # Parse components for the response.
    components = parse_blueprint_components(version_record)
    component_list = [BlueprintComponent(**c) for c in components]

    log_audit_event(db, current_user.username, "blueprint.created",
                    entity_type="blueprint", entity_id=new_bp.id,
                    project_id=request_body.project_id, course_id=request_body.course_id,
                    metadata={"title": bp_title})

    _log.info("blueprint_generate_complete  user=%s  bp_id=%d  components=%d",
              current_user.username, new_bp.id, len(component_list))

    return BlueprintGenerateResponse(
        blueprint_id=new_bp.id,
        title=bp_title,
        version="v1",
        sections_count=len(sections),
        components=component_list,
        full_content=raw_output,
        model_used=llm_result.model or request_body.model_choice,
        tokens_used=(
            (llm_result.prompt_tokens or 0) + (llm_result.completion_tokens or 0)
            if llm_result.prompt_tokens else None
        ),
        auto_pinned=True,
    )


@router.get("/{blueprint_id}", response_model=BlueprintRead, summary="Get a blueprint")
def get_blueprint(blueprint_id: int, db: Session = Depends(get_db), current_user=Depends(get_current_user)) -> BlueprintRead:
    """Return blueprint with its active version content."""
    from promptops_app.repositories import blueprint_repository
    bp = _get_blueprint_or_404(db, blueprint_id)
    result = BlueprintRead.model_validate(bp)
    if bp.active_version:
        ver = blueprint_repository.get_blueprint_version(db, blueprint_id, bp.active_version)
        if ver:
            result.active_content = BlueprintVersionRead.model_validate(ver)
    return result


@router.get("/{blueprint_id}/versions", response_model=list[BlueprintVersionListItem], summary="List blueprint versions")
def list_blueprint_versions(blueprint_id: int, db: Session = Depends(get_db), current_user=Depends(get_current_user)) -> list[BlueprintVersionListItem]:
    """List all saved versions for a blueprint."""
    from promptops_app.repositories import blueprint_repository
    _get_blueprint_or_404(db, blueprint_id)
    versions = blueprint_repository.list_blueprint_versions(db, blueprint_id)
    return [BlueprintVersionListItem.model_validate(v) for v in versions]


@router.get("/{blueprint_id}/versions/{version}", response_model=BlueprintVersionRead, summary="Get a specific version")
def get_blueprint_version(blueprint_id: int, version: str, db: Session = Depends(get_db), current_user=Depends(get_current_user)) -> BlueprintVersionRead:
    """Return full content of a specific blueprint version."""
    from promptops_app.repositories import blueprint_repository
    _get_blueprint_or_404(db, blueprint_id)
    ver = blueprint_repository.get_blueprint_version(db, blueprint_id, version)
    if not ver:
        raise NotFoundError(f"Blueprint version '{version}'", blueprint_id)
    return BlueprintVersionRead.model_validate(ver)


@router.post("/{blueprint_id}/versions/{version}/activate", response_model=BlueprintActivateVersionResponse, summary="Activate a blueprint version")
def activate_blueprint_version(blueprint_id: int, version: str, db: Session = Depends(get_db), current_user=Depends(require_permission("blueprint.version"))) -> BlueprintActivateVersionResponse:
    """Set a version as active. Deactivates all others."""
    from promptops_app.database import BlueprintVersion
    bp = _get_blueprint_or_404(db, blueprint_id)
    ver = db.query(BlueprintVersion).filter(BlueprintVersion.blueprint_id == blueprint_id, BlueprintVersion.version == version).first()
    if not ver:
        raise NotFoundError(f"Blueprint version '{version}'", blueprint_id)
    db.query(BlueprintVersion).filter(BlueprintVersion.blueprint_id == blueprint_id).update({BlueprintVersion.is_active: False})
    ver.is_active = True
    bp.active_version = version
    db.commit()
    _log.info("blueprint_version_activated  user=%s  bp_id=%d  version=%s", current_user.username, blueprint_id, version)
    return BlueprintActivateVersionResponse(blueprint_id=blueprint_id, active_version=version)


@router.post("/{blueprint_id}/versions", response_model=BlueprintVersionRead, status_code=201, summary="Commit a new blueprint version")
def create_blueprint_version(blueprint_id: int, request_body: BlueprintVersionCreateRequest, db: Session = Depends(get_db), current_user=Depends(require_permission("blueprint.version"))) -> BlueprintVersionRead:
    """Save edited content as a new named version."""
    from promptops_app.database import BlueprintVersion
    bp = _get_blueprint_or_404(db, blueprint_id)
    db.query(BlueprintVersion).filter(BlueprintVersion.blueprint_id == blueprint_id).update({BlueprintVersion.is_active: False})
    new_ver = BlueprintVersion(
        blueprint_id=blueprint_id, version=request_body.version_tag,
        full_content=request_body.full_content, sections=json.dumps(request_body.sections),
        change_reason=request_body.change_reason, is_active=True, created_by=current_user.username,
    )
    db.add(new_ver)
    bp.active_version = request_body.version_tag
    db.commit()
    db.refresh(new_ver)
    _log.info("blueprint_version_committed  user=%s  bp_id=%d  version=%s", current_user.username, blueprint_id, request_body.version_tag)
    return BlueprintVersionRead.model_validate(new_ver)


@router.post("/{blueprint_id}/pin", response_model=BlueprintPinResponse, summary="Pin blueprint to a course")
def pin_blueprint(blueprint_id: int, request_body: BlueprintPinRequest, db: Session = Depends(get_db), current_user=Depends(require_permission("blueprint.pin"))) -> BlueprintPinResponse:
    """Set as active blueprint for generation. Equivalent to the '📌 Set as Active Blueprint' button."""
    from promptops_app.repositories.course_repository import set_active_blueprint
    _get_blueprint_or_404(db, blueprint_id)
    set_active_blueprint(db, request_body.course_id, blueprint_id)
    _log.info("blueprint_pinned  user=%s  bp_id=%d  course_id=%d", current_user.username, blueprint_id, request_body.course_id)
    return BlueprintPinResponse(blueprint_id=blueprint_id, course_id=request_body.course_id)


@router.get(
    "/{blueprint_id}/components",
    response_model=BlueprintComponentsResponse,
    summary="Get parseable components from the active blueprint version",
    description="Returns lessons, assessments, and activities for the Generate page Content Type dropdown.",
)
def get_blueprint_components(
    blueprint_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
) -> BlueprintComponentsResponse:
    """
    Parse blueprint sections into a structured component list.

    Calls parse_blueprint_components() from blueprint_parser.py — the same
    function used in the Streamlit Generate page to build the Content Type dropdown.
    """
    from promptops_app.parsers.blueprint_parser import parse_blueprint_components
    from promptops_app.repositories import blueprint_repository

    bp = _get_blueprint_or_404(db, blueprint_id)
    ver = blueprint_repository.get_blueprint_version(db, blueprint_id, bp.active_version) if bp.active_version else None

    components = parse_blueprint_components(ver) if ver else []
    return BlueprintComponentsResponse(
        blueprint_id=blueprint_id,
        components=[BlueprintComponent(**c) for c in components],
    )


@router.get(
    "/{blueprint_id}/completion-status",
    summary="Module lesson completion status (assessment generation gate)",
    description="Returns whether all blueprint lessons have been generated.",
)
def get_blueprint_completion_status(
    blueprint_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Gate module assessment generation — mirrors Streamlit get_module_completion_status."""
    from app.schemas.generation import CompletionStatusResponse
    from promptops_app.core.content_utils import get_module_completion_status

    _get_blueprint_or_404(db, blueprint_id)
    status = get_module_completion_status(db, blueprint_id)
    return CompletionStatusResponse(
        completed=status.get("completed", False),
        generated_lessons=status.get("generated_lessons", 0),
        total_lessons=status.get("total_lessons", 0),
        missing_lessons=status.get("lesson_labels", []),
    )


@router.get("/{blueprint_id}/export", summary="Download blueprint as a file")
def export_blueprint(
    blueprint_id: int,
    format: str = Query(default="docx", description="docx | md"),
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("export.course")),
) -> Response:
    """Export the active blueprint version as a downloadable file."""
    from promptops_app.repositories import blueprint_repository
    from promptops_app.services.export_service import ExportRequest, export_content

    bp = _get_blueprint_or_404(db, blueprint_id)
    if not bp.active_version:
        raise WorkflowError("This blueprint has no active version to export.")

    ver = blueprint_repository.get_blueprint_version(db, blueprint_id, bp.active_version)
    if not ver:
        raise NotFoundError(f"Blueprint version '{bp.active_version}'", blueprint_id)

    export_req = ExportRequest(
        fmt=format, topic=bp.title,
        blocks=[("Blueprint Content", ver.full_content or "")],
        user_name=current_user.username, is_admin=(current_user.role == "admin"),
        entity_type="blueprint", entity_id=bp.id,
        file_name=f"Blueprint_{bp.title.replace(' ', '_')}_{bp.active_version}.{format}",
    )
    result = export_content(db, export_req)
    if not result.success:
        raise WorkflowError(f"Export failed: {result.error_message}")

    return Response(
        content=result.data, media_type=result.mime_type,
        headers={"Content-Disposition": f'attachment; filename="{result.file_name}"'},
    )
