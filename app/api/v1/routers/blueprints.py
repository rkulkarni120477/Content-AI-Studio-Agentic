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

from fastapi import APIRouter, Depends, HTTPException, Query
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
from app.schemas.archive import (
    ArchiveResponse,
    BulkArchiveRequest,
    BulkArchiveResponse,
    DocumentReferences,
    PurgeResponse,
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
from app.schemas.block_wide import BlockWideGenerateRequest, BlockWideJobResponse
from app.core.dis_client import dis_client
from app.core.dis_access import resolve_course_dis_client
from app.core.dis_day_context import resolve_day_context_block
from app.core.config import settings
from promptops_app.services.block_wide_service import run_block_wide_sync

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
    include_archived: bool = Query(
        default=False,
        description="Include archived blueprints. Off by default — the archive is a bin, not the list.",
    ),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
) -> PaginatedResponse[BlueprintListItem]:
    """Return blueprints scoped to the given project or course.

    Each row carries what references it, resolved in one batched pass over the
    page rather than a query per row.
    """
    from promptops_app.repositories import blueprint_repository
    from app.services import design_doc_archive as archive_svc

    if course_id:
        bps = blueprint_repository.list_blueprints_for_course(
            db, course_id=course_id, project_id=project_id, include_archived=include_archived,
        )
    else:
        bps = blueprint_repository.list_all_blueprints(db, include_archived=include_archived)

    total = len(bps)
    start = (page - 1) * page_size
    page_items = bps[start: start + page_size]
    refs = archive_svc.reference_counts(db, archive_svc.BLUEPRINT, [b.id for b in page_items])

    items = [
        BlueprintListItem.model_validate(b).model_copy(update={
            "is_archived": archive_svc.is_archived(b),
            "references": DocumentReferences.from_refs(
                refs.get(b.id, archive_svc.DocReferences())
            ),
        })
        for b in page_items
    ]
    return PaginatedResponse.create(
        items=items, total=total, page=page, page_size=page_size,
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

    # ── Block-wide digest Blueprint (flag-gated) ───────────────────────────────
    # Engages ONLY when a block is supplied AND the digest pipeline is enabled for
    # the course's client (both checked inside run_block_wide_sync) → produces a
    # day-by-day Block Blueprint from source digests (selected_module ignored). No
    # caller sends `block` today, so the legacy module-blueprint path below is
    # unchanged. Failure falls through to it.
    _block_resp = run_block_wide_sync(db, "blueprint", request_body, current_user)
    if _block_resp is not None:
        return _block_resp

    # Resolve course (needed for cluster_id below) and CDD context.
    from promptops_app.repositories import course_repository
    course = course_repository.get_course_by_id(db, request_body.course_id)
    cdd_id = request_body.cdd_id or (course.active_cdd_id if course else None)

    cdd_title = ""
    cdd_context = ""
    if cdd_id:
        cdd_row = cdd_repository.get_cdd_by_id(db, cdd_id)
        # An archived CDD must not feed a prompt. It cannot arrive here as the
        # course's pin (archiving clears that), but a stale tab can still post
        # an explicit cdd_id, and this is the one path that reads the document's
        # content into the generation rather than just stamping its id.
        if cdd_row is not None:
            from app.services import design_doc_archive as archive_svc
            archive_svc.assert_live(cdd_row, archive_svc.CDD)
        cdd_title = cdd_row.title if cdd_row else ""
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

    dis_client_id = resolve_course_dis_client(
        db, course_id=request_body.course_id, project_id=request_body.project_id,
    )

    # Day-scoped digest grounding (§7) — enriches a single Day selection with
    # structured context (topic, ACS codes, source units) instead of only the
    # generic whole-CDD summary above. Additive only: any miss (no day_number,
    # pipeline disabled for this client, no parseable block label, DIS failure)
    # leaves this a no-op and the legacy blob-query below still runs. Never
    # engages the whole-block digest pipeline — see run_block_wide_sync's
    # day_number guard.
    day_context_block = resolve_day_context_block(
        request_body.day_number, dis_client_id, current_user, "BLUEPRINT DAY CONTEXT",
        course.name if course else None, cdd_title,
    )

    # Skip the legacy blob-query retrieval entirely when day-scoped grounding
    # already succeeded — matches generations.py's `if not dis_context_block:`
    # replacement semantics (§5.4 anti-pattern: don't stack a free-text blob
    # query on top of a complete structured day bundle covering the same
    # material). Flagged by adversarial review: this used to run BOTH
    # unconditionally, paying for a real 12k-token retrieval call whose
    # results mostly duplicated what the day bundle already provided.
    if day_context_block:
        dis_context_block, dis_source_units = "", []
    else:
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
            # Scope retrieval to the COURSE's own Source Library (its project's
            # client), independent of who runs the generation.
            client_id=dis_client_id,
        )

    # Build prompt.
    style_context = ""
    if request_body.style_id:
        style = style_repository.get_style_by_id(db, request_body.style_id)
        if style:
            style_context = build_style_context(db, style, cluster_id=course.cluster_id if course else None)

    extra_block = request_body.extra_instructions or ""
    if day_context_block:
        extra_block = f"{extra_block}\n\n{day_context_block}".strip()
    if dis_context_block:
        extra_block = f"{extra_block}\n\n{dis_context_block}".strip()
    if style_context:
        extra_block = f"**ACTIVE STYLE:**\n{style_context}\n\n{extra_block}"

    if request_body.system_prompt_override and request_body.user_prompt_override:
        system_prompt = request_body.system_prompt_override
        user_prompt = request_body.user_prompt_override
        # day_context_block and dis_context_block are mutually exclusive (the
        # latter is only ever computed when the former came up empty, above) —
        # fold in whichever one actually succeeded. Flagged by adversarial
        # review: this only ever checked dis_context_block, so a day-scoped
        # request using a prompt override silently lost its DIS grounding
        # entirely once day-scoped grounding started replacing (rather than
        # supplementing) the blob query.
        if day_context_block:
            user_prompt = f"{user_prompt}\n\n{day_context_block}"
        elif dis_context_block:
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
            # Same name the CDD route supplies, so a block-wide prompt is portable
            # between the two rather than being written for one of them.
            "block":              getattr(request_body, "block", None) or "",
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

    # Same guard the CDD router applies, for the same reason: this path stores the
    # reply AS the document, so a prompt that withholds it saves an empty one.
    from promptops_app.services.prompt_capability import (
        context_was_dropped, reject_if_unsatisfiable,
    )
    reject_if_unsatisfiable(system_prompt, user_prompt, what="generating this Blueprint")
    if context_was_dropped(dis_context_block, system_prompt, user_prompt):
        _log.warning(
            "blueprint_source_context_dropped  user=%s  course=%s  context_chars=%d  "
            "prompt_source=%s — the selected prompt has no slot for it",
            current_user.username, request_body.course_id, len(dis_context_block),
            prompt_provenance.get("prompt_source"),
        )
        prompt_provenance["source_context_dropped"] = True

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


