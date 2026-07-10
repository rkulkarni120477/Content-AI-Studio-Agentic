"""
Authentication router — organization-code + Microsoft sign-in, platform-admin
password login, token refresh, and current user.

Login paths
-----------
  POST /auth/login
      platform_admin=True  -> username/password vs is_platform_admin users.
      else                 -> organization_code + username/password for a tenant
                              member that has a local password (e.g. the initial
                              tenant admin). Role comes from their membership.
  GET  /login/microsoft?organization_code=...   -> redirect to Microsoft.
  GET  /auth/microsoft/callback                 -> exchange, provision, redirect
                                                   to the frontend with a token.
"""

from __future__ import annotations

import logging
from urllib.parse import quote, urlencode

from fastapi import APIRouter, Depends, Query
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.core.config import PLATFORM_SLUG, settings
from app.core.dependencies import get_current_user, get_db
from app.core.exceptions import AuthenticationError
from app.core.permissions import get_permissions_for_role, role_label
from app.core.security import create_access_token, verify_password
from app.schemas.auth import (
    AuthConfigResponse,
    LoginRequest,
    TenantLoginInfoResponse,
    TokenResponse,
    UserProfileResponse,
)
from app.schemas.common import MessageResponse

_log = logging.getLogger(__name__)

router = APIRouter()


# ---------------------------------------------------------------------------
# Token / profile builders
# ---------------------------------------------------------------------------

def _build_token_response(user) -> TokenResponse:
    project_id = getattr(user, "_project_id", None)
    is_platform_admin = getattr(user, "_is_platform_admin", False)
    role = getattr(user, "_role", None) or user.role

    from app.core.dis_access import build_dis_profile

    token = create_access_token(
        user.username,
        role,
        project_id=project_id,
        is_platform_admin=is_platform_admin,
    )
    profile = UserProfileResponse(
        id=user.id,
        username=user.username,
        role=role,
        role_display=role_label(role),
        is_active=bool(user.is_active),
        permissions=get_permissions_for_role(role),
        project_id=project_id,
        is_platform_admin=is_platform_admin,
        **build_dis_profile(user),
    )
    return TokenResponse(
        access_token=token,
        token_type="bearer",
        expires_in=settings.jwt_token_expire_days * 86_400,
        user=profile,
    )


def _frontend(path: str = "", **params) -> str:
    base = settings.frontend_url.rstrip("/")
    url = f"{base}/{path.lstrip('/')}" if path else base
    if params:
        url = f"{url}?{urlencode(params)}"
    return url


# ---------------------------------------------------------------------------
# Public config
# ---------------------------------------------------------------------------

@router.get("/config", response_model=AuthConfigResponse, summary="Public sign-in options")
def auth_config() -> AuthConfigResponse:
    return AuthConfigResponse(
        microsoft_enabled=settings.microsoft_ready(),
        local_login_enabled=settings.local_login_enabled,
        platform_slug=PLATFORM_SLUG,
    )


@router.get(
    "/tenant-login/{slug}",
    response_model=TenantLoginInfoResponse,
    summary="Validate an organization code and report sign-in options",
)
def tenant_login_info(slug: str, db: Session = Depends(get_db)) -> TenantLoginInfoResponse:
    from app.services import microsoft_auth

    info = microsoft_auth.tenant_login_options(db, slug)
    if not info.get("valid"):
        return TenantLoginInfoResponse(valid=False, error="Unknown organization code.")
    if info.get("status") != "active":
        return TenantLoginInfoResponse(valid=False, error="Organization suspended.", **{
            k: info.get(k) for k in ("slug", "name", "status", "microsoft_enabled")
        })
    return TenantLoginInfoResponse(**info)


# ---------------------------------------------------------------------------
# Password login (platform admin OR tenant local user)
# ---------------------------------------------------------------------------

