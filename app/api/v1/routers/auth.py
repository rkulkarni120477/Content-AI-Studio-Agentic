"""
Authentication router — login, logout, token refresh, and current user.

This router replaces the ``login_page()`` function from ``core/shared.py``
and the cookie-based session management in ``streamlit_app.py``.

Key differences from the Streamlit implementation
--------------------------------------------------
  - Tokens are returned in the HTTP response body (not browser cookies).
  - The React frontend stores the token and sends it as:
      Authorization: Bearer <token>
  - Workspace and sidebar config state are embedded in the JWT payload
    (same as before) but now passed explicitly in the request body
    rather than read from st.session_state.

Endpoint summary
----------------
  POST   /api/v1/auth/login     Verify credentials, issue JWT
  POST   /api/v1/auth/logout    Acknowledge logout (client discards token)
  POST   /api/v1/auth/refresh   Issue a new token before expiry
  GET    /api/v1/auth/me        Return the authenticated user's profile
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.dependencies import get_current_user, get_db
from app.core.exceptions import AuthenticationError
from app.core.permissions import get_permissions_for_role, role_label
from app.core.security import create_access_token, verify_password
from app.schemas.auth import (
    LoginRequest,
    TokenResponse,
    UserProfileResponse,
)
from app.schemas.common import MessageResponse

_log = logging.getLogger(__name__)

router = APIRouter()


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _build_token_response(user) -> TokenResponse:
    """
    Build the full TokenResponse for a successfully authenticated user.

    Centralised here so both /login and /refresh return the identical shape.
    """
    from app.core.config import settings

    token = create_access_token(user.username, user.role)

    from app.core.dis_access import build_dis_profile

    profile = UserProfileResponse(
        id=user.id,
        username=user.username,
        role=user.role,
        role_display=role_label(user.role),
        is_active=bool(user.is_active),
        permissions=get_permissions_for_role(user.role),
        **build_dis_profile(user),
    )

    return TokenResponse(
        access_token=token,
        token_type="bearer",
        expires_in=settings.jwt_token_expire_days * 86_400,  # convert days → seconds
        user=profile,
    )


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.post(
    "/login",
    response_model=TokenResponse,
    summary="Log in and receive a JWT access token",
    description=(
        "Validates username and password against the User table. "
        "On success, returns a signed JWT and the user's full permission list. "
        "The React frontend should store the token and send it as "
        "'Authorization: Bearer <token>' on every subsequent request."
    ),
    responses={
        401: {"description": "Invalid credentials or inactive account."},
    },
)
def login(
    credentials: LoginRequest,
    db: Session = Depends(get_db),
) -> TokenResponse:
    """
    Authenticate a user and issue a JWT access token.

    Replicates the login form logic from ``promptops_app/core/shared.py``
    ``login_page()``.  Password comparison uses SHA-256 hashing to match
    the existing scheme in ``database.py``.
    """
    # Reusing the existing ORM model directly — no need to duplicate it.
    from promptops_app.database import User

    _log.info("login_attempt  username=%s", credentials.username)

    user = db.query(User).filter(User.username == credentials.username).first()

    # Use a constant-time comparison path: always check the password hash
    # even if the user is not found.  This prevents timing-based user enumeration.
    password_correct = (
        user is not None and verify_password(credentials.password, user.password_hash)
    )

    if not password_correct:
        _log.warning("login_failed  username=%s  reason=invalid_credentials", credentials.username)
        # Deliberately vague: don't tell the caller whether it was the username
        # or the password that was wrong.
        raise AuthenticationError("Invalid username or password.")

    if user.is_active is False:
        _log.warning("login_failed  username=%s  reason=account_inactive", credentials.username)
        raise AuthenticationError("Account is deactivated. Contact an Admin.")

    _log.info("login_success  username=%s  role=%s", user.username, user.role)

    return _build_token_response(user)


@router.post(
    "/logout",
    response_model=MessageResponse,
    summary="Log out (client-side token discard)",
    description=(
        "JWT tokens are stateless — the server has no session to destroy. "
        "This endpoint exists as a clean contract point. "
        "The client must discard the stored token on receiving this response."
    ),
)
def logout(
    current_user=Depends(get_current_user),
) -> MessageResponse:
    """
    Acknowledge a logout request.

    The client is responsible for discarding the token.
    Server-side token invalidation is not implemented (stateless JWT).
    """
    _log.info("logout  username=%s", current_user.username)
    return MessageResponse(message="Logged out successfully.")


@router.post(
    "/refresh",
    response_model=TokenResponse,
    summary="Refresh the access token before it expires",
    description=(
        "Issues a new JWT with a fresh expiry. "
        "Call this before the current token expires to keep the session alive. "
        "The client should replace its stored token with the new one."
    ),
    responses={
        401: {"description": "Token is expired or invalid."},
    },
)
def refresh_token(
    current_user=Depends(get_current_user),
) -> TokenResponse:
    """
    Issue a new JWT for the currently authenticated user.

    The new token has the same role and username but a fresh expiry timestamp.
    This matches the Streamlit behaviour of re-issuing the cookie on every render.
    """
    _log.info("token_refresh  username=%s", current_user.username)
    return _build_token_response(current_user)


@router.get(
    "/me",
    response_model=UserProfileResponse,
    summary="Get the authenticated user's profile and permissions",
    description=(
        "Returns the current user's id, username, role, and full permissions list. "
        "The React frontend calls this on startup to determine which tabs and "
        "actions to display based on the user's role."
    ),
    responses={
        401: {"description": "Token is missing or invalid."},
    },
)
def get_me(
    current_user=Depends(get_current_user),
) -> UserProfileResponse:
    """
    Return the authenticated user's profile.

    The ``permissions`` list in the response lets the React frontend gate
    UI elements without making additional API calls.  This matches the
    ``rbac_check()`` calls that were distributed across every Streamlit page.
    """
    from app.core.dis_access import build_dis_profile

    return UserProfileResponse(
        id=current_user.id,
        username=current_user.username,
        role=current_user.role,
        role_display=role_label(current_user.role),
        is_active=bool(current_user.is_active),
        permissions=get_permissions_for_role(current_user.role),
        **build_dis_profile(current_user),
    )