@router.post(
    "/generate-block",
    response_model=BlockWideJobResponse,
    status_code=202,
    summary="Generate a block-wide Block Blueprint via the digest pipeline (async)",
    description=(
        "Enqueues a background job that builds the block's day digests and reduces "
        "them into a day-by-day Block Blueprint. Always async: poll "
        "GET /api/v1/jobs/{job_id}; on completion the job's entity id is the new "
        "blueprint id. Requires the digest pipeline enabled for the course's client."
    ),
    responses={
        202: {"description": "Job queued."},
        400: {"description": "Digest pipeline not enabled for this client."},
        403: {"description": "Requires the blueprint.generate permission."},
    },
)
def generate_blueprint_block(
    request_body: BlockWideGenerateRequest,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("blueprint.generate")),
) -> BlockWideJobResponse:
    from promptops_app.repositories import job_repository
    from promptops_app.jobs import dispatch, block_wide_jobs

    dis_client_id = resolve_course_dis_client(
        db, course_id=request_body.course_id, project_id=request_body.project_id,
    )
    if not settings.digest_pipeline_on_for(dis_client_id):
        # Audited before raising. This refusal happens BEFORE create_job and before
        # the *.block_requested write below, so without this a rejected request left
        # no job row and no audit row — the user saw an error the system had no
        # record of. The UI now gates on the same question (courses carry
        # digest_pipeline_enabled), so reaching here means a stale client, a direct
        # API call, or the two checks drifting apart again; all three are worth
        # seeing.
        from promptops_app.services.audit_service import log_audit_event
        log_audit_event(
            db, current_user.username, "blueprint.block_refused",
            entity_type="blueprint", entity_id=None,
            project_id=request_body.project_id, course_id=request_body.course_id,
            metadata={"block": request_body.block, "dis_client_id": dis_client_id,
                      "reason": "digest_pipeline_disabled_for_client"},
        )
        raise HTTPException(400, "Digest pipeline is not enabled for this course's client.")

    params = request_body.model_dump()
    params["deliverable"] = "blueprint"
    params["user_name"] = current_user.username
    # Persist role so the async worker keeps the caller's DIS privilege.
    params["role"] = getattr(current_user, "role", "user")
    params["dis_client_id"] = dis_client_id
    job_id = job_repository.create_job(
        db, user_name=current_user.username, request_params=params,
        project_id=request_body.project_id, course_id=request_body.course_id,
        job_type="blueprint_block",
    )
    # Written before submit (see cdd.generate_cdd_block for why).
    # See cdd.generate_cdd_block: audited at enqueue because this path is async, so a
    # failed job would otherwise leave no audit trace of the request at all.
    from promptops_app.services.audit_service import log_audit_event
    log_audit_event(
        db, current_user.username, "blueprint.block_requested",
        entity_type="blueprint", entity_id=None,
        project_id=request_body.project_id, course_id=request_body.course_id,
        metadata={
            "job_id": job_id, "block": request_body.block,
            "quality_tier": request_body.quality_tier or "standard",
            "model_choice": request_body.model_choice,
            "dis_client_id": dis_client_id,
            "prompt_id": request_body.prompt_id,
            "cdd_id": request_body.cdd_id,
            "extra_instructions": request_body.extra_instructions,
            # See the matching comment in cdd.py's generate-block audit call.
            "style_id": request_body.style_id,
            "estimated_duration_hours": request_body.estimated_duration_hours,
        },
    )
    # dispatch, not job_runner: routes to Celery when enabled so a web-container
    # restart/OOM can't kill a long block-wide run (it falls back to the
    # threadpool automatically when Celery is off or the broker is unreachable).
    dispatch.submit(block_wide_jobs.run_block_wide_job, job_id)
    _log.info("blueprint_generate_block_queued  user=%s  course=%d  block=%s  job=%s",
              current_user.username, request_body.course_id, request_body.block, job_id)
    return BlockWideJobResponse(
        job_id=job_id, status="queued", deliverable="blueprint",
        block=request_body.block, poll_url=f"/api/v1/jobs/{job_id}",
    )


