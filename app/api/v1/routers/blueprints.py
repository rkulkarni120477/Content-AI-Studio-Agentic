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
import os
import re
import tempfile
from types import SimpleNamespace

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
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
    OutlineImportJobResponse,
)
from app.schemas.common import JobAcceptedResponse, PaginatedResponse
from app.schemas.block_wide import BlockWideGenerateRequest, BlockWideJobResponse
from app.core.dis_client import dis_client
from app.core.dis_access import resolve_course_dis_client
from app.core.dis_day_context import resolve_day_context_block
from app.core.config import settings
from promptops_app.services.block_wide_service import run_block_wide_sync

_log = logging.getLogger(__name__)
router = APIRouter()

# Uploaded Outline files are staged here for the async import worker to read by
# path (Celery payloads must be JSON-serialisable — a path, not bytes). Staged
# under the repo root, NOT the OS tempdir, because when Celery is on the worker
# is a different container; the repo root is the shared bind-mount both mount
# (docker-compose `volumes: .:/app`). Reuses the IMSCC import's staging dir so
# there's one place to clean. See imports.py `_IMPORT_STAGING_DIR`.
_OUTLINE_IMPORT_STAGING_DIR = os.environ.get("IMPORT_STAGING_DIR") or os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))),
    "import_uploads",
)

# Guard the whole-file read below — an Outline is an Excel/Word/PDF worksheet, so
# 25 MB is generous; a larger upload is almost certainly a mistake and would only
# bloat the staging dir / LLM prompt.
_MAX_IMPORT_BYTES = 25 * 1024 * 1024


def _dis_context_block(purpose: str, payload: dict, current_user, label: str,
                       client_id: str = "") -> tuple[str, list, str]:
    """Retrieved Source Library context, its units, and why there is none.

    Best-effort by design: a Source Library that cannot be reached degrades
    generation to CDD-and-style grounding rather than failing it (the same
    contract cdd.py documents at its own second-pass retrieval). That part is
    deliberate and unchanged.

    What was wrong is that the degradation was invisible. A blueprint generated
    with no source grounding was byte-indistinguishable to the requester from
    one fully grounded in the library, and nothing about it was recorded on the
    version either — so after the fact there was no way to tell which of the two
    a stored document had been. The only trace was a server log line nobody
    reads while looking at a document that seems fine.

    The third return value is that trace: "" when DIS answered — INCLUDING when
    it answered with nothing, which is the ordinary shape for a course whose
    library holds no matching material — and a short reason when the lookup
    itself failed. Callers record it with the version and tell the requester.
    """
    try:
        result = dis_client.retrieve_context_sync(purpose, payload, current_user=current_user, client_id=client_id)
    except Exception as exc:
        _log.warning("dis_%s_context_unavailable error=%s", purpose, exc)
        # The class name, not str(exc): this is stored on the version and shown
        # to the requester, and an upstream message can carry a URL, a token or
        # a stack fragment.
        return "", [], type(exc).__name__
    ctx = str(result.get("combined_context") or "").strip()
    units = result.get("source_units") or result.get("sources") or []
    if ctx:
        return (
            f"\n\n---\n{label} FROM DIS SOURCE LIBRARY\n"
            "Use this as source grounding only. Follow approved CDD, active style, selected module, and requested mode first. "
            "Do not expose internal DIS metadata.\n\n"
            f"{ctx}\n---\n",
            units,
            "",
        )
    # DIS answered, with nothing to add. Not a failure, and not reported as one.
    return "", [], ""


def _get_blueprint_or_404(db: Session, blueprint_id: int, current_user):
    """Fetch a blueprint by ID or raise HTTP 404, scoped to the caller's tenant.

    A cross-tenant blueprint 404s exactly like a nonexistent one (no
    enumeration oracle) — same contract as cdd._get_cdd_or_404.
    ``get_blueprint_by_id`` is itself unfiltered, so without this every one of
    this helper's ~16 callers (get/update/archive/restore/versions/pin/
    export/…) would let any authenticated user act on any tenant's blueprint
    just by knowing its id.
    """
    from promptops_app.repositories import blueprint_repository
    from app.core.tenant_context import visible_to_tenant

    bp = blueprint_repository.get_blueprint_by_id(db, blueprint_id)
    is_platform_admin = getattr(current_user, "_is_platform_admin", False)
    project_id = getattr(current_user, "_project_id", None)
    if bp is None or not visible_to_tenant(bp.project_id, project_id, is_platform_admin):
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

    # Was completely unfiltered when course_id was omitted (list_all_blueprints
    # — every tenant, no role/tenant check at all) and honored an arbitrary
    # ``project_id`` query param verbatim even for a non-platform-admin. A
    # tenant caller is always scoped to their own project regardless of what
    # they pass, same rule as cdd.list_cdds/prompts.list_prompts.
    is_platform_admin = getattr(current_user, "_is_platform_admin", False)
    effective_project_id = project_id if is_platform_admin else getattr(current_user, "_project_id", None)

    if effective_project_id:
        bps = blueprint_repository.list_blueprints_for_course(
            db, course_id=course_id, project_id=effective_project_id, include_archived=include_archived,
        )
    elif is_platform_admin and course_id:
        bps = blueprint_repository.list_blueprints_for_course(
            db, course_id=course_id, include_archived=include_archived,
        )
    elif is_platform_admin:
        bps = blueprint_repository.list_all_blueprints(db, include_archived=include_archived)
    else:
        # Non-platform-admin with no project assigned at all — nothing to
        # scope to, so nothing shown rather than every tenant's rows.
        bps = []

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
    response_model=JobAcceptedResponse,
    status_code=202,
    summary="Generate a module blueprint with AI (async)",
    description=(
        "Enqueues blueprint generation as a background job. Poll "
        "GET /api/v1/jobs/{job_id}; on completion ``generation_id`` is the new blueprint id."
    ),
)
def generate_blueprint(
    request_body: BlueprintGenerateRequest,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("blueprint.generate")),
) -> JobAcceptedResponse:
    """Queue blueprint generation; work runs in design_jobs.run_blueprint_generate_job."""
    from promptops_app.jobs import design_jobs, dispatch
    from promptops_app.repositories import job_repository

    params = request_body.model_dump()
    design_jobs.stamp_job_user(params, current_user)
    job_id = job_repository.create_job(
        db,
        user_name=current_user.username,
        request_params=params,
        project_id=request_body.project_id,
        course_id=request_body.course_id,
        job_type="blueprint",
    )
    dispatch.submit(design_jobs.run_blueprint_generate_job, job_id)
    _log.info(
        "blueprint_generate_queued  user=%s  course=%d  job=%s",
        current_user.username, request_body.course_id, job_id,
    )
    return JobAcceptedResponse(
        job_id=job_id, status="queued", status_url=f"/api/v1/jobs/{job_id}",
    )


