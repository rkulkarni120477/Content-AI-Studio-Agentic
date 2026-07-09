"""
Workflow router — approval pipeline state machine.

Streamlit equivalent: ``pages/workflow.py`` render_page()

Every state transition is a dedicated endpoint that calls the corresponding
function in workflow_service.py. The service layer enforces all business
rules — the router only handles HTTP concerns (auth, RBAC, response shaping).

State machine:
  draft → in_review → approved     → published → archived
                    → changes_requested → in_review
                    → rejected → draft

Endpoints:
  GET    /workflow/blocks            Filtered block list (Kanban view)
  GET    /workflow/pending           Blocks awaiting the current reviewer
  GET    /workflow/summary           Block counts per state
  POST   /blocks/{id}/submit         Submit for review
  POST   /blocks/{id}/approve        Approve
  POST   /blocks/{id}/request-changes Request changes
  POST   /blocks/{id}/reject         Reject
  POST   /blocks/{id}/publish        Publish
  POST   /blocks/{id}/archive        Archive
  POST   /workflow/bulk-approve      Bulk approve
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.dependencies import get_current_user, get_db, require_permission
from app.core.exceptions import NotFoundError, WorkflowError
from app.schemas.common import PaginatedResponse
from app.schemas.workflow import (
    ApproveBlockRequest,
    BulkApproveRequest,
    BulkApproveResponse,
    PendingQueueResponse,
    RejectBlockRequest,
    RequestChangesRequest,
    SLAStatus,
    SubmitForReviewRequest,
    WorkflowBlockRead,
    WorkflowEventRead,
    WorkflowProjectBreakdown,
    WorkflowSummaryResponse,
    WorkflowTransitionResponse,
    WorkflowUserBreakdown,
)

_log = logging.getLogger(__name__)
router = APIRouter()


def _get_block_or_404(db: Session, block_id: int):
    """Fetch a block by ID or raise HTTP 404."""
    from promptops_app.database import Block
    block = db.query(Block).filter(Block.id == block_id).first()
    if not block:
        raise NotFoundError("Block", block_id)
    return block


def _to_workflow_block(block, sla_hours: int = 24) -> WorkflowBlockRead:
    """Convert a Block ORM object to a WorkflowBlockRead with SLA metadata."""
    from promptops_app.services.workflow_service import get_sla_status

    sla_data = get_sla_status(block, sla_hours=sla_hours)
    result = WorkflowBlockRead.model_validate(block)
    result.sla = SLAStatus(**sla_data) if sla_data else None
    gen = getattr(block, "generation", None)
    if gen is not None:
        result.course_id = gen.course_id
        result.project_id = gen.project_id
    return result


# ---------------------------------------------------------------------------
# Query endpoints
# ---------------------------------------------------------------------------

@router.get(
    "/blocks",
    response_model=PaginatedResponse[WorkflowBlockRead],
    summary="List blocks with workflow filters",
    description="Powers the Kanban view. Supports filtering by state, reviewer, project, course, and text search.",
)
def list_workflow_blocks(
    state: str | None = Query(default=None, description="Filter by workflow state."),
    reviewer: str | None = Query(default=None, description="Filter by assigned reviewer username."),
    project_id: int | None = Query(default=None),
    course_id: int | None = Query(default=None),
    search: str | None = Query(default=None, description="Search block label text."),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=100, ge=1, le=500),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
) -> PaginatedResponse[WorkflowBlockRead]:
    """
    Return blocks matching the given filters with SLA metadata attached.

    Replicates the scoped block query in the Streamlit Workflow page.
    Non-admin users are automatically scoped to their own project.
    """
    from promptops_app.repositories import generation_repository
    from app.core.config import settings

    blocks = generation_repository.list_workflow_blocks_scoped(
        db,
        user_name=current_user.username,
        project_id=project_id,
        is_admin=(current_user.role == "admin"),
        is_lead=(current_user.role == "reviewer"),
        course_id=course_id,
        state_filter=state,
        page_size=page_size * 10,  # over-fetch then apply search filter
    )

    if search:
        blocks = [b for b in blocks if search.lower() in b.block_label.lower()]
    if reviewer:
        blocks = [b for b in blocks if b.assigned_reviewer == reviewer]

    total = len(blocks)
    start = (page - 1) * page_size
    items = [_to_workflow_block(b, sla_hours=settings.approval_sla_hours) for b in blocks[start: start + page_size]]

    return PaginatedResponse.create(items=items, total=total, page=page, page_size=page_size)


@router.get(
    "/pending",
    response_model=PendingQueueResponse,
    summary="Get blocks pending the current reviewer's action",
    description="Returns blocks assigned to the authenticated user that are still in_review or changes_requested.",
)
def get_pending_blocks(
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
) -> PendingQueueResponse:
    """Return the current reviewer's queue. Drives the reviewer banner in the Workflow page."""
    from promptops_app.services.workflow_service import get_pending_for_reviewer
    from app.core.config import settings

    blocks = get_pending_for_reviewer(db, current_user.username)
    items = [_to_workflow_block(b, sla_hours=settings.approval_sla_hours) for b in blocks]
    return PendingQueueResponse(count=len(items), items=items)


