"""Audit Repository — AuditLog database access.

All writes go through audit_service.log_audit_event() which wraps these
functions in a fail-safe try/except.  Reads are called directly by the
audit log UI in analytics.py.
"""

from __future__ import annotations

import json
from datetime import datetime

from sqlalchemy import func
from sqlalchemy.orm import defer

from promptops_app.database import AuditLog


# ── Write ─────────────────────────────────────────────────────────────────────

def create_audit_log(
    db,
    *,
    user_id: str,
    action: str,
    entity_type: str = None,
    entity_id: str = None,
    project_id: int = None,
    course_id: int = None,
    metadata: dict = None,
    ip_address: str = None,
) -> AuditLog:
    """Insert one audit row and return it.

    Callers should NOT call this directly — use audit_service.log_audit_event()
    which wraps this in a fail-safe try/except.
    """
    entry = AuditLog(
        user_id       = str(user_id)[:100],
        action        = str(action)[:120],
        entity_type   = str(entity_type)[:60]  if entity_type  else None,
        entity_id     = str(entity_id)[:64]    if entity_id    else None,
        project_id    = project_id,
        course_id     = course_id,
        metadata_json = json.dumps(metadata, ensure_ascii=False) if metadata else None,
        ip_address    = str(ip_address)[:45]   if ip_address   else None,
        created_at    = datetime.utcnow(),
    )
    db.add(entry)
    db.commit()
    return entry


# ── Read ──────────────────────────────────────────────────────────────────────

def count_audit_logs(
    db,
    *,
    user_id: str = None,
    action: str = None,
    entity_type: str = None,
    project_id: int = None,
    course_id: int = None,
    date_from: datetime = None,
    date_to: datetime = None,
) -> int:
    q = _base_query(db, select_col=func.count(AuditLog.id),
                    user_id=user_id, action=action, entity_type=entity_type,
                    project_id=project_id, course_id=course_id,
                    date_from=date_from, date_to=date_to)
    return q.scalar()


def list_audit_logs(
    db,
    *,
    user_id: str = None,
    action: str = None,
    entity_type: str = None,
    project_id: int = None,
    course_id: int = None,
    date_from: datetime = None,
    date_to: datetime = None,
    limit: int = 50,
    offset: int = 0,
    include_metadata: bool = True,
) -> list:
    """List audit rows, newest first.

    include_metadata=False defers metadata_json out of the SELECT entirely
    (not just out of the response) -- CAS-140: that column averages ~35KB
    and reaches 2.3MB on actions like content.generated/cdd.created, so a
    25-row page could carry several MB never rendered by the list view
    (metadata only shows once a row is expanded). Confirmed live against
    a real copy of prod data: fetching it for a page filtered to
    content.generated took ~4.7s; the same query without that column took
    ~0.2s, with json.loads/serialization of the column itself negligible
    (~20ms) -- the cost was purely transferring bytes never used. Callers
    that DO need it per row (CSV export) keep the default True.
    """
    q = _base_query(db, select_col=AuditLog,
                    user_id=user_id, action=action, entity_type=entity_type,
                    project_id=project_id, course_id=course_id,
                    date_from=date_from, date_to=date_to)
    if not include_metadata:
        q = q.options(defer(AuditLog.metadata_json))
    return q.order_by(AuditLog.created_at.desc()).offset(offset).limit(limit).all()


def get_audit_log_by_id(db, event_id: int) -> AuditLog | None:
    """Single row, metadata included -- for the on-demand detail fetch."""
    return db.query(AuditLog).filter(AuditLog.id == event_id).first()


def _base_query(
    db,
    *,
    select_col, user_id, action, entity_type, project_id, course_id, date_from, date_to,
):
    q = db.query(select_col)
    if user_id:
        q = q.filter(AuditLog.user_id == user_id)
    if action:
        q = q.filter(AuditLog.action == action)
    if entity_type:
        q = q.filter(AuditLog.entity_type == entity_type)
    if project_id:
        q = q.filter(AuditLog.project_id == project_id)
    if course_id:
        q = q.filter(AuditLog.course_id == course_id)
    if date_from:
        q = q.filter(AuditLog.created_at >= date_from)
    if date_to:
        q = q.filter(AuditLog.created_at <= date_to)
    return q


# ── Filter helpers ────────────────────────────────────────────────────────────

def list_distinct_actors(db) -> list[str]:
    rows = db.query(AuditLog.user_id).distinct().all()
    return sorted({r[0] for r in rows if r[0]})


def list_distinct_actions(db) -> list[str]:
    rows = db.query(AuditLog.action).distinct().all()
    return sorted({r[0] for r in rows if r[0]})


def list_distinct_entity_types(db) -> list[str]:
    rows = db.query(AuditLog.entity_type).distinct().all()
    return sorted({r[0] for r in rows if r[0]})


def export_to_csv_rows(
    db,
    *,
    user_id=None, action=None, entity_type=None,
    project_id=None, course_id=None,
    date_from=None, date_to=None,
    limit: int = 5000,
) -> list[dict]:
    """Return up to `limit` rows as plain dicts for CSV export."""
    records = list_audit_logs(
        db, user_id=user_id, action=action, entity_type=entity_type,
        project_id=project_id, course_id=course_id,
        date_from=date_from, date_to=date_to,
        limit=limit, offset=0,
    )
    return [
        {
            "id":          r.id,
            "user":        r.user_id,
            "action":      r.action,
            "entity_type": r.entity_type or "",
            "entity_id":   r.entity_id or "",
            "project_id":  r.project_id or "",
            "course_id":   r.course_id or "",
            "ip_address":  r.ip_address or "",
            "timestamp":   r.created_at.strftime("%Y-%m-%d %H:%M:%S") if r.created_at else "",
            "metadata":    r.metadata_json or "",
        }
        for r in records
    ]
