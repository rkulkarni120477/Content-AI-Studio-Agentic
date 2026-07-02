"""
Tenant data migration script — run ONCE on an existing single-tenant database.

What this script does
---------------------
  1. Creates a "default-org" tenant (idempotent — safe to re-run).
  2. Assigns every existing user (with tenant_id=NULL) to that tenant.
  3. Assigns all existing tenant-owned rows to that tenant:
       Projects, Styles, Documents, Prompts, CentralRepository,
       GenerationJob, LLMUsageLog, AuditLog.
  4. Creates a platform admin user account (idempotent).

Prerequisites
-------------
  - The Alembic migration 001_add_tenant_system must already be applied:
        alembic upgrade head
  - Or init_db() must have run (it applies the same _column_migrations).

Run
---
    python scripts/migrate_to_tenant.py

    # Or with custom credentials:
    PLATFORM_ADMIN_USERNAME=myadmin PLATFORM_ADMIN_PASSWORD=SecurePass1! \\
        python scripts/migrate_to_tenant.py
"""

import os
import sys
import uuid

# Ensure the project root is on the path so relative imports work.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from promptops_app.database import (
    SessionLocal,
    Tenant,
    User,
    Project,
    Style,
    Document,
    Prompt,
    CentralRepository,
    GenerationJob,
    LLMUsageLog,
    AuditLog,
    hash_password,
)

DEFAULT_TENANT_SLUG     = "default-org"
DEFAULT_TENANT_NAME     = "Default Organization"
DEFAULT_MAX_USERS       = 1000

PLATFORM_ADMIN_USERNAME = os.environ.get("PLATFORM_ADMIN_USERNAME", "platformadmin")
PLATFORM_ADMIN_PASSWORD = os.environ.get("PLATFORM_ADMIN_PASSWORD", "ChangeMe123!")


def _migrate_table(db, model, tenant_id: str, name: str) -> int:
    """Assign all NULL-tenant rows of `model` to `tenant_id`. Returns row count."""
    rows = db.query(model).filter(model.tenant_id.is_(None)).all()
    for row in rows:
        row.tenant_id = tenant_id
    db.flush()
    print(f"  Migrated {len(rows):>5} {name} rows → tenant '{DEFAULT_TENANT_SLUG}'")
    return len(rows)


def run():
    print("=" * 60)
    print("Content AI Studio — Tenant Migration")
    print("=" * 60)

    with SessionLocal() as db:
        # ── 1. Create (or find) the default tenant ─────────────────────
        tenant = db.query(Tenant).filter(Tenant.slug == DEFAULT_TENANT_SLUG).first()
        if tenant is None:
            tenant = Tenant(
                id=str(uuid.uuid4()),
                slug=DEFAULT_TENANT_SLUG,
                name=DEFAULT_TENANT_NAME,
                max_users=DEFAULT_MAX_USERS,
                status="active",
                created_by="migration",
            )
            db.add(tenant)
            db.flush()
            print(f"\nCreated tenant:  {DEFAULT_TENANT_SLUG}  (id={tenant.id})")
        else:
            print(f"\nTenant already exists:  {DEFAULT_TENANT_SLUG}  (id={tenant.id})")

        tid = tenant.id

        # ── 2. Migrate tenant-owned tables ─────────────────────────────
        print("\nMigrating rows to default tenant:")
        _migrate_table(db, User,              tid, "users (excluding platform admins)")
        _migrate_table(db, Project,           tid, "projects")
        _migrate_table(db, Style,             tid, "styles")
        _migrate_table(db, Document,          tid, "documents")
        _migrate_table(db, Prompt,            tid, "prompts")
        _migrate_table(db, CentralRepository, tid, "central_repository")
        _migrate_table(db, GenerationJob,     tid, "generation_jobs")
        _migrate_table(db, LLMUsageLog,       tid, "llm_usage_logs")
        _migrate_table(db, AuditLog,          tid, "audit_logs")

        # ── 3. Create platform admin (idempotent) ──────────────────────
        existing_admin = db.query(User).filter(
            User.username == PLATFORM_ADMIN_USERNAME,
            User.is_platform_admin == True,
        ).first()
        if existing_admin is None:
            platform_admin = User(
                username=PLATFORM_ADMIN_USERNAME,
                password_hash=hash_password(PLATFORM_ADMIN_PASSWORD),
                role="admin",
                tenant_id=None,        # platform admin has no tenant
                is_platform_admin=True,
                is_active=True,
            )
            db.add(platform_admin)
            print(f"\nCreated platform admin:  {PLATFORM_ADMIN_USERNAME}")
            print("  *** IMPORTANT: Change the platform admin password immediately! ***")
        else:
            print(f"\nPlatform admin already exists:  {PLATFORM_ADMIN_USERNAME}")

        db.commit()

    print("\nMigration complete.")
    print("=" * 60)


if __name__ == "__main__":
    run()
