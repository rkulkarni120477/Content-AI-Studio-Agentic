"""Course Repository — Course database access."""

from promptops_app.database import Course


def get_course_by_id(db, course_id: int):
    return db.query(Course).filter(Course.id == course_id).first()


def list_courses_for_project(db, project_id: int):
    return (
        db.query(Course)
        .filter(Course.project_id == project_id)
        .order_by(Course.name)
        .all()
    )
