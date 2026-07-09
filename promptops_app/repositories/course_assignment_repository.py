"""Course user assignment repository."""

from __future__ import annotations

from promptops_app.database import CourseUserAssignment


def get_assigned_usernames(db, course_id: int) -> set:
    rows = (
        db.query(CourseUserAssignment.username)
        .filter(CourseUserAssignment.course_id == course_id)
        .all()
    )
    return {r[0] for r in rows}


def assign_user_to_course(db, course_id: int, username: str) -> None:
    if username in get_assigned_usernames(db, course_id):
        return
    db.add(CourseUserAssignment(course_id=course_id, username=username))
    db.commit()


def unassign_user_from_course(db, course_id: int, username: str) -> None:
    db.query(CourseUserAssignment).filter(
        CourseUserAssignment.course_id == course_id,
        CourseUserAssignment.username == username,
    ).delete()
    db.commit()
