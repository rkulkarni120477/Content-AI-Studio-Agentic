"""Style Repository — Style lookup, document queries, and StyleVersion CRUD.

Core style operations (get_styles, get_active_style, create_style, etc.) live
in database.py.  This module provides the additional queries needed by the
Style and CDD pages, plus the full StyleVersion versioning API.
"""

from datetime import datetime, timezone

from app.core.tenant_context import apply_tenant_filter
from promptops_app.database import Document, Style, StyleVersion


# ── Style lookups ─────────────────────────────────────────────────────────────

def get_style_by_id(db, style_id: int, *, with_documents: bool = False, tenant_id=None, is_platform_admin=False):
    q = db.query(Style).filter(Style.id == style_id)
    if with_documents:
        from sqlalchemy.orm import joinedload
        from promptops_app.database import StyleDocument
        q = q.options(
            joinedload(Style.style_documents).joinedload(StyleDocument.document),
        )
    return apply_tenant_filter(q, Style, tenant_id, is_platform_admin).first()


def list_active_documents_for_style(db):
    """Return all active documents available to link to a style."""
    return db.query(Document).filter(Document.status == "active").all()


def get_available_documents_for_style(db, style):
    """Return documents not already linked to the given style."""
    existing_ids = {sd.document_id for sd in style.style_documents}
    return (
        db.query(Document)
        .filter(Document.status == "active", ~Document.id.in_(existing_ids))
        .all()
    )


# ── StyleVersion: create ──────────────────────────────────────────────────────

def create_style_version(
    db,
    style,
    understanding_content: str,
    change_summary: str,
    created_by: str,
) -> StyleVersion:
    """Insert a new active StyleVersion and deactivate all previous ones.

    Also syncs style.generated_summary for backward compatibility.
    Returns the new StyleVersion.
    """
    # Determine next version number
    last = (
        db.query(StyleVersion)
        .filter(StyleVersion.style_id == style.id)
        .order_by(StyleVersion.version_number.desc())
        .first()
    )
    next_num = (last.version_number + 1) if last else 1

    # Deactivate existing active versions
    db.query(StyleVersion).filter(
        StyleVersion.style_id == style.id,
        StyleVersion.is_active == True,
    ).update({StyleVersion.is_active: False})

    # Create new active version
    new_ver = StyleVersion(
        style_id              = style.id,
        version_number        = next_num,
        is_active             = True,
        understanding_content = understanding_content,
        change_summary        = str(change_summary)[:500] if change_summary else "Generated",
        created_by            = created_by,
        created_at            = datetime.now(timezone.utc),
    )
    db.add(new_ver)

    # Sync backward-compat field
    style.generated_summary   = understanding_content
    style.understanding_status = "fresh"
    style.updated_at           = datetime.now(timezone.utc)

    db.commit()
    db.refresh(new_ver)
    return new_ver


# ── StyleVersion: read ────────────────────────────────────────────────────────

def list_style_versions(db, style_id: int, limit: int = 50) -> list:
    return (
        db.query(StyleVersion)
        .filter(StyleVersion.style_id == style_id)
        .order_by(StyleVersion.version_number.desc())
        .limit(limit)
        .all()
    )


def get_active_style_version(db, style_id: int):
    return (
        db.query(StyleVersion)
        .filter(StyleVersion.style_id == style_id, StyleVersion.is_active == True)
        .first()
    )


def get_style_version_by_id(db, version_id: int):
    return db.query(StyleVersion).filter(StyleVersion.id == version_id).first()


def count_style_versions(db, style_id: int) -> int:
    return db.query(StyleVersion).filter(StyleVersion.style_id == style_id).count()


# ── StyleVersion: mutate ──────────────────────────────────────────────────────

def set_active_style_version(db, style, version_id: int) -> tuple[bool, str]:
    """Activate a specific version (without creating a new one).

    Returns (success, message).
    """
    ver = get_style_version_by_id(db, version_id)
    if not ver or ver.style_id != style.id:
        return False, "Version not found."
    if ver.is_active:
        return False, "This version is already active."

    db.query(StyleVersion).filter(
        StyleVersion.style_id == style.id,
        StyleVersion.is_active == True,
    ).update({StyleVersion.is_active: False})

    ver.is_active          = True
    style.generated_summary = ver.understanding_content
    style.updated_at        = datetime.now(timezone.utc)
    db.commit()
    return True, f"Version {ver.version_number} is now active."


def restore_style_version(
    db,
    style,
    version_id: int,
    created_by: str,
) -> tuple[StyleVersion | None, str | None]:
    """Restore a previous understanding version by creating a NEW version copy.

    The old content becomes the content of a fresh version (append-only history).
    Returns (new_version, None) on success, (None, error_msg) on failure.
    """
    source = get_style_version_by_id(db, version_id)
    if not source or source.style_id != style.id:
        return None, "Version not found."
    if source.is_active:
        return None, "This version is already active."

    new_ver = create_style_version(
        db,
        style,
        understanding_content = source.understanding_content,
        change_summary        = f"Restored from v{source.version_number}",
        created_by            = created_by,
    )
    return new_ver, None


# ── Back-fill helper (called at startup if needed) ────────────────────────────

def backfill_style_version_from_summary(db, style) -> StyleVersion | None:
    """If style has generated_summary but no StyleVersion rows, create v1.

    Call this lazily when a Style is first loaded in the version panel.
    """
    if not style.generated_summary:
        return None
    exists = db.query(StyleVersion).filter(StyleVersion.style_id == style.id).count()
    if exists:
        return None  # already has versions, nothing to backfill

    ver = StyleVersion(
        style_id              = style.id,
        version_number        = 1,
        is_active             = True,
        understanding_content = style.generated_summary,
        change_summary        = "Migrated from pre-versioning era",
        created_by            = style.created_by or "system",
        created_at            = style.updated_at or datetime.now(timezone.utc),
    )
    db.add(ver)
    db.commit()
    db.refresh(ver)
    return ver
