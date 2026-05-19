"""Course Repository — Course database access."""

from __future__ import annotations

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


# ---------------------------------------------------------------------------
# Requirement 1 — Target & Model configuration (course-level)
# ---------------------------------------------------------------------------

def get_course_config(db, course_id: int) -> dict:
    """Return the persisted Target & Model config and pinned component IDs.

    Keys in the returned dict mirror session-state variable names so callers
    can apply the result directly.  None values mean the course has no saved
    config for that field yet.
    """
    course = get_course_by_id(db, course_id)
    if not course:
        return {}
    return {
        "model_choice":        course.config_model_choice,
        "expert_domain":       course.config_expert_domain,
        "target_audience":     course.config_target_audience,
        "sidebar_aud_cat":     course.config_audience_category,
        "active_cdd_id":       course.active_cdd_id,
        "active_blueprint_id": course.active_blueprint_id,
    }


def save_course_target_config(
    db,
    course_id: int,
    *,
    model_choice: "str | None" = None,
    expert_domain: "str | None" = None,
    target_audience: "str | None" = None,
    audience_category: "str | None" = None,
) -> None:
    """Persist Target & Model form values for *course_id*.

    Only updates fields that are explicitly provided (non-None).
    """
    course = get_course_by_id(db, course_id)
    if not course:
        return
    if model_choice      is not None: course.config_model_choice      = model_choice
    if expert_domain     is not None: course.config_expert_domain     = expert_domain
    if target_audience   is not None: course.config_target_audience   = target_audience
    if audience_category is not None: course.config_audience_category = audience_category
    db.commit()


# ---------------------------------------------------------------------------
# Requirement 3 — Pinned component persistence (course-level)
# ---------------------------------------------------------------------------

def set_active_cdd(db, course_id: int, cdd_id: "int | None") -> None:
    """Persist the pinned CDD for *course_id* to the database."""
    course = get_course_by_id(db, course_id)
    if course:
        course.active_cdd_id = cdd_id
        db.commit()


def set_active_blueprint(db, course_id: int, blueprint_id: "int | None") -> None:
    """Persist the pinned Blueprint for *course_id* to the database."""
    course = get_course_by_id(db, course_id)
    if course:
        course.active_blueprint_id = blueprint_id
        db.commit()
