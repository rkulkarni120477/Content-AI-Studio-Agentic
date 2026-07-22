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
  GET    /blueprints/{id}/export-lessons Download all generated lessons for the module as one file
"""

from __future__ import annotations

import json
import logging
import re

from fastapi import APIRouter, Depends, Query
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
    BlueprintRegenerateItemRequest,
    BlueprintRegenerateItemResponse,
    BlueprintRegenerateSectionRequest,
    BlueprintRegenerateSectionResponse,
    BlueprintVersionCreateRequest,
    BlueprintVersionListItem,
    BlueprintVersionRead,
)
from app.schemas.common import PaginatedResponse
from app.core.dis_client import dis_client

_log = logging.getLogger(__name__)
router = APIRouter()


def _dis_context_block(purpose: str, payload: dict, current_user, label: str) -> tuple[str, list]:
    try:
        result = dis_client.retrieve_context_sync(purpose, payload, current_user=current_user)
        ctx = str(result.get("combined_context") or "").strip()
        units = result.get("source_units") or result.get("sources") or []
        if ctx:
            return (
                f"\n\n---\n{label} FROM DIS SOURCE LIBRARY\n"
                "Use this as source grounding only. Follow approved CDD, active style, selected module, and requested mode first. "
                "Do not expose internal DIS metadata.\n\n"
                f"{ctx}\n---\n",
                units,
            )
    except Exception as exc:
        _log.warning("dis_%s_context_unavailable error=%s", purpose, exc)
    return "", []


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
    from promptops_app.parsers.cdd_parser import extract_cdd_summary, extract_module_section
    from promptops_app.prompts.prompt_builder import PromptVariableError, build_prompt
    from promptops_app.repositories import blueprint_repository, cdd_repository, style_repository
    from promptops_app.repositories.course_repository import set_active_blueprint
    from promptops_app.services.audit_service import log_audit_event
    from promptops_app.services.llm_service import generate_with_metadata
    from promptops_app.services.usage_service import UsageLogContext
    from promptops_app.core.llm_client import safe_json_loads

    _log.info("blueprint_generate_start  user=%s  course=%d  module=%s",
              current_user.username, request_body.course_id, request_body.selected_module)

    # Resolve course (needed for cluster_id below) and CDD context.
    from promptops_app.repositories import course_repository
    course = course_repository.get_course_by_id(db, request_body.course_id)
    cdd_id = request_body.cdd_id or (course.active_cdd_id if course else None)

    cdd_context = ""
    if cdd_id:
        cdd_version = get_active_cdd_version(db, cdd_id)
        if cdd_version:
            cdd_context = extract_cdd_summary(cdd_version)
            module_section = extract_module_section(cdd_version, request_body.selected_module)
            if module_section:
                cdd_context = (
                    f"{cdd_context}\n\n"
                    f"**FULL DETAIL FOR {request_body.selected_module} "
                    f"(verbatim from the CDD's Course Structure — lessons, objectives, "
                    f"assessments defined for this module):**\n{module_section}"
                )

    dis_context_block, dis_source_units = _dis_context_block(
        "blueprint",
        {
            "purpose": "blueprint",
            "query": " ".join(str(x or "") for x in [
                request_body.selected_module,
                "",
                request_body.extra_instructions,
                cdd_context,
                "teacher" if request_body.teacher_mode else "student",
            ]),
            "filters": {
                "purpose": "blueprint",
                "selected_module": request_body.selected_module,
                # No hard document_types filter here. The DIS blueprint handler
                # already narrows to calendar/syllabus (content_types), and the
                # fixed names below did not match real stored doc types. Retrieval
                # relies on purpose + semantic ranking; security allow-set applies.
            },
            "retrieval": {"top_k": 12, "token_budget": 12000},
        },
        current_user,
        "BLUEPRINT CONTEXT",
    )

    # Build prompt.
    style_context = ""
    if request_body.style_id:
        style = style_repository.get_style_by_id(db, request_body.style_id)
        if style:
            style_context = build_style_context(db, style, cluster_id=course.cluster_id if course else None)

    extra_block = request_body.extra_instructions or ""
    if dis_context_block:
        extra_block = f"{extra_block}\n\n{dis_context_block}".strip()
    if style_context:
        extra_block = f"**ACTIVE STYLE:**\n{style_context}\n\n{extra_block}"

    if request_body.system_prompt_override and request_body.user_prompt_override:
        system_prompt = request_body.system_prompt_override
        user_prompt = request_body.user_prompt_override
        if dis_context_block:
            user_prompt = f"{user_prompt}\n\n{dis_context_block}"
        # Persist the override with the artifact (PL↔CAS sync review, plan
        # Phase 11) — inline-authored prompt text must stay recoverable.
        prompt_provenance = {
            "prompt_source": "override",
            "system_prompt_override": request_body.system_prompt_override,
            "user_prompt_override": request_body.user_prompt_override,
        }
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
            system_prompt, user_prompt, _tpl_name, _tpl_version = build_prompt(
                "blueprint_generation", variables, db=db,
                project_id=course.project_id if course else request_body.project_id,
                cluster_id=course.cluster_id if course else None,
                course_id=request_body.course_id,
                # Student/teacher are independently-versioned rows once authored
                # (variant-exact wins); the NULL-variant seeded default serves
                # both modes until then. Ignored while the resolution flag is off.
                variant="teacher" if request_body.teacher_mode else "student",
                # A user-selected pipeline prompt (dropdown) wins over scope/default.
                prompt_id=request_body.prompt_id,
            )
        except PromptVariableError as exc:
            # A declared-variable violation is a template misconfiguration —
            # surface it to the admin; never silently swap in the constant
            # fallback (that would mask which prompt generation actually used).
            raise PromptConfigurationError(
                str(exc),
                detail={"template": exc.template, "missing": exc.missing},
            ) from exc
        except Exception:
            mode = "teacher" if request_body.teacher_mode else "student"
            system_prompt, user_prompt_tmpl, _ = get_blueprint_prompts(mode)
            user_prompt = user_prompt_tmpl.format(
                cdd_context=cdd_context or "No CDD linked.",
                selected_module=request_body.selected_module,
                extra_instructions_block=extra_block,
            )
            prompt_provenance = {"prompt_source": "builtin_fallback"}
        else:
            prompt_provenance = {
                "prompt_source": "registry",
                "prompt_name": _tpl_name,
                "prompt_version": _tpl_version,
            }

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
    if request_body.is_course_end:
        # Sentinel for course-level end items (capstones, etc.) — distinguishes
        # them from real modules, which are always >= 1.
        module_number = 0
    else:
        mod_match = re.search(r"\d+", request_body.selected_module or "")
        module_number = int(mod_match.group()) if mod_match else 1
    new_bp = ModuleBlueprint(
        cdd_id=cdd_id,
        title=bp_title,
        module_title=f"Module {module_number}",
        module_number=module_number,
        active_version="v1",
        project_id=request_body.project_id,
        course_id=request_body.course_id,
        created_by=current_user.username,
    )
    db.add(new_bp)
    db.commit()
    db.refresh(new_bp)

    cdd_title = ""
    if cdd_id:
        cdd_row = cdd_repository.get_cdd_by_id(db, cdd_id)
        cdd_title = cdd_row.title if cdd_row else ""

    generation_params = {
        "cdd_id": cdd_id,
        "cdd_title": cdd_title,
        "module_number": module_number,
        "extra_instructions": request_body.extra_instructions or "",
        "dis_source_units": dis_source_units,
        "mode": "teacher" if request_body.teacher_mode else "student",
        # Prompt provenance — registry template (name+version), the full
        # inline-override text, or builtin_fallback (see the build above).
        **prompt_provenance,
    }

    version_record = BlueprintVersion(
        blueprint_id=new_bp.id,
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

    # Copy generated Blueprint body to DIS/S3 for retrieval and listing.
    try:
        dis_client.generated_upsert_sync({
            "generated_doc_id": f"blueprint_{new_bp.id}",
            "generated_type": "blueprint",
            "title": bp_title,
            "content": raw_output,
            "summary": raw_output[:500],
            "active": True,
            "metadata": {
                "selected_module": request_body.selected_module,
                "module_number": module_number,
                "course_id": request_body.course_id,
                "project_id": request_body.project_id,
                "cdd_id": cdd_id,
            },
            "source_documents_used": dis_source_units,
            "cas_ref": {"entity": "blueprint", "id": new_bp.id},
            "created_by": current_user.username,
        }, current_user=current_user)
    except Exception as exc:
        _log.warning("dis_generated_blueprint_upsert_failed blueprint_id=%s error=%s", new_bp.id, exc)

    # Auto-pin.
    set_active_blueprint(db, request_body.course_id, new_bp.id)

    # Parse components for the response.
    components = parse_blueprint_components(version_record)
    component_list = [BlueprintComponent(**c) for c in components]

    log_audit_event(db, current_user.username, "blueprint.created",
                    entity_type="blueprint", entity_id=new_bp.id,
                    project_id=request_body.project_id, course_id=request_body.course_id,
                    metadata={
                        "title": bp_title,
                        "model_choice": request_body.model_choice,
                        "selected_module": request_body.selected_module,
                        "cdd_id": cdd_id, "cdd_title": cdd_title,
                        "mode": "teacher" if request_body.teacher_mode else "student",
                        "extra_instructions": request_body.extra_instructions,
                        "dis_source_units_count": len(dis_source_units) if dis_source_units else 0,
                        "input_mode": "full",
                        "system_prompt": system_prompt,
                        "user_prompt": user_prompt,
                        "output": raw_output,
                    })

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


# ---------------------------------------------------------------------------
# Regeneration (AI) — ports the Streamlit blueprint regenerate controls
# ---------------------------------------------------------------------------

def _blueprint_cdd_summary(db: Session, bp, max_chars: int) -> str:
    """Return a short CDD summary for a blueprint's linked CDD, or '' if none."""
    if not getattr(bp, "cdd_id", None):
        return ""
    from promptops_app.database import get_active_cdd_version
    from promptops_app.parsers.cdd_parser import extract_cdd_summary
    cdd_version = get_active_cdd_version(db, bp.cdd_id)
    if not cdd_version:
        return ""
    return extract_cdd_summary(cdd_version, max_chars=max_chars) or ""


