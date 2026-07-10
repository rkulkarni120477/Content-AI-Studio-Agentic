"""
FastAPI dependency functions — injected into route handlers via Depends().

These are the building blocks used in every route handler.  They handle:
  - Database session lifecycle (one session per request, always closed)
  - JWT token extraction and user authentication
  - RBAC permission enforcement

How FastAPI dependencies work
------------------------------
FastAPI resolves Depends() automatically before calling the route function.
If a dependency raises an exception, the route function never runs and the
error propagates to the global exception handler.

Usage in a route
----------------
    @router.post("/cdd/generate")
    def generate_cdd(
        request_body: CDDGenerateRequest,
        db: Session = Depends(get_db),
        current_user: User = Depends(require_permission("cdd.generate")),
    ):
        ...
"""

from __future__ import annotations

import logging
from typing import Generator

from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.core.database import SessionLocal
from app.core.exceptions import AuthenticationError, PermissionDeniedError
from app.core.permissions import rbac_check
from app.core.security import decode_access_token

_log = logging.getLogger(__name__)

# HTTPBearer extracts the token from the "Authorization: Bearer <token>" header.
# auto_error=False means we get None instead of a 403 if the header is missing,
# allowing us to raise our own AuthenticationError with a better message.
_bearer_scheme = HTTPBearer(auto_error=False)


# ---------------------------------------------------------------------------
# Database session dependency
# ---------------------------------------------------------------------------

def get_db() -> Generator[Session, None, None]:
    """
    Yield a SQLAlchemy database session for the duration of a single request.

    The session is always closed in the finally block, even if the request
    raises an exception.  This prevents connection leaks.

    Use as a dependency:
        def my_endpoint(db: Session = Depends(get_db)): ...
    """
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Authentication dependency
# ---------------------------------------------------------------------------

def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer_scheme),
    db: Session = Depends(get_db),
):
    """
    Extract and validate the JWT from the Authorization header.

    Returns the authenticated User ORM object.
    Raises ``AuthenticationError`` (401) if:
      - No Authorization header is present.
      - The token is expired or invalid.
      - The user account is deactivated or deleted.

    Use as a dependency:
        def my_endpoint(current_user = Depends(get_current_user)): ...
    """
    # Avoid a circular import: import models here rather than at module level.
    from promptops_app.database import Project, TenantMembership, User

    if credentials is None:
        raise AuthenticationError("Authentication required. Provide a Bearer token.")

    payload = decode_access_token(credentials.credentials)

    username: str = payload.get("sub", "")
    if not username:
        raise AuthenticationError("Token is missing the subject claim.")

    project_id: int | None = payload.get("project_id")
    is_platform_admin: bool = payload.get("is_platform_admin", False)

    # Verify the user still exists and is active in the database.
    # This catches cases where an account was deactivated after token issuance.
    user = db.query(User).filter(User.username == username).first()

    if user is None:
        _log.warning("auth_user_not_found  username=%s", username)
        raise AuthenticationError("User account not found.")

    if user.is_active is False:
        _log.warning("auth_user_inactive  username=%s", username)
        raise AuthenticationError("User account is deactivated. Contact an Admin.")

    # Resolve the EFFECTIVE role for this session. A user's role is per-tenant
    # (TenantMembership), so re-validate membership + tenant status each request
    # — deactivation, role changes, and suspension take effect immediately.
    effective_role = user.role
    if is_platform_admin:
        effective_role = user.role or "admin"
        project_id = None
    elif project_id is not None:
        project = db.get(Project, project_id)
        if project is None or (getattr(project, "status", "active") or "active") != "active" or not project.is_active:
            raise AuthenticationError("This organization has been suspended.")
        membership = (
            db.query(TenantMembership)
            .filter(TenantMembership.user_id == user.id, TenantMembership.project_id == project_id)
            .first()
        )
        if membership is None or not membership.active:
            raise AuthenticationError("Your membership in this organization is inactive.")
        effective_role = membership.role

    # Attach context so route handlers can read it without extra DB calls.
    user._project_id        = project_id
    user._is_platform_admin = is_platform_admin
    user._role              = effective_role
    # Override the in-memory role so existing `current_user.role` reads reflect
    # the per-tenant role. User.role is a vestigial default (membership is the
    # source of truth), so an accidental flush is harmless.
    user.role = effective_role

    return user


# ---------------------------------------------------------------------------
# Permission enforcement dependency factory
# ---------------------------------------------------------------------------

def require_permission(permission: str):
    """
    Return a FastAPI dependency that enforces an RBAC permission.

    This is a dependency factory — it returns a function that FastAPI calls
    as a Depends().  The returned function checks the user's role against
    the required permission and raises ``PermissionDeniedError`` if denied.

    Usage:
        @router.post("/workflow/bulk-approve")
        def bulk_approve(
            user = Depends(require_permission("workflow.bulk_approve"))
        ):
            ...  # only admins reach this point
    """
    def _check(current_user=Depends(get_current_user)):
        """Verify the authenticated user holds the required permission."""
        if not rbac_check(current_user.role, permission):
            _log.warning(
                "permission_denied  user=%s  role=%s  permission=%s",
                current_user.username, current_user.role, permission,
            )
            raise PermissionDeniedError(permission, user_role=current_user.role)
        return current_user

    return _check


def get_tenant_context(current_user=Depends(get_current_user)):
    """Resolve (tenant_id, is_platform_admin) for project-scoped queries.

    tenant_id is the caller's own project id (as a string), or None for a
    platform admin (sees all) or an unassigned user.
    """
    from app.core.tenant_context import is_platform_admin_user, tenant_id_from_user

    return (tenant_id_from_user(current_user), is_platform_admin_user(current_user))
