"""Add review_checklists + review_checklist_items tables (CE Agent Review, Step 1).

A CE checklist is uploaded once per client (tenant), split by the LLM into
individual rules, and later used to review generated lessons. Both tables are
tenant-scoped via project_id. Items carry a stable ``item_key`` (so a rule keeps
its identity across edits and re-reviews) and an ``is_mandatory`` flag (only
failed mandatory rules block "Ready for Approval").

Additive only — no existing table is altered or dropped. The feature is gated by
the CE_REVIEW_ENABLED flag; these tables are simply unused until it is on.

Revision ID: 000100000028
Revises: 000100000027
"""

import sqlalchemy as sa
from alembic import op

revision = "000100000028"
down_revision = "000100000027"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "review_checklists",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("source_document_id", sa.Integer(), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("status", sa.String(20), nullable=False, server_default="active"),
        sa.Column("created_by", sa.String(100), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
    )
    op.create_foreign_key(
        "fk_review_checklists_project_id_projects",
        "review_checklists", "projects", ["project_id"], ["id"],
    )
    op.create_foreign_key(
        "fk_review_checklists_source_document_id_documents",
        "review_checklists", "documents", ["source_document_id"], ["id"],
        ondelete="SET NULL",
    )
    op.create_index("ix_review_checklists_project_id", "review_checklists", ["project_id"])
    op.create_index("ix_review_checklists_status", "review_checklists", ["status"])

    op.create_table(
        "review_checklist_items",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("checklist_id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("item_key", sa.String(80), nullable=False),
        sa.Column("section", sa.String(255), nullable=True),
        sa.Column("rule_text", sa.Text(), nullable=False),
        sa.Column("is_mandatory", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("applies_to", sa.JSON(), nullable=True),
        sa.Column("guidance", sa.Text(), nullable=True),
        sa.Column("position", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(), nullable=True),
    )
    op.create_foreign_key(
        "fk_review_checklist_items_checklist_id_review_checklists",
        "review_checklist_items", "review_checklists", ["checklist_id"], ["id"],
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        "fk_review_checklist_items_project_id_projects",
        "review_checklist_items", "projects", ["project_id"], ["id"],
    )
    op.create_index(
        "ix_review_checklist_items_checklist_id", "review_checklist_items", ["checklist_id"]
    )
    op.create_index(
        "ix_review_checklist_items_project_id", "review_checklist_items", ["project_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_review_checklist_items_project_id", table_name="review_checklist_items")
    op.drop_index("ix_review_checklist_items_checklist_id", table_name="review_checklist_items")
    op.drop_table("review_checklist_items")
    op.drop_index("ix_review_checklists_status", table_name="review_checklists")
    op.drop_index("ix_review_checklists_project_id", table_name="review_checklists")
    op.drop_table("review_checklists")
