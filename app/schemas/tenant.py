"""Pydantic schemas for tenant management endpoints."""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


class TenantRead(BaseModel):
    """Tenant summary returned by list and get endpoints."""

    id: str
    slug: str
    name: str
    max_users: int
    status: str
    active_users: int
    created_at: Optional[datetime] = None
    created_by: Optional[str] = None

    class Config:
        from_attributes = True


class TenantCreateRequest(BaseModel):
    """Body for POST /api/v1/platform/tenants."""

    slug: str = Field(..., min_length=1, max_length=64,
                      description="Org code: lowercase letters, numbers, hyphens only.")
    name: str = Field(..., min_length=1, max_length=200)
    max_users: int = Field(default=50, ge=1)
    admin_username: str = Field(..., min_length=1, max_length=150)
    admin_password: str = Field(..., min_length=6, max_length=256)
    admin_display_name: str = Field(default="")


class TenantUpdateRequest(BaseModel):
    """Body for PUT /api/v1/platform/tenants/{tenant_id}."""

    name: Optional[str] = Field(default=None, max_length=200)
    max_users: Optional[int] = Field(default=None, ge=1)
    status: Optional[str] = Field(default=None, description="'active' or 'suspended'")


class TenantUserCreateRequest(BaseModel):
    """Body for POST /api/v1/platform/tenants/{tenant_id}/users."""

    username: str = Field(..., min_length=1, max_length=150)
    password: str = Field(..., min_length=6, max_length=256)
    role: str = Field(default="author", description="admin | author | reviewer")
    display_name: str = Field(default="")


class TenantUserUpdateRequest(BaseModel):
    """Body for PUT /api/v1/platform/tenants/{tenant_id}/users/{username}."""

    role: Optional[str] = None
    password: Optional[str] = Field(default=None, min_length=6)
    is_active: Optional[bool] = None
    display_name: Optional[str] = None


class TenantUsageResponse(BaseModel):
    """Usage stats for a tenant."""

    active_users: int
    max_users: int
    usage_percent: float