@router.post(
    "/{blueprint_id}/regenerate-item",
    response_model=BlueprintRegenerateItemResponse,
    summary="Regenerate a single item within a blueprint section",
    responses={404: {"description": "Blueprint or item not found."}},
)
def regenerate_blueprint_item(
    blueprint_id: int,
    request_body: BlueprintRegenerateItemRequest,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("blueprint.version")),
) -> BlueprintRegenerateItemResponse:
    """
    Regenerate one item inside a blueprint section, preserving all siblings.
    Replicates the per-item "⟳" button in the Streamlit Blueprint tab.

    Stateless: returns the patched section content; the frontend commits a
    new blueprint version (matching the "Save Edit" flow).
    """
    from promptops_app.parsers.blueprint_parser import (
        parse_items_from_section,
        patch_item_in_section,
        regen_single_item,
    )

    bp = _get_blueprint_or_404(db, blueprint_id)

    original = request_body.section_content or ""
    item_index = request_body.item_index
    items = parse_items_from_section(original)
    if not items or item_index < 0 or item_index >= len(items):
        raise NotFoundError("Blueprint item", item_index)

    cdd_summary = _blueprint_cdd_summary(db, bp, max_chars=800)
    context = (
        f"Module: {bp.module_title}. "
        f"CDD context: {cdd_summary[:300] if cdd_summary else 'N/A'}."
    )
    instruction = f"{(request_body.feedback or '').strip()} {context}".strip()

    new_item_text = regen_single_item(
        section_title=request_body.section_key,
        section_content=original,
        item_index=item_index,
        item_text=items[item_index]["text"],
        custom_instruction=instruction,
        model_choice=request_body.model_choice,
    )
    updated_content = patch_item_in_section(original, item_index, new_item_text)

    _log.info("blueprint_item_regenerated  user=%s  bp_id=%d  section=%s  item=%d",
              current_user.username, blueprint_id, request_body.section_key, item_index)

    return BlueprintRegenerateItemResponse(
        updated_content=updated_content,
        patched_item=new_item_text or "",
    )


