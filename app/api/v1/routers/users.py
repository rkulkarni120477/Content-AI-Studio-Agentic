"""
Users router — user management and RBAC administration.

Streamlit equivalent: The user management section in ``pages/analytics.py``
(Create User form, role editor, activate/deactivate toggle).

All write operations are Admin only.
The ``GET /users/reviewers`` endpoint is used by the Workflow page to
populate the reviewer assignment dropdown.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.dependencies import get_current_user, get_db, require_permission
from app.core.exceptions import DuplicateResourceError, NotFoundError
from app.core.permissions import role_label
from app.core.security import hash_password
from app.schemas.common import MessageResponse, PaginatedResponse
from app.schemas.user import (
    UserCreateRequest,
    UserListItem,
    UserRead,
    UserRoleUpdateRequest,
    UserToggleRequest,
)

_log = logging.getLogger(__name__)
router = APIRouter()


def _get_user_or_404(db: Session, user_id: int):
    """Fetch a user by ID or raise HTTP 404."""
    from promptops_app.repositories import user_repository
    user = user_repository.get_user_by_id(db, user_id)
    if user is None:
        raise NotFoundError("User", user_id)
    return user


def _to_user_read(user) -> UserRead:
    """Convert a User ORM object to a UserRead schema."""
    data = UserRead.model_validate(user)
    data.role_display = role_label(user.role)
    return data


@router.get(
    "",
    response_model=PaginatedResponse[UserListItem],
    summary="List all users",
    description="Admin and Lead only. Includes active and inactive accounts.",
)
def list_users(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("users.view")),
) -> PaginatedResponse[UserListItem]:
    """List all platform users with their roles and active status."""
    from promptops_app.repositories import user_repository

    users = user_repository.list_all_users(db)
    total = len(users)
    start = (page - 1) * page_size
    items = []
    for u in users[start: start + page_size]:
        item = UserListItem.model_validate(u)
        item.role_display = role_label(u.role)
        items.append(item)

    return PaginatedResponse.create(items=items, total=total, page=page, page_size=page_size)


@router.post(
    "",
    response_model=UserRead,
    status_code=201,
    summary="Create a new user",
)
def create_user(
    request_body: UserCreateRequest,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("users.create")),
) -> UserRead:
    """
    Create a user account with a hashed password.

    Replicates the "Create User" form in the Analytics admin tab.
    Password is hashed with PBKDF2-HMAC-SHA256 to match the existing scheme.
    """
    from promptops_app.database import User
    from promptops_app.repositories import user_repository

    # Check for duplicate username.
    if user_repository.get_user_by_username(db, request_body.username):
        raise DuplicateResourceError("User", request_body.username)

    user = User(
        username=request_body.username,
        password_hash=hash_password(request_body.password),
        role=request_body.role,
        is_active=True,
    )
    db.add(user)
    db.commit()
    db.refresh(user)

    _log.info("user_created  by=%s  new_user=%s  role=%s",
              current_user.username, user.username, user.role)
    return _to_user_read(user)


@router.get(
    "/reviewers",
    response_model=list[UserListItem],
    summary="List users eligible to be assigned as reviewers",
    description="Returns all users with 'reviewer' or 'admin' role. Used to populate the reviewer dropdown in the Workflow page.",
)
def list_reviewers(
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
) -> list[UserListItem]:
    """Return users who can be assigned as block reviewers."""
    from promptops_app.repositories import user_repository

    reviewers = user_repository.list_reviewers_and_admins(db)
    items = []
    for u in reviewers:
        item = UserListItem.model_validate(u)
        item.role_display = role_label(u.role)
        items.append(item)
    return items


@router.get(
    "/me",
    response_model=UserRead,
    summary="Get the authenticated user's own profile",
)
def get_my_profile(
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
) -> UserRead:
    """Return the current user's full profile without requiring admin permission."""
    return _to_user_read(current_user)


@router.get(
    "/{user_id}",
    response_model=UserRead,
    summary="Get a single user",
)
def get_user(
    user_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("users.view")),
) -> UserRead:
    """Return user profile. Does not include password hash."""
    return _to_user_read(_get_user_or_404(db, user_id))


@router.put(
    "/{user_id}/role",
    response_model=UserRead,
    summary="Change a user's role",
)
def update_user_role(
    user_id: int,
    request_body: UserRoleUpdateRequest,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("users.edit")),
) -> UserRead:
    """Change a user's platform role. Admin only."""
    user = _get_user_or_404(db, user_id)
    old_role = user.role
    user.role = request_body.role
    db.commit()
    db.refresh(user)

    _log.info("user_role_changed  by=%s  user=%s  old=%s  new=%s",
              current_user.username, user.username, old_role, request_body.role)
    return _to_user_read(user)


@router.put(
    "/{user_id}/toggle",
    response_model=UserRead,
    summary="Activate or deactivate a user account",
)
def toggle_user(
    user_id: int,
    request_body: UserToggleRequest,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("users.toggle")),
) -> UserRead:
    """
    Enable or disable a user account.

    Replicates the active/inactive toggle in the Analytics admin tab.
    Deactivated users cannot log in but their data is preserved.
    """
    user = _get_user_or_404(db, user_id)
    user.is_active = request_body.is_active
    db.commit()
    db.refresh(user)

    action = "activated" if request_body.is_active else "deactivated"
    _log.info("user_%s  by=%s  user=%s", action, current_user.username, user.username)
    return _to_user_read(user)
