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

from app.core.dependencies import get_current_user, get_db, get_tenant_context, require_permission
from app.core.http import content_disposition
from app.core.exceptions import NotFoundError, ValidationError
from app.schemas.common import JobAcceptedResponse, PaginatedResponse
from app.core.config import settings
from app.core.dis_client import dis_client
from app.core.dis_access import resolve_course_dis_client
from app.core.dis_day_context import _dis_day_context_block, _render_day_context
from app.schemas.generation import (
    CompletionStatusResponse,
    GenerationLaunchRequest,
    GenerationListItem,
    GenerationRead,
    GenerationBlockSummary,
)

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
    from promptops_app.jobs import dispatch, generation_jobs
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

    # Scope retrieval to the COURSE's own Source Library (its project's client),
    # so it reads the right client's documents regardless of who runs the
    # generation. Empty -> falls back to per-user default.
    gen_client_id = resolve_course_dis_client(
        db, course_id=request_body.course_id, project_id=request_body.project_id,
    )

    dis_context_block, dis_source_units = "", []
    # §7 structured-first: when the request pins a block+day and the digest
    # pipeline is on for this client, ground on the complete day bundle (units +
    # digest + bounded kNN) instead of the free-text blob query (§5.4 anti-pattern).
    # Any DIS failure returns ('', []) and we fall through to the legacy path.
    if request_body.block and request_body.day and settings.digest_pipeline_on_for(gen_client_id):
        dis_context_block, dis_source_units = _dis_day_context_block(
            request_body.block, request_body.day, current_user,
            "COURSE GENERATION CONTEXT", client_id=gen_client_id,
        )

    if not dis_context_block:
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
                    # No hard document_types filter: those fixed names did not match
                    # real stored doc types (AIM: lesson_pdf/quiz/project; Cengage: pdf),
                    # which silently returned zero results. Retrieval now relies on
                    # purpose + semantic ranking; the security allow-set still applies.
                },
                "retrieval": {"top_k": 16, "token_budget": 16000},
            },
            current_user,
            "COURSE GENERATION CONTEXT",
            client_id=gen_client_id,
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
        # User-selected pipeline prompt (dropdown). None → default resolution.
        "prompt_id":           request_body.prompt_id,
    }

    job_id = job_repository.create_job(
        db,
        user_name=current_user.username,
        request_params=req_params,
        project_id=request_body.project_id,
        course_id=request_body.course_id,
    )

    dispatch.submit(generation_jobs.run_generation_job, job_id)

    # Log-only, read-only lookups to report exactly where the CDD/blueprint/
    # style used for this generation came from. Does not affect req_params,
    # job creation, or the response — purely observability.
    from promptops_app.database import Course, Project, get_active_style

    cdd_source = "explicit" if request_body.cdd_id else "course_active"
    bp_source = "explicit" if request_body.blueprint_id else "course_active"

    active_style = get_active_style(db, project_id=request_body.project_id, course_id=request_body.course_id)
    style_source = None
    if active_style:
        course_obj = db.get(Course, request_body.course_id) if request_body.course_id else None
        if course_obj and course_obj.active_style_id == active_style.id:
            style_source = "course"
        else:
            proj_obj = db.get(Project, request_body.project_id) if request_body.project_id else None
            style_source = "project" if proj_obj and proj_obj.active_style_id == active_style.id else "global_default"

    _log.info("generation_launched", extra={
        "event": "generation_launched",
        "user": current_user.username,
        "job_id": str(job_id),
        "project_id": request_body.project_id,
        "course_id": request_body.course_id,
        "component_label": request_body.component_label,
        "component_value": request_body.component_value,
        "component_type": request_body.component_type,
        "prompt_name": request_body.prompt_name,
        "model_choice": request_body.model_choice,
        "cdd_id": eff_cdd_id,
        "cdd_source": cdd_source,
        "blueprint_id": eff_bp_id,
        "blueprint_source": bp_source,
        "style_id": active_style.id if active_style else None,
        "style_name": active_style.name if active_style else None,
        "style_source": style_source,
        "target_audience": request_body.target_audience,
        "expert_domain": request_body.expert_domain,
        "audience_category": request_body.audience_category,
        "context_document_names": request_body.context_document_names,
        "supplementary_files": [f.model_dump() for f in request_body.supplementary_files],
        "extra_instructions": request_body.extra_instructions,
        "dis_source_units_count": len(dis_source_units) if dis_source_units else 0,
    })

    # Mirror the same info into the queryable Audit Trail (Analytics tab), so
    # "what went into this generation" is visible there too, not only in the
    # JSON log file. log_audit_event fails safe — never breaks this endpoint.
    from promptops_app.services.audit_service import log_audit_event

    log_audit_event(
        db, current_user.username, "generation.launched",
        entity_type="generation", entity_id=job_id,
        project_id=request_body.project_id, course_id=request_body.course_id,
        metadata={
            "component_label": request_body.component_label,
            "component_value": request_body.component_value,
            "component_type": request_body.component_type,
            "prompt_name": request_body.prompt_name,
            "model_choice": request_body.model_choice,
            "cdd_id": eff_cdd_id, "cdd_source": cdd_source,
            "blueprint_id": eff_bp_id, "blueprint_source": bp_source,
            "style_id": active_style.id if active_style else None,
            "style_name": active_style.name if active_style else None,
            "style_source": style_source,
            "target_audience": request_body.target_audience,
            "expert_domain": request_body.expert_domain,
            "audience_category": request_body.audience_category,
            "context_document_names": request_body.context_document_names,
            "extra_instructions": request_body.extra_instructions,
            "dis_source_units_count": len(dis_source_units) if dis_source_units else 0,
        },
    )

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
    page_size: int = Query(default=20, ge=1, le=500),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
) -> PaginatedResponse[GenerationListItem]:
    """Return recent generations. Used by the Editor page to list available content.

    The cap matches the repository fetch limit (500) so imported courses — which
    write one generation per item — list every item on a single page.
    """
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
    page_gens = generations[start: start + page_size]

    # Count blocks for the whole page in ONE grouped query rather than one
    # COUNT round-trip per generation (crippling over a remote DB at page_size=500).
    counts = generation_repository.count_blocks_for_generations(db, [g.id for g in page_gens])

    items = []
    for g in page_gens:
        item = GenerationListItem.model_validate(g)
        item.block_count = counts.get(g.id, 0)
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
        headers={"Content-Disposition": content_disposition(result.file_name)},
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
    "/{generation_id}/trace",
    summary="Get this generation's full prompt/response trace from Phoenix",
    description=(
        "Proxies trace detail (prompt, response, model, token usage) from Phoenix "
        "through CAS's own resource scoping — nobody needs a separate Phoenix login "
        "or deep-link. Every tenant admin who can already see this generation can see "
        "its trace."
    ),
)
def get_generation_trace(
    generation_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
    tenant=Depends(get_tenant_context),
) -> dict:
    """Look up the Phoenix trace_id for this generation's LLM call and fetch it.

    Scoping is the whole security property here: get_scoped_or_404 is the exact
    same tenant-filtered lookup every other scoped resource endpoint uses (see
    feedback.py) — a generation belonging to another tenant 404s before this
    function ever learns whether a trace exists for it. P1.5's isolation tests
    prove this holds; do not change this lookup to anything that skips it.
    """
    from promptops_app.database import Generation, GenerationJob, LLMUsageLog
    from app.core.tenant_context import get_scoped_or_404
    from app.core.phoenix_client import get_trace_observations
    from promptops_app.services.audit_service import log_audit_event

    tenant_id, is_platform_admin = tenant
    generation = get_scoped_or_404(db, Generation, generation_id, tenant_id, is_platform_admin)

    # The LLM call that produced this generation was logged (P0) and traced (P1.2)
    # under its GenerationJob's job_id, not the Generation row's own id — the
    # Generation doesn't exist yet at the moment the call is logged. Join through
    # GenerationJob.result_entity_id (set on job completion) to find it.
    # job_type filter matters: result_entity_id is just an int/string column,
    # not unique across job types (a regenerate_item job's result_entity_id is
    # a block_id, a block-wide job's is a cdd/blueprint id) — without it,
    # .first() with no ordering can match an unrelated job whose entity id
    # happens to collide numerically. Ordered so a retried/duplicate job row
    # resolves to the most recent one.
    job = (
        db.query(GenerationJob)
        .filter(GenerationJob.result_entity_id == generation.id, GenerationJob.job_type == "generation")
        .order_by(GenerationJob.id.desc())
        .first()
    )
    usage_row = None
    if job is not None:
        usage_row = (
            db.query(LLMUsageLog)
            .filter(LLMUsageLog.entity_type == "generation", LLMUsageLog.entity_id == job.id)
            .order_by(LLMUsageLog.id.desc())
            .first()
        )

    # Imported items (prompt_name/version="import", editor_builder.py) have no
    # GenerationJob at all — their content came from the uploaded package, not
    # an LLM call, so the lookup above always misses for them. But the import
    # DOES make one real, traced LLM call per module to reconstruct its
    # Blueprint (reverse_blueprint.py), and every item in that module is linked
    # to it via Generation.blueprint_id once that stage runs. Falling back to
    # that module-level trace is honest, not exact: several lessons in the same
    # module share one trace, since the call reconstructed the whole module,
    # not any single lesson. scope in the response tells the frontend which
    # case it got so it can label the trace accordingly.
    scope = "generation"
    if (usage_row is None or not usage_row.trace_id) and generation.blueprint_id:
        from promptops_app.database import ModuleBlueprint

        blueprint = db.get(ModuleBlueprint, generation.blueprint_id)
        if blueprint is not None:
            usage_row = (
                db.query(LLMUsageLog)
                .filter(LLMUsageLog.entity_type == "reverse_blueprint",
                        LLMUsageLog.entity_id == str(blueprint.module_number),
                        LLMUsageLog.course_id == blueprint.course_id)
                .order_by(LLMUsageLog.id.desc())
                .first()
            )
            scope = "module_reconstruction"

    if usage_row is None or not usage_row.trace_id:
        raise NotFoundError("Trace for generation", generation_id)

    observations = get_trace_observations(usage_row.trace_id)

    log_audit_event(
        db, current_user.username, "generation.trace_viewed",
        entity_type="generation", entity_id=str(generation_id),
        project_id=generation.project_id, course_id=generation.course_id,
    )

    return {
        "generation_id": generation_id,
        "trace_id": usage_row.trace_id,
        "observations": observations,
        "scope": scope,
    }


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
