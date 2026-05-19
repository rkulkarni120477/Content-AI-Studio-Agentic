"""Reference document library schemas."""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


class DocumentRead(BaseModel):
    id: int
    name: str
    file_type: Optional[str] = None
    source_type: Optional[str] = None
    char_count: Optional[int] = None
    course_id: Optional[int] = None
    project_id: Optional[int] = None
    created_at: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True)


class DocumentListItem(BaseModel):
    id: int
    name: str
    file_type: Optional[str] = None
    source_type: Optional[str] = None
    created_at: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True)


class DocumentContentResponse(BaseModel):
    """Full document text — used when injecting context into generation prompts."""

    id: int
    name: str
    content: str = Field(description="Parsed text extracted from the uploaded file.")
