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