# ---------------------------------------------------------------------------
# Archive / restore / permanently delete
#
# Mirrors the CDD endpoints exactly, over the same service, so the two document
# kinds cannot end up with different delete semantics. See
# app/services/design_doc_archive.py for the rules.
# ---------------------------------------------------------------------------

@router.post(
    "/bulk-archive",
    response_model=BulkArchiveResponse,
    summary="Archive several blueprints at once",
    description=(
        "Archives every blueprint named in `ids`, reporting each id's outcome. "
        "Explicit ids only — there is no predicate form. Ids that are missing, "
        "out of scope or pinned are skipped, not failed."
    ),
    responses={
        403: {"description": "Requires the blueprint.archive permission."},
        422: {"description": "More ids than a single request may carry."},
    },
)
def bulk_archive_blueprints(
    request_body: BulkArchiveRequest,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("blueprint.archive")),
) -> BulkArchiveResponse:
    """Archive a batch of blueprints, returning a row per id."""
    from app.services import design_doc_archive as archive_svc
    from promptops_app.services.audit_service import log_audit_event

    outcomes = archive_svc.bulk_archive(
        db, archive_svc.BLUEPRINT, request_body.ids,
        actor=current_user.username,
        unpin=request_body.unpin,
        scope_course_id=request_body.course_id,
        scope_project_id=request_body.project_id,
    )
    response = BulkArchiveResponse.from_outcomes(outcomes)

    # Ids go in metadata, not entity_id — see the CDD bulk endpoint: entity_id is
    # VARCHAR(64) and an overflow would silently drop the whole audit row.
    archived_ids = [o.doc_id for o in outcomes if o.status == "archived"]
    if archived_ids:
        log_audit_event(
            db, current_user.username, "blueprint.archived",
            entity_type="blueprint",
            course_id=request_body.course_id, project_id=request_body.project_id,
            metadata={
                "bulk": True,
                "archived_ids": archived_ids,
                "archived": response.archived,
                "skipped": response.skipped,
                "already_archived": response.already_archived,
                "unpin": request_body.unpin,
            },
        )
    _log.info("blueprint_bulk_archived  user=%s  archived=%d  skipped=%d",
              current_user.username, response.archived, response.skipped)
    return response


