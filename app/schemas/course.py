"""Course schemas."""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


class CourseCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=300, examples=["Module 1 — Fundamentals"])
    cluster_id: Optional[int] = Field(default=None, description="Optional cluster grouping.")


class CourseUpdateRequest(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=300)
    description: Optional[str] = Field(default=None, max_length=2000)
    cluster_id: Optional[int] = None


class CourseUserAssignRequest(BaseModel):
    username: str = Field(..., min_length=1, max_length=150)


class CourseUserListItem(BaseModel):
    username: str


class CourseRead(BaseModel):
    id: int
    name: str
    project_id: Optional[int] = None
    cluster_id: Optional[int] = None
    active_cdd_id: Optional[int] = Field(
        default=None,
        description="The CDD currently pinned for generation on this course.",
    )
    active_blueprint_id: Optional[int] = Field(
        default=None,
        description="The Blueprint currently pinned for generation on this course.",
    )
    created_at: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True)


class CourseListItem(BaseModel):
    id: int
    name: str
    description: Optional[str] = None
    cluster_id: Optional[int] = None
    active_cdd_id: Optional[int] = None
    active_blueprint_id: Optional[int] = None
    created_at: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True)
