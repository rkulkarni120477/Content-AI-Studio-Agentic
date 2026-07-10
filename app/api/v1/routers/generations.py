"""
Generations router — content generation job submission and status.

Streamlit equivalent: ``pages/generate.py`` render_page()

The generation pipeline is async: clicking "Launch Pipeline" creates a
GenerationJob and hands it to a Celery worker. The client polls
GET /api/v1/jobs/{job_id} for progress updates.

Endpoints:
  POST   /generations/launch                Submit a generation job
  GET    /generations                        List generations for a course
  GET    /generations/{id}                   Get generation with blocks
  GET    /generations/{id}/completion-status Module completion check
  GET    /courses/{id}/completion-status     Course-level completion check
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response
from sqlalchemy.orm import Session

from app.core.dependencies import get_current_user, get_db, require_permission
from app.core.exceptions import NotFoundError, ValidationError
from app.schemas.common import JobAcceptedResponse, PaginatedResponse
from app.core.dis_client import dis_client
from app.schemas.generation import (
    CompletionStatusResponse,
    GenerationLaunchRequest,
    GenerationListItem,
    GenerationRead,
    GenerationBlockSummary,
)

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
                "Use this as grounding for generated content. Follow active Style, approved CDD, active Blueprint, and selected content type first. "
                "Do not expose internal DIS metadata.\n\n"
                f"{ctx}\n---\n",
                units,
            )
    except Exception as exc:
        _log.warning("dis_%s_context_unavailable error=%s", purpose, exc)
    return "", []


@router.post(
    "/launch",
    response_model=JobAcceptedResponse,
    status_code=202,
    summary="Launch the content generation pipeline",
    description=(
        "Creates a GenerationJob record and submits it to the Celery worker queue. "
        "Returns immediately with a job_id. Poll GET /api/v1/jobs/{job_id} for status."
    ),
)
def launch_generation(
    request_body: GenerationLaunchRequest,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("generate.run")),
) -> JobAcceptedResponse:
    """
    Submit a content generation job.

    Replicates the "🚀 Launch Pipeline" button in the Streamlit Generate page.
    Validates the prompt template, resolves the effective CDD/Blueprint,
    creates a GenerationJob row, and submits to the Celery worker.
    """
    from promptops_app.jobs import generation_jobs, job_runner
    from promptops_app.repositories import blueprint_repository, cdd_repository, job_repository
    from promptops_app.core.content_utils import get_module_completion_status, get_all_modules_completion
    from promptops_app.parsers.blueprint_parser import is_component_type_module_level, is_component_type_course_level

    # Resolve effective CDD and blueprint IDs (fall back to course active IDs).
    eff_cdd_id = request_body.cdd_id
    eff_bp_id = request_body.blueprint_id

    if not eff_cdd_id or not eff_bp_id:
        from promptops_app.repositories import course_repository
        course = course_repository.get_course_by_id(db, request_body.course_id)
        if course:
            eff_cdd_id = eff_cdd_id or course.active_cdd_id
            eff_bp_id = eff_bp_id or course.active_blueprint_id

    # Completion gate — check if module/course prerequisites are met.
    if not request_body.assessment_override and eff_bp_id:
        if is_component_type_module_level(request_body.component_value):
            status = get_module_completion_status(db, eff_bp_id)
            if not status.get("completed"):
                raise ValidationError(
                    f"Not all lessons are complete ({status.get('generated_lessons', 0)}/"
                    f"{status.get('total_lessons', 0)}). "
                    "Set assessment_override=true to proceed anyway."
                )
        elif is_component_type_course_level(request_body.component_value) and eff_cdd_id:
            status = get_all_modules_completion(db, eff_cdd_id)
            if not status.get("completed"):
                raise ValidationError(
                    "Not all modules are complete. Complete all modules before generating course-level content."
                )

    dis_context_block, dis_source_units = _dis_context_block(
        "course-generation",
        {
            "purpose": "course_generation",
            "query": " ".join(str(x or "") for x in [
                request_body.component_label,
                request_body.component_value,
                request_body.component_type,
                request_body.target_audience,
                request_body.expert_domain,
                request_body.audience_category,
                request_body.extra_instructions,
            ]),
            "filters": {
                "purpose": "course_generation",
                "component_label": request_body.component_label,
                "component_type": request_body.component_type,
                "document_types": ["textbook_chapter", "activity", "assessment", "rubric", "lesson_plan", "slide_deck", "student_handout", "style_guide", "authoring_guide"],
            },
            "retrieval": {"top_k": 16, "token_budget": 16000},
        },
        current_user,
        "COURSE GENERATION CONTEXT",
    )

    # Build the request params dict (matches the structure used by generation_jobs.py).
    req_params = {
        "topic":               request_body.component_label,
        "b_type":              request_body.component_value,
        "eff_cdd_id":          eff_cdd_id,
        "eff_bp_id":           eff_bp_id,
        "model_choice":        request_body.model_choice,
        "target_audience":     request_body.target_audience,
        "expert_domain":       request_body.expert_domain,
        "aud_cat":             request_body.audience_category,
        "ctx_docs":            request_body.context_document_names,
        "user_name":           current_user.username,
        "project_id":          request_body.project_id,
        "course_id":           request_body.course_id,
        "selected_component":  {
            "label":    request_body.component_label,
            "value":    request_body.component_value,
            "type":     request_body.component_type,
            "metadata": {},
        },
        "supplementary_files": [f.model_dump() for f in request_body.supplementary_files],
        "extra_instructions":  (request_body.extra_instructions or "") + (dis_context_block or ""),
        "dis_source_units":    dis_source_units,
    }

    job_id = job_repository.create_job(
        db,
        user_name=current_user.username,
        request_params=req_params,
        project_id=request_body.project_id,
        course_id=request_body.course_id,
    )

    job_runner.submit(generation_jobs.run_generation_job, job_id)

    _log.info("generation_launched  user=%s  job_id=%s  component=%s  prompt=%s",
              current_user.username, job_id, request_body.component_label, request_body.prompt_name)

    return JobAcceptedResponse(
        job_id=str(job_id),
        status="queued",
        status_url=f"/api/v1/jobs/{job_id}",
    )


@router.get(
    "",
    response_model=PaginatedResponse[GenerationListItem],
    summary="List generations for a course",
)
def list_generations(
    project_id: int | None = Query(default=None),
    course_id: int | None = Query(default=None),
    blueprint_id: int | None = Query(default=None, description="Pin filter — active blueprint."),
    cdd_id: int | None = Query(default=None, description="Pin filter — active CDD (when no blueprint)."),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
) -> PaginatedResponse[GenerationListItem]:
    """Return recent generations. Used by the Editor page to list available content."""
    from promptops_app.repositories import generation_repository

    generations = generation_repository.list_editor_generations(
        db,
        course_id=course_id,
        project_id=project_id,
        blueprint_id=blueprint_id,
        cdd_id=cdd_id if not blueprint_id else None,
        limit=500,
    )
    total = len(generations)
    start = (page - 1) * page_size

    items = []
    for g in generations[start: start + page_size]:
        item = GenerationListItem.model_validate(g)
        # Count blocks without loading them all.
        from promptops_app.repositories import generation_repository as _gr
        item.block_count = _gr.count_blocks_for_generation(db, g.id)
        items.append(item)

    return PaginatedResponse.create(items=items, total=total, page=page, page_size=page_size)


@router.get(
    "/{generation_id}/export",
    summary="Export all blocks in a generation as a file",
    description="Supports md, json, html, docx, pdf. Matches Streamlit per-generation export buttons.",
)
def export_generation(
    generation_id: int,
    format: str = Query(default="docx", description="md | json | html | docx | pdf"),
    template: str = Query(default="default"),
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("export.course")),
) -> Response:
    from promptops_app.repositories import generation_repository
    from promptops_app.services.export_service import ExportRequest, export_content
    from app.core.exceptions import WorkflowError

    gen = generation_repository.get_generation_by_id(db, generation_id)
    if not gen:
        raise NotFoundError("Generation", generation_id)

    blocks = generation_repository.list_blocks_for_generation(db, generation_id)
    if not blocks:
        raise WorkflowError("No blocks found for this generation.")

    export_req = ExportRequest(
        fmt=format,
        topic=gen.topic or f"Generation #{generation_id}",
        blocks=[(b.block_label, b.content or "") for b in blocks],
        user_name=current_user.username,
        is_admin=(current_user.role == "admin"),
        entity_type="generation",
        entity_id=generation_id,
        template=template,
        file_name=f"{(gen.topic or 'generation').replace(' ', '_')}.{format}",
    )
    result = export_content(db, export_req)
    if not result.success:
        raise WorkflowError(result.error_message or "Export failed.")

    return Response(
        content=result.data,
        media_type=result.mime_type,
        headers={"Content-Disposition": f'attachment; filename="{result.file_name}"'},
    )


@router.get(
    "/{generation_id}",
    response_model=GenerationRead,
    summary="Get a generation with its block list",
)
def get_generation(
    generation_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
) -> GenerationRead:
    """Return a generation record and lightweight block list."""
    from promptops_app.repositories import generation_repository

    gen = generation_repository.get_generation_by_id(db, generation_id)
    if not gen:
        raise NotFoundError("Generation", generation_id)

    result = GenerationRead.model_validate(gen)
    blocks = generation_repository.list_blocks_for_generation(db, generation_id)
    result.blocks = [GenerationBlockSummary.model_validate(b) for b in blocks]
    return result


@router.get(
    "/{generation_id}/completion-status",
    response_model=CompletionStatusResponse,
    summary="Check module completion status (for assessment generation gate)",
    description=(
        "Returns whether all lessons in the blueprint have been generated. "
        "Used to determine if the Module Assessment can be generated."
    ),
)
def get_module_completion(
    generation_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
) -> CompletionStatusResponse:
    """Check if all lessons in the current blueprint are complete."""
    from promptops_app.core.shared import get_module_completion_status
    from promptops_app.repositories import generation_repository

    gen = generation_repository.get_generation_by_id(db, generation_id)
    if not gen:
        raise NotFoundError("Generation", generation_id)

    status = get_module_completion_status(db, gen.blueprint_id)
    return CompletionStatusResponse(
        completed=status.get("completed", False),
        generated_lessons=status.get("generated_lessons", 0),
        total_lessons=status.get("total_lessons", 0),
        missing_lessons=status.get("missing_lessons", []),
    )


@router.get(
    "/course/{course_id}/completion-status",
    response_model=CompletionStatusResponse,
    summary="Check course-level completion status",
    description="Returns whether all modules are complete. Used to gate course-level assessment generation.",
)
def get_course_completion(
    course_id: int,
    cdd_id: int | None = Query(
        default=None,
        description="Effective CDD id (e.g. override). Falls back to the course active CDD.",
    ),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
) -> CompletionStatusResponse:
    """Check if all modules in the course are complete."""
    from promptops_app.core.content_utils import get_all_modules_completion
    from promptops_app.repositories import course_repository

    course = course_repository.get_course_by_id(db, course_id)
    if not course:
        raise NotFoundError("Course", course_id)

    eff_cdd_id = cdd_id or course.active_cdd_id
    status = get_all_modules_completion(db, eff_cdd_id)
    return CompletionStatusResponse(
        completed=status.get("completed", False),
        generated_lessons=status.get("completed_modules", 0),
        total_lessons=status.get("total_modules", 0),
    )
