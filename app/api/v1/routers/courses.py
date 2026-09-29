"""
Courses router — CRUD for courses within a project.

Streamlit equivalent: ``core/shared.py`` course_selection_page() and
project_dashboard_page() course management sections.

Courses are the unit of work — every CDD, Blueprint, and Generation
belongs to a course.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.dependencies import get_current_user, get_db, require_permission
from app.core.exceptions import NotFoundError, ValidationError
from app.schemas.common import MessageResponse, PaginatedResponse
from app.core.dis_access import digest_pipeline_enabled_for_course
from app.schemas.course import (
    CourseCreateRequest,
    CourseListItem,
    CourseRead,
    CourseUpdateRequest,
    CourseUserAssignRequest,
    CourseUserListItem,
    build_course_list_item,
    build_course_read,
)
from app.schemas.cdd import CDDRead
from app.api.v1.cdd_response import build_cdd_read

_log = logging.getLogger(__name__)
router = APIRouter()


def _get_course_or_404(db: Session, course_id: int):
    """Fetch a course by ID or raise HTTP 404."""
    from promptops_app.repositories import course_repository
    course = course_repository.get_course_by_id(db, course_id)
    if course is None:
        raise NotFoundError("Course", course_id)
    return course


@router.get(
    "/projects/{project_id}/courses",
    response_model=PaginatedResponse[CourseListItem],
    summary="List courses in a project",
)
def list_courses(
    project_id: int,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
) -> PaginatedResponse[CourseListItem]:
    """Return all courses in a project. Replaces the course dropdown in Streamlit."""
    from promptops_app.repositories import course_repository

    courses = course_repository.list_courses_for_project(db, project_id)
    digest_on = digest_pipeline_enabled_for_course(db, project_id=project_id)
    total = len(courses)
    start = (page - 1) * page_size
    return PaginatedResponse.create(
        # Resolved once for the whole project, not per row: the flag is a property
        # of the project's client, so a per-course lookup would be N+1 queries for
        # N identical answers.
        items=[build_course_list_item(c, digest_pipeline_enabled=digest_on)
               for c in courses[start: start + page_size]],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.post(
    "/projects/{project_id}/courses",
    response_model=CourseRead,
    status_code=201,
    summary="Create a course within a project",
)
def create_course(
    project_id: int,
    request_body: CourseCreateRequest,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("course.create")),
) -> CourseRead:
    """Create a new course. Admin or Reviewer (Lead)."""
    from promptops_app.database import Course

    course = Course(
        name=request_body.name,
        project_id=project_id,
        cluster_id=request_body.cluster_id,
    )
    if request_body.description is not None:
        course.description = request_body.description.strip() or None
    db.add(course)
    db.commit()
    db.refresh(course)

    _log.info("course_created  user=%s  course_id=%d  project_id=%d  name=%s",
              current_user.username, course.id, project_id, course.name)
    return build_course_read(course, digest_pipeline_enabled=digest_pipeline_enabled_for_course(
        db, course_id=course.id, project_id=course.project_id))


@router.get(
    "/courses/{course_id}",
    response_model=CourseRead,
    summary="Get a single course",
)
def get_course(
    course_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
) -> CourseRead:
    """Return course details including active CDD and Blueprint IDs."""
    course = _get_course_or_404(db, course_id)
    return build_course_read(course, digest_pipeline_enabled=digest_pipeline_enabled_for_course(
        db, course_id=course.id, project_id=course.project_id))


@router.get(
    "/courses/{course_id}/active-cdd",
    response_model=CDDRead,
    summary="Get the CDD pinned on a course",
    description=(
        "Use this when you have a **course id** (e.g. from `/workspace/7/...`). "
        "Returns the full CDD for `courses.active_cdd_id`. "
        "Do not pass the course id to `GET /cdd/{cdd_id}`."
    ),
    responses={
        404: {"description": "Course not found, no CDD pinned, or pinned CDD record missing."},
    },
)
def get_course_active_cdd(
    course_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
) -> CDDRead:
    """Return the active/pinned CDD for a course by course id."""
    from promptops_app.repositories import cdd_repository

    course = _get_course_or_404(db, course_id)
    if not course.active_cdd_id:
        raise NotFoundError("Active CDD for course", course_id)

    cdd = cdd_repository.get_cdd_by_id(db, course.active_cdd_id)
    if cdd is None:
        raise NotFoundError("CDD", course.active_cdd_id)

    return build_cdd_read(db, cdd)


@router.put(
    "/courses/{course_id}",
    response_model=CourseRead,
    summary="Update a course",
)
def update_course(
    course_id: int,
    request_body: CourseUpdateRequest,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("course.edit")),
) -> CourseRead:
    """Update course name or cluster. Admin or Reviewer."""
    course = _get_course_or_404(db, course_id)

    if request_body.name is not None:
        course.name = request_body.name
    if request_body.description is not None:
        course.description = request_body.description.strip() or None
    if request_body.cluster_id is not None:
        course.cluster_id = request_body.cluster_id

    db.commit()
    db.refresh(course)
    _log.info("course_updated  user=%s  course_id=%d", current_user.username, course_id)
    return build_course_read(course, digest_pipeline_enabled=digest_pipeline_enabled_for_course(
        db, course_id=course.id, project_id=course.project_id))


@router.delete(
    "/courses/{course_id}",
    status_code=204,
    summary="Archive a course",
)
def delete_course(
    course_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("course.delete")),
) -> None:
    """Soft-delete (archive) a course. Content remains until permanently deleted."""
    course = _get_course_or_404(db, course_id)
    course.is_active = False
    db.commit()
    _log.info("course_archived  user=%s  course_id=%d", current_user.username, course_id)


@router.post(
    "/courses/{course_id}/restore",
    status_code=204,
    summary="Restore an archived course",
)
def restore_course(
    course_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("course.delete")),
) -> None:
    """Restore an archived course without changing its associated content."""
    course = _get_course_or_404(db, course_id)
    if not course.is_active:
        course.is_active = True
        db.commit()
        _log.info("course_restored  user=%s  course_id=%d", current_user.username, course_id)


@router.delete(
    "/courses/{course_id}/permanent",
    status_code=204,
    summary="Permanently delete an archived course",
)
def permanently_delete_course(
    course_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("course.delete")),
) -> None:
    """Hard-delete an archived course and all of its content.

    The course must already be archived (``is_active=False``). Active courses
    must be archived first via ``DELETE /courses/{id}``.
    """
    from promptops_app.repositories import course_repository
    from app.services.asset_cleanup import collect_course_assets, delete_unreferenced

    course = _get_course_or_404(db, course_id)
    if course.is_active:
        raise ValidationError(
            "Archive the course before permanently deleting it.",
            detail={"course_id": course_id, "is_active": True},
        )

    # Snapshot the course's uploaded images before its blocks/versions are gone.
    candidate_assets = collect_course_assets(db, course_id)

    course_repository.purge_course(db, course_id)

    # After purge, delete any of those images now referenced nowhere (best-effort).
    try:
        delete_unreferenced(db, candidate_assets)
    except Exception:  # noqa: BLE001 — cleanup must never fail the purge
        _log.exception("asset_cleanup_on_purge_failed  course_id=%d", course_id)

    _log.info("course_purged  user=%s  course_id=%d", current_user.username, course_id)


@router.get(
    "/courses/{course_id}/users",
    response_model=list[CourseUserListItem],
    summary="List users assigned to a course",
)
def list_course_users(
    course_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("users.assign")),
) -> list[CourseUserListItem]:
    """Return usernames with course-level access."""
    from promptops_app.repositories import course_assignment_repository

    _get_course_or_404(db, course_id)
    usernames = sorted(course_assignment_repository.get_assigned_usernames(db, course_id))
    return [CourseUserListItem(username=u) for u in usernames]


@router.post(
    "/courses/{course_id}/users",
    response_model=MessageResponse,
    status_code=201,
    summary="Assign a user to a course",
)
def assign_user_to_course(
    course_id: int,
    request_body: CourseUserAssignRequest,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("users.assign")),
) -> MessageResponse:
    """Grant course-level access by username."""
    from promptops_app.database import User
    from promptops_app.repositories import course_assignment_repository

    _get_course_or_404(db, course_id)
    user = db.query(User).filter(
        User.username == request_body.username,
        User.is_active == True,  # noqa: E712
        User.role != "admin",
    ).first()
    if user is None:
        raise NotFoundError("User", request_body.username)

    course_assignment_repository.assign_user_to_course(db, course_id, request_body.username)
    _log.info("course_user_assigned  by=%s  username=%s  course_id=%d",
              current_user.username, request_body.username, course_id)
    return MessageResponse(message=f"User '{request_body.username}' assigned to course {course_id}.")


@router.delete(
    "/courses/{course_id}/users/{username}",
    status_code=204,
    summary="Remove a user from a course",
)
def unassign_user_from_course(
    course_id: int,
    username: str,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("users.assign")),
) -> None:
    """Revoke course-level access."""
    from promptops_app.repositories import course_assignment_repository

    _get_course_or_404(db, course_id)
    course_assignment_repository.unassign_user_from_course(db, course_id, username)
    _log.info("course_user_unassigned  by=%s  username=%s  course_id=%d",
              current_user.username, username, course_id)