@router.post(
    "/{blueprint_id}/regenerate-section",
    response_model=BlueprintRegenerateSectionResponse,
    summary="Regenerate an entire blueprint section with AI",
    responses={404: {"description": "Blueprint not found."}},
)
def regenerate_blueprint_section(
    blueprint_id: int,
    request_body: BlueprintRegenerateSectionRequest,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("blueprint.version")),
) -> BlueprintRegenerateSectionResponse:
    """
    Regenerate a whole blueprint section using the section-regeneration prompt
    (student or teacher variant). Replicates "🔄 Regenerate Section" in Streamlit.

    Stateless: returns the new section content; the frontend commits a version.
    """
    from promptops_app.parsers.blueprint_parser import get_blueprint_prompts
    from promptops_app.services.llm_service import generate_text as call_llm

    bp = _get_blueprint_or_404(db, blueprint_id)
    mode = "teacher" if request_body.teacher_mode else "student"
    regen_system, _, regen_template = get_blueprint_prompts(mode)
    cdd_summary = _blueprint_cdd_summary(db, bp, max_chars=1500)

    regen_prompt = regen_template.format(
        section_title=request_body.section_key,
        module_title=bp.module_title,
        course_title=bp.title,
        cdd_summary=cdd_summary or "No CDD linked.",
        custom_instruction=request_body.feedback or "Improve this section.",
    )
    new_content = call_llm(request_body.model_choice, regen_system, regen_prompt)
    if not new_content or new_content.startswith("ERROR"):
        raise LLMGenerationError("Section regeneration failed. Please try again.")

    _log.info("blueprint_section_regenerated  user=%s  bp_id=%d  section=%s  mode=%s",
              current_user.username, blueprint_id, request_body.section_key, mode)

    return BlueprintRegenerateSectionResponse(updated_content=new_content.strip())


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
        missing_lessons=status.get("missing_lesson_labels", []),
    )


