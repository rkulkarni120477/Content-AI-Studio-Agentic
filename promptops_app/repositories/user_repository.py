"""User Repository — User database access."""

import json

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


def get_user_by_username(db, username: str):
    return db.query(User).filter(User.username == username).first()


def get_user_by_id(db, user_id: int):
    return db.query(User).filter(User.id == user_id).first()


def list_all_users(db):
    return db.query(User).order_by(User.role, User.username).all()


def list_reviewers_and_admins(db, project_id: int | None = None):
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

    Eligibility is "holds ``workflow.approve`` in THIS tenant", which is the
    admin/reviewer system roles plus any custom TenantRole whose stored
    permission list includes it — a custom role carries the "custom" sentinel
    in ``TenantMembership.role`` (resolve_membership_effective), so matching
    the role string alone would silently drop a tenant's custom Leads from
    their own reviewer dropdown.

    ``project_id=None`` means the caller has no tenant (a true platform admin
    who has not picked one), and returns nothing rather than falling back to
    a cross-tenant list — the router force-scopes every tenant-bound caller,
    so None here is never "scope unknown", it is "no tenant selected".
    """
    if project_id is None:
        return []

    approving_custom_role_ids = [
        role.id for role in db.query(TenantRole).filter(TenantRole.project_id == project_id).all()
        if APPROVE_PERMISSION in _role_permission_keys(role)
    ]

    eligible = TenantMembership.role.in_(["reviewer", "admin"])
    if approving_custom_role_ids:
        eligible = eligible | TenantMembership.custom_role_id.in_(approving_custom_role_ids)

    return (
        db.query(User)
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


def list_non_admin_active_users(db):
    return (
        db.query(User)
        .filter(User.role != "admin", User.is_active == True)
        .all()
    )
