"""
Create the first admin user.

Run this once after setting up a fresh database to create the initial
admin account.  After that, use the API's POST /api/v1/users endpoint
to create additional users.

Usage
-----
    python scripts/create_admin.py --platform-admin
    python scripts/create_admin.py --username shubham --platform-admin

Environment
-----------
Reads DATABASE_URL from the .env file in the project root.
For SQLite development databases, creates any missing tables before creating
the account. Passwords are prompted securely unless supplied explicitly.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Add the project root to sys.path so both `app` and `promptops_app` are importable.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

load_dotenv()


def create_admin(username: str, password: str, *, platform_admin: bool = False) -> None:
    """Create an admin user, optionally granting platform-admin access."""
    from app.core.security import hash_password
    from promptops_app.database import Base, SessionLocal, User, engine

    if engine.dialect.name == "sqlite":
        Base.metadata.create_all(bind=engine)

    db = SessionLocal()
    try:
        existing = db.query(User).filter(User.username == username).first()
        if existing:
            changed = False
            if existing.role != "admin":
                existing.role = "admin"
                changed = True
            if platform_admin and not existing.is_platform_admin:
                existing.is_platform_admin = True
                changed = True
            if changed:
                db.commit()
            print(f"User '{username}' already exists with role 'admin'.")
            if platform_admin:
                print("  Platform administrator access is enabled.")
            return

        user = User(
            username=username,
            password_hash=hash_password(password),
            role="admin",
            is_active=True,
            is_platform_admin=platform_admin,
        )
        db.add(user)
        db.commit()
        db.refresh(user)

        print("Admin user created:")
        print(f"  Username : {user.username}")
        print(f"  Role     : {user.role}")
        if platform_admin:
            print("  Platform administrator access: enabled")
        print(f"  ID       : {user.id}")
        print()
        print("You can now sign in through the frontend.")

    finally:
        db.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Create the first admin user.")
    parser.add_argument("--username", default="admin", help="Admin username (default: admin)")
    parser.add_argument("--password", default=None,   help="Admin password (prompted if omitted)")
    parser.add_argument(
        "--platform-admin",
        action="store_true",
        help="Grant platform-wide administrator access for the login checkbox",
    )
    args = parser.parse_args()

    password = args.password
    if not password:
        import getpass
        password = getpass.getpass(f"Password for '{args.username}': ")
        confirm = getpass.getpass("Confirm password: ")
        if password != confirm:
            print("Passwords do not match.")
            sys.exit(1)

    if len(password) < 6:
        print("Password must be at least 6 characters.")
        sys.exit(1)

    create_admin(args.username, password, platform_admin=args.platform_admin)


if __name__ == "__main__":
    main()
