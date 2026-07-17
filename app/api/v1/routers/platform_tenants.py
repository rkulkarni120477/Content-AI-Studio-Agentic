"""
Platform Tenants router — organization (tenant) management for the platform
super-admin. A tenant is a Project.

Mounted at /api/v1/platform/tenants. Every endpoint requires
is_platform_admin=True on the authenticated user.

Endpoints
---------
  GET    /platform/tenants                      list all tenants (+ usage)
  POST   /platform/tenants                      create tenant + initial admin
  PUT    /platform/tenants/{id}                 update name/status/license/azure
  GET    /platform/tenants/{id}/usage           active_users / max_users
  GET    /platform/tenants/{id}/users           list members
  POST   /platform/tenants/{id}/users           add a member (username+password)
  PUT    /platform/tenants/{id}/users/{user_id} edit role/display/password/active
  DELETE /platform/tenants/{id}/users/{user_id} remove membership
"""

from __future__ import annotations

import json
import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.dependencies import get_current_user, get_db
from app.core.exceptions import NotFoundError, ValidationError
from app.core.permission_catalog import PERMISSION_CATEGORIES
from app.core.permissions import role_label
from app.core.security import hash_password
from app.schemas.tenant import (
    TenantCreateRequest,
    TenantCreateResponse,
    TenantMemberCreateRequest,
    TenantMemberRead,
    TenantMemberUpdateRequest,
    TenantRead,
    TenantRoleCreateRequest,
    TenantRoleRead,
    TenantRoleUpdateRequest,
    TenantUpdateRequest,
    TenantUsageResponse,
)
from app.services import tenant_service

_log = logging.getLogger(__name__)
router = APIRouter()


# ---------------------------------------------------------------------------
# Guard
# ---------------------------------------------------------------------------

def _require_platform_admin(current_user=Depends(get_current_user)):
    if not getattr(current_user, "_is_platform_admin", False):
        raise HTTPException(status_code=403, detail="Platform admin access required.")
    return current_user


# ---------------------------------------------------------------------------
# Serializers
# ---------------------------------------------------------------------------

def _tenant_read(db: Session, project) -> TenantRead:
    return TenantRead(
        id=project.id,
        slug=project.slug,
        name=project.name,
        client_name=project.client_name,
        status=project.status or "active",
        max_users=project.max_users or 50,
        microsoft_auth_enabled=bool(getattr(project, "microsoft_auth_enabled", True)),
        allowed_email_domains=project.allowed_email_domains or None,
        active_users=tenant_service.active_member_count(db, project.id),
        created_at=project.created_at,
        created_by=project.created_by,
    )


def _member_read(user, membership) -> TenantMemberRead:
    role_display = role_label(membership.role)
    if membership.custom_role_id and membership.custom_role is not None:
        role_display = membership.custom_role.name
    return TenantMemberRead(
        user_id=user.id,
        username=user.username,
        display_name=user.display_name or user.username,
        role=membership.role,
        role_display=role_display,
        custom_role_id=membership.custom_role_id,
        active=bool(membership.active),
        email=user.email,
        auth_provider="microsoft" if user.microsoft_oid else "local",
    )


def _get_tenant_or_404(db: Session, project_id: int):
    from promptops_app.database import Project

    project = db.get(Project, project_id)
    if project is None:
        raise NotFoundError("Project", project_id)
    return project


def _get_membership_or_404(db: Session, project_id: int, user_id: int):
    from promptops_app.database import TenantMembership

    m = (
        db.query(TenantMembership)
        .filter(TenantMembership.project_id == project_id, TenantMembership.user_id == user_id)
        .first()
    )
    if m is None:
        raise NotFoundError("Membership", user_id)
    return m


# ---------------------------------------------------------------------------
# Tenant CRUD
# ---------------------------------------------------------------------------