@router.get(
    "/summary",
    response_model=WorkflowSummaryResponse,
    summary="Get block counts per workflow state",
    description="Powers the Kanban column headers showing counts per state.",
)
def get_workflow_summary(
    project_id: int | None = Query(default=None),
    course_id: int | None = Query(default=None),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
) -> WorkflowSummaryResponse:
    """Return counts of blocks in each workflow state for the Kanban overview."""
    from promptops_app.core.constants import WorkflowState
    from promptops_app.repositories import generation_repository

    summary = WorkflowSummaryResponse()
    for state in WorkflowState.ALL:
        blocks = generation_repository.list_workflow_blocks_scoped(
            db,
            user_name=current_user.username,
            project_id=project_id,
            is_admin=(current_user.role == "admin"),
            is_lead=(current_user.role == "reviewer"),
            course_id=course_id,
            state_filter=state,
            page_size=9999,
        )
        setattr(summary, state, len(blocks))

    return summary


@router.get(
    "/admin-breakdown",
    response_model=list[WorkflowProjectBreakdown],
    summary="Admin cross-project user breakdown",
    description="Powers the Admin — All Projects · User Breakdown section (Streamlit workflow.py).",
)
def get_admin_breakdown(
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("analytics.view_all")),
) -> list[WorkflowProjectBreakdown]:
    """Group blocks by project and generation author — matches Streamlit workflow page."""
    from promptops_app.repositories import generation_repository, project_repository

    result: list[WorkflowProjectBreakdown] = []
    for proj in project_repository.list_active_projects(db):
        proj_gens = generation_repository.list_generations_for_project(db, proj.id)
        if not proj_gens:
            continue
        proj_gen_ids = [g.id for g in proj_gens]
        user_gen_map: dict[str, list[int]] = {}
        for g in generation_repository.list_generations_by_ids(db, proj_gen_ids):
            user_gen_map.setdefault(g.created_by or "Unknown", []).append(g.id)

        users: list[WorkflowUserBreakdown] = []
        for uname in sorted(user_gen_map):
            ugen_ids = user_gen_map[uname]
            ublocks = generation_repository.list_blocks_for_gen_ids(db, ugen_ids)
            if not ublocks:
                continue
            state_counts: dict[str, int] = {}
            for b in ublocks:
                st = (b.workflow_state or "draft").lower()
                state_counts[st] = state_counts.get(st, 0) + 1
            users.append(
                WorkflowUserBreakdown(
                    username=uname,
                    block_count=len(ublocks),
                    state_counts=state_counts,
                )
            )
        if users:
            result.append(
                WorkflowProjectBreakdown(
                    project_id=proj.id,
                    project_name=proj.name,
                    users=users,
                )
            )
    return result


