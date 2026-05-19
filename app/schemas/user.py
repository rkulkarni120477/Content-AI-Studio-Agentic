"""User management schemas."""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


class UserCreateRequest(BaseModel):
    """Body for POST /api/v1/users — admin only."""

    username: str = Field(..., min_length=1, max_length=150)
    password: str = Field(
        ...,
        min_length=6,
        max_length=256,
        description="Plain-text password. Hashed with SHA-256 before storage.",
    )
    role: str = Field(
        ...,
        description="DB role: admin | reviewer | author",
        examples=["author"],
    )


class UserRoleUpdateRequest(BaseModel):
    """Body for PUT /api/v1/users/{id}/role."""

    role: str = Field(..., description="New role: admin | reviewer | author")


class UserToggleRequest(BaseModel):
    """Body for PUT /api/v1/users/{id}/toggle — activate or deactivate the account."""

    is_active: bool = Field(description="True to activate, False to deactivate.")


class UserRead(BaseModel):
    """Full user profile — password field is never included."""

    id: int
    username: str
    role: str
    role_display: str
    is_active: bool
    created_at: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True)


class UserListItem(BaseModel):
    """Lightweight user summary for list responses."""

    id: int
    username: str
    role: str
    role_display: str
    is_active: bool

    model_config = ConfigDict(from_attributes=True)