def execute_blueprint_generate(
    db: Session,
    request_body: BlueprintGenerateRequest,
    current_user,
) -> BlueprintGenerateResponse:
    """
    Generate a Module Blueprint using AI (called from the background worker).

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
    from promptops_app.prompts.prompt_builder import PromptVariableError, build_prompt_resolved
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
        dis_context_block, dis_source_units, dis_unavailable = "", [], ""
    else:
        dis_context_block, dis_source_units, dis_unavailable = _dis_context_block(
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
    # style_context is NOT prepended here — it is carried by the named
    # style_guidelines variable below. A template that renders both this and
    # extra_instructions used to receive the style twice, verbatim.

    if request_body.system_prompt_override and request_body.user_prompt_override:
        system_prompt = request_body.system_prompt_override
        user_prompt = request_body.user_prompt_override
        # extra_block (built above) folds in extra_instructions and the day/dis
        # grounding block — appended separately below to avoid duplicating
        # whichever one fired, so style_context and extra_instructions are
        # appended here directly instead, from the same variables the
        # non-override build reads (style_context is never in extra_block —
        # see the comment where extra_block is built, a few lines up). This
        # branch used to append neither: an inline-edited prompt replaces the
        # TEMPLATE, which is the intent, but was also silently dropping the
        # Style the user still had selected and the text still sitting in
        # Additional Instructions. Same section order as the non-override
        # build above (style + instructions, then grounding).
        if style_context:
            user_prompt = f"{user_prompt}\n\n**ACTIVE STYLE:**\n{style_context}"
        if request_body.extra_instructions:
            user_prompt = f"{user_prompt}\n\n{request_body.extra_instructions}"
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
            system_prompt, user_prompt, _tpl = build_prompt_resolved(
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
                "prompt_name": _tpl.name,
                "prompt_version": _tpl.version,
                # The row, not just the logical name. Twelve library prompts
                # resolve as "blueprint_generation" and each numbers its versions
                # from v1, so name+version cannot say which one ran: identifying
                # the prompt behind a stored blueprint previously meant full-text
                # searching every prompt version against the document. None when
                # the file tier served the template, which has no row.
                "prompt_row_id": _tpl.prompt_row_id,
                "prompt_title": _tpl.prompt_title,
                "prompt_tier": _tpl.source,
                # What the requester asked for, kept separately from what
                # resolution actually returned — a dropdown choice that missed
                # and fell through to the default is otherwise indistinguishable
                # from never having chosen.
                "prompt_id_requested": request_body.prompt_id,
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

    # Companion to the flag above. That one means the prompt had no slot for the
    # context we retrieved; this one means there was no context to put anywhere,
    # because the Source Library could not be reached. Both leave a document
    # grounded in less than the caller asked for, and neither is visible in the
    # document itself, so both are recorded on the version.
    if dis_unavailable:
        prompt_provenance["source_context_unavailable"] = dis_unavailable
        _log.warning(
            "blueprint_generated_without_source_grounding  user=%s  course=%s  reason=%s",
            current_user.username, request_body.course_id, dis_unavailable,
        )

    # Call LLM.
    #
    # max_tokens is the chosen model's own ceiling rather than the 16384 default
    # that omitting it inherits. Three models in the catalog return up to 64000,
    # so a blueprint generated on one of them was being held to a quarter of its
    # range for no reason — the same cap output_budget was introduced to lift on
    # the regeneration paths.
    from app.services import cdd_regen_context as regen_ctx_svc

    llm_result = generate_with_metadata(
        request_body.model_choice, system_prompt, user_prompt,
        usage_ctx=UsageLogContext(
            user_name=current_user.username,
            project_id=request_body.project_id,
            course_id=request_body.course_id,
            entity_type="blueprint",
        ),
        max_tokens=regen_ctx_svc.output_budget(request_body.model_choice),
    )

    if llm_result.status == "error":
        raise LLMGenerationError(
            f"Blueprint generation failed. Error type: {llm_result.error_type}"
        )

    # A reply that hit the output ceiling is a fragment, and this path stores the
    # reply AS the document: it becomes v1, is auto-pinned as the active version
    # below, and is what every later regeneration and downstream generation reads
    # as the blueprint. Nothing downstream can tell a complete document from one
    # that stops mid-table, so the only honest outcome is to fail visibly. Same
    # guard, for the same reason, as cdd.py's generation path.
    _reject_if_truncated(llm_result, "generating this Blueprint")

    raw_output = llm_result.text

    # Parse sections.
    from promptops_app.parsers.cdd_parser import parse_sections_from_text
    sections = parse_sections_from_text(raw_output)

    # Persist blueprint.
    # The requester's own title wins; the module-derived one is the default, not
    # the rule. bp.title is not merely a label — it feeds the Generate page's DLU
    # day dropdown (parse_day_and_title) and the section-regeneration prompt — so
    # it is stored trimmed and never blank.
    bp_title = (request_body.document_title or "").strip() or f"{request_body.selected_module} Blueprint"
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
        source_context_unavailable=dis_unavailable or None,
    )


@router.post(
    "/import",
    response_model=BlueprintGenerateResponse,
    status_code=201,
    summary="Import an existing DLU Outline file (Excel, DOCX, PDF)",
    description=(
        "Upload an Outline the user already has (Excel, Word or PDF). The file is "
        "extracted and normalized into the canonical DLU Outline day shape, then "
        "saved as a normal blueprint (same tables as a generated one), pinned as "
        "active, and rendered as the day accordions. The day is read from the file; "
        "if an Outline already exists for that day it is saved as a NEW VERSION of "
        "that Outline (history kept), otherwise a fresh Outline is created. "
        "Already-structured Outlines pass through unchanged; other files are "
        "reorganized by the LLM under a strict preserve-everything contract."
    ),
    responses={
        201: {"description": "Outline imported, pinned, and rendered as a day Outline."},
        400: {"description": "Unsupported file type, unreadable content, or undetermined day."},
        403: {"description": "User does not have the blueprint.generate permission."},
    },
)
def import_outline(
    file: UploadFile = File(..., description="Outline file (.xlsx, .xls, .docx, .pdf)."),
    course_id: int = Form(..., description="Course this imported Outline belongs to."),
    project_id: int = Form(..., description="Parent project id."),
    document_title: str = Form("", description="Outline title. Blank → '<Day|Module> N: <topic> Blueprint'."),
    unit_kind: str = Form("day", description="Dropdown context: 'day' (DLU) or 'module'. Only a hint — the file decides when it names a unit."),
    unit_number: int | None = Form(None, description="Day/module the user confirmed in the dropdown; used only when the file names no unit."),
    model_choice: str = Form("GPT-5.6 Terra", description="Model used only for the day-Outline LLM restructure path."),
    cdd_id: int | None = Form(None, description="Optional CDD to link the imported Outline to."),
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("blueprint.generate")),
) -> BlueprintGenerateResponse:
    """Extract → normalize → persist an uploaded Outline as an active blueprint.

    Handles both Outline kinds (CAS-98): a DLU **day** Outline (five-part
    accordions) and a **module** Outline (freeform sections). The kind and unit are
    read from the file; ``unit_kind``/``unit_number`` are the dropdown selection,
    used to disambiguate or when the file names no unit. An existing Outline for
    that unit gets a new version (the pinned one, else the most recent); otherwise a
    fresh Outline is created. Reuses the generate path's persistence primitives, so
    an imported Outline is indistinguishable downstream — only
    ``generation_params.prompt_source`` records that it was imported.
    """
    from promptops_app.parsers.blueprint_parser import parse_blueprint_components
    from promptops_app.services.outline_import_persist import persist_imported_outline
    from promptops_app.services.outline_import_service import normalize_import
    from promptops_app.services.usage_service import UsageLogContext

    raw = file.file.read()
    if not raw:
        raise HTTPException(400, "The uploaded file is empty.")

    usage_ctx = UsageLogContext(
        user_name=current_user.username,
        project_id=project_id,
        course_id=course_id,
        entity_type="outline_import",
    )

    try:
        result = normalize_import(
            file.filename or "outline",
            raw,
            document_title=document_title,
            hint_kind=unit_kind,
            hint_number=unit_number,
            model_choice=model_choice,
            usage_ctx=usage_ctx,
        )
    except ValueError as exc:
        # Content/format problem the user can act on — wrong type, empty file, or
        # an unresolvable/contradictory unit. normalize_import raises these (before
        # any LLM call) with a user-facing message; surface it as a clean 400.
        raise HTTPException(400, str(exc)) from exc

    bp, version_record, _was_new = persist_imported_outline(
        db, result, course_id=course_id, project_id=project_id, cdd_id=cdd_id,
        source_filename=file.filename, current_user=current_user,
    )

    components = parse_blueprint_components(version_record)
    component_list = [BlueprintComponent(**c) for c in components]

    return BlueprintGenerateResponse(
        blueprint_id=bp.id,
        title=bp.title,
        version=version_record.version,
        sections_count=len(result.sections),
        components=component_list,
        full_content=result.raw_output,
        model_used=(model_choice if "llm" in result.method else "import"),
        tokens_used=None,
        auto_pinned=True,
        # Surfaced so the UI can warn on a degraded import (e.g. content dumped as
        # one section) instead of showing the same success as a clean one.
        import_warnings=result.warnings or None,
    )


@router.post(
    "/import-async",
    response_model=OutlineImportJobResponse,
    status_code=202,
    summary="Import an existing Outline file asynchronously (Excel, DOCX, PDF)",
    description=(
        "Same behaviour as POST /import, but runs the extract + (day-Outline) LLM "
        "restructure in a background job so a slow file never hits a reverse-proxy "
        "read timeout (504). Returns a job handle immediately (202); poll "
        "GET /api/v1/jobs/{job_id} — on completion the job's generation_id is the "
        "imported blueprint id, and any degraded-import note is in the job's warning."
    ),
    responses={
        202: {"description": "Import job queued."},
        400: {"description": "Empty or too-large file."},
        403: {"description": "User does not have the blueprint.generate permission."},
    },
)
def import_outline_async(
    file: UploadFile = File(..., description="Outline file (.xlsx, .xls, .docx, .pdf)."),
    course_id: int = Form(..., description="Course this imported Outline belongs to."),
    project_id: int = Form(..., description="Parent project id."),
    document_title: str = Form("", description="Outline title. Blank → '<Day|Module> N: <topic> Blueprint'."),
    unit_kind: str = Form("day", description="Dropdown context: 'day' (DLU) or 'module'. Only a hint — the file decides when it names a unit."),
    unit_number: int | None = Form(None, description="Day/module the user confirmed in the dropdown; used only when the file names no unit."),
    model_choice: str = Form("GPT-5.6 Terra", description="Model used only for the day-Outline LLM restructure path."),
    cdd_id: int | None = Form(None, description="Optional CDD to link the imported Outline to."),
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("blueprint.generate")),
) -> OutlineImportJobResponse:
    """Stage the uploaded file, enqueue the import job, and return a poll handle.

    The heavy work (extract + LLM restructure + persist) runs in
    ``outline_import_jobs.run_outline_import_job`` — identical to the sync route,
    just off the request thread. Validation of the file's content/day happens in
    the worker; a user-actionable problem surfaces as the job's error_message.
    """
    from promptops_app.jobs import dispatch, outline_import_jobs
    from promptops_app.repositories import job_repository

    raw = file.file.read()
    if not raw:
        raise HTTPException(400, "The uploaded file is empty.")
    if len(raw) > _MAX_IMPORT_BYTES:
        raise HTTPException(400, "This file is too large to import. Please upload a file under 25 MB.")

    # Stage to the shared-volume dir so the worker (possibly a different container)
    # can read it by path; the job deletes it when done.
    os.makedirs(_OUTLINE_IMPORT_STAGING_DIR, exist_ok=True)
    suffix = os.path.splitext(file.filename or "")[1].lower()
    fd, package_path = tempfile.mkstemp(prefix="outline_", suffix=suffix, dir=_OUTLINE_IMPORT_STAGING_DIR)
    with os.fdopen(fd, "wb") as handle:
        handle.write(raw)

    job_id = job_repository.create_job(
        db,
        user_name=current_user.username,
        request_params={
            "package_path": package_path,
            "filename": file.filename or "outline",
            "course_id": course_id,
            "project_id": project_id,
            "document_title": document_title,
            "unit_kind": unit_kind,
            "unit_number": unit_number,
            "model_choice": model_choice,
            "cdd_id": cdd_id,
            "user_name": current_user.username,
            # Real id lets the worker's DIS access resolution find the caller's
            # tenant — see block_wide_jobs._reconstruct_user.
            "user_id": getattr(current_user, "id", None),
            "role": getattr(current_user, "role", "user"),
        },
        project_id=project_id,
        course_id=course_id,
        job_type="outline_import",
    )
    dispatch.submit(outline_import_jobs.run_outline_import_job, job_id)

    _log.info("outline_import_async_queued  user=%s  course=%d  file=%r  job=%s",
              current_user.username, course_id, file.filename, job_id)
    return OutlineImportJobResponse(job_id=job_id, status="queued", poll_url=f"/api/v1/jobs/{job_id}")


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
    # The id, not just the name: the worker rebuilds this caller to resolve DIS
    # access, and that resolution looks up tenant memberships by user id. Without
    # it the lookup fails and the caller is silently downgraded to the default DIS
    # client, which then builds a DIFFERENT client's block (see dis_access).
    params["user_id"] = getattr(current_user, "id", None)
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

    _get_blueprint_or_404(db, blueprint_id, current_user)
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

    bp = _get_blueprint_or_404(db, blueprint_id, current_user)
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

    bp = _get_blueprint_or_404(db, blueprint_id, current_user)
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

    bp = _get_blueprint_or_404(db, blueprint_id, current_user)
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
    bp = _get_blueprint_or_404(db, blueprint_id, current_user)
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
    _get_blueprint_or_404(db, blueprint_id, current_user)
    versions = blueprint_repository.list_blueprint_versions(db, blueprint_id)
    return [BlueprintVersionListItem.model_validate(v) for v in versions]


@router.get("/{blueprint_id}/versions/{version}", response_model=BlueprintVersionRead, summary="Get a specific version")
def get_blueprint_version(blueprint_id: int, version: str, db: Session = Depends(get_db), current_user=Depends(get_current_user)) -> BlueprintVersionRead:
    """Return full content of a specific blueprint version."""
    from promptops_app.repositories import blueprint_repository
    _get_blueprint_or_404(db, blueprint_id, current_user)
    ver = blueprint_repository.get_blueprint_version(db, blueprint_id, version)
    if not ver:
        raise NotFoundError(f"Blueprint version '{version}'", blueprint_id)
    return BlueprintVersionRead.model_validate(ver)


@router.post("/{blueprint_id}/versions/{version}/activate", response_model=BlueprintActivateVersionResponse, summary="Activate a blueprint version")
def activate_blueprint_version(blueprint_id: int, version: str, db: Session = Depends(get_db), current_user=Depends(require_permission("blueprint.version"))) -> BlueprintActivateVersionResponse:
    """Set a version as active. Deactivates all others."""
    from promptops_app.database import BlueprintVersion
    bp = _get_blueprint_or_404(db, blueprint_id, current_user)
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
    bp = _get_blueprint_or_404(db, blueprint_id, current_user)
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

def _reject_if_truncated(result, what: str) -> None:
    """Raise rather than let a fragment overwrite a stored blueprint.

    The model's own output cap is the one bound content may hit, but a reply that
    hit it is a fragment with nothing to say so: the prose reads as finished and
    the caller splices it straight back over the section it replaced. Committing
    that loses the tail silently, which is strictly worse than failing — a
    failure the user can see, they can act on by narrowing the request or picking
    a model with more output range.

    Mirrors ``_reject_if_truncated`` in the CDD router. Kept local rather than
    shared so the log line names the entity, and so hardening this path does not
    require editing the CDD one.
    """
    if not getattr(result, "truncated", False):
        return
    _log.error(
        "blueprint_llm_output_truncated  what=%s  model=%s  stop_reason=%s  completion_tokens=%s",
        what, getattr(result, "model", "?"), getattr(result, "stop_reason", None),
        getattr(result, "completion_tokens", None),
    )
    raise LLMGenerationError(
        f"The model ran out of output room part-way through {what}, so the reply "
        f"is incomplete and was not applied — nothing was changed. Narrow the "
        f"request, or choose a model with a larger output limit."
    )


def _generating_prompt_guidance(db: Session, bp, *, model_choice: str,
                                current_user) -> tuple[str, dict]:
    """Judgment guidance distilled from the prompt that produced this Blueprint.

    Section regeneration renders BLUEPRINT_SECTION_REGENERATE_PROMPT under
    BLUEPRINT_SYSTEM_PROMPT — a generic module-authoring constant — no matter
    which prompt authored the document. The revision template itself is right:
    it is a fixed contract ("revise this text, keep its facts"). What went
    missing was the document's own conventions. A DLU day outline written under
    an AIM prompt with day-type rules and an ACS disposition table was revised by
    a model that had never been told any of that existed, which is how
    blueprint_versions 396 came back as 397: a Day 20 exam outline replaced by
    generic Lesson 1/2/3 filler.

    So: distil, do not substitute. prompt_guidance is the module built for this —
    it extracts only a prompt's judgment and emphasis instructions, never its
    structure, precisely so an admin-edited prompt can refine how a fixed
    contract is filled without redefining it. Appending its output leaves the
    revision contract authoritative.

    The prompt is the one the document was generated with, read from the version
    that recorded it (5a6c523), which is why that had to land first. Regenerated
    versions record no generation_params yet, so the newest version that names a
    row wins and the search falls back through the lineage to v1.

    With no recorded row — every version predating 5a6c523 — this returns "" and
    the prompt is byte-identical to what it was before. Falling back to the
    course's current scope/default instead would be a guess, and the wrong guess
    is the failure being fixed: course 48's default resolves to a MODULE prompt
    while the document in question is DLU-shaped, so a legacy day outline would
    be revised under module conventions. Injecting the wrong conventions is worse
    than injecting none.

    Returns (guidance_text, provenance). Best-effort: prompt_guidance never
    raises and degrades to "", so a failure here costs the guidance, not the
    regeneration. BudgetExceededError is its one deliberate exception and is left
    to propagate to its 402 handler.
    """
    from promptops_app.core.llm_client import safe_json_loads
    from promptops_app.database import BlueprintVersion

    row_id, row_title, from_version = None, "", None
    # Four columns, not the entity. Every row here carries full_content and
    # sections, and blueprint 239 already has 12 versions holding 194 KB of them
    # between them — all of it loaded on every regeneration to read one JSON
    # field. Ordered in SQL (active first, then newest) and broken out at the
    # first match, so the common case reads one row.
    rows = (db.query(BlueprintVersion.id,
                     BlueprintVersion.version,
                     BlueprintVersion.is_active,
                     BlueprintVersion.generation_params)
              .filter(BlueprintVersion.blueprint_id == bp.id)
              .order_by(BlueprintVersion.is_active.desc(), BlueprintVersion.id.desc())
              .all())
    for _id, ver_name, _active, gen_params in rows:
        params = safe_json_loads(gen_params) if gen_params else {}
        if isinstance(params, dict) and params.get("prompt_row_id"):
            row_id = params["prompt_row_id"]
            row_title = params.get("prompt_title") or ""
            from_version = ver_name
            break

    if row_id is None:
        return "", {"applied": False, "reason": "no_recorded_prompt"}

    # The recorded row has to still BE that row. prompt_id resolution falls
    # through to the normal scope/default chain on a miss — a prompt deleted,
    # moved to another project, or belonging to another tenant — and that chain
    # ends at the file tier, whose blueprint_generation.md is the generic
    # module-level template. Distilling that would inject module conventions into
    # a document authored by something else: the exact failure this helper exists
    # to prevent, arrived at from the opposite direction. Verified against the
    # resolved template's own prompt_row_id, which is why 5a6c523 put it there.
    from promptops_app.prompts.prompt_loader import load_template

    try:
        resolved = load_template(
            "blueprint_generation", db=db, prompt_id=row_id,
            project_id=bp.project_id, course_id=bp.course_id,
        )
    except Exception as exc:  # noqa: BLE001 — a miss must cost the guidance only
        _log.warning("blueprint_regen_prompt_unresolvable  bp_id=%s  row_id=%s  error=%s",
                     bp.id, row_id, exc)
        return "", {"applied": False, "reason": "recorded_prompt_unresolvable",
                    "prompt_row_id": row_id}
    if getattr(resolved, "prompt_row_id", None) != row_id:
        _log.warning(
            "blueprint_regen_prompt_no_longer_resolves  bp_id=%s  recorded=%s  got=%s "
            "— revising without its conventions rather than borrowing another prompt's",
            bp.id, row_id, getattr(resolved, "prompt_row_id", None),
        )
        return "", {"applied": False, "reason": "recorded_prompt_no_longer_resolves",
                    "prompt_row_id": row_id, "prompt_title": row_title}

    # _resolve_prompt_text duck-types its input via getattr, so the regeneration
    # request — which carries no course/project/prompt fields — is adapted here
    # rather than by widening that function's contract for one caller. No
    # *_override attributes: an inline override belongs to the request that used
    # it, not to a later revision of the document it produced.
    shim = SimpleNamespace(
        course_id=bp.course_id,
        project_id=bp.project_id,
        prompt_id=row_id,
        model_choice=model_choice,
        # resolve_prompt_guidance builds this call's UsageLogContext with
        # entity_id=str(request_body.block). Without the attribute the
        # distillation's cost logged against an EMPTY entity id, so it could be
        # attributed to the project and course but never traced back to the
        # blueprint that triggered it. The blueprint id alongside
        # entity_type="blueprint" is the same pairing the regeneration call's own
        # usage context uses; the attribute is named `block` only because that is
        # the field the guidance service reads.
        block=str(bp.id),
    )

    from promptops_app.services.prompt_guidance import resolve_prompt_guidance

    guidance = resolve_prompt_guidance(db, shim, "blueprint", current_user) or ""
    return guidance, {
        "applied": bool(guidance.strip()),
        "prompt_row_id": row_id,
        "prompt_title": row_title,
        "from_version": from_version,
        "chars": len(guidance),
    }


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


def _blueprint_regen_context(db: Session, bp, *, instruction: str,
                             section_key: str, section_content: str) -> tuple:
    """Scoped CDD grounding for one blueprint regeneration: ``(text, provenance)``.

    What this replaces was not a summary. ``extract_cdd_summary`` looks for
    priority keys ("Learning Objectives", "Key Concepts & Terminology", …); a
    worksheet-shaped CDD has none of them, so it fell through to
    ``_cap(full_content, 1500)`` — the first 1,500 characters, cut mid-word. For
    CDD 183 (78,189 chars) that is Worksheet 1's prose explaining what a
    Blueprint document IS, and none of WORKSHEET 4: ACS CODE REGISTRY or
    WORKSHEET 5: DAY-BY-DAY MAP. An instruction asking why a day's ACS cells
    read N/A could not be answered from it, because the row that answers it
    ("Day 20 | Test — Block 2: Final Exam | … | Full-block coverage (AM.I.B,
    AM.I.E, AM.I.G — all codes taught Days 1–19)") sits at line 173 of 189.

    ``cdd_regen_context.build_context`` selects whole units instead — block
    overview, the scoped day's rows, the registry entries for that day's codes —
    caps each part and the whole by TOKENS, and reports what it used. Nothing is
    cut mid-word and nothing is dropped unreported.

    The scope comes from the blueprint's own day rather than from whatever the
    requester typed: the day is a property of the document, and ``parse_scope``
    only reads the instruction.
    """
    if not getattr(bp, "cdd_id", None):
        return "No CDD linked.", {"grounded": False, "reason": "no_cdd"}

    from promptops_app.repositories import cdd_repository
    from app.services import cdd_regen_context as regen_ctx_svc
    from promptops_app.parsers.blueprint_parser import parse_day_and_title

    cdd = cdd_repository.get_cdd_by_id(db, bp.cdd_id)
    if cdd is None:
        return "No CDD linked.", {"grounded": False, "reason": "cdd_missing"}

    day, _topic = parse_day_and_title(bp.title, bp.module_number)
    scoped_instruction = f"Day {day} {instruction}".strip() if day else (instruction or "")

    context = regen_ctx_svc.build_context(
        db, cdd, section_key=section_key,
        instruction=scoped_instruction, section_content=section_content,
    )
    if context.is_grounded:
        return context.text, context.provenance()

    # Not every CDD is worksheet-shaped — one authored before that structure has
    # no rows to scope to. Falling back to the old behaviour is worse context but
    # it is not nothing, and the provenance says which path was taken.
    fallback = _blueprint_cdd_summary(db, bp, max_chars=1500)
    return (fallback or "No CDD linked.",
            {"grounded": False, "reason": "cdd_not_worksheet_shaped",
             "fallback_chars": len(fallback)})


@router.post(
    "/{blueprint_id}/regenerate-item",
    response_model=JobAcceptedResponse,
    status_code=202,
    summary="Regenerate a single item within a blueprint section (async)",
    responses={404: {"description": "Blueprint or item not found."}},
)
def regenerate_blueprint_item(
    blueprint_id: int,
    request_body: BlueprintRegenerateItemRequest,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("blueprint.version")),
) -> JobAcceptedResponse:
    """Queue blueprint item regeneration; result lands in job.result_json."""
    from promptops_app.jobs import design_jobs, dispatch
    from promptops_app.repositories import job_repository

    bp = _get_blueprint_or_404(db, blueprint_id, current_user)
    params = request_body.model_dump()
    params["blueprint_id"] = blueprint_id
    design_jobs.stamp_job_user(params, current_user)
    job_id = job_repository.create_job(
        db,
        user_name=current_user.username,
        request_params=params,
        project_id=getattr(bp, "project_id", None),
        course_id=getattr(bp, "course_id", None),
        job_type="blueprint_regen_item",
    )
    dispatch.submit(design_jobs.run_blueprint_regen_item_job, job_id)
    return JobAcceptedResponse(
        job_id=job_id, status="queued", status_url=f"/api/v1/jobs/{job_id}",
    )


def execute_blueprint_regen_item(
    db: Session,
    blueprint_id: int,
    request_body: BlueprintRegenerateItemRequest,
    current_user,
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
        regen_single_item_with_result,
    )
    from promptops_app.services.usage_service import UsageLogContext
    from app.services import cdd_regen_context as regen_ctx_svc

    bp = _get_blueprint_or_404(db, blueprint_id, current_user)

    original = request_body.section_content or ""
    item_index = request_body.item_index
    items = parse_items_from_section(original)
    if not items or item_index < 0 or item_index >= len(items):
        raise NotFoundError("Blueprint item", item_index)

    item_text = items[item_index]["text"]

    # parse_items_from_section recognises lists and headings but NOT table rows,
    # so a markdown table parses to a single item: "one item" can be an entire
    # ACS coverage or day table. Refuse before spending when that item cannot come
    # back whole — patch_item_in_section would splice the fragment in and the
    # commit would succeed with rows missing.
    regen_ctx_svc.assert_can_emit(
        item_text, model_choice=request_body.model_choice,
        label=f'Item {item_index + 1} of "{request_body.section_key}"',
    )

    # The same scoped grounding the section route uses. What this replaces put
    # 300 characters of CDD into the INSTRUCTION field — so the model received
    # the requester's words with a truncated document blob glued onto them, and
    # regen_single_item's own `context` parameter (added for exactly this) went
    # unused. The module identity moves into the context block where it belongs.
    cdd_context, cdd_provenance = _blueprint_regen_context(
        db, bp,
        instruction=request_body.feedback or "",
        section_key=request_body.section_key,
        section_content=original,
    )
    item_context = f"Module: {bp.module_title}\n\n{cdd_context}" if cdd_context else ""

    usage_ctx = UsageLogContext(
        user_name=current_user.username, project_id=bp.project_id, course_id=bp.course_id,
        entity_type="blueprint_item_regen", entity_id=str(blueprint_id),
    )
    new_item_text, item_result = regen_single_item_with_result(
        section_title=request_body.section_key,
        section_content=original,
        item_index=item_index,
        item_text=item_text,
        custom_instruction=(request_body.feedback or "").strip(),
        model_choice=request_body.model_choice,
        usage_ctx=usage_ctx,
        context=item_context,
        # The same ceiling assert_can_emit sized the item against a few lines
        # above. Without it the call inherited a flat 16384 while the guard
        # allowed up to 64000, so an item between the two passed the check and
        # truncated anyway.
        max_tokens=regen_ctx_svc.output_budget(request_body.model_choice),
    )
    # The reply is about to be patched over a line of the stored document, and a
    # truncated item reads as a finished one. Refuse instead — the same guard the
    # section path applies, for the same reason.
    _reject_if_truncated(item_result, "regenerating this item")
    updated_content = patch_item_in_section(original, item_index, new_item_text)

    _log.info("blueprint_item_regenerated  user=%s  bp_id=%d  section=%s  item=%d  context=%s",
              current_user.username, blueprint_id, request_body.section_key, item_index,
              cdd_provenance)

    from promptops_app.services.budget_service import build_usage_summary

    return BlueprintRegenerateItemResponse(
        updated_content=updated_content,
        patched_item=new_item_text or "",
        usage_summary=build_usage_summary(db, usage_ctx, "blueprint_item_regen", str(blueprint_id)),
    )


@router.post(
    "/{blueprint_id}/regenerate-section",
    response_model=JobAcceptedResponse,
    status_code=202,
    summary="Regenerate an entire blueprint section with AI (async)",
    responses={404: {"description": "Blueprint not found."}},
)
def regenerate_blueprint_section(
    blueprint_id: int,
    request_body: BlueprintRegenerateSectionRequest,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("blueprint.version")),
) -> JobAcceptedResponse:
    """Queue blueprint section regeneration; result lands in job.result_json."""
    from promptops_app.jobs import design_jobs, dispatch
    from promptops_app.repositories import job_repository

    bp = _get_blueprint_or_404(db, blueprint_id, current_user)
    params = request_body.model_dump()
    params["blueprint_id"] = blueprint_id
    design_jobs.stamp_job_user(params, current_user)
    job_id = job_repository.create_job(
        db,
        user_name=current_user.username,
        request_params=params,
        project_id=getattr(bp, "project_id", None),
        course_id=getattr(bp, "course_id", None),
        job_type="blueprint_regen_section",
    )
    dispatch.submit(design_jobs.run_blueprint_regen_section_job, job_id)
    return JobAcceptedResponse(
        job_id=job_id, status="queued", status_url=f"/api/v1/jobs/{job_id}",
    )


def execute_blueprint_regen_section(
    db: Session,
    blueprint_id: int,
    request_body: BlueprintRegenerateSectionRequest,
    current_user,
) -> BlueprintRegenerateSectionResponse:
    """Regenerate an entire blueprint section (background worker entry)."""
    """
    Regenerate a whole blueprint section using the section-regeneration prompt
    (student or teacher variant). Replicates "🔄 Regenerate Section" in Streamlit.

    Stateless: returns the new section content; the frontend commits a version.
    """
    from promptops_app.parsers.blueprint_parser import get_blueprint_prompts
    from promptops_app.services.llm_service import generate_with_metadata
    from promptops_app.services.usage_service import UsageLogContext
    from app.services import cdd_regen_context as regen_ctx_svc

    bp = _get_blueprint_or_404(db, blueprint_id, current_user)
    mode = "teacher" if request_body.teacher_mode else "student"
    regen_system, _, regen_template = get_blueprint_prompts(mode)
    cdd_summary, cdd_provenance = _blueprint_regen_context(
        db, bp,
        instruction=request_body.feedback or "",
        section_key=request_body.section_key,
        section_content=request_body.section_content or "",
    )

    # Refuse before spending anything when the target cannot come back whole.
    # Whole-document regeneration is the case that matters: the model emits what
    # fits, the page splices it in, and the commit succeeds with the tail gone.
    # The helper is entity-agnostic (text + model + label), so this is the same
    # guard the CDD path already uses.
    if (request_body.section_content or "").strip():
        regen_ctx_svc.assert_can_emit(
            request_body.section_content,
            model_choice=request_body.model_choice,
            label=f'The "{request_body.section_key}" section',
        )

    regen_prompt = regen_template.format(
        section_title=request_body.section_key,
        module_title=bp.module_title,
        course_title=bp.title,
        cdd_summary=cdd_summary or "No CDD linked.",
        custom_instruction=request_body.feedback or "Improve this section.",
        # Sent in full, never clipped: the caller is asking for a revision of THIS
        # text, and a section trimmed to fit is a section the model will silently
        # finish from its own assumptions.
        current_content=request_body.section_content or "",
    )

    # Appended, not substituted. The block above is the revision contract and
    # stays authoritative; this carries the conventions of the prompt that
    # authored the document, which the generic system prompt above knows nothing
    # about. Empty when no prompt is resolvable or the distillation fails, in
    # which case the prompt is byte-identical to what it was before.
    guidance, guidance_provenance = _generating_prompt_guidance(
        db, bp, model_choice=request_body.model_choice, current_user=current_user,
    )
    if guidance.strip():
        regen_prompt = (
            f"{regen_prompt}\n\n"
            "---\n"
            "**Conventions of the prompt this document was written under.** These "
            "refine how you revise; they never override the instructions above, "
            "and they never license adding or removing structure the section does "
            "not already have.\n\n"
            f"{guidance}\n---\n"
        )

    usage_ctx = UsageLogContext(
        user_name=current_user.username, project_id=bp.project_id, course_id=bp.course_id,
        entity_type="blueprint_section_regen", entity_id=str(blueprint_id),
    )
    # generate_with_metadata, not generate_text: the latter returns a bare string,
    # so this route could neither ask for the model's real output ceiling (it
    # inherited a flat default, capping a 64k model at a quarter of its range) nor
    # see whether the reply hit that ceiling. Both matter when the reply is about
    # to overwrite stored content.
    result = generate_with_metadata(
        request_body.model_choice, regen_system, regen_prompt, usage_ctx,
        max_tokens=regen_ctx_svc.output_budget(request_body.model_choice),
    )
    new_content = f"ERROR: {result.text}" if result.is_error else result.text
    if not new_content or new_content.startswith("ERROR"):
        raise LLMGenerationError("Section regeneration failed. Please try again.")
    _reject_if_truncated(result, "regenerating this section")

    _log.info("blueprint_section_regenerated  user=%s  bp_id=%d  section=%s  mode=%s  "
              "grounded=%s  context=%s  prompt_guidance=%s",
              current_user.username, blueprint_id, request_body.section_key, mode,
              bool((request_body.section_content or "").strip()), cdd_provenance,
              guidance_provenance)

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
    bp = _get_blueprint_or_404(db, blueprint_id, current_user)
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

    bp = _get_blueprint_or_404(db, blueprint_id, current_user)
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

    _get_blueprint_or_404(db, blueprint_id, current_user)
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
    from promptops_app.services.deliverable_labels import (
        label as deliverable_label, filename_slug,
        apply_terminology as deliverable_apply_terminology,
    )

    bp = _get_blueprint_or_404(db, blueprint_id, current_user)
    if not bp.active_version:
        raise WorkflowError("This blueprint has no active version to export.")

    ver = blueprint_repository.get_blueprint_version(db, blueprint_id, bp.active_version)
    if not ver:
        raise NotFoundError(f"Blueprint version '{bp.active_version}'", blueprint_id)

    # Tenant's word swapped into the title for the deliverable only (heading +
    # filename); the stored bp.title is left untouched so the DLU day/topic parser
    # (parse_day_and_title) keeps working on the canonical wording.
    bp_prefix = filename_slug(deliverable_label(db, bp.project_id, 'blueprint'))
    disp_title = deliverable_apply_terminology(bp.title, db, bp.project_id, ['blueprint'])

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
                # Swap the tenant's word into the body (e.g. "CONTENT TYPE: BLUEPRINT").
                sheets.append((label, deliverable_apply_terminology(clean, db, bp.project_id)))
        if sheets:
            buf = build_xlsx_worksheets(disp_title, sheets)
            fname = f"{bp_prefix}_{disp_title.replace(' ', '_')}_{bp.active_version}.xlsx"
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
        # Swap the tenant's word into the body (e.g. "CONTENT TYPE: BLUEPRINT").
        export_blocks.append((section_key, deliverable_apply_terminology(content, db, bp.project_id)))

    if not export_blocks:
        export_blocks = [("Blueprint Content",
                          deliverable_apply_terminology(ver.full_content or "", db, bp.project_id))]

    export_req = ExportRequest(
        fmt=format, topic=disp_title,
        blocks=export_blocks,
        user_name=current_user.username, is_admin=(current_user.role == "admin"),
        entity_type="blueprint", entity_id=bp.id,
        file_name=f"{bp_prefix}_{disp_title.replace(' ', '_')}_{bp.active_version}.{format}",
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
    regenerated 9 times still contributes one section). Editor exports intentionally
    include content in every workflow state so authors can download work in progress.
    """
    from promptops_app.repositories import generation_repository
    from promptops_app.services.export_service import ExportRequest, export_content
    from promptops_app.services.deliverable_labels import (
        apply_terminology as deliverable_apply_terminology,
    )

    bp = _get_blueprint_or_404(db, blueprint_id, current_user)
    # Deliverable-only word swap; stored bp.title untouched (see export_blueprint).
    disp_title = deliverable_apply_terminology(bp.title, db, bp.project_id, ['blueprint'])

    latest_gens = generation_repository.list_latest_generations_for_blueprint(db, blueprint_id)
    if not latest_gens:
        raise WorkflowError("No generated lessons found for this module.")

    gen_ids = [g.id for g in latest_gens]
    lesson_blocks = generation_repository.list_blocks_for_gen_ids(db, gen_ids)
    if not lesson_blocks:
        raise WorkflowError("No lesson content found for this module yet.")

    # Order sections by lesson number (parsed from the generation's topic), not by
    # database insertion order, so the combined file reads Lesson 1 -> Lesson 2 -> ...
    topic_by_gen_id = {g.id: (g.topic or "") for g in latest_gens}

    def _lesson_order(block):
        topic = topic_by_gen_id.get(block.generation_id, "")
        m = re.match(r"lesson\s*(\d+)", topic, re.I)
        return (int(m.group(1)) if m else 9999, topic, block.id)

    lesson_blocks.sort(key=_lesson_order)

    export_req = ExportRequest(
        fmt=format, topic=disp_title,
        blocks=[(b.block_label, deliverable_apply_terminology(b.content or "", db, bp.project_id)) for b in lesson_blocks],
        user_name=current_user.username, is_admin=(current_user.role == "admin"),
        entity_type="module", entity_id=bp.id,
        file_name=f"{disp_title.replace(' ', '_')}_lessons.{format}",
    )
    result = export_content(db, export_req)
    if not result.success:
        raise WorkflowError(f"Export failed: {result.error_message}")

    _log.info("module_lessons_exported  user=%s  blueprint_id=%d  format=%s  blocks=%d",
              current_user.username, blueprint_id, format, len(lesson_blocks))

    return Response(
        content=result.data, media_type=result.mime_type,
        headers={"Content-Disposition": content_disposition(result.file_name)},
    )
