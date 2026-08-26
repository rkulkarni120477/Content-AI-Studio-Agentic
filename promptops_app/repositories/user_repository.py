"""User Repository — User database access."""

import json

from app.core.permissions import ROLE_DISPLAY_NAMES, rbac_check, role_label
from promptops_app.database import TenantMembership, TenantRole, User

# Assigning someone as a reviewer means they will be asked to approve, so
# workflow.approve is the eligibility test (see app/core/permissions.py).
APPROVE_PERMISSION = "workflow.approve"


def _role_permission_keys(role) -> list[str]:
    """Permission keys stored on a TenantRole; [] if the JSON is unusable."""
    try:
        return json.loads(role.permissions or "[]")
    except (TypeError, ValueError):
        return []


def _system_roles_with_permission(permission: str) -> list[str]:
    """System roles (admin/reviewer/author) that hold *permission*.

    Derived from the real RBAC table instead of a hand-maintained literal —
    ``["reviewer", "admin"]`` duplicated ``_PERMISSIONS["workflow.approve"]``
    from app/core/permissions.py, so a role gaining or losing the permission
    there would silently stop matching (or start wrongly matching) here.
    """
    return [role for role in ROLE_DISPLAY_NAMES if rbac_check(role, permission)]


def _custom_role_names(db, project_id: int) -> dict[int, str]:
    """id -> display name for every custom TenantRole in one tenant."""
    return {r.id: r.name for r in db.query(TenantRole).filter(TenantRole.project_id == project_id).all()}


def _role_display(membership, custom_role_names: dict[int, str]) -> str:
    """Human-readable role for one membership — the source of truth for
    display purposes, not the vestigial ``User.role`` (see User's docstring
    in promptops_app/database.py: a user's effective role is per-tenant).
    Reading ``User.role`` here would show a custom Lead as "author"/"ID" —
    platform_tenants.update_member writes membership.role and never syncs
    user.role, so that drift is the normal case, not an edge one.
    """
    if membership.custom_role_id:
        return custom_role_names.get(membership.custom_role_id, "Custom")
    return role_label(membership.role)


def get_user_by_username(db, username: str):
    return db.query(User).filter(User.username == username).first()


def get_user_by_id(db, user_id: int):
    return db.query(User).filter(User.id == user_id).first()


def list_all_users(db, *, project_id: int):
    """Users assignable to a project/course via the "Manage Users" picker.

    Scoped to one tenant's active memberships — every member is a candidate
    (the picker filters out admins client-side itself), no permission check.
    Unlike list_reviewers_and_admins there is no "no tenant" case: this
    endpoint only ever backs that one picker (account creation and
    platform-wide administration live under
    /api/v1/platform/tenants/{id}/users — see this router's module
    docstring), and the picker always has a concrete project in scope — the
    project being managed, or the course's own project. project_id is
    keyword-only and required so a caller who forgets it gets a TypeError
    instead of a silently empty (or, before this fix, cross-tenant) list.

    Returns ``(User, role_display)`` pairs — see ``_role_display``.
    """
    custom_role_names = _custom_role_names(db, project_id)
    rows = (
        db.query(User, TenantMembership)
        .join(TenantMembership, TenantMembership.user_id == User.id)
        .filter(
            TenantMembership.project_id == project_id,
            TenantMembership.active == True,  # noqa: E712
        )
        .order_by(User.username)
        .all()
    )
    return [(u, _role_display(m, custom_role_names)) for u, m in rows]


def list_reviewers_and_admins(db, *, project_id: int | None):
    """Users eligible to be assigned as a reviewer in one tenant.

    ``User.role`` is a vestigial default — a user's effective role is
    per-tenant (``TenantMembership.role``; see promptops_app/database.py's
    User docstring). Filtering on it directly, with no tenant scope, returned
    every reviewer/admin on the whole platform: a leftover admin/reviewer from
    a hard-deleted tenant (its Project row and TenantMembership rows are
    removed, but the User row itself is deliberately preserved — see
    tenant_service.hard_delete_tenant) kept showing up in every OTHER
    tenant's dropdown forever, indistinguishable from a real member.

    Scoping to that tenant's active memberships fixes both: a membership row
    can only exist for a tenant that still exists, so a deleted tenant's
    members drop out on their own.

    Eligibility is "holds ``workflow.approve`` in THIS tenant" — the system
    roles that grant it (see ``_system_roles_with_permission``) plus any
    custom TenantRole whose stored permission list includes it. A custom role
    carries the "custom" sentinel in ``TenantMembership.role``
    (resolve_membership_effective), so matching the role string alone would
    silently drop a tenant's custom Leads from their own reviewer dropdown.

    ``project_id`` is keyword-only and required — pass ``None`` explicitly
    for "the caller has no tenant" (a true platform admin who has not picked
    one), which returns nothing rather than falling back to a cross-tenant
    list; the router force-scopes every tenant-bound caller, so ``None``
    here is never "scope unknown", it is "no tenant selected".

    Returns ``(User, role_display)`` pairs — see ``_role_display``.
    """
    if project_id is None:
        return []

    custom_roles = db.query(TenantRole).filter(TenantRole.project_id == project_id).all()
    custom_role_names = {r.id: r.name for r in custom_roles}
    approving_custom_role_ids = [r.id for r in custom_roles if APPROVE_PERMISSION in _role_permission_keys(r)]

    eligible = TenantMembership.role.in_(_system_roles_with_permission(APPROVE_PERMISSION))
    if approving_custom_role_ids:
        eligible = eligible | TenantMembership.custom_role_id.in_(approving_custom_role_ids)

    rows = (
        db.query(User, TenantMembership)
        .join(TenantMembership, TenantMembership.user_id == User.id)
        .filter(
            TenantMembership.project_id == project_id,
            TenantMembership.active == True,  # noqa: E712
            eligible,
            User.is_active == True,  # noqa: E712
        )
        .order_by(User.username)
        .all()
    )
    return [(u, _role_display(m, custom_role_names)) for u, m in rows]


def list_non_admin_active_users(db):
    return (
        db.query(User)
        .filter(User.role != "admin", User.is_active == True)
        .all()
    )
