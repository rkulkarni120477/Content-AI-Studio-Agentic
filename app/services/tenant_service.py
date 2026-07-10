"""
Tenant (organization) provisioning and license checks.

A tenant IS a Project. Creating a tenant creates the Project plus an initial
tenant-admin User and their admin membership. Licenses cap the number of
active members per tenant.

Ported/adapted from the prompt-library tenant_service, mapped onto Content AI
Studio's roles (admin | reviewer | author).
"""

from __future__ import annotations

import json
import re

from sqlalchemy.orm import Session

from app.core.config import PLATFORM_SLUG
from app.core.exceptions import NotFoundError, ValidationError
from app.core.permissions import get_permissions_for_role

# Tenant-scoped roles a member can hold (maps to prompt-library
# tenant_admin/prompt_manager/user). Platform admin is a separate flag.
TENANT_ASSIGNABLE_ROLES = ("admin", "reviewer", "author")
DEFAULT_MEMBER_ROLE = "author"

SLUG_RE = re.compile(r"^[a-z0-9]([a-z0-9-]{0,62}[a-z0-9])?$")


def validate_slug(slug: str) -> str:
    """Normalise + validate an organization code. Raises ValidationError."""
    s = (slug or "").strip().lower()
    if s == PLATFORM_SLUG:
        raise ValidationError("Reserved organization code.")
    if not SLUG_RE.match(s):
        raise ValidationError(
            "Organization code must be lowercase letters, numbers, and hyphens (2-64 chars)."
        )
    return s


def normalize_role(role: str | None) -> str:
    r = (role or "").strip().lower()
    return r if r in TENANT_ASSIGNABLE_ROLES else DEFAULT_MEMBER_ROLE


def active_member_count(db: Session, project_id: int) -> int:
    from promptops_app.database import TenantMembership

    return (
        db.query(TenantMembership)
        .filter(TenantMembership.project_id == project_id, TenantMembership.active.is_(True))
        .count()
    )


def tenant_usage(db: Session, project_id: int) -> dict:
    from promptops_app.database import Project

    project = db.get(Project, project_id)
    if not project:
        raise NotFoundError("Project", project_id)
    return {"active_users": active_member_count(db, project_id), "max_users": project.max_users}


def assert_can_add_member(db: Session, project_id: int) -> None:
    """Raise ValidationError if the tenant is at its license cap."""
    from promptops_app.database import Project

    project = db.get(Project, project_id)
    if not project:
        raise NotFoundError("Project", project_id)
    count = active_member_count(db, project_id)
    if count >= project.max_users:
        raise ValidationError(
            f"User license limit reached ({count}/{project.max_users}). "
            "Contact your platform administrator to increase capacity."
        )


def create_tenant(
    db: Session,
    *,
    slug: str,
    name: str,
    max_users: int,
    created_by: str,
    admin_username: str,
    admin_password_hash: str,
    admin_display_name: str,
):
    """Create a Project (tenant) + its initial admin User + admin membership.

    Returns (project, admin_user). Commits nothing — the caller commits.
    """
    from promptops_app.database import Project, TenantMembership, User

    slug = validate_slug(slug)
    if db.query(Project).filter(Project.slug == slug).first():
        raise ValidationError("Organization code already exists.")
    if int(max_users) < 1:
        raise ValidationError("Max users must be at least 1.")
    # Usernames are globally unique in Content AI Studio.
    if db.query(User).filter(User.username == admin_username).first():
        raise ValidationError(f"Username '{admin_username}' is already taken.")

    project = Project(
        name=name.strip(),
        slug=slug,
        status="active",
        max_users=int(max_users),
        microsoft_auth_enabled=True,
        created_by=created_by,
        is_active=True,
    )
    db.add(project)
    db.flush()  # get project.id

    admin_user = User(
        username=admin_username.strip(),
        password_hash=admin_password_hash,
        display_name=(admin_display_name or admin_username).strip(),
        role="admin",
        project_id=project.id,
        is_active=True,
        is_platform_admin=False,
    )
    db.add(admin_user)
    db.flush()  # get admin_user.id

    db.add(TenantMembership(
        user_id=admin_user.id,
        project_id=project.id,
        role="admin",
        active=True,
        created_by=created_by,
    ))
    db.flush()
    return project, admin_user


# ---------------------------------------------------------------------------
# Custom roles — per-tenant, DB-defined permission bundles.
#
# System roles (admin/reviewer/author) are NOT rows in tenant_roles; they stay
# the static catalog in app/core/permissions.py. This section only handles
# platform-admin-authored custom roles scoped to one tenant each.
# ---------------------------------------------------------------------------

