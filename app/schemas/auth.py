"""
Authentication schemas — request and response models for the auth endpoints.

These schemas define exactly what the login endpoint accepts and what it returns.
The React frontend uses ``TokenResponse`` to store the JWT and user profile
immediately after login.
"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Request schemas
# ---------------------------------------------------------------------------

class LoginRequest(BaseModel):
    """
    Credentials submitted to ``POST /api/v1/auth/login``.

    Both fields are required.  The username is case-sensitive (matching the
    existing User table where usernames are stored as-entered).
    """

    username: str = Field(
        ...,
        min_length=1,
        max_length=150,
        description="The user's login name.",
        examples=["shubham"],
    )
    password: str = Field(
        ...,
        min_length=1,
        max_length=256,
        description="The user's plain-text password (transmitted over HTTPS only).",
    )
    organization_code: Optional[str] = Field(
        default=None, max_length=64,
        description="Tenant org code (required for tenant local login; ignored for platform admin).",
    )
    platform_admin: bool = Field(
        default=False,
        description="True to authenticate as a platform administrator (org code ignored).",
    )


class AuthConfigResponse(BaseModel):
    """Public sign-in options for the login screen."""

    microsoft_enabled: bool = False
    local_login_enabled: bool = True
    platform_slug: str = "platform"


class TenantLoginInfoResponse(BaseModel):
    """Public info for a given organization code."""

    valid: bool
    slug: Optional[str] = None
    name: Optional[str] = None
    status: Optional[str] = None
    microsoft_enabled: bool = False
    error: Optional[str] = None


# ---------------------------------------------------------------------------
# Response schemas
# ---------------------------------------------------------------------------

class UserProfileResponse(BaseModel):
    """
    Basic user profile embedded in the login response and returned by /me.

    The ``permissions`` list lets the React frontend decide which tabs,
    buttons, and actions to display without making additional API calls.
    """

    id: int = Field(description="User's database ID.")
    username: str = Field(description="User's login name.")
    role: str = Field(description="DB role value: admin | reviewer | author")
    role_display: str = Field(description="Human-readable role label: Admin | Lead | ID")
    is_active: bool = Field(description="Whether the account is active.")
    permissions: list[str] = Field(
        description="All permission keys this user holds. Used by the frontend for UI gating."
    )
    project_id: Optional[int] = Field(default=None, description="Active tenant (project) id; None for platform admin.")
    is_platform_admin: bool = Field(default=False, description="True for platform super-admins.")
    # DIS / Source Library access is resolved by CAS from username/role/config.
    # Super admins can switch clients in the Source Library UI; client users are
    # automatically locked to their mapped client.
    client_id: str | None = Field(default=None, description="Resolved default DIS client/workspace id.")
    tenant_id: str | None = Field(default=None, description="Resolved DIS tenant id.")
    dis_role: str | None = Field(default=None, description="Resolved DIS role: super_admin | client_admin | user.")
    is_dis_super_admin: bool = Field(default=False, description="Whether this user can switch DIS clients.")
    available_clients: list[str] = Field(default_factory=list, description="Clients visible to this user in Source Library.")
    # Capability flags the frontend uses to gate opt-in features without a
    # second round-trip. Digest pipeline = block-wide CDD/Blueprint generation;
    # resolved per the user's DIS client against the master switch + allowlist.
    digest_pipeline_enabled: bool = Field(
        default=False,
        description="Whether block-wide (digest-pipeline) CDD/Blueprint generation is enabled for this user's DIS client.",
    )
    ce_review_enabled: bool = Field(
        default=False,
        description="Whether the CE Agent Review (checklist-based content review) feature is enabled.",
    )


class TokenResponse(BaseModel):
    """
    Returned by ``POST /api/v1/auth/login`` and ``POST /api/v1/auth/refresh``.

    The client must store ``access_token`` and send it as
    ``Authorization: Bearer <access_token>`` on every subsequent request.
    """

    access_token: str = Field(description="Signed JWT access token.")
    token_type: str = Field(default="bearer", description="Always 'bearer'.")
    expires_in: int = Field(
        description="Token validity in seconds. Refresh before this elapses."
    )
    user: UserProfileResponse = Field(
        description="Authenticated user's profile and permissions."
    )


class WorkspaceUpdateResponse(BaseModel):
    """
    Returned by ``PUT /api/v1/workspace`` and ``PUT /api/v1/workspace/config``.

    The client must replace its stored token with this new one, which embeds
    the updated workspace/config state in its payload.
    """

    access_token: str = Field(description="New JWT with updated workspace state embedded.")
    token_type: str = Field(default="bearer")