@router.get("", response_model=list[TenantRead], summary="List all tenants")
def list_tenants(
    db: Session = Depends(get_db),
    _=Depends(_require_platform_admin),
) -> list[TenantRead]:
    from promptops_app.database import Project

    projects = db.query(Project).filter(Project.is_active == True).order_by(Project.name).all()  # noqa: E712
    return [_tenant_read(db, p) for p in projects]


@router.post("", response_model=TenantCreateResponse, status_code=201, summary="Create a tenant")
def create_tenant(
    body: TenantCreateRequest,
    db: Session = Depends(get_db),
    current_user=Depends(_require_platform_admin),
) -> TenantCreateResponse:
    try:
        project, admin_user = tenant_service.create_tenant(
            db,
            slug=body.slug,
            name=body.name,
            max_users=body.max_users,
            created_by=current_user.username,
            admin_username=body.admin_username,
            admin_password_hash=hash_password(body.admin_password),
            admin_display_name=body.admin_display_name or body.admin_username,
            client_name=body.client_name,
        )
        db.commit()
    except ValidationError:
        db.rollback()
        raise
    except Exception as exc:  # pragma: no cover
        db.rollback()
        _log.exception("tenant_create_failed")
        raise HTTPException(status_code=500, detail=f"Failed to create tenant: {exc}")

    db.refresh(project)
    _log.info("tenant_created  by=%s  slug=%s  admin=%s",
              current_user.username, project.slug, admin_user.username)
    base = _tenant_read(db, project)
    return TenantCreateResponse(**base.model_dump(), initial_admin_username=admin_user.username)


@router.put("/{project_id}", response_model=TenantRead, summary="Update a tenant")
def update_tenant(
    project_id: int,
    body: TenantUpdateRequest,
    db: Session = Depends(get_db),
    _=Depends(_require_platform_admin),
) -> TenantRead:
    project = _get_tenant_or_404(db, project_id)

    if body.name is not None:
        project.name = body.name.strip()
    if body.client_name is not None:
        project.client_name = body.client_name.strip() or None
    if body.status is not None:
        if body.status not in ("active", "suspended"):
            raise ValidationError("status must be 'active' or 'suspended'.")
        project.status = body.status
    if body.max_users is not None:
        project.max_users = int(body.max_users)
    if body.microsoft_auth_enabled is not None:
        project.microsoft_auth_enabled = bool(body.microsoft_auth_enabled)
    if body.allowed_email_domains is not None:
        project.allowed_email_domains = body.allowed_email_domains.strip() or None
    if body.azure_tenant_id is not None:
        project.azure_tenant_id = body.azure_tenant_id.strip() or None
    if body.azure_client_id is not None:
        project.azure_client_id = body.azure_client_id.strip() or None
    if body.azure_client_secret is not None and body.azure_client_secret.strip():
        project.azure_client_secret = body.azure_client_secret.strip()
    if body.azure_new_user_role is not None:
        role = body.azure_new_user_role.strip() or None
        if role and role not in tenant_service.TENANT_ASSIGNABLE_ROLES:
            raise ValidationError("azure_new_user_role must be admin, reviewer, or author.")
        project.azure_new_user_role = role

    db.commit()
    db.refresh(project)
    _log.info("tenant_updated  project_id=%d", project_id)
    return _tenant_read(db, project)


@router.get("/{project_id}/usage", response_model=TenantUsageResponse, summary="Tenant license usage")
def tenant_usage(
    project_id: int,
    db: Session = Depends(get_db),
    _=Depends(_require_platform_admin),
) -> TenantUsageResponse:
    _get_tenant_or_404(db, project_id)
    return TenantUsageResponse(**tenant_service.tenant_usage(db, project_id))


# ---------------------------------------------------------------------------
# Tenant members
# ---------------------------------------------------------------------------

