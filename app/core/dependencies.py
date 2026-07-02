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
    except Exception:
        db.rollback()
        raise
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

    Returns the authenticated User ORM object with tenant context attached as
    private attributes (_tenant_id, _tenant_slug, _is_platform_admin).

    Raises ``AuthenticationError`` (401) if:
      - No Authorization header is present.
      - The token is expired or invalid.
      - The user account is deactivated or deleted.

    Use as a dependency:
        def my_endpoint(current_user = Depends(get_current_user)): ...
    """
    from promptops_app.database import User

    if credentials is None:
        raise AuthenticationError("Authentication required. Provide a Bearer token.")

    payload = decode_access_token(credentials.credentials)

    username: str = payload.get("sub", "")
    if not username:
        raise AuthenticationError("Token is missing the subject claim.")

    tenant_id: str | None = payload.get("tenant_id")
    is_platform_admin: bool = payload.get("is_platform_admin", False)

    # Scope the lookup so a token from tenant-A cannot authenticate as tenant-B.
    if is_platform_admin:
        user = db.query(User).filter(
            User.username == username,
            User.is_platform_admin == True,
        ).first()
    elif tenant_id:
        user = db.query(User).filter(
            User.username == username,
            User.tenant_id == tenant_id,
        ).first()
    else:
        # Fallback for tokens issued before tenant system (backward compat).
        user = db.query(User).filter(User.username == username).first()

    if user is None:
        _log.warning("auth_user_not_found  username=%s  tenant_id=%s", username, tenant_id)
        raise AuthenticationError("User account not found.")

    if user.is_active is False:
        _log.warning("auth_user_inactive  username=%s", username)
        raise AuthenticationError("User account is deactivated. Contact an Admin.")

    # Attach tenant context from the token so route handlers don't need extra DB calls.
    user._tenant_id = tenant_id
    user._tenant_slug = payload.get("tenant_slug")
    user._is_platform_admin = is_platform_admin

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


# ---------------------------------------------------------------------------
# Tenant context helpers
# ---------------------------------------------------------------------------

def get_tenant_context(current_user=Depends(get_current_user)):
    """
    Return (tenant_id, is_platform_admin) for the current request.

    Use as a dependency in any route that needs to scope queries:
        def my_endpoint(ctx = Depends(get_tenant_context)):
            tenant_id, is_platform_admin = ctx
            ...
    """
    from app.core.tenant_context import tenant_id_from_user, is_platform_admin_user
    return (
        tenant_id_from_user(current_user),
        is_platform_admin_user(current_user),
    )
