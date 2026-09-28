"""Add review_findings (CE Agent Review, Step 4 — issue detection).

One row per detected issue. Carries the verbatim quote it sits on (anchor),
optional replacement text + tier (inline | large | guidance), severity, a
fingerprint for dedup/dismissal carry-forward, and the apply/dismiss columns
Step 5 fills. Additive; gated by CE_REVIEW_ENABLED.

Revision ID: 000100000030
Revises: 000100000029
"""
import sqlalchemy as sa
from alembic import op

revision = "000100000030"
down_revision = "000100000029"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "review_findings",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("review_id", sa.Integer(), nullable=False),
        sa.Column("block_id", sa.Integer(), nullable=True),        # null = cross-block finding
        sa.Column("project_id", sa.Integer(), nullable=True),      # denormalised for tenant filter
        sa.Column("category", sa.String(40), nullable=False),
        sa.Column("severity", sa.String(10), nullable=False),      # blocker | major | minor
        sa.Column("checklist_item_key", sa.String(80), nullable=True),
        sa.Column("title", sa.String(255), nullable=True),
        sa.Column("detail", sa.Text(), nullable=True),
        sa.Column("anchor_quote", sa.String(300), nullable=True),  # verbatim locator
        sa.Column("anchor_hash", sa.String(64), nullable=True),
        sa.Column("suggested_replacement", sa.Text(), nullable=True),
        sa.Column("tier", sa.String(12), nullable=False, server_default="guidance"),  # inline|large|guidance
        sa.Column("auto_applicable", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("status", sa.String(12), nullable=False, server_default="open"),     # open|applied|dismissed|stale|apply_failed
        sa.Column("fingerprint", sa.String(64), nullable=True),
        # Apply / dismiss (filled in Step 5).
        sa.Column("applied_version_id", sa.Integer(), nullable=True),
        sa.Column("applied_by", sa.String(100), nullable=True),
        sa.Column("applied_at", sa.DateTime(), nullable=True),
        sa.Column("dismissed_by", sa.String(100), nullable=True),
        sa.Column("dismissed_at", sa.DateTime(), nullable=True),
        sa.Column("dismiss_reason", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
    )
    op.create_foreign_key(
        "fk_review_findings_review_id_content_reviews",
        "review_findings", "content_reviews", ["review_id"], ["id"], ondelete="CASCADE",
    )
    op.create_index("ix_review_findings_review_status", "review_findings", ["review_id", "status"])
    op.create_index("ix_review_findings_block_id", "review_findings", ["block_id"])
    op.create_index("ix_review_findings_fingerprint", "review_findings", ["fingerprint"])
    op.create_index("ix_review_findings_project_id", "review_findings", ["project_id"])


def downgrade() -> None:
    op.drop_index("ix_review_findings_project_id", table_name="review_findings")
    op.drop_index("ix_review_findings_fingerprint", table_name="review_findings")
    op.drop_index("ix_review_findings_block_id", table_name="review_findings")
    op.drop_index("ix_review_findings_review_status", table_name="review_findings")
    op.drop_table("review_findings")