@router.get("/{project_id}/users", response_model=list[TenantMemberRead], summary="List tenant members")
def list_members(
    project_id: int,
    db: Session = Depends(get_db),
    _=Depends(_require_platform_admin),
) -> list[TenantMemberRead]:
    from promptops_app.database import TenantMembership, User

    _get_tenant_or_404(db, project_id)
    rows = (
        db.query(TenantMembership, User)
        .join(User, User.id == TenantMembership.user_id)
        .filter(TenantMembership.project_id == project_id)
        .order_by(User.username)
        .all()
    )
    return [_member_read(user, m) for (m, user) in rows]


@router.post("/{project_id}/users", response_model=TenantMemberRead, status_code=201, summary="Add a tenant member")
def add_member(
    project_id: int,
    body: TenantMemberCreateRequest,
    db: Session = Depends(get_db),
    current_user=Depends(_require_platform_admin),
) -> TenantMemberRead:
    from promptops_app.database import TenantMembership, User

    _get_tenant_or_404(db, project_id)
    tenant_service.assert_can_add_member(db, project_id)

    custom_role = None
    if body.custom_role_id is not None:
        custom_role = tenant_service.get_custom_role_or_404(db, project_id, body.custom_role_id)
        role = "custom"
    else:
        role = tenant_service.normalize_role(body.role)

    # Usernames are globally unique. Reuse an existing user if the same username
    # exists; otherwise create one. Then attach a membership for this tenant.
    user = db.query(User).filter(User.username == body.username.strip()).first()
    if user is None:
        user = User(
            username=body.username.strip(),
            password_hash=hash_password(body.password),
            display_name=(body.display_name or body.username).strip(),
            role=role,
            project_id=project_id,
            is_active=True,
            is_platform_admin=False,
        )
        db.add(user)
        db.flush()
    else:
        if db.query(TenantMembership).filter(
            TenantMembership.project_id == project_id, TenantMembership.user_id == user.id
        ).first():
            raise ValidationError(f"User '{user.username}' is already a member of this tenant.")

    membership = TenantMembership(
        user_id=user.id, project_id=project_id, role=role, active=True,
        custom_role_id=(custom_role.id if custom_role else None),
        created_by=current_user.username,
    )
    db.add(membership)
    db.commit()
    db.refresh(membership)
    db.refresh(user)
    _log.info("member_added  by=%s  project_id=%d  username=%s  role=%s",
              current_user.username, project_id, user.username, role)
    return _member_read(user, membership)


@router.put("/{project_id}/users/{user_id}", response_model=TenantMemberRead, summary="Edit a tenant member")
def update_member(
    project_id: int,
    user_id: int,
    body: TenantMemberUpdateRequest,
    db: Session = Depends(get_db),
    _=Depends(_require_platform_admin),
) -> TenantMemberRead:
    from promptops_app.database import User

    _get_tenant_or_404(db, project_id)
    membership = _get_membership_or_404(db, project_id, user_id)
    user = db.get(User, user_id)
    if user is None:
        raise NotFoundError("User", user_id)

    if body.display_name is not None:
        user.display_name = body.display_name.strip() or user.username
    if body.password:
        user.password_hash = hash_password(body.password)
    if body.custom_role_id is not None:
        custom_role = tenant_service.get_custom_role_or_404(db, project_id, body.custom_role_id)
        membership.custom_role_id = custom_role.id
        membership.role = "custom"
    elif body.role is not None:
        membership.role = tenant_service.normalize_role(body.role)
        membership.custom_role_id = None
    if body.active is not None:
        if body.active and not membership.active:
            tenant_service.assert_can_add_member(db, project_id)
        membership.active = bool(body.active)

    db.commit()
    db.refresh(membership)
    db.refresh(user)
    _log.info("member_updated  project_id=%d  user_id=%d", project_id, user_id)
    return _member_read(user, membership)