@router.get(
    "/{blueprint_id}/references",
    response_model=DocumentReferences,
    summary="What currently references this blueprint",
    responses={404: {"description": "Blueprint not found."}},
)
def get_blueprint_references(
    blueprint_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
) -> DocumentReferences:
    """Return the reference/blocker snapshot for one blueprint."""
    from app.services import design_doc_archive as archive_svc

    _get_blueprint_or_404(db, blueprint_id)
    return DocumentReferences.from_refs(
        archive_svc.references_for(db, archive_svc.BLUEPRINT, blueprint_id)
    )


@router.delete(
    "/{blueprint_id}",
    response_model=ArchiveResponse,
    summary="Archive a blueprint",
    description=(
        "Removes the blueprint from the list without deleting anything — it can "
        "be restored. One pinned as active on a course is refused unless "
        "`unpin=true`."
    ),
    responses={
        404: {"description": "Blueprint not found."},
        403: {"description": "Requires the blueprint.archive permission."},
        409: {"description": "Pinned as active and unpin was not requested."},
    },
)
def archive_blueprint(
    blueprint_id: int,
    unpin: bool = Query(
        default=False,
        description="Clear the course's active-blueprint pin so a pinned blueprint can be archived.",
    ),
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("blueprint.archive")),
) -> ArchiveResponse:
    """Soft-delete one blueprint."""
    from app.services import design_doc_archive as archive_svc
    from app.core.exceptions import ResourceInUseError
    from promptops_app.services.audit_service import log_audit_event

    bp = _get_blueprint_or_404(db, blueprint_id)
    outcome = archive_svc.archive(
        db, archive_svc.BLUEPRINT, bp, actor=current_user.username, unpin=unpin,
    )
    if not outcome.ok:
        raise ResourceInUseError(
            outcome.reason, blockers=[outcome.reason], detail={"id": blueprint_id},
        )

    if outcome.status == "archived":
        log_audit_event(
            db, current_user.username, "blueprint.archived",
            entity_type="blueprint", entity_id=blueprint_id,
            course_id=bp.course_id, project_id=bp.project_id,
            metadata={"title": bp.title, "unpinned_courses": list(outcome.unpinned_courses)},
        )
        _log.info("blueprint_archived  user=%s  bp_id=%d  unpinned=%s",
                  current_user.username, blueprint_id, outcome.unpinned_courses or "none")

    return ArchiveResponse(
        id=blueprint_id,
        archived=True,
        unpinned_courses=list(outcome.unpinned_courses),
        message=(
            "Already archived." if outcome.status == "already_archived"
            else "Archived. Restore it any time from the archived list."
        ),
    )


