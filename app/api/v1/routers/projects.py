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
from app.schemas.common import MessageResponse, PaginatedResponse
from app.schemas.project import (
    ProjectCreateRequest,
    ProjectListItem,
    ProjectRead,
    ProjectUpdateRequest,
    ProjectUserAssignRequest,
)

_log = logging.getLogger(__name__)
router = APIRouter()


def _get_project_or_404(db: Session, project_id: int):
    """Fetch a project by ID or raise HTTP 404."""
    from promptops_app.repositories import project_repository
    project = project_repository.get_project_by_id(db, project_id)
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
    """Return projects scoped to the authenticated user's role."""
    from promptops_app.database import _get_user_projects
    from promptops_app.repositories import project_repository

    if current_user.role == "admin":
        projects = project_repository.list_active_projects(db)
    else:
        projects = _get_user_projects(db, current_user.username)

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

    project = Project(
        name=request_body.name,
        client_name=request_body.client_name,
        description=request_body.description,
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
    return ProjectRead.model_validate(_get_project_or_404(db, project_id))


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
    project = _get_project_or_404(db, project_id)

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
    """Soft-delete a project. Admin only."""
    project = _get_project_or_404(db, project_id)
    db.delete(project)
    db.commit()
    _log.info("project_deleted  user=%s  project_id=%d", current_user.username, project_id)


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
    """Assign a user to a project with a specific role."""
    from promptops_app.database import ProjectUserAssignment

    _get_project_or_404(db, project_id)

    # Upsert: update if assignment already exists.
    existing = db.query(ProjectUserAssignment).filter(
        ProjectUserAssignment.project_id == project_id,
        ProjectUserAssignment.user_id == request_body.user_id,
    ).first()

    if existing:
        existing.role = request_body.role
    else:
        db.add(ProjectUserAssignment(
            project_id=project_id,
            user_id=request_body.user_id,
            role=request_body.role,
        ))

    db.commit()
    _log.info("user_assigned  by=%s  user_id=%d  project_id=%d  role=%s",
              current_user.username, request_body.user_id, project_id, request_body.role)
    return MessageResponse(message=f"User assigned to project {project_id} as {request_body.role}.")
