"""User Repository — User database access."""

from promptops_app.database import User


def get_user_by_username(db, username: str):
    return db.query(User).filter(User.username == username).first()


def get_user_by_id(db, user_id: int):
    return db.query(User).filter(User.id == user_id).first()


def list_all_users(db):
    return db.query(User).order_by(User.role, User.username).all()


def list_reviewers_and_admins(db):
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
