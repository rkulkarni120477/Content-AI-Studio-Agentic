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
from app.core.exceptions import NotFoundError
from app.schemas.common import MessageResponse, PaginatedResponse
from app.schemas.course import (
    CourseCreateRequest,
    CourseListItem,
    CourseRead,
    CourseUpdateRequest,
    CourseUserAssignRequest,
    CourseUserListItem,
)

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
    total = len(courses)
    start = (page - 1) * page_size
    return PaginatedResponse.create(
        items=[CourseListItem.model_validate(c) for c in courses[start: start + page_size]],
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
    db.add(course)
    db.commit()
    db.refresh(course)

    _log.info("course_created  user=%s  course_id=%d  project_id=%d  name=%s",
              current_user.username, course.id, project_id, course.name)
    return CourseRead.model_validate(course)


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
    return CourseRead.model_validate(_get_course_or_404(db, course_id))


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
    return CourseRead.model_validate(course)


@router.delete(
    "/courses/{course_id}",
    status_code=204,
    summary="Delete a course",
)
def delete_course(
    course_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("course.delete")),
) -> None:
    """Archive a course. Admin only."""
    course = _get_course_or_404(db, course_id)
    course.is_active = False
    db.commit()
    _log.info("course_deleted  user=%s  course_id=%d", current_user.username, course_id)


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