SYSTEM_ROLE_LABELS = {
    "admin":    "Tenant Admin",
    "reviewer": "Prompt Manager",
    "author":   "User",
}

_KEY_RE = re.compile(r"[^a-z0-9]+")


def slugify_role_key(name: str) -> str:
    key = _KEY_RE.sub("_", (name or "").strip().lower()).strip("_")
    return key or "role"


def resolve_membership_effective(db: Session, membership) -> tuple[str, list[str], int | None]:
    """Returns (role_for_token, permissions_list, custom_role_id) for a membership.

    If the membership points at a custom role, permissions come from that
    role's stored permission list and the token role becomes the "custom"
    sentinel. Otherwise this is just the existing system-role lookup.
    """
    if membership.custom_role_id:
        from promptops_app.database import TenantRole

        custom = db.get(TenantRole, membership.custom_role_id)
        if custom is not None:
            return "custom", sorted(json.loads(custom.permissions or "[]")), custom.id
    return membership.role, get_permissions_for_role(membership.role), None


def list_tenant_roles(db: Session, project_id: int) -> list[dict]:
    """Return system roles (synthesized) + custom roles (DB rows) for a tenant."""
    from promptops_app.database import TenantMembership, TenantRole

    system_rows = [
        {
            "id": None,
            "key": role_key,
            "name": label,
            "description": f"System role: {role_key}",
            "type": "system",
            "permissions": get_permissions_for_role(role_key),
        }
        for role_key, label in SYSTEM_ROLE_LABELS.items()
    ]

    custom_rows = (
        db.query(TenantRole)
        .filter(TenantRole.project_id == project_id)
        .order_by(TenantRole.name)
        .all()
    )
    custom_out = [
        {
            "id": r.id,
            "key": r.key,
            "name": r.name,
            "description": r.description,
            "type": "custom",
            "permissions": json.loads(r.permissions or "[]"),
        }
        for r in custom_rows
    ]
    return system_rows + custom_out


def validate_permission_keys(keys: list[str]) -> list[str]:
    """Raise ValidationError if any permission key isn't in the real catalog."""
    from app.core.permissions import _PERMISSIONS  # noqa: SLF001 — intentional catalog access

    unknown = sorted(set(keys) - set(_PERMISSIONS.keys()))
    if unknown:
        raise ValidationError(f"Unknown permission key(s): {', '.join(unknown)}")
    return sorted(set(keys))


def create_custom_role(db: Session, *, project_id: int, name: str, description: str | None,
                        permissions: list[str], created_by: str):
    from promptops_app.database import TenantRole

    name = (name or "").strip()
    if not name:
        raise ValidationError("Role name is required.")
    perms = validate_permission_keys(permissions or [])

    key = base_key = slugify_role_key(name)
    suffix = 2
    while db.query(TenantRole).filter(TenantRole.project_id == project_id, TenantRole.key == key).first():
        key = f"{base_key}_{suffix}"
        suffix += 1

    role = TenantRole(
        project_id=project_id,
        key=key,
        name=name,
        description=(description or "").strip() or None,
        permissions=json.dumps(perms),
        created_by=created_by,
    )
    db.add(role)
    db.flush()
    return role


def get_custom_role_or_404(db: Session, project_id: int, role_id: int):
    from promptops_app.database import TenantRole

    role = (
        db.query(TenantRole)
        .filter(TenantRole.id == role_id, TenantRole.project_id == project_id)
        .first()
    )
    if role is None:
        raise NotFoundError("Role", role_id)
    return role


def update_custom_role(db: Session, role, *, name: str | None, description: str | None,
                        permissions: list[str] | None):
    if name is not None:
        name = name.strip()
        if not name:
            raise ValidationError("Role name is required.")
        role.name = name
    if description is not None:
        role.description = description.strip() or None
    if permissions is not None:
        role.permissions = json.dumps(validate_permission_keys(permissions))
    db.flush()
    return role


def delete_custom_role(db: Session, role) -> None:
    from promptops_app.database import TenantMembership

    in_use = (
        db.query(TenantMembership)
        .filter(TenantMembership.custom_role_id == role.id)
        .count()
    )
    if in_use:
        raise ValidationError(
            f"This role is assigned to {in_use} member(s). Reassign them before deleting it."
        )
    db.delete(role)