@router.get("/{blueprint_id}/export", summary="Download blueprint as a file")
def export_blueprint(
    blueprint_id: int,
    format: str = Query(default="docx", description="docx | md"),
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("export.course")),
) -> Response:
    """Export the active blueprint version as a downloadable file."""
    from promptops_app.core.llm_client import safe_json_loads
    from promptops_app.parsers.blueprint_parser import _is_bp_section_hidden
    from promptops_app.parsers.cdd_parser import _strip_ui_hidden_text, parse_sections_from_text
    from promptops_app.repositories import blueprint_repository
    from promptops_app.services.export_service import ExportRequest, export_content

    bp = _get_blueprint_or_404(db, blueprint_id)
    if not bp.active_version:
        raise WorkflowError("This blueprint has no active version to export.")

    ver = blueprint_repository.get_blueprint_version(db, blueprint_id, bp.active_version)
    if not ver:
        raise NotFoundError(f"Blueprint version '{bp.active_version}'", blueprint_id)

    # Build export blocks from the same UI-visible sections as the renderer —
    # filtered through _is_bp_section_hidden + _strip_ui_hidden_text so backend-only
    # scaffolding (Step 1/5, Narrative, Modularity, "blueprint complete/ready") never
    # leaks into the downloaded file.
    raw_sections = safe_json_loads(ver.sections) if ver.sections else {}
    if not raw_sections and ver.full_content:
        raw_sections = parse_sections_from_text(ver.full_content)

    export_blocks = []
    for section_key, section_value in raw_sections.items():
        if _is_bp_section_hidden(section_key):
            continue
        content = (section_value or "").strip()
        if not content:
            continue
        content = _strip_ui_hidden_text(content)
        if not content:
            continue
        export_blocks.append((section_key, content))

    if not export_blocks:
        export_blocks = [("Blueprint Content", ver.full_content or "")]

    export_req = ExportRequest(
        fmt=format, topic=bp.title,
        blocks=export_blocks,
        user_name=current_user.username, is_admin=(current_user.role == "admin"),
        entity_type="blueprint", entity_id=bp.id,
        file_name=f"Blueprint_{bp.title.replace(' ', '_')}_{bp.active_version}.{format}",
    )
    result = export_content(db, export_req)
    if not result.success:
        raise WorkflowError(f"Export failed: {result.error_message}")

    return Response(
        content=result.data, media_type=result.mime_type,
        headers={"Content-Disposition": content_disposition(result.file_name)},
    )


