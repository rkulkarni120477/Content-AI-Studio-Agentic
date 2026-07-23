"""CDD Repository — CourseDesignDocument and CDDVersion database access."""

from sqlalchemy import or_

from promptops_app.database import CourseDesignDocument, CDDVersion


def list_cdds_for_project(db, project_id: int, limit: int = 100):
    return (
        db.query(CourseDesignDocument)
        .filter(CourseDesignDocument.project_id == project_id)
        .order_by(CourseDesignDocument.created_at.desc())
        .limit(limit)
        .all()
    )


def list_cdds_for_scope(
    db,
    project_id: int | None = None,
    course_id: int | None = None,
    limit: int = 200,
):
    """List CDDs for a project/course.

    When ``course_id`` is given, it is always an AND filter — CDDs from other
    courses in the same project must never leak in. Legacy rows with a null
    ``project_id`` are matched by course_id alone rather than excluded.
    """
    q = db.query(CourseDesignDocument)
    if course_id is not None:
        if project_id is not None:
            q = q.filter(
                CourseDesignDocument.course_id == course_id,
                or_(
                    CourseDesignDocument.project_id == project_id,
                    CourseDesignDocument.project_id.is_(None),
                ),
            )
        else:
            q = q.filter(CourseDesignDocument.course_id == course_id)
    elif project_id is not None:
        q = q.filter(CourseDesignDocument.project_id == project_id)
    return (
        q.order_by(CourseDesignDocument.created_at.desc())
        .limit(limit)
        .all()
    )


def list_all_cdds(db, limit: int = 200):
    return (
        db.query(CourseDesignDocument)
        .order_by(CourseDesignDocument.created_at.desc())
        .limit(limit)
        .all()
    )


def get_cdd_by_id(db, cdd_id: int):
    return (
        db.query(CourseDesignDocument)
        .filter(CourseDesignDocument.id == cdd_id)
        .first()
    )


def list_cdd_versions(db, cdd_id: int):
    return (
        db.query(CDDVersion)
        .filter(CDDVersion.cdd_id == cdd_id)
        .order_by(CDDVersion.created_at.asc())
        .all()
    )


def get_cdd_version(db, cdd_id: int, version: str):
    return (
        db.query(CDDVersion)
        .filter(CDDVersion.cdd_id == cdd_id, CDDVersion.version == version)
        .first()
    )


def create_cdd_version(
    db,
    cdd,
    content: str,
    sections_json: str,
    generation_params_json: str,
    change_summary: str,
    created_by: str,
    *,
    parent_version_id: int = None,
) -> "CDDVersion":
    """Create a new CDDVersion, deactivate others, and update cdd.active_version."""
    from promptops_app.database import CDDVersion
    existing = list_cdd_versions(db, cdd.id)
    next_label  = f"v{len(existing) + 1}"
    next_number = len(existing) + 1

    db.query(CDDVersion).filter(CDDVersion.cdd_id == cdd.id).update({CDDVersion.is_active: False})

    new_ver = CDDVersion(
        cdd_id            = cdd.id,
        version           = next_label,
        version_number    = next_number,
        parent_version_id = parent_version_id,
        full_content      = content,
        sections          = sections_json,
        generation_params = generation_params_json,
        change_reason     = str(change_summary)[:500] if change_summary else "",
        is_active         = True,
        created_by        = created_by,
    )
    db.add(new_ver)
    cdd.active_version = next_label
    db.commit()
    db.refresh(new_ver)
    return new_ver


def restore_cdd_version(
    db,
    cdd,
    version_label: str,
    created_by: str,
) -> tuple:
    """Restore a previous CDD version by creating a new version copy of its content.

    Returns (new_version, None) on success, (None, error_msg) on failure.
    """
    source = get_cdd_version(db, cdd.id, version_label)
    if not source:
        return None, f"Version '{version_label}' not found."
    if source.is_active:
        return None, "This version is already active."

    new_ver = create_cdd_version(
        db,
        cdd,
        content               = source.full_content or "",
        sections_json         = source.sections or "",
        generation_params_json = source.generation_params or "",
        change_summary        = f"Restored from {version_label}",
        created_by            = created_by,
        parent_version_id     = source.id,
    )
    return new_ver, None


def count_cdds_scoped(db, user_name: str = None, project_id: int = None, is_admin: bool = True) -> int:
    q = db.query(CourseDesignDocument)
    if not is_admin and user_name:
        q = q.filter(CourseDesignDocument.created_by == user_name)
        if project_id:
            q = q.filter(CourseDesignDocument.project_id == project_id)
    return q.count()


def list_cdds_scoped(db, user_name: str = None, project_id: int = None, is_admin: bool = True):
    q = db.query(CourseDesignDocument)
    if not is_admin and user_name:
        q = q.filter(CourseDesignDocument.created_by == user_name)
        if project_id:
            q = q.filter(CourseDesignDocument.project_id == project_id)
    return q.all()
