"""Add feedback_documents + feedback_items tables.

Reviewer-feedback documents are uploaded against a course, analysed by the
LLM into individual feedback items, and shown in the Feedback workspace tab.
Both tables are tenant-scoped via project_id and course-scoped via course_id.

Revision ID: 000100000011
Revises: 000100000010
"""

import sqlalchemy as sa
from alembic import op

revision = "000100000011"
down_revision = "000100000010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "feedback_documents",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("course_id", sa.Integer(), nullable=True),
        sa.Column("filename", sa.String(255), nullable=False),
        sa.Column("file_type", sa.String(120), nullable=True),
        sa.Column("content", sa.Text(), nullable=True),
        sa.Column("item_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("model_used", sa.String(100), nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="active"),
        sa.Column("created_by", sa.String(100), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
    )
    op.create_foreign_key(
        "fk_feedback_documents_project_id_projects",
        "feedback_documents", "projects", ["project_id"], ["id"],
    )
    op.create_foreign_key(
        "fk_feedback_documents_course_id_courses",
        "feedback_documents", "courses", ["course_id"], ["id"],
    )
    op.create_index("ix_feedback_documents_project_id", "feedback_documents", ["project_id"])
    op.create_index("ix_feedback_documents_course_id", "feedback_documents", ["course_id"])

    op.create_table(
        "feedback_items",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("document_id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("course_id", sa.Integer(), nullable=True),
        sa.Column("feedback_text", sa.Text(), nullable=False),
        sa.Column("source_location", sa.String(500), nullable=True),
        sa.Column("theme", sa.String(255), nullable=True),
        sa.Column("sentiment", sa.String(30), nullable=True),
        sa.Column("priority", sa.String(20), nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="active"),
        sa.Column("created_by", sa.String(100), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
    )
    op.create_foreign_key(
        "fk_feedback_items_document_id_feedback_documents",
        "feedback_items", "feedback_documents", ["document_id"], ["id"],
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        "fk_feedback_items_project_id_projects",
        "feedback_items", "projects", ["project_id"], ["id"],
    )
    op.create_foreign_key(
        "fk_feedback_items_course_id_courses",
        "feedback_items", "courses", ["course_id"], ["id"],
    )
    op.create_index("ix_feedback_items_document_id", "feedback_items", ["document_id"])
    op.create_index("ix_feedback_items_project_id", "feedback_items", ["project_id"])
    op.create_index("ix_feedback_items_course_id", "feedback_items", ["course_id"])


def downgrade() -> None:
    op.drop_index("ix_feedback_items_course_id", table_name="feedback_items")
    op.drop_index("ix_feedback_items_project_id", table_name="feedback_items")
    op.drop_index("ix_feedback_items_document_id", table_name="feedback_items")
    op.drop_constraint("fk_feedback_items_course_id_courses", "feedback_items", type_="foreignkey")
    op.drop_constraint("fk_feedback_items_project_id_projects", "feedback_items", type_="foreignkey")
    op.drop_constraint("fk_feedback_items_document_id_feedback_documents", "feedback_items", type_="foreignkey")
    op.drop_table("feedback_items")

    op.drop_index("ix_feedback_documents_course_id", table_name="feedback_documents")
    op.drop_index("ix_feedback_documents_project_id", table_name="feedback_documents")
    op.drop_constraint("fk_feedback_documents_course_id_courses", "feedback_documents", type_="foreignkey")
    op.drop_constraint("fk_feedback_documents_project_id_projects", "feedback_documents", type_="foreignkey")
    op.drop_table("feedback_documents")
