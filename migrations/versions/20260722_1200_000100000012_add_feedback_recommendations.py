"""Add AI-recommendation columns to feedback_items.

Extends the reviewer-feedback feature: each feedback item can carry an
AI-generated recommendation (how to revise the relevant course content),
the blocks that recommendation referenced, and provenance (model, status,
who/when). Additive, nullable columns only — no backfill required.

Revision ID: 000100000012
Revises: 000100000011
"""

import sqlalchemy as sa
from alembic import op

revision = "000100000012"
down_revision = "000100000011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("feedback_items", sa.Column("recommendation", sa.Text(), nullable=True))
    op.add_column("feedback_items", sa.Column("recommendation_refs", sa.Text(), nullable=True))
    op.add_column("feedback_items", sa.Column("recommendation_model", sa.String(100), nullable=True))
    op.add_column(
        "feedback_items",
        sa.Column("recommendation_status", sa.String(20), nullable=False, server_default="none"),
    )
    op.add_column("feedback_items", sa.Column("recommended_at", sa.DateTime(), nullable=True))
    op.add_column("feedback_items", sa.Column("recommended_by", sa.String(100), nullable=True))


def downgrade() -> None:
    op.drop_column("feedback_items", "recommended_by")
    op.drop_column("feedback_items", "recommended_at")
    op.drop_column("feedback_items", "recommendation_status")
    op.drop_column("feedback_items", "recommendation_model")
    op.drop_column("feedback_items", "recommendation_refs")
    op.drop_column("feedback_items", "recommendation")
