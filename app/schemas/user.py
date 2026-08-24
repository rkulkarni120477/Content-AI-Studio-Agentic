"""User management schemas."""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


class UserRoleUpdateRequest(BaseModel):
    """Body for PUT /api/v1/users/{id}/role."""

    role: str = Field(..., description="New role: admin | reviewer | author")


class UserRead(BaseModel):
    """Full user profile — password field is never included."""

    id: int
    username: str
    role: str
    role_display: str = ""
    is_active: bool
    created_at: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True)


class UserListItem(BaseModel):
    """Lightweight user summary for list responses."""

    id: int
    username: str
    role: str
    role_display: str = ""
    is_active: bool

    model_config = ConfigDict(from_attributes=True)
