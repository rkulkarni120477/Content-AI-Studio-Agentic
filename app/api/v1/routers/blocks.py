"""
Blocks router — content block editing, versioning, and quality tools.

Streamlit equivalent: ``pages/editor.py`` render_page()

A Block is the atomic unit of generated content. Each generation produces
multiple blocks (Introduction, Lesson Body, Knowledge Check, etc.).

This router handles:
  - Listing and viewing blocks
  - Manual editing (creates a BlockVersion with change_source='edit')
  - Autosave (no version — just updates the draft)
  - AI regeneration (full block or single item)
  - Version history and restore
  - Manual snapshots
  - Quality scoring (LLM evaluation)
  - Content validation (rule-based)
  - Plagiarism scan trigger and status
  - Rating (reviewer star rating)
  - Full course export
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response
from sqlalchemy.orm import Session

from app.core.dependencies import get_current_user, get_db, require_permission
from app.core.exceptions import LLMGenerationError, NotFoundError, WorkflowError
from app.schemas.block import (
    BlockAutosaveRequest,
    BlockAutosaveResponse,
    BlockCanvasHtmlResponse,
    BlockListItem,
    CourseModuleCreateRequest,
    CourseModuleRead,
    CourseModuleRenameRequest,
    CourseModuleReorderRequest,
    CourseModulesResponse,
    ModuleBlock,
    ModuleLayoutRequest,
    ModuleLayoutResponse,
    BlockRatingRequest,
    BlockRead,
    BlockRegenerateItemRequest,
    BlockRegenerateItemResponse,
    BlockRegenerateRequest,
    BlockRegenerateResponse,
    BlockReorderRequest,
    BlockReorderResponse,
    BlockRestoreResponse,
    BlockScoreResponse,
    BlockSnapshotRequest,
    BlockSnapshotResponse,
    BlockUpdateRequest,
    BlockValidateResponse,
    BlockVersionListItem,
    BlockVersionRead,
    GenerationValidateResponse,
    PlagiarismStatusResponse,
    PlagiarismTriggerResponse,
    ValidationIssue,
)
from app.schemas.common import PaginatedResponse

_log = logging.getLogger(__name__)
router = APIRouter()


def _get_block_or_404(db: Session, block_id: int):
    """Fetch a block by ID or raise HTTP 404."""
    from promptops_app.database import Block
    block = db.query(Block).filter(Block.id == block_id).first()
    if block is None:
        raise NotFoundError("Block", block_id)
    return block


# ---------------------------------------------------------------------------
# List and read
# ---------------------------------------------------------------------------

@router.get(
    "/search",
    response_model=list[BlockListItem],
    summary="Search blocks by label or content (Editor page)",
)
def search_blocks(
    q: str = Query(..., min_length=1, description="Search query."),
    limit: int = Query(default=50, ge=1, le=200),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
) -> list[BlockListItem]:
    """Global block search — matches Streamlit search_blocks()."""
    from promptops_app.database import search_blocks as _search_blocks

    blocks = _search_blocks(db, q)[:limit]
    return [
        BlockListItem(
            id=b.id,
            block_label=b.block_label,
            content_preview=(b.content or "")[:300] if b.content else None,
            workflow_state=b.workflow_state,
            position=b.position or 0,
            rating=b.rating,
            generation_id=b.generation_id,
        )
        for b in blocks
    ]


@router.get(
    "/generations/{generation_id}/blocks",
    response_model=PaginatedResponse[BlockListItem],
    summary="List blocks for a generation",
)
def list_blocks(
    generation_id: int,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=25, ge=1, le=100),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
) -> PaginatedResponse[BlockListItem]:
    """Return paginated blocks for a generation. Matches the Editor page block listing."""
    from promptops_app.repositories import generation_repository

    blocks = generation_repository.list_blocks_for_generation(db, generation_id)
    total = len(blocks)
    start = (page - 1) * page_size

    items = []
    for b in blocks[start: start + page_size]:
        item = BlockListItem(
            id=b.id,
            block_label=b.block_label,
            content_preview=(b.content or "")[:300] if b.content else None,
            workflow_state=b.workflow_state,
            position=b.position or 0,
            rating=b.rating,
            generation_id=b.generation_id,
        )
        items.append(item)

    return PaginatedResponse.create(items=items, total=total, page=page, page_size=page_size)


@router.get(
    "/courses/{course_id}/blocks",
    response_model=PaginatedResponse[BlockListItem],
    summary="List all blocks across all generations for a course",
    description="Used for full-course export and the completion banner.",
)
def list_course_blocks(
    course_id: int,
    workflow_state: str | None = Query(default=None, description="Filter by state, e.g. 'approved'"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
) -> PaginatedResponse[BlockListItem]:
    """Return all blocks for a course, optionally filtered by workflow state."""
    from promptops_app.repositories import generation_repository

    gens = generation_repository.list_course_generations(db, course_id=course_id)
    gen_ids = [g.id for g in gens]
    all_blocks = generation_repository.list_blocks_for_gen_ids(db, gen_ids)

    if workflow_state:
        all_blocks = [b for b in all_blocks if b.workflow_state.lower() == workflow_state.lower()]

    total = len(all_blocks)
    start = (page - 1) * page_size
    items = [
        BlockListItem(
            id=b.id, block_label=b.block_label,
            content_preview=(b.content or "")[:300],
            workflow_state=b.workflow_state, position=b.position or 0, rating=b.rating,
            generation_id=b.generation_id,
            has_html=bool(getattr(b, "content_html", None) and b.content_html.strip()),
        )
        for b in all_blocks[start: start + page_size]
    ]
    return PaginatedResponse.create(items=items, total=total, page=page, page_size=page_size)


@router.put(
    "/courses/{course_id}/blocks/reorder",
    response_model=BlockReorderResponse,
    summary="Reorder published/approved blocks (TOC)",
    description="Sets display order for course blocks. Used by the Workflow published TOC panel.",
)
def reorder_course_blocks(
    course_id: int,
    request_body: BlockReorderRequest,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("export.course")),
) -> BlockReorderResponse:
    """Persist a new TOC order for blocks in a course."""
    from promptops_app.repositories import generation_repository

    updated = generation_repository.reorder_course_blocks(
        db, course_id, request_body.block_ids,
    )
    return BlockReorderResponse(
        updated=len(updated),
        block_ids=[b.id for b in updated],
    )


@router.get("/{block_id}", response_model=BlockRead, summary="Get full block content")
def get_block(block_id: int, db: Session = Depends(get_db), current_user=Depends(get_current_user)) -> BlockRead:
    """Return full block content and metadata."""
    return BlockRead.model_validate(_get_block_or_404(db, block_id))


# ---------------------------------------------------------------------------
# Edit and autosave
# ---------------------------------------------------------------------------

@router.put(
    "/{block_id}",
    response_model=BlockRead,
    summary="Save manual edits to a block",
    description="Creates a BlockVersion with change_source='edit' before saving.",
)
def update_block(
    block_id: int,
    request_body: BlockUpdateRequest,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("editor.edit")),
) -> BlockRead:
    """
    Save manually edited content.

    Replicates the text area save button in the Streamlit Editor.
    Always creates a version snapshot before overwriting the content.
    """
    from promptops_app.repositories.block_repo import save_block_version
    from promptops_app.core.constants import ChangeSource

    block = _get_block_or_404(db, block_id)
    save_block_version(db, block, change_source=ChangeSource.EDIT, created_by=current_user.username)
    block.content = request_body.content
    db.commit()
    db.refresh(block)

    _log.info("block_edited  user=%s  block_id=%d", current_user.username, block_id)
    return BlockRead.model_validate(block)


@router.post(
    "/{block_id}/autosave",
    response_model=BlockAutosaveResponse,
    summary="Autosave block draft (no version created)",
)
def autosave_block(
    block_id: int,
    request_body: BlockAutosaveRequest,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
) -> BlockAutosaveResponse:
    """
    Persist a draft without creating a version entry.

    Called from React on a debounce timer (every ~10 seconds of inactivity).
    Replicates autosave_service.autosave_block() behaviour.
    Skipped if the block is in an approved or published state.
    """
    from promptops_app.core.constants import WorkflowState

    block = _get_block_or_404(db, block_id)

    # Do not autosave approved/published blocks — they are locked.
    if block.workflow_state.lower() in (WorkflowState.APPROVED, WorkflowState.PUBLISHED):
        from datetime import datetime, timezone
        return BlockAutosaveResponse(saved=False, saved_at=datetime.now(timezone.utc).isoformat())

    block.content = request_body.content
    db.commit()

    from datetime import datetime, timezone
    return BlockAutosaveResponse(saved=True, saved_at=datetime.now(timezone.utc).isoformat())


# ---------------------------------------------------------------------------
# Regeneration
# ---------------------------------------------------------------------------

@router.post(
    "/{block_id}/regenerate",
    response_model=BlockRegenerateResponse,
    summary="Regenerate the full block content with AI",
)
def regenerate_block(
    block_id: int,
    request_body: BlockRegenerateRequest,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("editor.edit")),
) -> BlockRegenerateResponse:
    """
    Regenerate block content using the IMPROVISE prompt.

    Replicates the "🔁 Regenerate" button in the Streamlit Editor.
    Saves the current content as a version before overwriting.
    """
    from promptops_app.core.constants import ChangeSource
    from promptops_app.prompt_templates import IMPROVISE_BLOCK_PROMPT_TEMPLATE, PERSONA_PREFIX_TEMPLATE
    from promptops_app.repositories.block_repo import save_block_version
    from promptops_app.services.llm_service import generate_with_metadata
    from promptops_app.services.usage_service import UsageLogContext

    block = _get_block_or_404(db, block_id)

    feedback = request_body.feedback_instruction.strip() if request_body.feedback_instruction else ""
    system_prompt = PERSONA_PREFIX_TEMPLATE
    user_prompt = IMPROVISE_BLOCK_PROMPT_TEMPLATE.format(
        block_label=block.block_label,
        current_content=block.content or "",
        feedback_instruction=feedback or "Improve overall quality, clarity, and engagement.",
    )

    llm_result = generate_with_metadata(
        request_body.model_choice, system_prompt, user_prompt,
        usage_ctx=UsageLogContext(user_name=current_user.username, entity_type="block"),
    )

    if llm_result.status == "error":
        raise LLMGenerationError("Block regeneration failed. Please try again.")

    # Save current as a version before overwriting.
    save_block_version(db, block, change_source=ChangeSource.REGENERATION, created_by=current_user.username)
    versions = block.versions if hasattr(block, 'versions') else []
    version_label = f"v{len(versions) + 1}"

    block.content = llm_result.text
    db.commit()

    _log.info("block_regenerated  user=%s  block_id=%d  model=%s",
              current_user.username, block_id, llm_result.model)

    return BlockRegenerateResponse(
        block_id=block_id,
        content=llm_result.text,
        model_used=llm_result.model or request_body.model_choice,
        tokens_used=(
            (llm_result.prompt_tokens or 0) + (llm_result.completion_tokens or 0)
            if llm_result.prompt_tokens else None
        ),
        version_created=version_label,
    )


@router.post(
    "/{block_id}/regenerate-item",
    response_model=BlockRegenerateItemResponse,
    summary="Regenerate a single item within a block",
)
def regenerate_block_item(
    block_id: int,
    request_body: BlockRegenerateItemRequest,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("editor.edit")),
) -> BlockRegenerateItemResponse:
    """
    Regenerate one bullet, question, or item within a block.

    Calls regen_single_item() from blueprint_parser.py.
    """
    from promptops_app.parsers.blueprint_parser import (
        parse_items_from_section,
        patch_item_in_section,
        regen_single_item,
    )

    block = _get_block_or_404(db, block_id)

    original = block.content or ""
    item_index = request_body.item_index
    items = parse_items_from_section(original)
    if not items or item_index < 0 or item_index >= len(items):
        raise NotFoundError(f"Item index {item_index} not found in block {block_id}.")

    target = items[item_index]
    new_item_text = regen_single_item(
        section_title=request_body.section_key or (block.block_label or "content"),
        section_content=original,
        item_index=item_index,
        item_text=target["text"],
        custom_instruction=request_body.feedback or "",
        model_choice=request_body.model_choice,
    )
    updated_content = patch_item_in_section(original, item_index, new_item_text)

    block.content = updated_content
    db.commit()

    _log.info("block_item_regenerated  user=%s  block_id=%d  item=%d",
              current_user.username, block_id, item_index)

    return BlockRegenerateItemResponse(
        block_id=block_id,
        updated_content=updated_content,
        patched_item=new_item_text or "",
    )


# ---------------------------------------------------------------------------
# Version history
# ---------------------------------------------------------------------------

@router.get("/{block_id}/versions", response_model=list[BlockVersionListItem], summary="List block version history")
def list_block_versions(block_id: int, db: Session = Depends(get_db), current_user=Depends(get_current_user)) -> list[BlockVersionListItem]:
    """Return all saved versions for a block, newest first."""
    from promptops_app.repositories.block_repo import get_block_versions
    _get_block_or_404(db, block_id)
    versions = get_block_versions(db, block_id)
    return [BlockVersionListItem.model_validate(v) for v in versions]


@router.post(
    "/{block_id}/versions/{version_id}/restore",
    response_model=BlockRestoreResponse,
    summary="Restore a block to a previous version",
)
def restore_block_version(
    block_id: int,
    version_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("editor.edit")),
) -> BlockRestoreResponse:
    """
    Restore block content to a saved version.

    Creates a pre-restore snapshot of the current content first,
    then applies the historical version. Matches restore_block_version() behaviour.
    """
    from promptops_app.repositories.block_repo import restore_block_version as _restore

    block = _get_block_or_404(db, block_id)
    ok, message = _restore(db, block, version_id, restored_by=current_user.username)
    if not ok:
        raise WorkflowError(message or "Could not restore version.")

    db.refresh(block)
    _log.info("block_restored  user=%s  block_id=%d  to_version=%d", current_user.username, block_id, version_id)
    return BlockRestoreResponse(block_id=block_id, restored_to_version=version_id, content=block.content or "")


@router.post("/{block_id}/snapshot", response_model=BlockSnapshotResponse, status_code=201, summary="Save a manual snapshot")
def create_snapshot(
    block_id: int,
    request_body: BlockSnapshotRequest,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("editor.snapshot")),
) -> BlockSnapshotResponse:
    """Save the current block content as a named snapshot."""
    from promptops_app.core.constants import ChangeSource
    from promptops_app.repositories.block_repo import save_block_version
    from datetime import datetime, timezone

    block = _get_block_or_404(db, block_id)
    version = save_block_version(
        db, block,
        change_source=ChangeSource.MANUAL_SNAPSHOT,
        created_by=current_user.username,
        label=request_body.label,
    )
    db.commit()
    _log.info("block_snapshot  user=%s  block_id=%d", current_user.username, block_id)
    return BlockSnapshotResponse(
        version_id=version.id if version else 0,
        label=request_body.label,
        created_at=datetime.now(timezone.utc).isoformat(),
    )


# ---------------------------------------------------------------------------
# Quality, validation, plagiarism
# ---------------------------------------------------------------------------

@router.post("/{block_id}/score", response_model=BlockScoreResponse, summary="AI quality score a block")
def score_block(
    block_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("editor.review")),
) -> BlockScoreResponse:
    """Run LLM-based quality evaluation. Returns a score 0-100 and structured feedback."""
    from promptops_app.services.evaluation_service import llm_evaluate_block

    block = _get_block_or_404(db, block_id)
    result = llm_evaluate_block(block)
    return BlockScoreResponse(
        block_id=block_id,
        score=result.get("score", 0),
        feedback=result.get("feedback", ""),
        metadata=result.get("metadata", {}),
    )


@router.post("/{block_id}/validate", response_model=BlockValidateResponse, summary="Validate block content rules")
def validate_block(
    block_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
) -> BlockValidateResponse:
    """Run rule-based content validation. Returns errors and warnings."""
    from promptops_app.services.validation_service import validate_blocks

    block = _get_block_or_404(db, block_id)
    result = validate_blocks([block])
    return BlockValidateResponse(
        block_id=block_id,
        passed=result["summary"]["errors"] == 0,
        errors=result.get("errors", []),
        warnings=result.get("warnings", []),
        summary=result.get("summary", {}),
    )


@router.post(
    "/courses/{course_id}/validate",
    response_model=GenerationValidateResponse,
    summary="Validate all blocks in a course",
)
def validate_course_blocks(
    course_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
) -> GenerationValidateResponse:
    """Validate all blocks across every generation in a course."""
    from promptops_app.repositories import generation_repository
    from promptops_app.services.validation_service import validate_blocks

    gens = generation_repository.list_course_generations(db, course_id=course_id)
    gen_ids = [g.id for g in gens]
    blocks = generation_repository.list_blocks_for_gen_ids(db, gen_ids)
    overall = validate_blocks(blocks)

    block_issues = []
    for block in blocks:
        r = validate_blocks([block])
        if r["summary"]["errors"] or r["summary"]["warnings"]:
            block_issues.append(ValidationIssue(
                block_id=block.id,
                block_label=block.block_label,
                errors=r.get("errors", []),
                warnings=r.get("warnings", []),
            ))

    return GenerationValidateResponse(
        generation_id=0,
        passed=overall["summary"]["errors"] == 0,
        summary=overall["summary"],
        blocks=block_issues,
    )


@router.post("/generations/{generation_id}/validate", response_model=GenerationValidateResponse, summary="Validate all blocks in a generation")
def validate_generation(
    generation_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
) -> GenerationValidateResponse:
    """Validate all blocks in a generation at once. Used before exporting a course."""
    from promptops_app.repositories import generation_repository
    from promptops_app.services.validation_service import validate_blocks

    blocks = generation_repository.list_blocks_for_generation(db, generation_id)
    overall = validate_blocks(blocks)

    block_issues = []
    for block in blocks:
        r = validate_blocks([block])
        if r["summary"]["errors"] or r["summary"]["warnings"]:
            block_issues.append(ValidationIssue(
                block_id=block.id,
                block_label=block.block_label,
                errors=r.get("errors", []),
                warnings=r.get("warnings", []),
            ))

    return GenerationValidateResponse(
        generation_id=generation_id,
        passed=overall["summary"]["errors"] == 0,
        summary=overall["summary"],
        blocks=block_issues,
    )


@router.post("/{block_id}/plagiarism", response_model=PlagiarismTriggerResponse, status_code=202, summary="Trigger a Copyleaks plagiarism scan")
def trigger_plagiarism_scan(
    block_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("editor.edit")),
) -> PlagiarismTriggerResponse:
    """
    Queue a Copyleaks plagiarism scan for a block.

    Creates a PlagiarismReport row, then fires run_plagiarism_scan.delay().
    Returns immediately — poll GET /blocks/{id}/plagiarism/{report_id} for results.
    """
    from promptops_app.database import PlagiarismReport
    from promptops_app.jobs.plagiarism_jobs import run_plagiarism_scan
    from promptops_app.services.plagiarism_service import generate_scan_id

    block = _get_block_or_404(db, block_id)

    report = PlagiarismReport(
        block_id=block_id,
        scan_id=generate_scan_id(),
        status="pending",
    )
    db.add(report)
    db.commit()
    db.refresh(report)

    task = run_plagiarism_scan.delay(report.id, block.content)
    report.celery_task_id = task.id
    db.commit()

    _log.info("plagiarism_queued  user=%s  block_id=%d  report_id=%d", current_user.username, block_id, report.id)
    return PlagiarismTriggerResponse(
        report_id=report.id,
        scan_id=report.scan_id,
        status_url=f"/api/v1/blocks/{block_id}/plagiarism/{report.id}",
    )


@router.get("/{block_id}/plagiarism/{report_id}", response_model=PlagiarismStatusResponse, summary="Get plagiarism scan result")
def get_plagiarism_status(
    block_id: int,
    report_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
) -> PlagiarismStatusResponse:
    """Return the status and results of a plagiarism scan."""
    from promptops_app.database import PlagiarismReport

    report = db.query(PlagiarismReport).filter(
        PlagiarismReport.id == report_id,
        PlagiarismReport.block_id == block_id,
    ).first()
    if not report:
        raise NotFoundError("PlagiarismReport", report_id)

    return PlagiarismStatusResponse(
        report_id=report.id,
        status=report.status,
        similarity_score=report.similarity_score,
        ai_score=report.ai_score,
        sources=report.sources or [],
    )


@router.put("/{block_id}/rating", response_model=BlockRead, summary="Set quality rating on a block")
def rate_block(
    block_id: int,
    request_body: BlockRatingRequest,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
) -> BlockRead:
    """Set a 1–5 star rating on a block. Used by reviewers in the Editor."""
    block = _get_block_or_404(db, block_id)
    block.rating = request_body.rating
    db.commit()
    db.refresh(block)
    return BlockRead.model_validate(block)


# ---------------------------------------------------------------------------
# Canvas HTML rendition (preview / regenerate)
# ---------------------------------------------------------------------------

@router.get(
    "/{block_id}/canvas-html",
    response_model=BlockCanvasHtmlResponse,
    summary="Get a block's Canvas-ready HTML rendition (preview)",
    description="Returns the LMS-ready HTML generated at publish, used by the IMSCC export.",
)
def get_block_canvas_html(
    block_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("export.course")),
) -> BlockCanvasHtmlResponse:
    """Return the stored Canvas HTML for a block (for preview in the Export screen)."""
    block = _get_block_or_404(db, block_id)
    html = getattr(block, "content_html", None)
    return BlockCanvasHtmlResponse(
        block_id=block.id,
        block_label=block.block_label or f"Block {block.id}",
        has_html=bool(html and html.strip()),
        content_html=html,
        content_html_at=getattr(block, "content_html_at", None),
    )


@router.post(
    "/{block_id}/canvas-html/regenerate",
    response_model=BlockCanvasHtmlResponse,
    summary="Regenerate a block's Canvas-ready HTML rendition",
    description="Re-runs the Canvas HTML Lesson Generator on the block's content and stores the result.",
)
def regenerate_block_canvas_html(
    block_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("export.course")),
) -> BlockCanvasHtmlResponse:
    """Regenerate and persist the Canvas HTML for a single block."""
    from datetime import datetime, timezone

    from promptops_app.services.canvas_html_service import generate_canvas_html
    from promptops_app.services.usage_service import UsageLogContext

    block = _get_block_or_404(db, block_id)

    if not (block.content or "").strip():
        raise WorkflowError("Block has no content to convert to HTML.")

    gen = block.generation
    usage_ctx = UsageLogContext(
        user_name=current_user.username,
        project_id=getattr(gen, "project_id", None) if gen else None,
        course_id=getattr(gen, "course_id", None) if gen else None,
        entity_type="canvas_html",
        entity_id=str(block.id),
    )
    html = generate_canvas_html(
        label=block.block_label or "",
        content=block.content or "",
        block_type=block.block_type or "",
        usage_ctx=usage_ctx,
    )
    if not html:
        raise LLMGenerationError("Canvas HTML generation failed. Please try again.")

    block.content_html = html
    block.content_html_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(block)

    _log.info("block_canvas_html_regenerated  user=%s  block_id=%d",
              current_user.username, block_id)

    return BlockCanvasHtmlResponse(
        block_id=block.id,
        block_label=block.block_label or f"Block {block.id}",
        has_html=True,
        content_html=block.content_html,
        content_html_at=block.content_html_at,
    )


# ---------------------------------------------------------------------------
# Course modules (Canvas-style export grouping)
# ---------------------------------------------------------------------------

def _has_html(b) -> bool:
    return bool(getattr(b, "content_html", None) and b.content_html.strip())


def _module_block(b) -> ModuleBlock:
    return ModuleBlock(
        id=b.id,
        block_label=b.block_label or f"Block {b.id}",
        position=b.position or 0,
        workflow_state=b.workflow_state,
        has_html=_has_html(b),
    )


@router.get(
    "/courses/{course_id}/modules",
    response_model=CourseModulesResponse,
    summary="List course modules with their published blocks (Export screen)",
)
def list_course_modules(
    course_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("export.course")),
) -> CourseModulesResponse:
    """Return modules (ordered) with their published blocks, plus unassigned published blocks."""
    from promptops_app.repositories import generation_repository as repo
    from promptops_app.core.constants import WorkflowState

    gens = repo.list_course_generations(db, course_id=course_id)
    gen_ids = [g.id for g in gens]
    all_blocks = repo.list_blocks_for_gen_ids(db, gen_ids)
    published = [b for b in all_blocks if b.workflow_state.lower() in WorkflowState.EXPORTABLE]

    modules = repo.list_course_modules(db, course_id)
    by_module: dict[int, list] = {m.id: [] for m in modules}
    unassigned = []
    for b in published:
        mid = getattr(b, "module_id", None)
        if mid in by_module:
            by_module[mid].append(b)
        else:
            unassigned.append(b)

    module_reads = [
        CourseModuleRead(
            id=m.id,
            title=m.title,
            position=m.position or 0,
            blocks=[_module_block(b) for b in by_module.get(m.id, [])],
        )
        for m in modules
    ]
    return CourseModulesResponse(
        modules=module_reads,
        unassigned=[_module_block(b) for b in unassigned],
    )


@router.post(
    "/courses/{course_id}/modules",
    response_model=CourseModuleRead,
    summary="Create a new course module",
)
def create_course_module(
    course_id: int,
    request_body: CourseModuleCreateRequest,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("export.course")),
) -> CourseModuleRead:
    from promptops_app.repositories import generation_repository as repo

    module = repo.create_course_module(db, course_id, request_body.title)
    return CourseModuleRead(id=module.id, title=module.title, position=module.position or 0, blocks=[])


@router.put(
    "/modules/{module_id}",
    response_model=CourseModuleRead,
    summary="Rename a course module",
)
def rename_course_module(
    module_id: int,
    request_body: CourseModuleRenameRequest,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("export.course")),
) -> CourseModuleRead:
    from promptops_app.repositories import generation_repository as repo

    module = repo.rename_course_module(db, module_id, request_body.title)
    if module is None:
        raise NotFoundError("Module", module_id)
    return CourseModuleRead(id=module.id, title=module.title, position=module.position or 0, blocks=[])


@router.delete(
    "/modules/{module_id}",
    summary="Delete a course module (blocks become unassigned)",
)
def delete_course_module(
    module_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("export.course")),
) -> dict:
    from promptops_app.repositories import generation_repository as repo

    ok = repo.delete_course_module(db, module_id)
    if not ok:
        raise NotFoundError("Module", module_id)
    return {"deleted": True, "module_id": module_id}


@router.put(
    "/courses/{course_id}/modules/reorder",
    response_model=CourseModulesResponse,
    summary="Reorder course modules",
)
def reorder_course_modules(
    course_id: int,
    request_body: CourseModuleReorderRequest,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("export.course")),
) -> CourseModulesResponse:
    from promptops_app.repositories import generation_repository as repo

    repo.reorder_course_modules(db, course_id, request_body.module_ids)
    return list_course_modules(course_id, db, current_user)


@router.put(
    "/courses/{course_id}/modules/layout",
    response_model=ModuleLayoutResponse,
    summary="Save module order + block placement in one call",
    description="Persists module ordering and which blocks belong to each module (Export screen drag-drop).",
)
def save_course_module_layout(
    course_id: int,
    request_body: ModuleLayoutRequest,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("export.course")),
) -> ModuleLayoutResponse:
    from promptops_app.repositories import generation_repository as repo

    layout = [{"module_id": row.module_id, "block_ids": row.block_ids} for row in request_body.modules]
    updated = repo.save_course_module_layout(db, course_id, layout)
    return ModuleLayoutResponse(updated=updated)


# ---------------------------------------------------------------------------
# Full course export
# ---------------------------------------------------------------------------

@router.get(
    "/courses/{course_id}/export",
    summary="Export the full course as a file",
    description="Exports all approved/published blocks across all generations. Supports docx, pdf, html, md, json, zip, imscc.",
)
def export_course(
    course_id: int,
    format: str = Query(default="docx", description="docx | pdf | html | md | json | zip | imscc"),
    template: str = Query(default="default", description="default | storyboard | teacher_guide | quiz_bank | client"),
    workflow_state: str | None = Query(
        default=None,
        description="Optional filter, e.g. 'published' — only include blocks in this state.",
    ),
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("export.course")),
) -> Response:
    """
    Export all approved blocks for a course as a formatted document.

    Replicates the full-course download in the Streamlit Editor completion banner.
    Only blocks in 'approved' or 'published' state are included.
    """
    from promptops_app.repositories import generation_repository
    from promptops_app.services.export_service import ExportRequest, export_content
    from promptops_app.core.constants import WorkflowState

    gens = generation_repository.list_course_generations(db, course_id=course_id)
    gen_ids = [g.id for g in gens]
    all_blocks = generation_repository.list_blocks_for_gen_ids(db, gen_ids)

    approved_blocks = [b for b in all_blocks if b.workflow_state.lower() in WorkflowState.EXPORTABLE]
    if workflow_state:
        approved_blocks = [
            b for b in approved_blocks
            if b.workflow_state.lower() == workflow_state.lower()
        ]
    if not approved_blocks:
        raise WorkflowError("No approved or published blocks found for this course.")

    from promptops_app.repositories import course_repository
    course = course_repository.get_course_by_id(db, course_id)
    topic = course.name if course else f"Course {course_id}"

    # Flatten blocks in module layout order so IMSCC indexes align with the manifest.
    modules_struct = None
    course_modules = generation_repository.list_course_modules(db, course_id)
    if course_modules:
        module_ids = {m.id for m in course_modules}
        ordered: list = []
        modules_struct = []
        for m in course_modules:
            members = [
                b for b in approved_blocks
                if getattr(b, "module_id", None) == m.id
            ]
            members.sort(key=lambda b: (b.position or 0, b.id))
            if members:
                start = len(ordered)
                ordered.extend(members)
                modules_struct.append((m.title, list(range(start, start + len(members)))))
        # Unassigned published blocks trail the module-grouped ones (→ "Course Content" in IMSCC).
        unassigned = [
            b for b in approved_blocks
            if getattr(b, "module_id", None) not in module_ids
        ]
        unassigned.sort(key=lambda b: (b.position or 0, b.id))
        ordered.extend(unassigned)
        if ordered:
            approved_blocks = ordered

    export_req = ExportRequest(
        fmt=format,
        topic=topic,
        blocks=[(b.block_label, b.content or "") for b in approved_blocks],
        block_types=[b.block_type or "" for b in approved_blocks],
        block_html=[getattr(b, "content_html", None) or "" for b in approved_blocks],
        modules=modules_struct,
        user_name=current_user.username,
        is_admin=(current_user.role == "admin"),
        entity_type="full_course",
        template=template,
        file_name=f"{topic.replace(' ', '_')}_export.{format if format != 'imscc' else 'imscc'}",
    )

    result = export_content(db, export_req)
    if not result.success:
        raise WorkflowError(f"Export failed: {result.error_message}")

    _log.info("course_exported  user=%s  course_id=%d  format=%s  blocks=%d",
              current_user.username, course_id, format, len(approved_blocks))

    return Response(
        content=result.data, media_type=result.mime_type,
        headers={"Content-Disposition": f'attachment; filename="{result.file_name}"'},
    )
