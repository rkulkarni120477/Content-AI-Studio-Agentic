"""
Users router — platform-wide user lookup and role administration.

``GET /users`` backs the project/title "Manage Users" picker (assigning
existing accounts to a project or course) — account creation and
activate/deactivate now live per-tenant under
``/api/v1/platform/tenants/{id}/users`` (platform-admin only).
The ``GET /users/reviewers`` endpoint is used by the Workflow page to
populate the reviewer assignment dropdown.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.dependencies import get_current_user, get_db, require_permission
from app.core.exceptions import NotFoundError
from app.core.permissions import role_label
from app.schemas.common import MessageResponse, PaginatedResponse
from app.schemas.user import (
    UserListItem,
    UserRead,
    UserRoleUpdateRequest,
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
    description=(
        "Backs the project/title \"Manage Users\" assignment picker — members of the "
        "caller's own tenant only. Only a true platform admin may pass project_id to "
        "look at another tenant; for everyone else it is ignored and the caller's own "
        "tenant is used."
    ),
)
def list_users(
    project_id: int | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("users.view")),
) -> PaginatedResponse[UserListItem]:
    """List users assignable to a project/course in the caller's own tenant."""
    from promptops_app.repositories import user_repository

    # Same tenant-isolation shape as list_reviewers below: derive scope from
    # the caller's token, never trust the client-supplied parameter. This was
    # the same cross-tenant leak as the /reviewers dropdown, one function
    # above it in this same module — a tenant Admin saw and could assign
    # every other tenant's users through this exact picker.
    is_platform_admin = bool(getattr(current_user, "_is_platform_admin", False))
    scoped_project_id = project_id if is_platform_admin else getattr(current_user, "_project_id", None)

    if scoped_project_id is None:
        return PaginatedResponse.create(items=[], total=0, page=page, page_size=page_size)

    rows = user_repository.list_all_users(db, project_id=scoped_project_id)
    total = len(rows)
    start = (page - 1) * page_size
    items = []
    for u, role_display in rows[start: start + page_size]:
        item = UserListItem.model_validate(u)
        item.role_display = role_display
        items.append(item)

    return PaginatedResponse.create(items=items, total=total, page=page, page_size=page_size)


@router.get(
    "/reviewers",
    response_model=list[UserListItem],
    summary="List users eligible to be assigned as reviewers",
    description=(
        "Returns users who can approve content in the caller's own tenant — an "
        "active membership whose effective role grants 'workflow.approve'. Backs the "
        "reviewer dropdowns in the Workflow and Editor pages. Only a true platform "
        "admin may pass project_id to look at another tenant; for everyone else it "
        "is ignored and the caller's own tenant is used."
    ),
)
def list_reviewers(
    project_id: int | None = Query(default=None),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
) -> list[UserListItem]:
    """Return users who can be assigned as block reviewers."""
    from promptops_app.repositories import user_repository

    # Tenant isolation: derive the scope from the caller's token, never from a
    # client-supplied parameter. Only a true platform admin may choose an
    # arbitrary project via the query param — every tenant-scoped caller,
    # INCLUDING a tenant-role "admin", is force-scoped to their own tenant.
    # Mirrors analytics.py's llm_cost_dashboard. Without this, trusting the
    # frontend to send the right project_id was the whole control: any user
    # could enumerate another tenant's members by passing its id, or get the
    # old platform-wide list back just by omitting the param.
    is_platform_admin = bool(getattr(current_user, "_is_platform_admin", False))
    scoped_project_id = project_id if is_platform_admin else getattr(current_user, "_project_id", None)

    reviewers = user_repository.list_reviewers_and_admins(db, project_id=scoped_project_id)
    items = []
    for u, role_display in reviewers:
        item = UserListItem.model_validate(u)
        item.role_display = role_display
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