@router.get("/{blueprint_id}/export-lessons", summary="Export all generated lessons in a module as one combined file")
def export_module_lessons(
    blueprint_id: int,
    format: str = Query(default="md", description="md | docx"),
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("export.course")),
) -> Response:
    """
    Combine every lesson generated for this module (blueprint) into a single file.

    Only the most recent generation per distinct lesson topic is included (a lesson
    regenerated 9 times still contributes one section), and only blocks in an
    exportable workflow state (approved/published) — mirrors export_course.
    """
    from promptops_app.repositories import generation_repository
    from promptops_app.services.export_service import ExportRequest, export_content
    from promptops_app.core.constants import WorkflowState

    bp = _get_blueprint_or_404(db, blueprint_id)

    latest_gens = generation_repository.list_latest_generations_for_blueprint(db, blueprint_id)
    if not latest_gens:
        raise WorkflowError("No generated lessons found for this module.")

    gen_ids = [g.id for g in latest_gens]
    all_blocks = generation_repository.list_blocks_for_gen_ids(db, gen_ids)
    exportable_blocks = [b for b in all_blocks if b.workflow_state.lower() in WorkflowState.EXPORTABLE]
    if not exportable_blocks:
        raise WorkflowError("No approved or published lessons found for this module yet.")

    # Order sections by lesson number (parsed from the generation's topic), not by
    # database insertion order, so the combined file reads Lesson 1 -> Lesson 2 -> ...
    topic_by_gen_id = {g.id: (g.topic or "") for g in latest_gens}

    def _lesson_order(block):
        topic = topic_by_gen_id.get(block.generation_id, "")
        m = re.match(r"lesson\s*(\d+)", topic, re.I)
        return (int(m.group(1)) if m else 9999, topic, block.id)

    exportable_blocks.sort(key=_lesson_order)

    export_req = ExportRequest(
        fmt=format, topic=bp.title,
        blocks=[(b.block_label, b.content or "") for b in exportable_blocks],
        user_name=current_user.username, is_admin=(current_user.role == "admin"),
        entity_type="module", entity_id=bp.id,
        file_name=f"{bp.title.replace(' ', '_')}_lessons.{format}",
    )
    result = export_content(db, export_req)
    if not result.success:
        raise WorkflowError(f"Export failed: {result.error_message}")

    _log.info("module_lessons_exported  user=%s  blueprint_id=%d  format=%s  blocks=%d",
              current_user.username, blueprint_id, format, len(exportable_blocks))

    return Response(
        content=result.data, media_type=result.mime_type,
        headers={"Content-Disposition": content_disposition(result.file_name)},
    )
