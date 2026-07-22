"""Reviewer-feedback schemas."""

from __future__ import annotations

from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field


class FeedbackItemRead(BaseModel):
    """One extracted feedback point, as shown in a Feedback table row."""

    id: int
    document_id: int
    course_id: Optional[int] = None
    blueprint_id: Optional[int] = None
    module_label: Optional[str] = None       # "Entire course" or "Module N — Title"
    feedback_text: str
    source_location: Optional[str] = None
    theme: Optional[str] = None
    sentiment: Optional[str] = None            # suggestion | concern | praise | neutral
    priority: Optional[str] = None             # high | medium | low
    document_name: Optional[str] = None        # source document filename
    created_at: Optional[datetime] = None
    # ── AI recommendation ──────────────────────────────────────────────────────
    recommendation: Optional[str] = None
    recommendation_refs: List[str] = Field(default_factory=list)  # referenced block labels
    recommendation_model: Optional[str] = None
    recommendation_status: str = "none"        # none | ready | error
    recommended_at: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True)


class FeedbackDocumentRead(BaseModel):
    """Metadata for one uploaded, analysed feedback document."""

    id: int
    course_id: Optional[int] = None
    blueprint_id: Optional[int] = None
    filename: str
    file_type: Optional[str] = None
    item_count: int = 0
    model_used: Optional[str] = None
    created_by: Optional[str] = None
    created_at: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True)


class FeedbackAnalyzeResponse(BaseModel):
    """Result of uploading + AI-analysing a feedback document."""

    document: FeedbackDocumentRead
    items: List[FeedbackItemRead]


class FeedbackListResponse(BaseModel):
    """All active feedback items for a course (or the whole tenant)."""

    items: List[FeedbackItemRead]
    total: int


class FeedbackBulkDeleteRequest(BaseModel):
    ids: List[int] = Field(..., min_length=1, description="Feedback item ids to delete.")


class FeedbackBulkDeleteResponse(BaseModel):
    deleted: int


class FeedbackRecommendRequest(BaseModel):
    item_ids: List[int] = Field(
        ..., min_length=1, max_length=25,
        description="Feedback item ids to generate AI recommendations for (max 25).",
    )
    guidance: Optional[str] = Field(
        default=None, max_length=2000,
        description="Optional reviewer steering instruction applied to this run.",
    )
    model_choice: Optional[str] = Field(
        default=None,
        description=(
            "Optional model display name to use for this run. Unknown values "
            "fall back to the course's configured model / catalog default."
        ),
    )


class FeedbackRecommendResponse(BaseModel):
    """Result of an AI-recommendation run — updated items plus a tally."""

    items: List[FeedbackItemRead]
    recommended: int   # items that produced a recommendation
    failed: int        # items whose recommendation errored


class FeedbackItemUpdateRequest(BaseModel):
    """Remap a feedback item to a module (or entire course when null)."""

    blueprint_id: Optional[int] = Field(
        default=None,
        description="Module blueprint id, or null for entire course.",
    )


class FeedbackApplyRequest(BaseModel):
    """Apply selected feedback items to regenerate a module's content blocks."""

    item_ids: List[int] = Field(..., min_length=1, description="Feedback item ids to apply.")
    blueprint_id: Optional[int] = Field(
        default=None,
        description=(
            "Target module blueprint. Required when selected items are course-wide "
            "or map to more than one module."
        ),
    )


class FeedbackApplyBlockResult(BaseModel):
    block_id: int
    block_label: Optional[str] = None


class FeedbackApplyResponse(BaseModel):
    """Summary of an apply-feedback regenerate run."""

    instruction: str
    regenerated: List[FeedbackApplyBlockResult]
    skipped: int = 0
    blueprint_id: int
    module_label: str
