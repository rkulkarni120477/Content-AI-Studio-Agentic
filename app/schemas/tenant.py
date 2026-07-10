"""Tenant (organization) + membership schemas for the platform-admin API."""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


class TenantRead(BaseModel):
    id: int
    slug: Optional[str] = None
    name: str
    status: str = "active"
    max_users: int = 50
    microsoft_auth_enabled: bool = True
    allowed_email_domains: Optional[str] = None
    active_users: int = 0
    created_at: Optional[datetime] = None
    created_by: Optional[str] = None

    model_config = ConfigDict(from_attributes=True)


class TenantCreateRequest(BaseModel):
    slug: str = Field(..., min_length=2, max_length=64, examples=["aim003"])
    name: str = Field(..., min_length=1, max_length=200, examples=["AIM 16 Block Development"])
    max_users: int = Field(default=50, ge=1)
    admin_username: str = Field(..., min_length=1, max_length=150)
    admin_password: str = Field(..., min_length=6, max_length=256)
    admin_display_name: Optional[str] = Field(default=None, max_length=200)


class TenantUpdateRequest(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=200)
    status: Optional[str] = Field(default=None, description="active | suspended")
    max_users: Optional[int] = Field(default=None, ge=1)
    microsoft_auth_enabled: Optional[bool] = None
    allowed_email_domains: Optional[str] = Field(default=None, max_length=500)
    azure_tenant_id: Optional[str] = Field(default=None, max_length=64)
    azure_client_id: Optional[str] = Field(default=None, max_length=64)
    azure_client_secret: Optional[str] = Field(default=None, max_length=512)
    azure_new_user_role: Optional[str] = Field(default=None, max_length=32)


class TenantUsageResponse(BaseModel):
    active_users: int
    max_users: int


class TenantMemberRead(BaseModel):
    user_id: int
    username: str
    display_name: Optional[str] = None
    role: str
    role_display: str = ""
    custom_role_id: Optional[int] = None
    active: bool = True
    email: Optional[str] = None
    auth_provider: str = "local"   # "microsoft" | "local"


class TenantMemberCreateRequest(BaseModel):
    username: str = Field(..., min_length=1, max_length=150)
    password: str = Field(..., min_length=6, max_length=256)
    display_name: Optional[str] = Field(default=None, max_length=200)
    role: Optional[str] = Field(default=None, description="admin | reviewer | author — omit when custom_role_id is set")
    custom_role_id: Optional[int] = Field(default=None, description="Assign a tenant custom role instead of a system role")


class TenantMemberUpdateRequest(BaseModel):
    display_name: Optional[str] = Field(default=None, max_length=200)
    role: Optional[str] = Field(default=None, description="admin | reviewer | author — omit when custom_role_id is set")
    custom_role_id: Optional[int] = Field(default=None, description="Assign a tenant custom role instead of a system role")
    password: Optional[str] = Field(default=None, min_length=6, max_length=256)
    active: Optional[bool] = None


class TenantCreateResponse(TenantRead):
    initial_admin_username: Optional[str] = None


class TenantRoleRead(BaseModel):
    id: Optional[int] = None          # None for system roles (not DB rows)
    key: str
    name: str
    description: Optional[str] = None
    type: str                          # "system" | "custom"
    permissions: list[str] = Field(default_factory=list)


class TenantRoleCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    description: Optional[str] = Field(default=None, max_length=255)
    permissions: list[str] = Field(default_factory=list)


class TenantRoleUpdateRequest(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=100)
    description: Optional[str] = Field(default=None, max_length=255)
    permissions: Optional[list[str]] = None
