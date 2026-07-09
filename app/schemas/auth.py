"""
Authentication schemas — request and response models for the auth endpoints.
"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field


class LoginRequest(BaseModel):
    """Credentials submitted to POST /api/v1/auth/login.

    Two password paths:
      - Platform admin: platform_admin=True, username+password (org code ignored).
      - Tenant local user: organization_code + username + password.
    Microsoft sign-in is a separate GET redirect flow, not this endpoint.
    """

    username: str = Field(..., min_length=1, max_length=150)
    password: str = Field(..., min_length=1, max_length=256)
    organization_code: Optional[str] = Field(default=None, max_length=64)
    platform_admin: bool = Field(default=False)


class AuthConfigResponse(BaseModel):
    """Public sign-in options for the login screen."""

    microsoft_enabled: bool = False
    local_login_enabled: bool = True
    platform_slug: str = "platform"


class TenantLoginInfoResponse(BaseModel):
    """Public info for a given organization code (validates it + MS availability)."""

    valid: bool
    slug: Optional[str] = None
    name: Optional[str] = None
    status: Optional[str] = None
    microsoft_enabled: bool = False
    error: Optional[str] = None


class UserProfileResponse(BaseModel):
    """
    User profile embedded in the login response and returned by /me.

    project_id is None for platform admins (they can see all projects).
    """

    id: int
    username: str
    role: str
    role_display: str
    is_active: bool
    permissions: list[str]
    project_id: Optional[int] = None
    is_platform_admin: bool = False


class TokenResponse(BaseModel):
    """Returned by POST /api/v1/auth/login and POST /api/v1/auth/refresh."""

    access_token: str
    token_type: str = "bearer"
    expires_in: int
    user: UserProfileResponse


class WorkspaceUpdateResponse(BaseModel):
    """Returned by PUT /api/v1/workspace and PUT /api/v1/workspace/config."""

    access_token: str
    token_type: str = "bearer"
