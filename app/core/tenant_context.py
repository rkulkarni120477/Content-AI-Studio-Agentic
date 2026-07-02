"""
Tenant scoping utilities — the single enforcement point for multi-tenant isolation.

Every repository function that reads or writes tenant-owned data must route through
these helpers.  The pattern ensures:
  - Platform admins see all tenants' data (or a single tenant when they pass one).
  - Tenant users only ever see data belonging to their own tenant.
  - Cross-tenant access returns 404, never 403 (don't leak resource existence).

Usage in a route
----------------
    from app.core.tenant_context import apply_tenant_filter, get_scoped_or_404

    def list_docs(db, current_user):
        tid   = tenant_id_from_user(current_user)
        admin = is_platform_admin_user(current_user)
        q = db.query(Document).filter(Document.status == "active")
        return apply_tenant_filter(q, Document, tid, admin).all()
"""

from __future__ import annotations

from sqlalchemy.orm import Query


# ---------------------------------------------------------------------------
# Query scoping
# ---------------------------------------------------------------------------

def apply_tenant_filter(
    q: Query,
    model,
    tenant_id: str | None,
    is_platform_admin: bool = False,
) -> Query:
    """
    Scope a SQLAlchemy query to a tenant.

    Rules
    -----
    - Model has no tenant_id column  → return query unchanged
    - Platform admin + no tenant_id  → return all rows (cross-tenant view)
    - Platform admin + tenant_id     → filter to that tenant
    - Regular user                   → always filter to their tenant_id
    """
    if not hasattr(model, "tenant_id"):
        return q
    if is_platform_admin and not tenant_id:
        return q  # platform admin sees everything unless scoped explicitly
    if tenant_id:
        return q.filter(model.tenant_id == tenant_id)
    return q


def get_scoped_or_404(
    db,
    model,
    pk: int | str,
    tenant_id: str | None,
    is_platform_admin: bool = False,
):
    """
    Fetch one row by primary key, scoped to the current tenant.

    Raises NotFoundError (404) if the row does not exist or belongs to a
    different tenant — intentionally indistinguishable to prevent leaking
    resource existence across tenants.
    """
    from app.core.exceptions import NotFoundError

    q = db.query(model)
    q = apply_tenant_filter(q, model, tenant_id, is_platform_admin)
    obj = q.filter(model.id == pk).first()
    if obj is None:
        raise NotFoundError(model.__tablename__, pk)
    return obj


# ---------------------------------------------------------------------------
# User attribute helpers
# ---------------------------------------------------------------------------

def tenant_id_from_user(current_user) -> str | None:
    """Extract the tenant_id private attribute from an authenticated user."""
    return getattr(current_user, "_tenant_id", None)


def is_platform_admin_user(current_user) -> bool:
    """Return True if the current user is a platform super-admin."""
    return bool(getattr(current_user, "_is_platform_admin", False))


def tenant_slug_from_user(current_user) -> str | None:
    """Extract the tenant_slug private attribute from an authenticated user."""
    return getattr(current_user, "_tenant_slug", None)


# ---------------------------------------------------------------------------
# Write-path helpers
# ---------------------------------------------------------------------------

def assert_project_in_tenant(
    db,
    project_id: int | None,
    tenant_id: str | None,
    is_platform_admin: bool = False,
) -> None:
    """
    Guard for CDD/Blueprint/Generation reads that take a project_id param.

    Platform admins can access any project.
    Tenant users must only access projects that belong to their tenant.
    Raises HTTP 404 (not 403) to avoid leaking project existence.
    """
    if is_platform_admin or project_id is None:
        return
    if not tenant_id:
        return
    from fastapi import HTTPException
    from promptops_app.database import Project
    proj = db.query(Project).filter(
        Project.id == project_id,
        Project.tenant_id == tenant_id,
    ).first()
    if proj is None:
        raise HTTPException(status_code=404, detail="Project not found.")


def effective_tenant_id_for_write(
    current_user,
    body_tenant_id: str | None = None,
) -> str:
    """
    Return the tenant_id to use for a CREATE or UPDATE operation.

    Platform admin:  reads tenant_id from the request body (required — they
                     must explicitly target a tenant when writing data).
    Regular user:    always uses their own tenant_id.

    Raises HTTP 400 if a platform admin omits tenant_id in the body.
    Raises HTTP 403 if a regular user somehow has no tenant context.
    """
    from fastapi import HTTPException

    if is_platform_admin_user(current_user):
        if not body_tenant_id:
            raise HTTPException(
                status_code=400,
                detail="tenant_id is required in the request body for platform admin write operations.",
            )
        return body_tenant_id

    tid = tenant_id_from_user(current_user)
    if not tid:
        raise HTTPException(status_code=403, detail="No tenant context — cannot write.")
    return tid