@router.delete("/{project_id}/users/{user_id}", status_code=204, summary="Remove a tenant member")
def delete_member(
    project_id: int,
    user_id: int,
    db: Session = Depends(get_db),
    _=Depends(_require_platform_admin),
) -> None:
    from promptops_app.database import TenantMembership, User

    _get_tenant_or_404(db, project_id)
    membership = _get_membership_or_404(db, project_id, user_id)
    db.delete(membership)

    # If this user has no other memberships, is not a platform admin, and has no
    # password (Microsoft-only), remove the orphaned user row too.
    user = db.get(User, user_id)
    remaining = (
        db.query(TenantMembership)
        .filter(TenantMembership.user_id == user_id, TenantMembership.project_id != project_id)
        .count()
    )
    if user and remaining == 0 and not user.is_platform_admin and not user.password_hash:
        db.delete(user)

    db.commit()
    _log.info("member_removed  project_id=%d  user_id=%d", project_id, user_id)


# ---------------------------------------------------------------------------
# Permission catalog (shared reference for the custom-role builder UI)
# ---------------------------------------------------------------------------

@router.get("/permission-catalog", summary="Real permission catalog, grouped by category")
def get_permission_catalog(_=Depends(_require_platform_admin)) -> list[dict]:
    return PERMISSION_CATEGORIES


# ---------------------------------------------------------------------------
# Tenant custom roles
# ---------------------------------------------------------------------------

def _role_read(row: dict) -> TenantRoleRead:
    return TenantRoleRead(**row)


@router.get("/{project_id}/roles", response_model=list[TenantRoleRead], summary="List tenant roles (system + custom)")
def list_roles(
    project_id: int,
    db: Session = Depends(get_db),
    _=Depends(_require_platform_admin),
) -> list[TenantRoleRead]:
    _get_tenant_or_404(db, project_id)
    return [_role_read(r) for r in tenant_service.list_tenant_roles(db, project_id)]


@router.post("/{project_id}/roles", response_model=TenantRoleRead, status_code=201, summary="Create a custom role")
def create_role(
    project_id: int,
    body: TenantRoleCreateRequest,
    db: Session = Depends(get_db),
    current_user=Depends(_require_platform_admin),
) -> TenantRoleRead:
    _get_tenant_or_404(db, project_id)
    role = tenant_service.create_custom_role(
        db, project_id=project_id, name=body.name, description=body.description,
        permissions=body.permissions, created_by=current_user.username,
    )
    db.commit()
    db.refresh(role)
    _log.info("tenant_role_created  by=%s  project_id=%d  key=%s",
              current_user.username, project_id, role.key)
    return TenantRoleRead(
        id=role.id, key=role.key, name=role.name, description=role.description,
        type="custom", permissions=sorted(json.loads(role.permissions)),
    )


@router.put("/{project_id}/roles/{role_id}", response_model=TenantRoleRead, summary="Edit a custom role")
def update_role(
    project_id: int,
    role_id: int,
    body: TenantRoleUpdateRequest,
    db: Session = Depends(get_db),
    _=Depends(_require_platform_admin),
) -> TenantRoleRead:
    _get_tenant_or_404(db, project_id)
    role = tenant_service.get_custom_role_or_404(db, project_id, role_id)
    tenant_service.update_custom_role(
        db, role, name=body.name, description=body.description, permissions=body.permissions,
    )
    db.commit()
    db.refresh(role)
    _log.info("tenant_role_updated  project_id=%d  role_id=%d", project_id, role_id)
    return TenantRoleRead(
        id=role.id, key=role.key, name=role.name, description=role.description,
        type="custom", permissions=sorted(json.loads(role.permissions)),
    )


@router.delete("/{project_id}/roles/{role_id}", status_code=204, summary="Delete a custom role")
def delete_role(
    project_id: int,
    role_id: int,
    db: Session = Depends(get_db),
    _=Depends(_require_platform_admin),
) -> None:
    _get_tenant_or_404(db, project_id)
    role = tenant_service.get_custom_role_or_404(db, project_id, role_id)
    tenant_service.delete_custom_role(db, role)
    db.commit()
    _log.info("tenant_role_deleted  project_id=%d  role_id=%d", project_id, role_id)
