"""User Repository — User database access."""

from app.core.tenant_context import apply_tenant_filter
from promptops_app.database import User


def get_user_by_username(db, username: str, tenant_id=None, is_platform_admin=False):
    q = db.query(User).filter(User.username == username)
    return apply_tenant_filter(q, User, tenant_id, is_platform_admin).first()


def get_user_by_id(db, user_id: int, tenant_id=None, is_platform_admin=False):
    q = db.query(User).filter(User.id == user_id)
    return apply_tenant_filter(q, User, tenant_id, is_platform_admin).first()


def list_all_users(db, tenant_id=None, is_platform_admin=False):
    # Exclude platform admin accounts from regular user lists.
    q = db.query(User).filter(User.is_platform_admin == False).order_by(User.role, User.username)
    return apply_tenant_filter(q, User, tenant_id, is_platform_admin).all()


def list_reviewers_and_admins(db, tenant_id=None, is_platform_admin=False):
    q = (
        db.query(User)
        .filter(User.role.in_(["reviewer", "admin"]), User.is_active == True)
        .order_by(User.username)
    )
    return apply_tenant_filter(q, User, tenant_id, is_platform_admin).all()


def list_non_admin_active_users(db, tenant_id=None, is_platform_admin=False):
    q = db.query(User).filter(User.role != "admin", User.is_active == True)
    return apply_tenant_filter(q, User, tenant_id, is_platform_admin).all()
