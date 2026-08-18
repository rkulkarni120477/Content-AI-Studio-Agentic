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
import logging
import re

from sqlalchemy.orm import Session

from app.core.config import PLATFORM_SLUG
from app.core.dis_access import normalize_client_name
from app.core.exceptions import NotFoundError, ValidationError
from app.core.permissions import get_permissions_for_role

_log = logging.getLogger(__name__)

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

    # DIS provisioning (dis_provisioning.provision_dis_client) keys its client
    # id on normalize_client_name(client_name or name) — two tenants whose
    # names normalize to the same id (e.g. "Cengage Learning" and "Cengage")
    # would be handed the same DIS namespace, sharing Source Library content.
    # Caught here, before the tenant commits, rather than in that best-effort
    # background task where there's no request left to reject.
    effective_client_name = (client_name or "").strip() or name.strip()
    new_slug = normalize_client_name(effective_client_name)
    if new_slug:
        for existing_name in db.query(Project.client_name, Project.name).filter(Project.is_active.is_(True)).all():
            other = (existing_name.client_name or "").strip() or existing_name.name.strip()
            if normalize_client_name(other) == new_slug:
                raise ValidationError(
                    f"'{effective_client_name}' normalizes to the same DIS client id "
                    f"('{new_slug}') as an existing tenant's client name — choose a "
                    "name that's distinct after lowercasing/underscoring."
                )

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