# ---------------------------------------------------------------------------
# State transition endpoints
# ---------------------------------------------------------------------------

@router.post(
    "/blocks/{block_id}/submit",
    response_model=WorkflowTransitionResponse,
    summary="Submit a block for review",
    responses={409: {"description": "Block is not in a submittable state."}},
)
def submit_for_review(
    block_id: int,
    request_body: SubmitForReviewRequest,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("workflow.submit")),
) -> WorkflowTransitionResponse:
    """
    Move a block from draft/rejected/changes_requested → in_review.

    Assigns the reviewer and starts the SLA clock.
    Calls workflow_service.submit_for_review() unchanged.
    """
    from promptops_app.services.workflow_service import submit_for_review as _submit

    block = _get_block_or_404(db, block_id)
    ok, reason = _submit(db, block, request_body.reviewer_username, actor=current_user.username)

    if not ok:
        raise WorkflowError(reason or "Submission failed.")

    _log.info("block_submitted  user=%s  block_id=%d  reviewer=%s",
              current_user.username, block_id, request_body.reviewer_username)
    return WorkflowTransitionResponse(block_id=block_id, workflow_state="in_review")


@router.post(
    "/blocks/{block_id}/approve",
    response_model=WorkflowTransitionResponse,
    summary="Approve a block",
    responses={409: {"description": "Block is not in_review."}},
)
def approve_block(
    block_id: int,
    request_body: ApproveBlockRequest,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("workflow.approve")),
) -> WorkflowTransitionResponse:
    """Move in_review → approved. Calls workflow_service.approve_block()."""
    from promptops_app.services.workflow_service import approve_block as _approve

    block = _get_block_or_404(db, block_id)
    ok, reason = _approve(db, block, actor=current_user.username, comment=request_body.comment)

    if not ok:
        raise WorkflowError(reason or "Approval failed.")

    _log.info("block_approved  user=%s  block_id=%d", current_user.username, block_id)
    return WorkflowTransitionResponse(block_id=block_id, workflow_state="approved")


@router.post(
    "/blocks/{block_id}/request-changes",
    response_model=WorkflowTransitionResponse,
    summary="Request changes on a block",
    responses={409: {"description": "Block is not in_review."}},
)
def request_changes(
    block_id: int,
    request_body: RequestChangesRequest,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("workflow.request_changes")),
) -> WorkflowTransitionResponse:
    """Move in_review → changes_requested. A reason is mandatory."""
    from promptops_app.services.workflow_service import request_changes as _request

    block = _get_block_or_404(db, block_id)
    ok, reason = _request(db, block, actor=current_user.username, reason=request_body.reason)

    if not ok:
        raise WorkflowError(reason or "Request changes failed.")

    _log.info("block_changes_requested  user=%s  block_id=%d", current_user.username, block_id)
    return WorkflowTransitionResponse(block_id=block_id, workflow_state="changes_requested")


@router.post(
    "/blocks/{block_id}/reject",
    response_model=WorkflowTransitionResponse,
    summary="Reject a block",
    responses={409: {"description": "Block is not in_review."}},
)
def reject_block(
    block_id: int,
    request_body: RejectBlockRequest,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("workflow.approve")),
) -> WorkflowTransitionResponse:
    """Move in_review → rejected (hard decline). A reason is mandatory."""
    from promptops_app.services.workflow_service import reject_block as _reject

    block = _get_block_or_404(db, block_id)
    ok, reason = _reject(db, block, actor=current_user.username, reason=request_body.reason)

    if not ok:
        raise WorkflowError(reason or "Rejection failed.")

    _log.info("block_rejected  user=%s  block_id=%d", current_user.username, block_id)
    return WorkflowTransitionResponse(block_id=block_id, workflow_state="rejected")


