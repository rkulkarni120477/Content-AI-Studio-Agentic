"""
Tenant context — project-as-tenant isolation layer.

Every non-platform-admin User belongs to a single Project (User.project_id).
That Project IS the tenant: a regular user only ever sees data belonging to
their own project. Platform admins (is_platform_admin=True, project_id=None)
see everything unless they explicitly scope a request to one project.

Scoping strategy (apply_tenant_filter)
---------------------------------------
Different tables carry the tenant boundary on different columns:
  - Project itself        -> filter on Project.id (the tenant's own identity)
  - Cluster/Course/User/…  -> filter on their integer ``project_id`` FK
  - Document/Style/Prompt  -> filter on their legacy string ``tenant_id``
                              column (no project_id FK exists on these
                              tables); we store str(project_id) into it.
                              NULL tenant_id rows (system-seeded defaults,
                              and everything created before tenant isolation
                              existed) are treated as shared/global and stay
                              visible to every tenant — only rows explicitly
                              tagged with a *different* project are excluded.
  - CentralRepository      -> never filtered — it is a platform-wide shared
                              library, intentionally visible across projects
"""

from __future__ import annotations

from sqlalchemy import false, or_
from sqlalchemy.orm import Query


def _as_int(value) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def apply_tenant_filter(q: Query, model, tenant_id=None, is_platform_admin: bool = False) -> Query:
    """Scope a query to the caller's project (tenant).

    Platform admin with no explicit tenant_id sees every row. A resolvable
    tenant_id restricts to that project's rows. A user with no tenant_id
    (unassigned, awaiting assignment) matches nothing.
    """
    from promptops_app.database import CentralRepository, Project

    if model is CentralRepository:
        # Central Repository is a platform-wide shared library — not tenant-scoped.
        return q

    if is_platform_admin and tenant_id is None:
        return q

    pid = _as_int(tenant_id)
    if pid is None:
        return q.filter(false())

    if model is Project:
        return q.filter(Project.id == pid)
    if hasattr(model, "project_id"):
        return q.filter(model.project_id == pid)
    if hasattr(model, "tenant_id"):
        # NULL tenant_id = shared/global content (system defaults, pre-tenancy
        # rows) — visible to every tenant, not just an accidental leak.
        return q.filter(or_(model.tenant_id.is_(None), model.tenant_id == str(pid)))
    return q


def get_scoped_or_404(db, model, pk, tenant_id=None, is_platform_admin: bool = False):
    """Fetch one row by primary key, scoped to tenant; raises NotFoundError (404) if missing."""
    from app.core.exceptions import NotFoundError

    q = apply_tenant_filter(db.query(model), model, tenant_id, is_platform_admin)
    obj = q.filter(model.id == pk).first()
    if obj is None:
        raise NotFoundError(model.__tablename__, pk)
    return obj


def tenant_id_from_user(current_user) -> str | None:
    """Return the caller's tenant identifier (their project id, as a string).

    None for a platform admin (sees all) or for a regular user not yet
    assigned to a project (awaiting assignment).
    """
    if bool(getattr(current_user, "_is_platform_admin", False)):
        return None
    pid = getattr(current_user, "_project_id", None)
    return str(pid) if pid is not None else None


def is_platform_admin_user(current_user) -> bool:
    """Return True if the current user is a platform super-admin."""
    return bool(getattr(current_user, "_is_platform_admin", False))


def tenant_slug_from_user(current_user) -> None:
    """Always returns None — no slug concept in the project-as-tenant model."""
    return None


def assert_project_in_tenant(db, project_id, tenant_id=None, is_platform_admin: bool = False) -> None:
    """Raise NotFoundError (404) if project_id does not belong to the caller's tenant.

    A None project_id means there is nothing to check (e.g. an optional
    filter query param was omitted) and is always allowed through.
    """
    if project_id is None:
        return
    if is_platform_admin:
        return

    from app.core.exceptions import NotFoundError

    pid = _as_int(tenant_id)
    if pid is None or pid != _as_int(project_id):
        raise NotFoundError("Project", project_id)


def effective_tenant_id_for_write(current_user, body_tenant_id=None) -> str | None:
    """Return the tenant id (project id, as a string) to stamp on a new row.

    Platform admin: an explicit override (e.g. from the request body) or None.
    Regular user: always their own project id — a body override is ignored so
    a regular user can never write into another tenant.
    """
    if bool(getattr(current_user, "_is_platform_admin", False)):
        pid = _as_int(body_tenant_id)
        return str(pid) if pid is not None else None
    pid = getattr(current_user, "_project_id", None)
    return str(pid) if pid is not None else None
