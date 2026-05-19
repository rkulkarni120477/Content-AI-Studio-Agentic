"""
Seed the database with minimal development data.

Creates:
  - 1 admin user     (admin / admin123)
  - 1 reviewer user  (reviewer / reviewer123)
  - 1 author user    (author / author123)
  - 1 sample project (Academian Demo)
  - 1 sample course  (Module 1 — Foundations)

Safe to run multiple times — skips records that already exist.

Usage
-----
    python scripts/seed_db.py

Environment
-----------
Reads DATABASE_URL from the .env file in the project root.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv
load_dotenv()


def seed() -> None:
    from app.core.security import hash_password
    from promptops_app.database import SessionLocal, User

    db = SessionLocal()
    try:
        _seed_users(db)
        _seed_projects(db)
        print("\nDatabase seeded successfully.")
        print("Login credentials:")
        print("  admin    / admin123")
        print("  reviewer / reviewer123")
        print("  author   / author123")
    finally:
        db.close()


def _seed_users(db) -> None:
    from app.core.security import hash_password
    from promptops_app.database import User

    users = [
        ("admin",    "admin123",    "admin"),
        ("reviewer", "reviewer123", "reviewer"),
        ("author",   "author123",   "author"),
    ]
    for username, password, role in users:
        if not db.query(User).filter(User.username == username).first():
            db.add(User(
                username=username,
                password_hash=hash_password(password),
                role=role,
                is_active=True,
            ))
            print(f"  Created user: {username} ({role})")
        else:
            print(f"  Skipped user: {username} (already exists)")
    db.commit()


def _seed_projects(db) -> None:
    try:
        from promptops_app.database import Project, Course

        if not db.query(Project).filter(Project.name == "Academian Demo").first():
            project = Project(name="Academian Demo", client_name="Academian")
            db.add(project)
            db.flush()

            course = Course(
                name="Module 1 — Foundations",
                project_id=project.id,
            )
            db.add(course)
            db.commit()
            print(f"  Created project: Academian Demo (id={project.id})")
            print(f"  Created course:  Module 1 — Foundations (id={course.id})")
        else:
            print("  Skipped project: Academian Demo (already exists)")
    except Exception as exc:
        # Project/Course models may have different column requirements.
        # Fail gracefully and let the developer create these via the API.
        db.rollback()
        print(f"  Skipped project seeding: {exc}")


if __name__ == "__main__":
    seed()
