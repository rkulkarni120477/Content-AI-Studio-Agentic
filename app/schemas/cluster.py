"""Cluster schemas."""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


class ClusterCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=255, examples=["Science Fundamentals"])
    description: Optional[str] = Field(default=None, max_length=2000)
    copy_prompt_ids: list[int] = Field(
        default_factory=list,
        description=(
            "IDs of existing active ClusterPrompts to clone into this new cluster. "
            "Prompts are copied (new rows), not linked by reference."
        ),
    )


class ClusterUpdateRequest(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=255)
    description: Optional[str] = Field(default=None, max_length=2000)


class ClusterRead(BaseModel):
    id: int
    project_id: int
    name: str
    description: Optional[str] = None
    created_by: Optional[str] = None
    created_at: Optional[datetime] = None
    is_active: bool = True

    model_config = ConfigDict(from_attributes=True)


class ClusterListItem(BaseModel):
    id: int
    project_id: int
    name: str
    description: Optional[str] = None
    created_at: Optional[datetime] = None
    course_count: int = 0

    model_config = ConfigDict(from_attributes=True)
