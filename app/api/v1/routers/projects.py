"""
Projects router — CRUD for the top-level project hierarchy.

Streamlit equivalent: project selection in ``core/shared.py`` project_dashboard_page()
and admin project management in ``pages/analytics.py``.

RBAC:
  project.create → Admin only
  project.edit   → Admin only
  project.delete → Admin only
  List/Get       → All authenticated users (scoped by role)
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.dependencies import get_current_user, get_db, require_permission
from app.core.exceptions import NotFoundError
from app.core.tenant_context import (
    effective_tenant_id_for_write,
    is_platform_admin_user,
    tenant_id_from_user,
)
from app.schemas.common import MessageResponse, PaginatedResponse
from app.schemas.project import (
    ProjectCreateRequest,
    ProjectListItem,
    ProjectRead,
    ProjectUpdateRequest,
    ProjectUserAssignRequest,
    ProjectUserListItem,
)

_log = logging.getLogger(__name__)
router = APIRouter()


def _get_project_or_404(db: Session, project_id: int, tenant_id=None, is_platform_admin=False):
    """Fetch a project by ID (tenant-scoped) or raise HTTP 404."""
    from promptops_app.repositories import project_repository
    project = project_repository.get_project_by_id(
        db, project_id, tenant_id=tenant_id, is_platform_admin=is_platform_admin
    )
    if project is None:
        raise NotFoundError("Project", project_id)
    return project


@router.get(
    "",
    response_model=PaginatedResponse[ProjectListItem],
    summary="List projects",
    description="Admin sees all projects. Other roles see only their assigned projects.",
)
def list_projects(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
) -> PaginatedResponse[ProjectListItem]:
    """Return projects scoped to the authenticated user's role and tenant."""
    from promptops_app.database import _get_user_projects
    from promptops_app.repositories import project_repository

    tid      = tenant_id_from_user(current_user)
    is_admin = is_platform_admin_user(current_user)

    if current_user.role == "admin" or is_admin:
        projects = project_repository.list_active_projects(db, tenant_id=tid, is_platform_admin=is_admin)
    else:
        projects = _get_user_projects(db, current_user.username, current_user.role, tenant_id=tid)

    total = len(projects)
    start = (page - 1) * page_size
    return PaginatedResponse.create(
        items=[ProjectListItem.model_validate(p) for p in projects[start: start + page_size]],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.post(
    "",
    response_model=ProjectRead,
    status_code=201,
    summary="Create a project",
)
def create_project(
    request_body: ProjectCreateRequest,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("project.create")),
) -> ProjectRead:
    """Create a new project. Admin only."""
    from promptops_app.database import Project

    tid = effective_tenant_id_for_write(current_user)
    project = Project(
        name=request_body.name,
        client_name=request_body.client_name,
        description=request_body.description,
        tenant_id=tid,
    )
    db.add(project)
    db.commit()
    db.refresh(project)

    _log.info("project_created  user=%s  project_id=%d  name=%s", current_user.username, project.id, project.name)
    return ProjectRead.model_validate(project)


@router.get(
    "/{project_id}",
    response_model=ProjectRead,
    summary="Get a single project",
)
def get_project(
    project_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
) -> ProjectRead:
    """Return project details."""
    tid      = tenant_id_from_user(current_user)
    is_admin = is_platform_admin_user(current_user)
    return ProjectRead.model_validate(_get_project_or_404(db, project_id, tid, is_admin))


@router.put(
    "/{project_id}",
    response_model=ProjectRead,
    summary="Update a project",
)
def update_project(
    project_id: int,
    request_body: ProjectUpdateRequest,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("project.edit")),
) -> ProjectRead:
    """Update project name, client name, or description. Admin only."""
    project = _get_project_or_404(
        db, project_id, tenant_id_from_user(current_user), is_platform_admin_user(current_user)
    )

    if request_body.name is not None:
        project.name = request_body.name
    if request_body.client_name is not None:
        project.client_name = request_body.client_name
    if request_body.description is not None:
        project.description = request_body.description

    db.commit()
    db.refresh(project)

    _log.info("project_updated  user=%s  project_id=%d", current_user.username, project_id)
    return ProjectRead.model_validate(project)


@router.delete(
    "/{project_id}",
    status_code=204,
    summary="Delete a project",
)
def delete_project(
    project_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("project.delete")),
) -> None:
    """Soft-delete (archive) a project. Admin only."""
    project = _get_project_or_404(
        db, project_id, tenant_id_from_user(current_user), is_platform_admin_user(current_user)
    )
    project.is_active = False
    db.commit()
    _log.info("project_deleted  user=%s  project_id=%d", current_user.username, project_id)


@router.get(
    "/{project_id}/users",
    response_model=list[ProjectUserListItem],
    summary="List users assigned to a project",
)
def list_project_users(
    project_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("users.assign")),
) -> list[ProjectUserListItem]:
    """Return usernames assigned to this project."""
    from promptops_app.repositories import project_repository

    _get_project_or_404(
        db, project_id, tenant_id_from_user(current_user), is_platform_admin_user(current_user)
    )
    usernames = sorted(project_repository.get_assigned_usernames(db, project_id))
    return [ProjectUserListItem(username=u) for u in usernames]


@router.post(
    "/{project_id}/users",
    response_model=MessageResponse,
    status_code=201,
    summary="Assign a user to a project",
)
def assign_user_to_project(
    project_id: int,
    request_body: ProjectUserAssignRequest,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("users.assign")),
) -> MessageResponse:
    """Assign a user to a project by username."""
    from promptops_app.database import User
    from promptops_app.repositories import project_repository

    tid      = tenant_id_from_user(current_user)
    is_admin = is_platform_admin_user(current_user)
    _get_project_or_404(db, project_id, tid, is_admin)
    user = db.query(User).filter(
        User.username == request_body.username,
        User.is_active == True,  # noqa: E712
        User.role != "admin",
    )
    if tid:
        user = user.filter(User.tenant_id == tid)
    user = user.first()
    if user is None:
        raise NotFoundError("User", request_body.username)

    project_repository.assign_user_to_project(db, project_id, request_body.username)
    _log.info("user_assigned  by=%s  username=%s  project_id=%d",
              current_user.username, request_body.username, project_id)
    return MessageResponse(message=f"User '{request_body.username}' assigned to project {project_id}.")


@router.delete(
    "/{project_id}/users/{username}",
    status_code=204,
    summary="Remove a user from a project",
)
def unassign_user_from_project(
    project_id: int,
    username: str,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("users.assign")),
) -> None:
    """Remove a user's project assignment."""
    from promptops_app.repositories import project_repository

    _get_project_or_404(
        db, project_id, tenant_id_from_user(current_user), is_platform_admin_user(current_user)
    )
    project_repository.unassign_user_from_project(db, project_id, username)
    _log.info("user_unassigned  by=%s  username=%s  project_id=%d",
              current_user.username, username, project_id)
