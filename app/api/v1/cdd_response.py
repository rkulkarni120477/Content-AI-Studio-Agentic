"""Build CDDRead responses from ORM rows (shared by CDD and course routers)."""

from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from app.schemas.cdd import CDDRead, CDDVersionRead

_log = logging.getLogger(__name__)


def build_cdd_read(db: Session, cdd) -> CDDRead:
    """Serialize a CDD ORM row plus its active version for API responses."""
    from promptops_app.repositories import cdd_repository

    active_content = None
    if cdd.active_version:
        version_record = cdd_repository.get_cdd_version(db, cdd.id, cdd.active_version)
        if version_record:
            try:
                active_content = CDDVersionRead.model_validate(version_record)
            except Exception:
                _log.exception(
                    "cdd_version_validate_failed  cdd_id=%s  version=%s",
                    cdd.id,
                    cdd.active_version,
                )
                raise

    base = CDDRead.model_validate(cdd)
    # is_archived is derived, not an ORM column, so model_validate cannot fill
    # it — without this the detail view of an archived CDD looks live.
    return base.model_copy(update={
        "active_content": active_content,
        "is_archived": getattr(cdd, "deleted_at", None) is not None,
    })
