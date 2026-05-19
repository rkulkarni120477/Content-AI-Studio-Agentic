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
    # Avoid a circular import: import User model here rather than at module level.
    from promptops_app.database import User  # reusing the existing ORM model

    if credentials is None:
        raise AuthenticationError("Authentication required. Provide a Bearer token.")

    payload = decode_access_token(credentials.credentials)

    username: str = payload.get("sub", "")
    if not username:
        raise AuthenticationError("Token is missing the subject claim.")

    # Verify the user still exists and is active in the database.
    # This catches cases where an account was deactivated after token issuance.
    user = db.query(User).filter(User.username == username).first()

    if user is None:
        _log.warning("auth_user_not_found  username=%s", username)
        raise AuthenticationError("User account not found.")

    if user.is_active is False:
        _log.warning("auth_user_inactive  username=%s", username)
        raise AuthenticationError("User account is deactivated. Contact an Admin.")

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
