"""Add budget_policies and budget_period_spend tables (P2 of claude_plan_platform_hardening).

budget_policies: an admin-configured spend limit for a project, course, or user.
budget_period_spend: the race-safe running-total table P2.3/P2.9's atomic
reserve checks against (not a SUM() over llm_usage_logs, which stays for the
dashboard/historical view only).

Also adds the missing index on llm_usage_logs.course_id — it was the only one
of the three scope columns (project_id, user_id, course_id) without one, and
P2.2's per-course spend lookup would otherwise table-scan the highest-volume
table in the system.

Revision ID: 000100000016
Revises: 000100000015
"""

import sqlalchemy as sa
from alembic import op

revision = "000100000016"
down_revision = "000100000015"
branch_labels = None
depends_on = None


def _has_table(table: str) -> bool:
    bind = op.get_bind()
    return table in sa.inspect(bind).get_table_names()


def _has_index(table: str, index_name: str) -> bool:
    bind = op.get_bind()
    return index_name in {ix["name"] for ix in sa.inspect(bind).get_indexes(table)}


def upgrade() -> None:
    # Idempotent throughout — see migration 000100000014/000100000015's own notes
    # on this codebase's DB_AUTO_DDL path sometimes applying schema ahead of
    # alembic's bookkeeping.
    if not _has_table("budget_policies"):
        op.create_table(
            "budget_policies",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("scope", sa.String(length=20), nullable=False),
            sa.Column("scope_id", sa.String(length=100), nullable=False),
            sa.Column("period", sa.String(length=20), nullable=False, server_default="monthly"),
            sa.Column("limit_usd", sa.Float(), nullable=False),
            sa.Column("warn_threshold_pct", sa.Float(), nullable=False, server_default="80.0"),
            sa.Column("last_warned_period", sa.String(length=20), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=True),
            sa.Column("updated_at", sa.DateTime(), nullable=True),
            sa.UniqueConstraint("scope", "scope_id", name="uq_budget_policy_scope"),
        )

    if not _has_table("budget_period_spend"):
        op.create_table(
            "budget_period_spend",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("scope", sa.String(length=20), nullable=False),
            sa.Column("scope_id", sa.String(length=100), nullable=False),
            sa.Column("period_key", sa.String(length=20), nullable=False),
            sa.Column("spent_usd", sa.Float(), nullable=False, server_default="0"),
            sa.Column("updated_at", sa.DateTime(), nullable=True),
            sa.UniqueConstraint("scope", "scope_id", "period_key", name="uq_budget_period_spend"),
        )

    if not _has_index("llm_usage_logs", "ix_llm_usage_logs_course_id"):
        op.create_index("ix_llm_usage_logs_course_id", "llm_usage_logs", ["course_id"])


def downgrade() -> None:
    if _has_index("llm_usage_logs", "ix_llm_usage_logs_course_id"):
        op.drop_index("ix_llm_usage_logs_course_id", table_name="llm_usage_logs")
    if _has_table("budget_period_spend"):
        op.drop_table("budget_period_spend")
    if _has_table("budget_policies"):
        op.drop_table("budget_policies")
