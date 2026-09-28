"""Add review_checklist_results (CE Agent Review, Step 3 — checklist pass).

One row per checklist rule that did NOT pass (fail | warning | na) for a given
review. Pass rows are omitted (absence = pass) to keep the table ~70% smaller.
Additive; gated by CE_REVIEW_ENABLED.

Revision ID: 000100000029
Revises: 000100000028
"""
import sqlalchemy as sa
from alembic import op

revision = "000100000029"
down_revision = "000100000028"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "review_checklist_results",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("review_id", sa.Integer(), nullable=False),
        sa.Column("item_key", sa.String(80), nullable=False),      # which rule
        sa.Column("rule_text", sa.Text(), nullable=True),          # snapshot, self-contained
        sa.Column("status", sa.String(12), nullable=False),        # fail | warning | na
        sa.Column("explanation", sa.Text(), nullable=True),
        sa.Column("recommendation", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
    )
    op.create_foreign_key(
        "fk_review_checklist_results_review_id_content_reviews",
        "review_checklist_results", "content_reviews", ["review_id"], ["id"],
        ondelete="CASCADE",
    )
    op.create_index("ix_review_checklist_results_review_id", "review_checklist_results", ["review_id"])


def downgrade() -> None:
    op.drop_index("ix_review_checklist_results_review_id", table_name="review_checklist_results")
    op.drop_table("review_checklist_results")
