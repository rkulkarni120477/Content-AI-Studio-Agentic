"""
Create the first admin user.

Run this once after setting up a fresh database to create the initial
admin account.  After that, use the API's POST /api/v1/users endpoint
to create additional users.

Usage
-----
    python scripts/create_admin.py
    python scripts/create_admin.py --username shubham --password secret123

Environment
-----------
Reads DATABASE_URL from the .env file in the project root.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Add the project root to sys.path so both `app` and `promptops_app` are importable.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv
load_dotenv()


def create_admin(username: str, password: str) -> None:
    """Create an admin user if one with that username does not already exist."""
    from app.core.security import hash_password
    from promptops_app.database import SessionLocal, User

    db = SessionLocal()
    try:
        existing = db.query(User).filter(User.username == username).first()
        if existing:
            print(f"User '{username}' already exists with role '{existing.role}'.")
            if existing.role != "admin":
                existing.role = "admin"
                db.commit()
                print(f"  → Role updated to 'admin'.")
            return

        user = User(
            username=username,
            password=hash_password(password),
            role="admin",
            is_active=True,
        )
        db.add(user)
        db.commit()
        db.refresh(user)

        print(f"Admin user created:")
        print(f"  Username : {user.username}")
        print(f"  Role     : {user.role}")
        print(f"  ID       : {user.id}")
        print()
        print("You can now log in at  POST /api/v1/auth/login")

    finally:
        db.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Create the first admin user.")
    parser.add_argument("--username", default="admin", help="Admin username (default: admin)")
    parser.add_argument("--password", default=None,   help="Admin password (prompted if omitted)")
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

    create_admin(args.username, password)


if __name__ == "__main__":
    main()
