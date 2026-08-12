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
    client_name: str = "",
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
        # Client decides which Source Library content the org's members can
        # access (via membership-based DIS access resolution). Never leave empty.
        client_name=(client_name or "").strip() or None,
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


def hard_delete_tenant(db: Session, project) -> None:
    """Permanently delete a tenant (Project) and everything scoped under it.

    Per-course content (blocks, generations, blueprints, CDDs, imports, etc.)
    is purged via course_repository.purge_course, which already implements
    that full cascade (and commits per course — not one atomic transaction
    for the whole tenant, matching purge_course's own existing behavior
    elsewhere). What's left here is everything scoped directly to the
    project or one of its clusters rather than to a specific course:
    clusters, memberships, custom roles, the legacy project_user_assignments
    grant table, and project-level (course_id IS NULL) fixings/preferences/
    history/jobs/feedback.

    LLM usage logs, audit log entries, and budget policies/spend rows are
    deliberately left untouched — retained for billing/compliance history
    even after the tenant itself is gone. They carry a bare project_id/
    scope_id with no FK, so nothing breaks either way.
    """
    from promptops_app.database import (
        CentralRepository, Cluster, Course, FeedbackDocument, FeedbackItem,
        GenerationJob, ProjectUserAssignment, PromptFixing, TenantMembership,
        TenantRole, User, UserPromptHistory, UserPromptPreference,
    )
    from promptops_app.repositories.course_repository import purge_course

    project_id = project.id

    course_ids = [c.id for c in db.query(Course.id).filter(Course.project_id == project_id).all()]
    for course_id in course_ids:
        purge_course(db, course_id)

    cluster_ids = [c.id for c in db.query(Cluster.id).filter(Cluster.project_id == project_id).all()]

    # Project-level-only rows (course_id IS NULL) — course-scoped rows were
    # already handled per-course by purge_course above.
    db.query(PromptFixing).filter(
        PromptFixing.project_id == project_id, PromptFixing.course_id.is_(None),
    ).delete(synchronize_session=False)
    db.query(UserPromptPreference).filter(
        UserPromptPreference.project_id == project_id, UserPromptPreference.course_id.is_(None),
    ).delete(synchronize_session=False)
    db.query(UserPromptHistory).filter(
        UserPromptHistory.project_id == project_id, UserPromptHistory.course_id.is_(None),
    ).delete(synchronize_session=False)
    db.query(GenerationJob).filter(
        GenerationJob.project_id == project_id, GenerationJob.course_id.is_(None),
    ).delete(synchronize_session=False)

    fb_doc_ids = [
        d.id for d in db.query(FeedbackDocument.id)
        .filter(FeedbackDocument.project_id == project_id, FeedbackDocument.course_id.is_(None))
        .all()
    ]
    if fb_doc_ids:
        db.query(FeedbackItem).filter(FeedbackItem.document_id.in_(fb_doc_ids)).delete(
            synchronize_session=False
        )
    db.query(FeedbackItem).filter(
        FeedbackItem.project_id == project_id, FeedbackItem.course_id.is_(None),
    ).delete(synchronize_session=False)
    db.query(FeedbackDocument).filter(
        FeedbackDocument.project_id == project_id, FeedbackDocument.course_id.is_(None),
    ).delete(synchronize_session=False)

    # Reusable templates stay — just detach them from this (now-gone) project/cluster.
    db.query(CentralRepository).filter(CentralRepository.project_id == project_id).update(
        {CentralRepository.project_id: None}, synchronize_session=False,
    )
    if cluster_ids:
        db.query(CentralRepository).filter(CentralRepository.cluster_id.in_(cluster_ids)).update(
            {CentralRepository.cluster_id: None}, synchronize_session=False,
        )

    # Membership/role/legacy-assignment rows — delete the grant, never the User.
    db.query(TenantMembership).filter(TenantMembership.project_id == project_id).delete(
        synchronize_session=False
    )
    db.query(TenantRole).filter(TenantRole.project_id == project_id).delete(
        synchronize_session=False
    )
    db.query(ProjectUserAssignment).filter(ProjectUserAssignment.project_id == project_id).delete(
        synchronize_session=False
    )
    # Just each user's "last-active tenant" pointer, not membership.
    db.query(User).filter(User.project_id == project_id).update(
        {User.project_id: None}, synchronize_session=False,
    )

    # ClusterPrompt rows cascade automatically (DB-level ON DELETE CASCADE).
    if cluster_ids:
        db.query(Cluster).filter(Cluster.id.in_(cluster_ids)).delete(synchronize_session=False)

    db.delete(project)
