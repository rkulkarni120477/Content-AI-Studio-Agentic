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
    is_platform_admin_user,
    tenant_id_from_user,
)
from app.schemas.common import MessageResponse, PaginatedResponse
from app.schemas.project import (
    ProjectCreateRequest,
    ProjectCreateResponse,
    ProjectInitialAdmin,
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
    description="Platform admin sees all projects. A regular user sees only their own project (tenant).",
)
def list_projects(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
) -> PaginatedResponse[ProjectListItem]:
    """Return projects scoped to the authenticated user's tenant.

    Under the project-as-tenant model a regular user belongs to exactly one
    project, so they only ever see that single project here — never the full
    dashboard. An unassigned user (awaiting assignment) sees an empty list.
    """
    from promptops_app.repositories import project_repository

    tid      = tenant_id_from_user(current_user)
    is_admin = is_platform_admin_user(current_user)

    projects = project_repository.list_active_projects(db, tenant_id=tid, is_platform_admin=is_admin)

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
    response_model=ProjectCreateResponse,
    status_code=201,
    summary="Create a project (tenant)",
    description=(
        "Platform admin only. A project is a tenant — creating one may "
        "optionally create its first user in the same call by supplying "
        "admin_username/admin_password."
    ),
)
def create_project(
    request_body: ProjectCreateRequest,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("project.create")),
) -> ProjectCreateResponse:
    """Create a new project (tenant). Platform admin only."""
    from fastapi import HTTPException
    from promptops_app.database import Project, User

    if not is_platform_admin_user(current_user):
        raise HTTPException(status_code=403, detail="Platform admin access required.")

    project = Project(
        name=request_body.name,
        client_name=request_body.client_name,
        description=request_body.description,
    )
    db.add(project)
    db.flush()

    initial_admin: ProjectInitialAdmin | None = None
    if request_body.admin_username and request_body.admin_password:
        from app.core.security import hash_password

        # Usernames are globally unique (not per-tenant) — check unscoped.
        if db.query(User).filter(User.username == request_body.admin_username).first():
            db.rollback()
            raise HTTPException(status_code=409, detail=f"Username '{request_body.admin_username}' is already taken.")

        admin_user = User(
            username=request_body.admin_username,
            password_hash=hash_password(request_body.admin_password),
            display_name=(request_body.admin_display_name or request_body.admin_username).strip(),
            role=request_body.admin_role,
            project_id=project.id,
            is_active=True,
            is_platform_admin=False,
        )
        db.add(admin_user)
        db.flush()
        initial_admin = ProjectInitialAdmin(
            id=admin_user.id,
            username=admin_user.username,
            role=admin_user.role,
            display_name=admin_user.display_name,
        )

    db.commit()
    db.refresh(project)

    _log.info("project_created  user=%s  project_id=%d  name=%s  initial_admin=%s",
              current_user.username, project.id, project.name,
              initial_admin.username if initial_admin else None)
    return ProjectCreateResponse(**ProjectRead.model_validate(project).model_dump(), initial_admin=initial_admin)


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
    """Return usernames assigned to this project (User.project_id == project_id)."""
    from promptops_app.database import User

    _get_project_or_404(
        db, project_id, tenant_id_from_user(current_user), is_platform_admin_user(current_user)
    )
    usernames = sorted(
        u for (u,) in db.query(User.username).filter(User.project_id == project_id).all()
    )
    return [ProjectUserListItem(username=u) for u in usernames]


@router.post(
    "/{project_id}/users",
    response_model=MessageResponse,
    status_code=201,
    summary="Assign a user to a project",
    description="Platform admin only. Sets the user's home project (tenant) — this is what determines what they can see and where they land after login.",
)
def assign_user_to_project(
    project_id: int,
    request_body: ProjectUserAssignRequest,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("users.assign")),
) -> MessageResponse:
    """Assign a user to a project by username. Platform admin only."""
    from fastapi import HTTPException
    from promptops_app.database import User

    if not is_platform_admin_user(current_user):
        raise HTTPException(status_code=403, detail="Platform admin access required.")

    _get_project_or_404(db, project_id, None, True)
    user = db.query(User).filter(
        User.username == request_body.username,
        User.is_active == True,  # noqa: E712
        User.role != "admin",
        User.is_platform_admin == False,  # noqa: E712
    ).first()
    if user is None:
        raise NotFoundError("User", request_body.username)

    user.project_id = project_id
    db.commit()
    _log.info("user_assigned  by=%s  username=%s  project_id=%d",
              current_user.username, request_body.username, project_id)
    return MessageResponse(message=f"User '{request_body.username}' assigned to project {project_id}.")


@router.delete(
    "/{project_id}/users/{username}",
    status_code=204,
    summary="Remove a user from a project",
    description="Platform admin only. Clears the user's project assignment — they will see an 'awaiting assignment' screen until reassigned.",
)
def unassign_user_from_project(
    project_id: int,
    username: str,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("users.assign")),
) -> None:
    """Remove a user's project assignment. Platform admin only."""
    from fastapi import HTTPException
    from promptops_app.database import User

    if not is_platform_admin_user(current_user):
        raise HTTPException(status_code=403, detail="Platform admin access required.")

    _get_project_or_404(db, project_id, None, True)
    user = db.query(User).filter(User.username == username, User.project_id == project_id).first()
    if user is not None:
        user.project_id = None
        db.commit()
    _log.info("user_unassigned  by=%s  username=%s  project_id=%d",
              current_user.username, username, project_id)
