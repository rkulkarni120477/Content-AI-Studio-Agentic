"""Analytics and observability schemas."""

from __future__ import annotations

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


class PromptPerformanceItem(BaseModel):
    prompt: str
    avg_rating: float
    samples: int


class QualityTrendsResponse(BaseModel):
    """Raw rating values in chronological order — used for the area chart."""

    ratings: list[int]


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


class AuditEventRead(BaseModel):
    id: int
    actor: Optional[str] = None
    action: Optional[str] = None
    entity_type: Optional[str] = None
    entity_id: Optional[int] = None
    project_id: Optional[int] = None
    course_id: Optional[int] = None
    metadata: Optional[dict] = None
    created_at: Optional[datetime] = None


class GenerationHistoryRow(BaseModel):
    id: int
    topic: str
    prompt_name: Optional[str] = None
    prompt_version: Optional[str] = None
    cdd_label: str = "—"
    blueprint_label: str = "—"
    created_at: Optional[datetime] = None
