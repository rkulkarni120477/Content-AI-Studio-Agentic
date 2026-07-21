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
    feedback_text: str
    source_location: Optional[str] = None
    theme: Optional[str] = None
    sentiment: Optional[str] = None            # suggestion | concern | praise | neutral
    priority: Optional[str] = None             # high | medium | low
    document_name: Optional[str] = None        # source document filename
    created_at: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True)


class FeedbackDocumentRead(BaseModel):
    """Metadata for one uploaded, analysed feedback document."""

    id: int
    course_id: Optional[int] = None
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