@router.post(
    "/login",
    response_model=TokenResponse,
    summary="Log in with a password (platform admin or tenant local user)",
    responses={401: {"description": "Invalid credentials, inactive account, or suspended org."}},
)
def login(credentials: LoginRequest, db: Session = Depends(get_db)) -> TokenResponse:
    from promptops_app.database import Project, TenantMembership, User

    if not settings.local_login_enabled:
        raise AuthenticationError("Password login is disabled. Use Microsoft sign-in.")

    username = credentials.username.strip()
    _log.info("login_attempt  username=%s  platform_admin=%s", username, credentials.platform_admin)

    # ── Platform admin path ────────────────────────────────────────────────
    if credentials.platform_admin:
        user = (
            db.query(User)
            .filter(User.username == username, User.is_platform_admin == True)  # noqa: E712
            .first()
        )
        if not user or not verify_password(credentials.password, user.password_hash or ""):
            raise AuthenticationError("Invalid platform administrator credentials.")
        if user.is_active is False:
            raise AuthenticationError("Account is deactivated.")
        user._project_id = None
        user._is_platform_admin = True
        user._role = user.role or "admin"
        return _build_token_response(user)

    # ── Tenant local-password path ─────────────────────────────────────────
    org = (credentials.organization_code or "").strip().lower()
    if not org:
        raise AuthenticationError("Organization code is required.")

    project = db.query(Project).filter(Project.slug == org).first()
    if not project:
        raise AuthenticationError("Unknown organization code.")
    if (project.status or "active") != "active" or not project.is_active:
        raise AuthenticationError("This organization has been suspended.")

    user = db.query(User).filter(User.username == username).first()
    if not user or not verify_password(credentials.password, user.password_hash or ""):
        raise AuthenticationError("Invalid username or password.")
    if user.is_active is False:
        raise AuthenticationError("Account is deactivated. Contact an admin.")

    membership = (
        db.query(TenantMembership)
        .filter(TenantMembership.user_id == user.id, TenantMembership.project_id == project.id)
        .first()
    )
    if membership is None or not membership.active:
        raise AuthenticationError("You are not an active member of this organization.")

    user._project_id = project.id
    user._is_platform_admin = False
    user._role = membership.role
    user.project_id = project.id
    db.commit()

    _log.info("login_success  username=%s  org=%s  role=%s", username, org, membership.role)
    return _build_token_response(user)


# ---------------------------------------------------------------------------
# Microsoft OAuth
# ---------------------------------------------------------------------------

@router.get("/login/microsoft", summary="Redirect to Microsoft sign-in for an organization")
def microsoft_login(
    organization_code: str = Query(..., min_length=1),
    next: str = Query(default="/"),
    db: Session = Depends(get_db),
):
    from app.services import microsoft_auth

    try:
        url = microsoft_auth.build_login_url(db, organization_code, next)
    except Exception as exc:
        return RedirectResponse(_frontend("login", error=str(exc)), status_code=302)
    return RedirectResponse(url, status_code=302)


@router.get("/microsoft/callback", summary="Microsoft OAuth callback")
def microsoft_callback(
    db: Session = Depends(get_db),
    code: str | None = Query(default=None),
    state: str | None = Query(default=None),
    error: str | None = Query(default=None),
    error_description: str | None = Query(default=None),
):
    from app.services import microsoft_auth

    if error:
        return RedirectResponse(_frontend("login", error=error_description or error), status_code=302)
    if not code or not state:
        return RedirectResponse(_frontend("login", error="Missing authorization code."), status_code=302)

    try:
        slug = microsoft_auth.slug_from_state(state)
        claims = microsoft_auth.exchange_code_for_claims(db, code, slug)
        user, membership = microsoft_auth.find_or_create_user(db, claims, slug)
        db.commit()
    except Exception as exc:
        db.rollback()
        return RedirectResponse(_frontend("login", error=str(exc)), status_code=302)

    user._project_id = membership.project_id
    user._is_platform_admin = False
    user._role = membership.role
    token = _build_token_response(user).access_token

    _log.info("ms_login_success  username=%s  project_id=%s  role=%s",
              user.username, membership.project_id, membership.role)
    # Hand the token to the frontend login page, which stores it and routes on.
    return RedirectResponse(_frontend("login", token=token), status_code=302)


# ---------------------------------------------------------------------------
# Session helpers
# ---------------------------------------------------------------------------

@router.post("/logout", response_model=MessageResponse, summary="Log out")
def logout(current_user=Depends(get_current_user)) -> MessageResponse:
    _log.info("logout  username=%s", current_user.username)
    return MessageResponse(message="Logged out successfully.")


@router.post("/refresh", response_model=TokenResponse, summary="Refresh the access token")
def refresh_token(current_user=Depends(get_current_user)) -> TokenResponse:
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

    role = getattr(current_user, "_role", None) or current_user.role
    return UserProfileResponse(
        id=current_user.id,
        username=current_user.username,
        role=role,
        role_display=role_label(role),
        is_active=bool(current_user.is_active),
        permissions=get_permissions_for_role(role),
        project_id=getattr(current_user, "_project_id", None),
        is_platform_admin=getattr(current_user, "_is_platform_admin", False),
        **build_dis_profile(current_user),
    )
