"""
Platform Tenant Management router — platform super-admin only.

All endpoints in this file require the caller to be a platform admin
(is_platform_admin=True in the JWT).  Regular tenant users cannot reach any
of these endpoints — they will receive a 403 before any business logic runs.

Mount point: /api/v1/platform/tenants
"""

from __future__ import annotations

import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.dependencies import get_current_user, get_db
from app.core.security import hash_password
from app.schemas.tenant import (
    TenantCreateRequest,
    TenantRead,
    TenantUpdateRequest,
    TenantUserCreateRequest,
    TenantUserUpdateRequest,
    TenantUsageResponse,
)

_log = logging.getLogger(__name__)

router = APIRouter()


# ---------------------------------------------------------------------------
# Internal guard — called at the top of every endpoint
# ---------------------------------------------------------------------------

def _require_platform_admin(current_user) -> None:
    if not getattr(current_user, "_is_platform_admin", False):
        raise HTTPException(status_code=403, detail="Platform admin access required.")


def _tenant_or_404(db: Session, tenant_id: str):
    from promptops_app.database import Tenant
    tenant = db.query(Tenant).filter(Tenant.id == tenant_id).first()
    if tenant is None:
        raise HTTPException(status_code=404, detail=f"Tenant '{tenant_id}' not found.")
    return tenant


def _tenant_dict(tenant, db: Session) -> TenantRead:
    from promptops_app.database import User
    active_users = db.query(User).filter(
        User.tenant_id == tenant.id, User.is_active == True,
    ).count()
    return TenantRead(
        id=tenant.id,
        slug=tenant.slug,
        name=tenant.name,
        max_users=tenant.max_users,
        status=tenant.status,
        active_users=active_users,
        created_at=tenant.created_at,
        created_by=tenant.created_by,
    )


# ---------------------------------------------------------------------------
# Tenant CRUD
# ---------------------------------------------------------------------------

@router.get(
    "",
    response_model=list[TenantRead],
    summary="List all tenants",
)
def list_tenants(
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
) -> list[TenantRead]:
    """Return all tenants ordered by name. Platform admin only."""
    from promptops_app.database import Tenant

    _require_platform_admin(current_user)
    tenants = db.query(Tenant).order_by(Tenant.name).all()
    return [_tenant_dict(t, db) for t in tenants]


@router.post(
    "",
    response_model=TenantRead,
    status_code=201,
    summary="Create a tenant with an initial admin user",
)
def create_tenant(
    body: TenantCreateRequest,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
) -> TenantRead:
    """
    Create a new tenant and its initial admin user in one atomic operation.

    The slug must be unique, lowercase, and must not be "platform" (reserved).
    """
    from promptops_app.database import Tenant, User

    _require_platform_admin(current_user)

    slug = body.slug.strip().lower()
    if slug == "platform":
        raise HTTPException(status_code=400, detail="'platform' is a reserved organisation code.")
    if db.query(Tenant).filter(Tenant.slug == slug).first():
        raise HTTPException(status_code=409, detail=f"Organisation code '{slug}' already exists.")

    tenant = Tenant(
        id=str(uuid.uuid4()),
        slug=slug,
        name=body.name,
        max_users=body.max_users,
        status="active",
        created_by=current_user.username,
    )
    db.add(tenant)
    db.flush()  # assigns tenant.id before creating the admin user

    admin_user = User(
        username=body.admin_username,
        password_hash=hash_password(body.admin_password),
        role="admin",
        tenant_id=tenant.id,
        is_active=True,
        is_platform_admin=False,
    )
    db.add(admin_user)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        detail = str(exc.orig) if exc.orig else str(exc)
        if "username" in detail.lower():
            raise HTTPException(
                status_code=409,
                detail=f"Username '{body.admin_username}' is already taken. Choose a different admin username.",
            )
        raise HTTPException(status_code=409, detail="A duplicate value violates a unique constraint.")
    db.refresh(tenant)

    _log.info("tenant_created  by=%s  slug=%s  id=%s", current_user.username, slug, tenant.id)
    return _tenant_dict(tenant, db)


@router.put(
    "/{tenant_id}",
    response_model=TenantRead,
    summary="Update a tenant's name, max_users, or status",
)
def update_tenant(
    tenant_id: str,
    body: TenantUpdateRequest,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
) -> TenantRead:
    """Update tenant metadata. Platform admin only."""
    _require_platform_admin(current_user)
    tenant = _tenant_or_404(db, tenant_id)

    if body.name is not None:
        tenant.name = body.name
    if body.max_users is not None:
        tenant.max_users = body.max_users
    if body.status in ("active", "suspended"):
        tenant.status = body.status

    db.commit()
    db.refresh(tenant)
    _log.info("tenant_updated  by=%s  tenant_id=%s", current_user.username, tenant_id)
    return _tenant_dict(tenant, db)


