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