def hard_delete_tenant(db: Session, project, deleted_by: str) -> None:
    """Permanently delete a tenant (Project) and everything scoped under it.

    Per-course content (blocks, generations, blueprints, CDDs, imports, etc.)
    is purged via course_repository.purge_course, which already implements
    that full cascade (and commits per course — not one atomic transaction
    for the whole tenant, matching purge_course's own existing behavior
    elsewhere; purge_course is idempotent — it no-ops on an already-gone
    course — so re-running this after a partial failure is safe). What's
    left here is everything scoped directly to the project or one of its
    clusters rather than to a specific course: clusters, memberships, custom
    roles, the legacy project_user_assignments grant table, project-level
    fixings/preferences/history/jobs/feedback, tenant-authored styles, and
    any generation/CDD/blueprint/import that carries project_id directly but
    was never attached to a course (each with its own full dependency-order
    cleanup, same shape as purge_course's — these aren't just extra rows,
    real Postgres FK constraints will reject deleting a Generation/CDD/
    blueprint that still has children).

    Every uploaded editor image referenced by this tenant's courses is
    snapshotted before purging and deleted from S3 afterward if it ends up
    referenced nowhere — mirrors courses.py's permanently_delete_course,
    which needs the same before/after bracket around purge_course for the
    same reason (purging a course destroys the only DB references to those
    keys).

    LLM usage logs, audit log entries, and budget policies/spend rows are
    deliberately left untouched — retained for billing/compliance history
    even after the tenant itself is gone. They carry a bare project_id/
    scope_id with no FK, so nothing breaks either way.

    Known gaps (not handled here): DIS-side artifacts (S3 digests / OpenSearch
    docs scoped by project) are untouched, and any in-flight generation/digest
    job for this tenant keeps running against courses that no longer exist.
    """
    from app.services.asset_cleanup import collect_course_assets, delete_unreferenced
    from promptops_app.database import (
        BlockComment, BlockVersion, BlueprintVersion, Block, CDDVersion,
        CentralRepository, Cluster, Course, CourseDesignDocument, CourseImport,
        FeedbackDocument, FeedbackItem, FeedbackSignal, Generation, GenerationJob,
        ImportProvenance, ModuleBlueprint, ProjectUserAssignment, PromptFixing,
        Review, Style, StyleDocument, StyleVersion, TenantMembership, TenantRole,
        User, UserPromptHistory, UserPromptPreference, WorkflowEvent,
    )
    from promptops_app.repositories.course_repository import purge_course
    from promptops_app.services.audit_service import log_audit_event

    project_id = project.id

    course_ids = [c.id for c in db.query(Course.id).filter(Course.project_id == project_id).all()]

    log_audit_event(
        db, deleted_by, "project.deleted",
        entity_type="project", entity_id=project_id, project_id=project_id,
        metadata={"slug": project.slug, "name": project.name, "course_count": len(course_ids)},
    )

    candidate_assets: set[str] = set()
    for course_id in course_ids:
        candidate_assets |= collect_course_assets(db, course_id)
        purge_course(db, course_id)

    try:
        delete_unreferenced(db, candidate_assets)
    except Exception:  # noqa: BLE001 — cleanup must never fail the tenant delete
        _log.exception("tenant_asset_cleanup_failed  project_id=%d", project_id)

    cluster_ids = [c.id for c in db.query(Cluster.id).filter(Cluster.project_id == project_id).all()]

    # Every real course under this tenant is already gone by this point (the
    # loop above), so filtering by project_id alone — no course_id.is_(None)
    # narrowing — is both sufficient and safer: a course_id.is_(None) filter
    # would leave behind any row whose course_id pointed at a course that
    # doesn't (or no longer) resolves cleanly, which for FeedbackDocument/
    # FeedbackItem (project_id is a NOT NULL FK) would then block
    # db.delete(project) below with an IntegrityError partway through.
    db.query(PromptFixing).filter(PromptFixing.project_id == project_id).delete(
        synchronize_session=False
    )
    db.query(UserPromptPreference).filter(UserPromptPreference.project_id == project_id).delete(
        synchronize_session=False
    )
    db.query(UserPromptHistory).filter(UserPromptHistory.project_id == project_id).delete(
        synchronize_session=False
    )
    db.query(GenerationJob).filter(GenerationJob.project_id == project_id).delete(
        synchronize_session=False
    )
    # Legacy/orphan generations never attached to a course — purge_course
    # above only ever touches Generation.course_id == <a real course>, so a
    # row with project_id set but course_id NULL is otherwise never reached
    # and would keep its real output_text content indefinitely. Mirrors
    # purge_course's own block/review/feedback-signal dependency order: those
    # three FKs have no ON DELETE clause, so deleting a Generation with
    # existing blocks raises ForeignKeyViolation on Postgres — and by this
    # point every real course has already been purge_course'd and committed,
    # so a mid-way failure here would leave the tenant irrecoverably
    # half-deleted (SQLite, used by the test suite, doesn't enforce FKs, so
    # this only ever shows up against real Postgres).
    orphan_gen_ids = [
        g.id for g in db.query(Generation.id)
        .filter(Generation.project_id == project_id, Generation.course_id.is_(None))
        .all()
    ]
    if orphan_gen_ids:
        orphan_block_ids = [
            b.id for b in db.query(Block.id).filter(Block.generation_id.in_(orphan_gen_ids)).all()
        ]
        if orphan_block_ids:
            db.query(WorkflowEvent).filter(WorkflowEvent.block_id.in_(orphan_block_ids)).delete(
                synchronize_session=False
            )
            db.query(FeedbackSignal).filter(FeedbackSignal.block_id.in_(orphan_block_ids)).delete(
                synchronize_session=False
            )
            db.query(Review).filter(Review.block_id.in_(orphan_block_ids)).delete(
                synchronize_session=False
            )
            db.query(BlockComment).filter(BlockComment.block_id.in_(orphan_block_ids)).delete(
                synchronize_session=False
            )
            db.query(BlockVersion).filter(BlockVersion.block_id.in_(orphan_block_ids)).delete(
                synchronize_session=False
            )
            # PlagiarismReport.block_id has ON DELETE CASCADE — no explicit delete needed.
            db.query(Block).filter(Block.id.in_(orphan_block_ids)).delete(synchronize_session=False)

        db.query(Review).filter(Review.generation_id.in_(orphan_gen_ids)).delete(
            synchronize_session=False
        )
        db.query(FeedbackSignal).filter(FeedbackSignal.generation_id.in_(orphan_gen_ids)).delete(
            synchronize_session=False
        )
        db.query(Generation).filter(Generation.id.in_(orphan_gen_ids)).update(
            {Generation.cdd_id: None, Generation.blueprint_id: None}, synchronize_session=False,
        )
        db.query(Generation).filter(Generation.id.in_(orphan_gen_ids)).delete(
            synchronize_session=False
        )

    # Same gap, same fix, for CDDs/blueprints/imports that carry project_id
    # directly but were never attached to a course — purge_course only ever
    # filters these four tables by course_id, so an orphan row would
    # otherwise survive (and for CDD/blueprint, keep real generated text)
    # indefinitely.
    orphan_cdd_ids = [
        d.id for d in db.query(CourseDesignDocument.id)
        .filter(CourseDesignDocument.project_id == project_id, CourseDesignDocument.course_id.is_(None))
        .all()
    ]
    if orphan_cdd_ids:
        orphan_bp_ids_via_cdd = [
            b.id for b in db.query(ModuleBlueprint.id).filter(ModuleBlueprint.cdd_id.in_(orphan_cdd_ids)).all()
        ]
        if orphan_bp_ids_via_cdd:
            db.query(BlueprintVersion).filter(
                BlueprintVersion.blueprint_id.in_(orphan_bp_ids_via_cdd)
            ).delete(synchronize_session=False)
            db.query(ModuleBlueprint).filter(ModuleBlueprint.id.in_(orphan_bp_ids_via_cdd)).delete(
                synchronize_session=False
            )
        db.query(CDDVersion).filter(CDDVersion.cdd_id.in_(orphan_cdd_ids)).delete(
            synchronize_session=False
        )
        db.query(CourseDesignDocument).filter(CourseDesignDocument.id.in_(orphan_cdd_ids)).delete(
            synchronize_session=False
        )

    # Remaining orphan blueprints not already caught above (no cdd_id, or a
    # cdd_id pointing at a CDD that wasn't itself an orphan needing cleanup).
    orphan_bp_ids = [
        b.id for b in db.query(ModuleBlueprint.id)
        .filter(ModuleBlueprint.project_id == project_id, ModuleBlueprint.course_id.is_(None))
        .all()
    ]
    if orphan_bp_ids:
        db.query(BlueprintVersion).filter(BlueprintVersion.blueprint_id.in_(orphan_bp_ids)).delete(
            synchronize_session=False
        )
        db.query(ModuleBlueprint).filter(ModuleBlueprint.id.in_(orphan_bp_ids)).delete(
            synchronize_session=False
        )

    orphan_import_ids = [
        i.id for i in db.query(CourseImport.id)
        .filter(CourseImport.project_id == project_id, CourseImport.course_id.is_(None))
        .all()
    ]
    if orphan_import_ids:
        db.query(ImportProvenance).filter(ImportProvenance.import_id.in_(orphan_import_ids)).delete(
            synchronize_session=False
        )
        db.query(CourseImport).filter(CourseImport.id.in_(orphan_import_ids)).delete(
            synchronize_session=False
        )

    fb_doc_ids = [
        d.id for d in db.query(FeedbackDocument.id).filter(FeedbackDocument.project_id == project_id).all()
    ]
    if fb_doc_ids:
        db.query(FeedbackItem).filter(FeedbackItem.document_id.in_(fb_doc_ids)).delete(
            synchronize_session=False
        )
    db.query(FeedbackItem).filter(FeedbackItem.project_id == project_id).delete(
        synchronize_session=False
    )
    db.query(FeedbackDocument).filter(FeedbackDocument.project_id == project_id).delete(
        synchronize_session=False
    )

    # Tenant-authored styles (project_id set) — not the shared/global ones
    # (project_id NULL), which purge_course already correctly leaves alone.
    style_ids = [s.id for s in db.query(Style.id).filter(Style.project_id == project_id).all()]
    if style_ids:
        db.query(StyleVersion).filter(StyleVersion.style_id.in_(style_ids)).delete(
            synchronize_session=False
        )
        db.query(StyleDocument).filter(StyleDocument.style_id.in_(style_ids)).delete(
            synchronize_session=False
        )
        db.query(Style).filter(Style.id.in_(style_ids)).delete(synchronize_session=False)

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
