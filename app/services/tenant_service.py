"""
Tenant (organization) provisioning and license checks.

A tenant IS a Project. Creating a tenant creates the Project plus an initial
tenant-admin User and their admin membership. Licenses cap the number of
active members per tenant.

Ported/adapted from the prompt-library tenant_service, mapped onto Content AI
Studio's roles (admin | reviewer | author).
"""

from __future__ import annotations

import re

from sqlalchemy.orm import Session

from app.core.config import PLATFORM_SLUG
from app.core.exceptions import NotFoundError, ValidationError

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