# ---------------------------------------------------------------------------
# Tenant user management
# ---------------------------------------------------------------------------

@router.get(
    "/{tenant_id}/users",
    summary="List all users in a tenant",
)
def list_tenant_users(
    tenant_id: str,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Return all non-platform-admin users belonging to this tenant."""
    from promptops_app.database import User
    from app.core.permissions import role_label

    _require_platform_admin(current_user)
    _tenant_or_404(db, tenant_id)

    users = (
        db.query(User)
        .filter(User.tenant_id == tenant_id, User.is_platform_admin == False)
        .order_by(User.username)
        .all()
    )
    return [
        {
            "id":           u.id,
            "username":     u.username,
            "role":         u.role,
            "role_display": role_label(u.role),
            "is_active":    u.is_active,
        }
        for u in users
    ]


@router.post(
    "/{tenant_id}/users",
    status_code=201,
    summary="Create a user in a specific tenant",
)
def create_tenant_user(
    tenant_id: str,
    body: TenantUserCreateRequest,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Create a new user inside a specific tenant. Enforces max_users license."""
    from promptops_app.database import Tenant, User

    _require_platform_admin(current_user)
    tenant = _tenant_or_404(db, tenant_id)

    # License check.
    active_count = db.query(User).filter(
        User.tenant_id == tenant_id, User.is_active == True,
    ).count()
    if active_count >= tenant.max_users:
        raise HTTPException(status_code=403, detail="User license limit reached for this organisation.")

    # Duplicate username check within tenant.
    if db.query(User).filter(User.username == body.username, User.tenant_id == tenant_id).first():
        raise HTTPException(status_code=409, detail=f"Username '{body.username}' already exists in this tenant.")

    user = User(
        username=body.username,
        password_hash=hash_password(body.password),
        role=body.role,
        tenant_id=tenant_id,
        is_active=True,
        is_platform_admin=False,
    )
    db.add(user)
    db.commit()
    db.refresh(user)

    _log.info("tenant_user_created  by=%s  tenant_id=%s  username=%s", current_user.username, tenant_id, body.username)
    return {"id": user.id, "username": user.username, "role": user.role, "tenant_id": tenant_id}


@router.put(
    "/{tenant_id}/users/{username}",
    summary="Update a user in a specific tenant",
)
def update_tenant_user(
    tenant_id: str,
    username: str,
    body: TenantUserUpdateRequest,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Update role, password, active status, or display name for a tenant user."""
    from promptops_app.database import User

    _require_platform_admin(current_user)
    _tenant_or_404(db, tenant_id)

    user = db.query(User).filter(User.username == username, User.tenant_id == tenant_id).first()
    if user is None:
        raise HTTPException(status_code=404, detail=f"User '{username}' not found in this tenant.")

    if body.role is not None:
        user.role = body.role
    if body.password is not None:
        user.password_hash = hash_password(body.password)
    if body.is_active is not None:
        user.is_active = body.is_active

    db.commit()
    db.refresh(user)
    _log.info("tenant_user_updated  by=%s  tenant_id=%s  username=%s", current_user.username, tenant_id, username)
    return {"username": user.username, "role": user.role, "is_active": user.is_active}


@router.delete(
    "/{tenant_id}/users/{username}",
    status_code=204,
    summary="Remove a user from a tenant",
)
def delete_tenant_user(
    tenant_id: str,
    username: str,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
) -> None:
    """Permanently delete a user from a tenant. Platform admin only."""
    from promptops_app.database import User

    _require_platform_admin(current_user)
    _tenant_or_404(db, tenant_id)

    user = db.query(User).filter(User.username == username, User.tenant_id == tenant_id).first()
    if user is None:
        raise HTTPException(status_code=404, detail=f"User '{username}' not found in this tenant.")

    db.delete(user)
    db.commit()
    _log.info("tenant_user_deleted  by=%s  tenant_id=%s  username=%s", current_user.username, tenant_id, username)


# ---------------------------------------------------------------------------
# Tenant usage
# ---------------------------------------------------------------------------

@router.get(
    "/{tenant_id}/usage",
    response_model=TenantUsageResponse,
    summary="Get license usage for a tenant",
)
def get_tenant_usage(
    tenant_id: str,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
) -> TenantUsageResponse:
    """Return active_users, max_users, and usage_percent for a tenant."""
    from promptops_app.database import User

    _require_platform_admin(current_user)
    tenant = _tenant_or_404(db, tenant_id)

    active_users = db.query(User).filter(
        User.tenant_id == tenant_id, User.is_active == True,
    ).count()
    usage_percent = round((active_users / tenant.max_users) * 100, 1) if tenant.max_users else 0.0

    return TenantUsageResponse(
        active_users=active_users,
        max_users=tenant.max_users,
        usage_percent=usage_percent,
    )
