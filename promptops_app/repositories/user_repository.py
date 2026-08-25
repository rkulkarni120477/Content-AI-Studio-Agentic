"""User Repository — User database access."""

from promptops_app.database import TenantMembership, User


def get_user_by_username(db, username: str):
    return db.query(User).filter(User.username == username).first()


def get_user_by_id(db, user_id: int):
    return db.query(User).filter(User.id == user_id).first()


def list_all_users(db):
    return db.query(User).order_by(User.role, User.username).all()


def list_reviewers_and_admins(db, project_id: int | None = None):
    """Users eligible to be assigned as a reviewer.

    ``User.role`` is a vestigial default — a user's effective role is
    per-tenant (``TenantMembership.role``; see promptops_app/database.py's
    User docstring). Filtering on it directly, with no tenant scope, returned
    every reviewer/admin on the whole platform: a leftover admin/reviewer from
    a hard-deleted tenant (its Project row and TenantMembership rows are
    removed, but the User row itself is deliberately preserved — see
    tenant_service.hard_delete_tenant) kept showing up in every OTHER
    tenant's dropdown forever, indistinguishable from a real member.

    When ``project_id`` is given, scope to that tenant's active memberships
    instead — a membership row can only exist for a tenant that still exists,
    so a deleted tenant's members drop out on their own. Falls back to the
    unscoped, legacy query when no project_id is available (e.g. no course/
    tenant context yet); callers should pass one whenever they have it.
    """
    if project_id is not None:
        return (
            db.query(User)
            .join(TenantMembership, TenantMembership.user_id == User.id)
            .filter(
                TenantMembership.project_id == project_id,
                TenantMembership.active == True,  # noqa: E712
                TenantMembership.role.in_(["reviewer", "admin"]),
                User.is_active == True,  # noqa: E712
            )
            .order_by(User.username)
            .all()
        )
    return (
        db.query(User)
        .filter(User.role.in_(["reviewer", "admin"]), User.is_active == True)
        .order_by(User.username)
        .all()
    )


def list_non_admin_active_users(db):
    return (
        db.query(User)
        .filter(User.role != "admin", User.is_active == True)
        .all()
    )
