"""Add review_dismissals (CE Agent Review, Step 7 — dismissal carry-forward).

Persists a dismissed finding's fingerprint per generation, so re-reviewing the
same lesson keeps it dismissed instead of resurfacing it (decision 8). Outlives
any single review run. Additive; gated by CE_REVIEW_ENABLED.

Revision ID: 000100000031
Revises: 000100000030
"""
import sqlalchemy as sa
from alembic import op

revision = "000100000031"
down_revision = "000100000030"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "review_dismissals",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("generation_id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=True),
        sa.Column("fingerprint", sa.String(64), nullable=False),
        sa.Column("dismissed_by", sa.String(100), nullable=True),
        sa.Column("dismissed_at", sa.DateTime(), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
    )
    op.create_foreign_key(
        "fk_review_dismissals_generation_id_generations",
        "review_dismissals", "generations", ["generation_id"], ["id"], ondelete="CASCADE",
    )
    # One dismissal per (lesson, fingerprint) — the carry-forward lookup key.
    op.create_index("ix_review_dismissals_gen_fp", "review_dismissals",
                    ["generation_id", "fingerprint"], unique=True)


def downgrade() -> None:
    op.drop_index("ix_review_dismissals_gen_fp", table_name="review_dismissals")
    op.drop_table("review_dismissals")
