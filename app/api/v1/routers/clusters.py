"""
Clusters router — CRUD for clusters within a project.

Streamlit equivalent: cluster management in ``core/shared.py``
project_dashboard_page() cluster sections.

Hierarchy: Project → Cluster → Course.
Every course belongs to exactly one cluster.

RBAC:
  cluster.create → Admin only
  cluster.edit   → Admin only
  cluster.delete → Admin only
  List/Get       → All authenticated users
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.dependencies import get_current_user, get_db, require_permission
from app.core.exceptions import NotFoundError, ValidationError
from app.schemas.cluster import (
    ClusterCreateRequest,
    ClusterListItem,
    ClusterRead,
    ClusterUpdateRequest,
)
from app.schemas.common import MessageResponse, PaginatedResponse
from app.schemas.course import CourseListItem

_log = logging.getLogger(__name__)
router = APIRouter()


def _get_cluster_or_404(db: Session, cluster_id: int):
    """Fetch a cluster by ID or raise HTTP 404."""
    from promptops_app.database import Cluster
    cluster = db.query(Cluster).filter(Cluster.id == cluster_id, Cluster.is_active == True).first()  # noqa: E712
    if cluster is None:
        raise NotFoundError("Cluster", cluster_id)
    return cluster


@router.get(
    "/projects/{project_id}/clusters",
    response_model=PaginatedResponse[ClusterListItem],
    summary="List clusters in a project",
    description="Returns all active clusters for a project. Hierarchy: Project → Cluster → Course.",
)
def list_clusters(
    project_id: int,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
) -> PaginatedResponse[ClusterListItem]:
    """Return all clusters in a project. Replaces the cluster dropdown in Streamlit."""
    from sqlalchemy import func
    from promptops_app.database import Cluster, Course

    rows = (
        db.query(Cluster, func.count(Course.id).label("course_count"))
        .outerjoin(Course, Course.cluster_id == Cluster.id)
        .filter(Cluster.project_id == project_id, Cluster.is_active == True)  # noqa: E712
        .group_by(Cluster.id)
        .order_by(Cluster.created_at.asc())
        .all()
    )
    total = len(rows)
    start = (page - 1) * page_size
    items = [
        ClusterListItem(
            id=cluster.id,
            project_id=cluster.project_id,
            name=cluster.name,
            description=cluster.description,
            created_at=cluster.created_at,
            course_count=count,
        )
        for cluster, count in rows[start: start + page_size]
    ]
    return PaginatedResponse.create(items=items, total=total, page=page, page_size=page_size)


@router.post(
    "/projects/{project_id}/clusters",
    response_model=ClusterRead,
    status_code=201,
    summary="Create a cluster within a project",
    description="Creates a new cluster to group related courses. Admin only.",
)
def create_cluster(
    project_id: int,
    request_body: ClusterCreateRequest,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("cluster.create")),
) -> ClusterRead:
    """Create a new cluster inside a project. Replaces cluster creation form in Streamlit."""
    from promptops_app.database import Cluster, ClusterPrompt, Project

    project = db.query(Project).filter(Project.id == project_id).first()
    if project is None:
        raise NotFoundError("Project", project_id)

    cluster = Cluster(
        project_id=project_id,
        name=request_body.name,
        description=request_body.description,
        created_by=current_user.username,
    )
    db.add(cluster)
    db.flush()  # obtain cluster.id for the clone below

    # Clone selected ClusterPrompts into the new cluster (copy, not link —
    # mirrors the Streamlit cluster_selection_page() create-cluster form).
    if request_body.copy_prompt_ids:
        source_prompts = (
            db.query(ClusterPrompt)
            .filter(
                ClusterPrompt.id.in_(request_body.copy_prompt_ids),
                ClusterPrompt.is_active == True,  # noqa: E712
            )
            .all()
        )
        for src in source_prompts:
            db.add(ClusterPrompt(
                cluster_id=cluster.id,
                name=src.name,
                description=src.description,
                system_prompt=src.system_prompt,
                user_prompt_template=src.user_prompt_template,
                created_by=current_user.username,
                is_active=True,
            ))

    db.commit()
    db.refresh(cluster)

    _log.info("cluster_created  user=%s  cluster_id=%d  project_id=%d  name=%s  copied_prompts=%d",
              current_user.username, cluster.id, project_id, cluster.name, len(request_body.copy_prompt_ids))
    return ClusterRead.model_validate(cluster)


@router.get(
    "/clusters/{cluster_id}",
    response_model=ClusterRead,
    summary="Get a single cluster",
)
def get_cluster(
    cluster_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
) -> ClusterRead:
    """Return cluster details."""
    return ClusterRead.model_validate(_get_cluster_or_404(db, cluster_id))


@router.put(
    "/clusters/{cluster_id}",
    response_model=ClusterRead,
    summary="Update a cluster",
    description="Update cluster name or description. Admin only.",
)
def update_cluster(
    cluster_id: int,
    request_body: ClusterUpdateRequest,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("cluster.edit")),
) -> ClusterRead:
    """Update cluster name or description. Replaces cluster edit form in Streamlit."""
    cluster = _get_cluster_or_404(db, cluster_id)

    if request_body.name is not None:
        cluster.name = request_body.name
    if request_body.description is not None:
        cluster.description = request_body.description

    db.commit()
    db.refresh(cluster)

    _log.info("cluster_updated  user=%s  cluster_id=%d", current_user.username, cluster_id)
    return ClusterRead.model_validate(cluster)


@router.delete(
    "/clusters/{cluster_id}",
    response_model=MessageResponse,
    summary="Delete a cluster",
    description="Soft-deletes a cluster (sets is_active=False). Admin only.",
)
def delete_cluster(
    cluster_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("cluster.delete")),
) -> MessageResponse:
    """Soft-delete a cluster. Replaces cluster delete action in Streamlit."""
    from promptops_app.database import Course

    cluster = _get_cluster_or_404(db, cluster_id)

    active_course_count = (
        db.query(Course)
        .filter(Course.cluster_id == cluster_id, Course.is_active == True)  # noqa: E712
        .count()
    )
    if active_course_count > 0:
        raise ValidationError(
            f"Cannot delete cluster '{cluster.name}' — it has {active_course_count} active course(s). "
            "Move or remove those courses first."
        )

    cluster.is_active = False
    db.commit()

    _log.info("cluster_deleted  user=%s  cluster_id=%d", current_user.username, cluster_id)
    return MessageResponse(message=f"Cluster {cluster_id} deleted.")


@router.get(
    "/clusters/{cluster_id}/courses",
    response_model=PaginatedResponse[CourseListItem],
    summary="List courses inside a cluster",
    description="Returns all courses belonging to this cluster.",
)
def list_courses_in_cluster(
    cluster_id: int,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
) -> PaginatedResponse[CourseListItem]:
    """Return all courses inside a cluster."""
    from promptops_app.database import Course

    _get_cluster_or_404(db, cluster_id)

    courses = (
        db.query(Course)
        .filter(Course.cluster_id == cluster_id)
        .order_by(Course.created_at.asc())
        .all()
    )
    total = len(courses)
    start = (page - 1) * page_size
    return PaginatedResponse.create(
        items=[CourseListItem.model_validate(c) for c in courses[start: start + page_size]],
        total=total,
        page=page,
        page_size=page_size,
    )
