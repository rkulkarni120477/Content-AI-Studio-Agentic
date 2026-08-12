"""Add token-cap columns to budget_policies/budget_period_spend (P2 token-cap follow-up).

limit_type picks which of limit_usd/limit_tokens is the active cap for a
policy — exactly one is set, matching the admin UI's either/or "Cap type"
choice. limit_usd becomes nullable so a token-capped row doesn't need a
meaningless $ ceiling. spent_tokens is tracked unconditionally on every
scope's running-total row regardless of which cap type it enforces, since
the dashboard/meter always wants both numbers.

These columns previously existed only via the DB_AUTO_DDL escape hatch
(promptops_app/database.py's _run_legacy_ddl), which is off by default — a
normal `alembic upgrade head` deploy left budget_policies/budget_period_spend
without them, and budget_service.check_budget() fails OPEN on the resulting
"column does not exist" error, silently disabling enforcement platform-wide.
This migration is the real fix; the DB_AUTO_DDL entries stay for hosts that
still run that path ahead of alembic's bookkeeping (see migration
000100000018's own note on that ordering).

Revision ID: 000100000019
Revises: 000100000018
"""

import sqlalchemy as sa
from alembic import op

revision = "000100000019"
down_revision = "000100000018"
branch_labels = None
depends_on = None


def _has_column(table: str, column: str) -> bool:
    bind = op.get_bind()
    return column in {c["name"] for c in sa.inspect(bind).get_columns(table)}


def upgrade() -> None:
    if not _has_column("budget_policies", "limit_type"):
        op.add_column(
            "budget_policies",
            sa.Column("limit_type", sa.String(length=10), nullable=False, server_default="usd"),
        )
    if not _has_column("budget_policies", "limit_tokens"):
        op.add_column("budget_policies", sa.Column("limit_tokens", sa.Integer(), nullable=True))
    # Idempotent in Postgres — dropping NOT NULL on an already-nullable column is a no-op.
    op.alter_column("budget_policies", "limit_usd", existing_type=sa.Float(), nullable=True)

    if not _has_column("budget_period_spend", "spent_tokens"):
        op.add_column(
            "budget_period_spend",
            sa.Column("spent_tokens", sa.Integer(), nullable=False, server_default="0"),
        )


def downgrade() -> None:
    if _has_column("budget_period_spend", "spent_tokens"):
        op.drop_column("budget_period_spend", "spent_tokens")

    op.alter_column("budget_policies", "limit_usd", existing_type=sa.Float(), nullable=False)
    if _has_column("budget_policies", "limit_tokens"):
        op.drop_column("budget_policies", "limit_tokens")
    if _has_column("budget_policies", "limit_type"):
        op.drop_column("budget_policies", "limit_type")
