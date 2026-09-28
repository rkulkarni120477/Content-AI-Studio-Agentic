"""CE review run schemas (Step 2)."""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


class ContentReviewRead(BaseModel):
    """One review run's state, as shown in the Editor's AI Review panel."""

    id: int
    generation_id: int
    project_id: Optional[int] = None
    run_status: str                             # queued | running | completed | failed | cancelled
    verdict: Optional[str] = None               # null until Step 6
    review_basis: str                           # checklist | style | none
    checklist_version: Optional[str] = None
    counts: Optional[dict] = None
    rerun_of_id: Optional[int] = None
    model_used: Optional[str] = None
    error_message: Optional[str] = None
    created_by: Optional[str] = None
    created_at: Optional[datetime] = None
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True)


class ChecklistResultRead(BaseModel):
    """One non-pass checklist rule outcome for a review."""

    item_key: str
    rule_text: Optional[str] = None
    status: str                                 # fail | warning | na
    explanation: Optional[str] = None
    recommendation: Optional[str] = None

    model_config = ConfigDict(from_attributes=True)


class FindingRead(BaseModel):
    """One detected issue, as shown in the review panel."""

    id: int
    block_id: Optional[int] = None
    category: str
    severity: str                               # blocker | major | minor
    title: Optional[str] = None
    detail: Optional[str] = None
    anchor_quote: Optional[str] = None
    suggested_replacement: Optional[str] = None
    tier: str                                   # inline | large | guidance
    auto_applicable: bool = False
    status: str                                 # open | applied | dismissed | stale | apply_failed

    model_config = ConfigDict(from_attributes=True)


class ApplyManyRequest(BaseModel):
    """Apply selected findings, or all eligible when finding_ids is omitted."""
    finding_ids: Optional[list[int]] = Field(default=None, description="Findings to apply; null = all eligible.")


class ApplySummary(BaseModel):
    applied: int = 0
    skipped: int = 0
    stale: int = 0


class DismissRequest(BaseModel):
    reason: Optional[str] = Field(default=None, max_length=500)


class ReviewStartRequest(BaseModel):
    generation_id: int = Field(..., description="The generation (lesson) to review.")
    model_choice: Optional[str] = Field(default=None, description="Override the AI model; optional.")


class ReviewStartResponse(BaseModel):
    """Result of starting (or reusing) a review run."""

    review_id: int
    run_status: str
    reused: bool = False                        # True = identical completed run returned, no new work
    job_id: Optional[str] = None                # present only when a new run was launched
    status_url: Optional[str] = None            # poll this job for progress
