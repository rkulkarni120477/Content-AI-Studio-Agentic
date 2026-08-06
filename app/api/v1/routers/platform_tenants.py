"""
Platform Tenants router — organization (tenant) management for the platform
super-admin. A tenant is a Project.

Mounted at /api/v1/platform/tenants. Every endpoint requires
is_platform_admin=True on the authenticated user.

Endpoints
---------
  GET    /platform/tenants                      list all tenants (+ usage)
  POST   /platform/tenants                      create tenant + initial admin
  PUT    /platform/tenants/{id}                 update name/status/license/azure/labels
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
from app.schemas.budget import BudgetPolicyRead, BudgetPolicyUpsertRequest
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
from app.schemas.ui_labels import dump_ui_labels
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
        ui_labels=getattr(project, "ui_labels", None),
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
# Budget policies (P3.1 of claude_plan_platform_hardening)
#
# Registered BEFORE the /{project_id}... routes below on purpose: FastAPI/
# Starlette matches routes in registration order, and a plain PUT /{project_id}
# (the tenant-update route) would otherwise swallow PUT /budgets, matching
# "budgets" as a literal project_id value and 422ing on int conversion instead
# of ever reaching this handler. Confirmed live — this is the fix for that,
# not a defensive guess.
#
# Platform-admin-only for v1 (P3.3) — same _require_platform_admin gate as
# every other endpoint in this file, not a new permission key. Widening to a
# tenant-admin self-manage boundary is a narrower follow-up, not this pass.
# ---------------------------------------------------------------------------

def _as_int(value: str) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        raise ValidationError(f"scope_id must be a numeric id for this scope: {value!r}")


def _validate_budget_scope_id(db: Session, scope: str, scope_id: str) -> None:
    """BudgetPolicy has no DB-level FK (scope_id is polymorphic, matching
    LLMUsageLog's own no-FK convention) — validate it here instead."""
    from promptops_app.database import Course, Project, User

    if scope == "project":
        if db.query(Project).filter(Project.id == _as_int(scope_id)).first() is None:
            raise NotFoundError("Project", scope_id)
    elif scope == "course":
        if db.query(Course).filter(Course.id == _as_int(scope_id)).first() is None:
            raise NotFoundError("Course", scope_id)
    elif scope == "user":
        # A username, not a numeric id — matches LLMUsageLog.user_id/UsageLogContext.user_name.
        if db.query(User).filter(User.username == scope_id).first() is None:
            raise NotFoundError("User", scope_id)


def _assert_can_view_budget_scope(db: Session, current_user, scope: str | None, scope_id: str | None) -> None:
    """Platform admins may list/filter freely. Everyone else must name exactly
    the one scope they themselves belong to (P3.3: managing stays
    platform-admin-only, but viewing your own spend does not)."""
    from promptops_app.database import Course

    if getattr(current_user, "_is_platform_admin", False):
        return
    if not scope or not scope_id:
        raise HTTPException(status_code=403, detail="Platform admin access required to list all budget policies.")

    own_project_id = getattr(current_user, "_project_id", None)
    if scope == "project":
        if own_project_id is None or scope_id != str(own_project_id):
            raise HTTPException(status_code=403, detail="You may only view your own project's budget.")
    elif scope == "course":
        course = db.query(Course).filter(Course.id == _as_int(scope_id)).first()
        if course is None or own_project_id is None or course.project_id != own_project_id:
            raise NotFoundError("Course", scope_id)
    elif scope == "user":
        if scope_id != current_user.username:
            raise HTTPException(status_code=403, detail="You may only view your own budget.")
    else:
        raise HTTPException(status_code=403, detail="Platform admin access required to list all budget policies.")


@router.get("/budgets", response_model=list[BudgetPolicyRead], summary="List budget policies")
def list_budgets(
    scope: str | None = None,
    scope_id: str | None = None,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
) -> list[BudgetPolicyRead]:
    from promptops_app.database import BudgetPolicy
    from promptops_app.services.budget_service import current_period_usage

    _assert_can_view_budget_scope(db, current_user, scope, scope_id)

    q = db.query(BudgetPolicy)
    if scope:
        q = q.filter(BudgetPolicy.scope == scope)
    if scope_id:
        q = q.filter(BudgetPolicy.scope_id == scope_id)

    out = []
    for policy in q.order_by(BudgetPolicy.scope, BudgetPolicy.scope_id).all():
        read = BudgetPolicyRead.model_validate(policy)
        spend, tokens = current_period_usage(db, policy.scope, policy.scope_id, policy.period)
        read.current_spend_usd = round(spend, 4)
        read.current_tokens = tokens
        out.append(read)
    return out


@router.put("/budgets/policy", response_model=BudgetPolicyRead, summary="Set (create or update) a budget policy")
def upsert_budget(
    body: BudgetPolicyUpsertRequest,
    db: Session = Depends(get_db),
    _=Depends(_require_platform_admin),
) -> BudgetPolicyRead:
    from promptops_app.database import BudgetPolicy
    from promptops_app.services.budget_service import current_period_usage

    _validate_budget_scope_id(db, body.scope, body.scope_id)

    policy = db.query(BudgetPolicy).filter(
        BudgetPolicy.scope == body.scope, BudgetPolicy.scope_id == body.scope_id,
    ).first()
    if policy is None:
        policy = BudgetPolicy(scope=body.scope, scope_id=body.scope_id)
        db.add(policy)

    policy.period = body.period
    policy.limit_usd = body.limit_usd
    policy.warn_threshold_pct = body.warn_threshold_pct
    db.commit()
    db.refresh(policy)

    read = BudgetPolicyRead.model_validate(policy)
    spend, tokens = current_period_usage(db, policy.scope, policy.scope_id, policy.period)
    read.current_spend_usd = round(spend, 4)
    read.current_tokens = tokens
    return read


@router.delete("/budgets/{policy_id}", status_code=204, summary="Delete a budget policy")
def delete_budget(
    policy_id: int,
    db: Session = Depends(get_db),
    _=Depends(_require_platform_admin),
) -> None:
    from promptops_app.database import BudgetPolicy

    policy = db.query(BudgetPolicy).filter(BudgetPolicy.id == policy_id).first()
    if policy is None:
        raise NotFoundError("BudgetPolicy", policy_id)
    db.delete(policy)
    db.commit()


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
    if body.ui_labels is not None:
        # Full replace: the Configuration form always submits every box, so a
        # cleared box must actually clear the override. dump_ui_labels drops
        # blanks and unknown keys, and stores NULL when nothing is left.
        project.ui_labels = dump_ui_labels(body.ui_labels)

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
