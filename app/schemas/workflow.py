"""Workflow approval pipeline schemas."""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


class SLAStatus(BaseModel):
    overdue: bool
    hours_remaining: Optional[float] = None
    label: str = ""


class WorkflowBlockRead(BaseModel):
    """Block with workflow context — used in the Kanban view and approval center."""

    id: int
    block_label: str
    workflow_state: str
    assigned_reviewer: Optional[str] = None
    submitted_by: Optional[str] = None
    review_requested_at: Optional[datetime] = None
    review_comments: Optional[str] = None
    approved_by: Optional[str] = None
    approved_at: Optional[datetime] = None
    reviewed_by: Optional[str] = None
    archived_by: Optional[str] = None
    generation_id: Optional[int] = None
    project_id: Optional[int] = None
    course_id: Optional[int] = None
    sla: Optional[SLAStatus] = None

    model_config = ConfigDict(from_attributes=True)


class WorkflowSummaryResponse(BaseModel):
    """Block counts by state — used to populate the Kanban column headers."""

    draft: int = 0
    in_review: int = 0
    changes_requested: int = 0
    approved: int = 0
    published: int = 0
    archived: int = 0
    rejected: int = 0


class PendingQueueResponse(BaseModel):
    """Blocks assigned to the current reviewer still awaiting action."""

    count: int
    items: list[WorkflowBlockRead]


# ---------------------------------------------------------------------------
# Transition request bodies
# ---------------------------------------------------------------------------

class SubmitForReviewRequest(BaseModel):
    reviewer_username: str = Field(
        ...,
        min_length=1,
        description="Username of the reviewer to assign the block to.",
    )


class ApproveBlockRequest(BaseModel):
    comment: str = Field(default="", max_length=1000)


class RequestChangesRequest(BaseModel):
    reason: str = Field(
        ...,
        min_length=1,
        max_length=1000,
        description="Required explanation of what needs to change.",
    )


class RejectBlockRequest(BaseModel):
    reason: str = Field(
        ...,
        min_length=1,
        max_length=1000,
        description="Required rejection reason.",
    )


class BulkApproveRequest(BaseModel):
    block_ids: list[int] = Field(..., min_length=1)


class BulkSubmitRequest(BaseModel):
    """Body for bulk draft → in_review. A reviewer is mandatory, same as the
    single-block submit endpoint — one reviewer is assigned to every block."""

    block_ids: list[int] = Field(..., min_length=1, max_length=500)
    reviewer_username: str = Field(..., min_length=1)


class BulkPublishRequest(BaseModel):
    block_ids: list[int] = Field(..., min_length=1, max_length=500)


# ---------------------------------------------------------------------------
# Transition response
# ---------------------------------------------------------------------------

class WorkflowTransitionResponse(BaseModel):
    """Returned by every single-block workflow transition endpoint."""

    block_id: int
    workflow_state: str
    message: str = ""


class BulkApproveResponse(BaseModel):
    approved: list[int]
    skipped: list[int]
    errors: list[int]
    total_approved: int


class BulkTransitionItemResult(BaseModel):
    """One block's outcome within a bulk transition — a partial failure has
    to be legible, not rounded to a bare count."""

    block_id: int
    ok: bool
    reason: str | None = None


class BulkTransitionResponse(BaseModel):
    """Shared response shape for bulk-submit and bulk-publish."""

    succeeded: int
    failed: int
    results: list[BulkTransitionItemResult]


class WorkflowUserBreakdown(BaseModel):
    """Per-user block counts within a project (admin breakdown)."""

    username: str
    block_count: int
    state_counts: dict[str, int] = Field(default_factory=dict)


class WorkflowProjectBreakdown(BaseModel):
    """Project row in the admin user-breakdown accordion."""

    project_id: int
    project_name: str
    users: list[WorkflowUserBreakdown] = Field(default_factory=list)


class WorkflowEventRead(BaseModel):
    """Single workflow transition log entry."""

    created_at: datetime
    actor: str
    from_state: str
    to_state: str
    action: str
    comment: str = ""

    model_config = ConfigDict(from_attributes=True)