@router.post(
    "/blocks/{block_id}/publish",
    response_model=WorkflowTransitionResponse,
    summary="Publish an approved block",
    responses={409: {"description": "Block is not approved."}},
)
def publish_block(
    block_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("workflow.publish")),
) -> WorkflowTransitionResponse:
    """Move approved → published."""
    from promptops_app.services.workflow_service import publish_block as _publish

    block = _get_block_or_404(db, block_id)
    ok, reason = _publish(db, block, actor=current_user.username)

    if not ok:
        raise WorkflowError(reason or "Publish failed.")

    _log.info("block_published  user=%s  block_id=%d", current_user.username, block_id)
    return WorkflowTransitionResponse(block_id=block_id, workflow_state="published")


@router.post(
    "/blocks/{block_id}/archive",
    response_model=WorkflowTransitionResponse,
    summary="Archive a block",
    responses={409: {"description": "Block is not approved or published."}},
)
def archive_block(
    block_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("workflow.archive")),
) -> WorkflowTransitionResponse:
    """Move approved/published → archived. Admin only."""
    from promptops_app.services.workflow_service import archive_block as _archive

    block = _get_block_or_404(db, block_id)
    ok, reason = _archive(db, block, actor=current_user.username)

    if not ok:
        raise WorkflowError(reason or "Archive failed.")

    _log.info("block_archived  user=%s  block_id=%d", current_user.username, block_id)
    return WorkflowTransitionResponse(block_id=block_id, workflow_state="archived")


@router.post(
    "/bulk-approve",
    response_model=BulkApproveResponse,
    summary="Bulk approve multiple blocks",
    description="Approves all in_review blocks in the given list. Admin only.",
)
def bulk_approve(
    request_body: BulkApproveRequest,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("workflow.bulk_approve")),
) -> BulkApproveResponse:
    """
    Approve multiple blocks at once.

    Calls workflow_service.bulk_approve() unchanged.
    Skips blocks that are not in in_review state.
    """
    from promptops_app.services.workflow_service import bulk_approve as _bulk

    results = _bulk(db, request_body.block_ids, actor=current_user.username)

    _log.info("bulk_approve  user=%s  approved=%d  skipped=%d",
              current_user.username, len(results["approved"]), len(results["skipped"]))

    return BulkApproveResponse(
        approved=results["approved"],
        skipped=results["skipped"],
        errors=results["errors"],
        total_approved=len(results["approved"]),
    )


@router.get(
    "/blocks/{block_id}/events",
    response_model=list[WorkflowEventRead],
    summary="List workflow transition history for a block",
)
def list_block_workflow_events(
    block_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
) -> list[WorkflowEventRead]:
    """Return recent workflow events for the Approval Center history expander."""
    from promptops_app.repositories import generation_repository

    _get_block_or_404(db, block_id)
    events = generation_repository.list_workflow_events_for_block(db, block_id, limit=20)
    return [WorkflowEventRead.model_validate(e) for e in events]


@router.post(
    "/blocks/{block_id}/reset-draft",
    response_model=WorkflowTransitionResponse,
    summary="Reset a block back to draft",
)
def reset_block_to_draft(
    block_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("workflow.reset_draft")),
) -> WorkflowTransitionResponse:
    """Reset approved/rejected/changes_requested blocks to draft (Streamlit parity)."""
    from promptops_app.database import apply_transition_local, log_event

    block = _get_block_or_404(db, block_id)
    apply_transition_local(db, block, "reset_to_draft", current_user.username)
    log_event(
        db,
        "workflow_transition",
        current_user.username,
        f"Block #{block_id} reset to Draft",
        {"block_id": block_id},
    )
    _log.info("block_reset_draft  user=%s  block_id=%d", current_user.username, block_id)
    return WorkflowTransitionResponse(block_id=block_id, workflow_state="draft")
