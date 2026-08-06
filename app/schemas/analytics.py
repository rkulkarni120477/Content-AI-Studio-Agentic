"""Analytics and observability schemas."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


class AnalyticsSummaryResponse(BaseModel):
    """Dashboard metric counters — returned by GET /api/v1/analytics/summary."""

    generations: int = 0
    blocks: int = 0
    prompt_assets: int = 0
    documents: int = 0
    cdds: int = 0
    blueprints: int = 0


class ProjectAnalyticsRow(BaseModel):
    project_id: int
    project_name: str
    client: str = "—"
    generations: int = 0
    blocks: int = 0
    cdds: int = 0
    blueprints: int = 0


class UsageByModelItem(BaseModel):
    model: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost_usd: float = 0.0


class UsageSummaryResponse(BaseModel):
    total_prompt_tokens: int = 0
    total_completion_tokens: int = 0
    estimated_cost_usd: float = 0.0
    by_model: list[UsageByModelItem] = Field(default_factory=list)


def _parse_audit_metadata(value):
    if value is None or value == "":
        return None
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return None
    return value


class AuditEventRead(BaseModel):
    id: int
    actor: Optional[str] = None
    action: Optional[str] = None
    label: Optional[str] = None
    icon: Optional[str] = None
    entity_type: Optional[str] = None
    entity_id: Optional[str] = None
    project_id: Optional[int] = None
    course_id: Optional[int] = None
    ip_address: Optional[str] = None
    metadata: Optional[dict] = None
    created_at: Optional[datetime] = None


class AuditTrailFiltersResponse(BaseModel):
    actors: list[str] = Field(default_factory=list)
    actions: list[str] = Field(default_factory=list)
    entity_types: list[str] = Field(default_factory=list)
    projects: list[dict] = Field(default_factory=list)
    page_sizes: list[int] = Field(default_factory=lambda: [25, 50, 100])


class AuditTrailQuery(BaseModel):
    """Query parameters for GET /api/v1/analytics/audit-trail and export."""

    entity_type: Optional[str] = Field(
        default=None,
        description="Filter by entity type (e.g. cdd, blueprint, export).",
    )
    actor: Optional[str] = Field(
        default=None,
        description="Filter by username. Non-admins are scoped to their own user automatically.",
    )
    action: Optional[str] = Field(
        default=None,
        description="Filter by action key (e.g. export.course, user.login).",
    )
    project_id: Optional[int] = Field(
        default=None,
        description="Filter by project ID.",
    )
    date_from: Optional[str] = Field(
        default=None,
        description="Inclusive start date (YYYY-MM-DD).",
    )
    date_to: Optional[str] = Field(
        default=None,
        description="Inclusive end date (YYYY-MM-DD).",
    )
    page: int = Field(default=1, ge=1, description="Page number (1-indexed).")
    page_size: int = Field(
        default=25, ge=1, le=200, description="Number of rows per page.",
    )


class FeedbackSummaryResponse(BaseModel):
    total: int = 0
    learning: int = 0
    one_time: int = 0


class FeedbackItemRead(BaseModel):
    id: int
    source: str
    scope: str
    block_type: Optional[str] = None
    instruction: str = ""
    author: Optional[str] = None
    created_at: Optional[datetime] = None


class ReviewAnalyticsResponse(BaseModel):
    total: int = 0
    approved: int = 0
    avg_score: float = 0.0
    items: list["ReviewItemRead"] = Field(default_factory=list)


class ReviewItemRead(BaseModel):
    block_id: Optional[int] = None
    reviewer: Optional[str] = None
    reviewer_role: Optional[str] = None
    score: Optional[int] = None
    approved: bool = False
    comments: str = ""
    created_at: Optional[datetime] = None

    model_config = {"from_attributes": True}


class PromptVersionHistoryRow(BaseModel):
    prompt_id: int
    version: str
    notes: str = ""
    created_at: Optional[datetime] = None

    model_config = {"from_attributes": True}


class DocumentUploadHistoryRow(BaseModel):
    filename: str
    tag: Optional[str] = "general"
    file_type: Optional[str] = None
    user: Optional[str] = None
    created_at: Optional[datetime] = None


class CddBlueprintEventRow(BaseModel):
    event: str
    actor: Optional[str] = None
    details: Optional[str] = None
    created_at: Optional[datetime] = None


class LlmCostSummary(BaseModel):
    total_calls: int = 0
    total_tokens: int = 0
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    total_cost: float = 0.0
    avg_duration_ms: float = 0.0
    failed_calls: int = 0


class LlmCostDashboardResponse(BaseModel):
    summary: LlmCostSummary
    by_model: list[dict] = Field(default_factory=list)
    monthly: list[dict] = Field(default_factory=list)
    by_project: list[dict] = Field(default_factory=list)
    by_course: list[dict] = Field(default_factory=list)
    by_user: list[dict] = Field(default_factory=list)


class GenerationHistoryRow(BaseModel):
    id: int
    topic: str
    prompt_name: Optional[str] = None
    prompt_version: Optional[str] = None
    cdd_label: str = "—"
    blueprint_label: str = "—"
    created_at: Optional[datetime] = None


ReviewAnalyticsResponse.model_rebuild()
