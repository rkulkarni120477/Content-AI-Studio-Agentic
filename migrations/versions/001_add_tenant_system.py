"""add_tenant_system

Revision ID: 001_add_tenant_system
Revises:
Create Date: 2026-06-30

Adds the tenants table and tenant_id / is_platform_admin columns to all
tenant-owned tables. All new columns are nullable so existing rows remain
valid without a data migration.  Run scripts/migrate_to_tenant.py after
applying this migration to backfill existing data into the default tenant.
"""

from alembic import op
import sqlalchemy as sa

# revision identifiers used by Alembic
revision = "001_add_tenant_system"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ------------------------------------------------------------------
    # 1. Create tenants table
    # ------------------------------------------------------------------
    op.create_table(
        "tenants",
        sa.Column("id",         sa.String(36),  primary_key=True),
        sa.Column("slug",       sa.String(64),  nullable=False),
        sa.Column("name",       sa.String(200), nullable=False),
        sa.Column("max_users",  sa.Integer(),   nullable=False, server_default="50"),
        sa.Column("status",     sa.String(20),  nullable=False, server_default="active"),
        sa.Column("created_at", sa.DateTime(),  nullable=True),
        sa.Column("created_by", sa.String(100), nullable=True),
    )
    op.create_index("ix_tenants_slug", "tenants", ["slug"], unique=True)

    # ------------------------------------------------------------------
    # 2. Add tenant_id + is_platform_admin to users
    # ------------------------------------------------------------------
    op.add_column("users", sa.Column("tenant_id",         sa.String(36),  nullable=True))
    op.add_column("users", sa.Column("is_platform_admin", sa.Boolean(),   nullable=False, server_default="false"))
    op.create_index("ix_users_tenant_id", "users", ["tenant_id"])

    # ------------------------------------------------------------------
    # 3. Add tenant_id to all tenant-owned tables
    # ------------------------------------------------------------------
    tenant_tables = [
        "projects",
        "styles",
        "documents",
        "prompts",
        "central_repositories",
        "generation_jobs",
        "llm_usage_logs",
        "audit_logs",
    ]
    for table in tenant_tables:
        op.add_column(table, sa.Column("tenant_id", sa.String(36), nullable=True))
        op.create_index(f"ix_{table}_tenant_id", table, ["tenant_id"])


def downgrade() -> None:
    tenant_tables = [
        "projects",
        "styles",
        "documents",
        "prompts",
        "central_repositories",
        "generation_jobs",
        "llm_usage_logs",
        "audit_logs",
    ]
    for table in tenant_tables:
        op.drop_index(f"ix_{table}_tenant_id", table_name=table)
        op.drop_column(table, "tenant_id")

    op.drop_index("ix_users_tenant_id", table_name="users")
    op.drop_column("users", "is_platform_admin")
    op.drop_column("users", "tenant_id")

    op.drop_index("ix_tenants_slug", table_name="tenants")
    op.drop_table("tenants")
