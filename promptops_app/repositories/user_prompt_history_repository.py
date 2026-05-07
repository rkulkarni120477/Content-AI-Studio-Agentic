"""User Prompt History Repository.

CRUD access for the user_prompt_history table — free-text additional
instructions saved by users during CDD, Blueprint, Generate, and Style
operations, scoped to project / cluster / course.
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from promptops_app.database import UserPromptHistory


# ---------------------------------------------------------------------------
# Queries
# ---------------------------------------------------------------------------

def list_latest_for_component(
    db,
    component: str,
    project_id: Optional[int] = None,
    cluster_id: Optional[int] = None,
    course_id: Optional[int] = None,
    limit: int = 100,
) -> list[UserPromptHistory]:
    """Return the single latest version of each named prompt for this scope.

    Scoping precedence: filters are AND-ed when provided.
    Returns distinct names ordered by most-recently updated.
    """
    q = (
        db.query(UserPromptHistory)
        .filter(
            UserPromptHistory.component == component,
            UserPromptHistory.is_active == True,  # noqa: E712
        )
    )
    if project_id is not None:
        q = q.filter(UserPromptHistory.project_id == project_id)
    if course_id is not None:
        q = q.filter(UserPromptHistory.course_id == course_id)

    rows = q.order_by(
        UserPromptHistory.name.asc(),
        UserPromptHistory.version_number.desc(),
    ).all()

    # Deduplicate — keep only highest version_number per name
    seen: set[str] = set()
    latest: list[UserPromptHistory] = []
    for r in rows:
        if r.name not in seen:
            seen.add(r.name)
            latest.append(r)

    # Sort final list by updated_at DESC for natural "most recent first" UX
    latest.sort(key=lambda x: x.updated_at or x.created_at or datetime.min, reverse=True)
    return latest[:limit]


def get_versions_for_name(
    db,
    name: str,
    component: str,
    project_id: Optional[int] = None,
    course_id: Optional[int] = None,
) -> list[UserPromptHistory]:
    """Return all active versions for a named prompt, newest first."""
    q = db.query(UserPromptHistory).filter(
        UserPromptHistory.name == name,
        UserPromptHistory.component == component,
        UserPromptHistory.is_active == True,  # noqa: E712
    )
    if project_id is not None:
        q = q.filter(UserPromptHistory.project_id == project_id)
    if course_id is not None:
        q = q.filter(UserPromptHistory.course_id == course_id)
    return q.order_by(UserPromptHistory.version_number.desc()).all()


def get_by_id(db, record_id: int) -> Optional[UserPromptHistory]:
    return db.query(UserPromptHistory).filter(UserPromptHistory.id == record_id).first()


def get_next_version_number(
    db,
    name: str,
    component: str,
    project_id: Optional[int],
    course_id: Optional[int],
) -> int:
    """Return the next version_number for a named prompt (1 if new)."""
    versions = get_versions_for_name(db, name, component, project_id, course_id)
    return (versions[0].version_number + 1) if versions else 1


# ---------------------------------------------------------------------------
# Mutations
# ---------------------------------------------------------------------------

def save_prompt(
    db,
    name: str,
    component: str,
    content: str,
    project_id: Optional[int],
    cluster_id: Optional[int],
    course_id: Optional[int],
    created_by: str,
    as_new_version: bool = False,
) -> UserPromptHistory:
    """Persist a user instruction prompt.

    Parameters
    ----------
    as_new_version:
        If True, always create a new version_number (version history).
        If False, upsert the content of the latest existing record with this
        name (or create v1 if none exists yet).
    """
    existing = get_versions_for_name(db, name, component, project_id, course_id)

    if not existing or as_new_version:
        # New record
        next_v = (existing[0].version_number + 1) if existing else 1
        entry = UserPromptHistory(
            name=name.strip(),
            component=component,
            content=content,
            version_number=next_v,
            project_id=project_id,
            cluster_id=cluster_id,
            course_id=course_id,
            created_by=created_by,
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow(),
            is_active=True,
        )
        db.add(entry)
        db.commit()
        db.refresh(entry)
        return entry
    else:
        # Update content of the current latest version in place
        latest = existing[0]
        latest.content    = content
        latest.updated_at = datetime.utcnow()
        db.commit()
        db.refresh(latest)
        return latest


def soft_delete(
    db,
    name: str,
    component: str,
    project_id: Optional[int],
    course_id: Optional[int],
) -> int:
    """Soft-delete all versions of a named prompt. Returns count deleted."""
    q = db.query(UserPromptHistory).filter(
        UserPromptHistory.name == name,
        UserPromptHistory.component == component,
        UserPromptHistory.is_active == True,  # noqa: E712
    )
    if project_id is not None:
        q = q.filter(UserPromptHistory.project_id == project_id)
    if course_id is not None:
        q = q.filter(UserPromptHistory.course_id == course_id)
    rows = q.all()
    for r in rows:
        r.is_active  = False
        r.updated_at = datetime.utcnow()
    db.commit()
    return len(rows)