@router.post(
    "/{blueprint_id}/restore",
    response_model=ArchiveResponse,
    summary="Restore an archived blueprint",
    responses={
        404: {"description": "Blueprint not found."},
        403: {"description": "Requires the blueprint.archive permission."},
    },
)
def restore_blueprint(
    blueprint_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("blueprint.archive")),
) -> ArchiveResponse:
    """Undo an archive."""
    from app.services import design_doc_archive as archive_svc
    from promptops_app.services.audit_service import log_audit_event

    bp = _get_blueprint_or_404(db, blueprint_id)
    changed = archive_svc.restore(db, archive_svc.BLUEPRINT, bp)
    if changed:
        log_audit_event(
            db, current_user.username, "blueprint.restored",
            entity_type="blueprint", entity_id=blueprint_id,
            course_id=bp.course_id, project_id=bp.project_id,
            metadata={"title": bp.title},
        )
        _log.info("blueprint_restored  user=%s  bp_id=%d", current_user.username, blueprint_id)

    return ArchiveResponse(
        id=blueprint_id,
        archived=False,
        message=(
            "Restored. Pin it if you want generation to use it."
            if changed else "That blueprint was not archived."
        ),
    )


@router.delete(
    "/{blueprint_id}/permanent",
    response_model=PurgeResponse,
    summary="Permanently delete an archived blueprint",
    description=(
        "Irreversible. Refused unless the blueprint is archived first and "
        "nothing references it — including reviewer feedback mapped to it, which "
        "a delete would silently unmap. Admin only."
    ),
    responses={
        404: {"description": "Blueprint not found."},
        403: {"description": "Requires the blueprint.purge permission (admin)."},
        409: {"description": "Something still references it; see detail.blockers."},
        422: {"description": "Not archived yet — archive it first."},
    },
)
def permanently_delete_blueprint(
    blueprint_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("blueprint.purge")),
) -> PurgeResponse:
    """Hard-delete an archived, unreferenced blueprint and its versions."""
    from app.services import design_doc_archive as archive_svc
    from promptops_app.services.audit_service import log_audit_event

    bp = _get_blueprint_or_404(db, blueprint_id)
    title, course_id, project_id = bp.title, bp.course_id, bp.project_id

    refs = archive_svc.purge(db, archive_svc.BLUEPRINT, bp)

    log_audit_event(
        db, current_user.username, "blueprint.purged",
        entity_type="blueprint", entity_id=blueprint_id,
        course_id=course_id, project_id=project_id,
        metadata={"title": title, "versions_deleted": refs.version_count},
    )
    _log.warning("blueprint_purged  user=%s  bp_id=%d  versions=%d",
                 current_user.username, blueprint_id, refs.version_count)
    return PurgeResponse(
        id=blueprint_id, deleted=True,
        message=f"Permanently deleted, along with {refs.version_count} saved version(s).",
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
    from promptops_app.services.usage_service import UsageLogContext

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

    usage_ctx = UsageLogContext(
        user_name=current_user.username, project_id=bp.project_id, course_id=bp.course_id,
        entity_type="blueprint_item_regen", entity_id=str(blueprint_id),
    )
    new_item_text = regen_single_item(
        section_title=request_body.section_key,
        section_content=original,
        item_index=item_index,
        item_text=items[item_index]["text"],
        custom_instruction=instruction,
        model_choice=request_body.model_choice,
        usage_ctx=usage_ctx,
    )
    updated_content = patch_item_in_section(original, item_index, new_item_text)

    _log.info("blueprint_item_regenerated  user=%s  bp_id=%d  section=%s  item=%d",
              current_user.username, blueprint_id, request_body.section_key, item_index)

    from promptops_app.services.budget_service import build_usage_summary

    return BlueprintRegenerateItemResponse(
        updated_content=updated_content,
        patched_item=new_item_text or "",
        usage_summary=build_usage_summary(db, usage_ctx, "blueprint_item_regen", str(blueprint_id)),
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
    from promptops_app.services.usage_service import UsageLogContext

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
    usage_ctx = UsageLogContext(
        user_name=current_user.username, project_id=bp.project_id, course_id=bp.course_id,
        entity_type="blueprint_section_regen", entity_id=str(blueprint_id),
    )
    new_content = call_llm(request_body.model_choice, regen_system, regen_prompt, usage_ctx)
    if not new_content or new_content.startswith("ERROR"):
        raise LLMGenerationError("Section regeneration failed. Please try again.")

    _log.info("blueprint_section_regenerated  user=%s  bp_id=%d  section=%s  mode=%s",
              current_user.username, blueprint_id, request_body.section_key, mode)

    from promptops_app.services.budget_service import build_usage_summary

    return BlueprintRegenerateSectionResponse(
        updated_content=new_content.strip(),
        usage_summary=build_usage_summary(db, usage_ctx, "blueprint_section_regen", str(blueprint_id)),
    )


@router.post("/{blueprint_id}/pin", response_model=BlueprintPinResponse, summary="Pin blueprint to a course")
def pin_blueprint(blueprint_id: int, request_body: BlueprintPinRequest, db: Session = Depends(get_db), current_user=Depends(require_permission("blueprint.pin"))) -> BlueprintPinResponse:
    """Set as active blueprint for generation. Equivalent to the '📌 Set as Active Blueprint' button."""
    from promptops_app.repositories.course_repository import set_active_blueprint
    from app.services import design_doc_archive as archive_svc
    bp = _get_blueprint_or_404(db, blueprint_id)
    # Pinning an archive would quietly put a document someone deliberately
    # retired back in front of every generation for this course.
    archive_svc.assert_live(bp, archive_svc.BLUEPRINT)
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
    from promptops_app.parsers.blueprint_parser import (
        parse_blueprint_components, is_dlu_blueprint,
    )
    from promptops_app.repositories import blueprint_repository

    bp = _get_blueprint_or_404(db, blueprint_id)
    ver = blueprint_repository.get_blueprint_version(db, blueprint_id, bp.active_version) if bp.active_version else None

    # DLU course: the Content Type dropdown lists EVERY generated DLU day for
    # the course (one entry per day, latest blueprint wins), so any day's
    # content can be generated from one place. Standard blueprints are
    # unaffected — they fall through to the per-blueprint parse below.
    if ver and is_dlu_blueprint(ver) and bp.course_id:
        dlu_components = _list_dlu_day_components(db, bp.course_id)
        if dlu_components:
            return BlueprintComponentsResponse(
                blueprint_id=blueprint_id,
                components=[BlueprintComponent(**c) for c in dlu_components],
            )

    components = parse_blueprint_components(ver) if ver else []
    return BlueprintComponentsResponse(
        blueprint_id=blueprint_id,
        components=[BlueprintComponent(**c) for c in components],
    )


def _list_dlu_day_components(db: Session, course_id: int) -> list[dict]:
    """Aggregate a course's DLU day-blueprints into Content Type dropdown items.

    One entry per day — the latest blueprint for that day wins (regenerating a
    day creates a new blueprint row). Each option carries its own
    ``blueprint_id`` in metadata so generation targets that specific day.
    Returns [] for non-DLU courses (caller then uses the standard parse).
    """
    from datetime import datetime
    from promptops_app.parsers.blueprint_parser import is_dlu_blueprint, parse_day_and_title
    from promptops_app.repositories import blueprint_repository

    blueprints = blueprint_repository.list_blueprints_for_course(db, course_id=course_id)
    by_day: dict[int, dict] = {}
    for mb in blueprints:
        ver = (
            blueprint_repository.get_blueprint_version(db, mb.id, mb.active_version)
            if mb.active_version else None
        )
        if not ver or not is_dlu_blueprint(ver):
            continue
        day, topic = parse_day_and_title(mb.title, mb.module_number)
        if day is None:
            continue
        sort_key = (mb.created_at or datetime.min, mb.id)   # latest wins
        prev = by_day.get(day)
        if prev is None or sort_key > prev["sort_key"]:
            by_day[day] = {"sort_key": sort_key, "bp_id": mb.id, "topic": topic}

    components: list[dict] = []
    for day in sorted(by_day):
        entry = by_day[day]
        topic = entry["topic"]
        label = f"Day {day}: {topic}" if topic else f"Day {day}"
        components.append({
            "label":    label,
            "value":    f"dlu_day::{entry['bp_id']}",
            "type":     "lesson",
            "metadata": {
                "structure":    "dlu",
                "day_number":   day,
                "blueprint_id": entry["bp_id"],
                "topic":        topic,
            },
        })
    return components


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
    format: str = Query(default="docx", description="docx | md | xlsx"),
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("export.course")),
) -> Response:
    """Export the active blueprint version as a downloadable file."""
    from promptops_app.core.llm_client import safe_json_loads
    from promptops_app.parsers.blueprint_parser import _is_bp_section_hidden
    from promptops_app.parsers.cdd_parser import (
        _strip_ui_hidden_text, is_dlu_cdd, parse_sections_from_text, split_cdd_worksheets,
    )
    from promptops_app.repositories import blueprint_repository
    from promptops_app.services.export_service import ExportRequest, export_content

    bp = _get_blueprint_or_404(db, blueprint_id)
    if not bp.active_version:
        raise WorkflowError("This blueprint has no active version to export.")

    ver = blueprint_repository.get_blueprint_version(db, blueprint_id, bp.active_version)
    if not ver:
        raise NotFoundError(f"Blueprint version '{bp.active_version}'", blueprint_id)

    # Block-wide Blueprint + XLSX → one sheet per worksheet (reuses cdd.py's
    # DLU-CDD parser/exporter — worksheet detection is heading-shape-based, not
    # CDD-specific). Gated on prompt_source == "digest_pipeline", NOT on content
    # shape alone: this route is shared with the regular (non-block-wide) per-
    # module Blueprint/"Outline" feature, which predates this pipeline and is
    # out of scope for it. That feature's own AIM-specific prompt template
    # ("Block Blueprint New") ALSO produces "## WORKSHEET N: X"-shaped output
    # for unrelated reasons, so a shape-only check would silently reroute ITS
    # xlsx export through this new path too — a real regression caught live,
    # not theoretical. prompt_source is only ever "digest_pipeline" for
    # content this pipeline generated, which itself is client-gated
    # (DIGEST_PIPELINE_CLIENTS), so other tenants can never produce it.
    gen_params = safe_json_loads(ver.generation_params) if ver.generation_params else {}
    is_block_wide = isinstance(gen_params, dict) and gen_params.get("prompt_source") == "digest_pipeline"
    if format == "xlsx" and is_block_wide and is_dlu_cdd(ver.full_content or ""):
        from promptops_app.exporters.xlsx_exporter import build_xlsx_worksheets

        sheets = []
        for label, content in split_cdd_worksheets(ver.full_content or ""):
            clean = _strip_ui_hidden_text(content)
            if (clean or "").strip():
                sheets.append((label, clean))
        if sheets:
            buf = build_xlsx_worksheets(bp.title, sheets)
            fname = f"Blueprint_{bp.title.replace(' ', '_')}_{bp.active_version}.xlsx"
            _log.info(
                "blueprint_exported_dlu_xlsx  user=%s  blueprint_id=%d  sheets=%d",
                current_user.username, blueprint_id, len(sheets),
            )
            return Response(
                content=buf.read(),
                media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                headers={"Content-Disposition": content_disposition(fname)},
            )

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
