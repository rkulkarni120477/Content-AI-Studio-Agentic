"""Add blueprint_id scope to feedback_documents and feedback_items.

Maps reviewer feedback to a ModuleBlueprint (module) or NULL for entire course.
Items inherit the document scope on upload and can be remapped individually.

Revision ID: 000100000013
Revises: 000100000012
"""

import sqlalchemy as sa
from alembic import op

revision = "000100000013"
down_revision = "000100000012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "feedback_documents",
        sa.Column("blueprint_id", sa.Integer(), nullable=True),
    )
    op.create_foreign_key(
        "fk_feedback_documents_blueprint_id_module_blueprints",
        "feedback_documents", "module_blueprints",
        ["blueprint_id"], ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_feedback_documents_blueprint_id",
        "feedback_documents",
        ["blueprint_id"],
    )

    op.add_column(
        "feedback_items",
        sa.Column("blueprint_id", sa.Integer(), nullable=True),
    )
    op.create_foreign_key(
        "fk_feedback_items_blueprint_id_module_blueprints",
        "feedback_items", "module_blueprints",
        ["blueprint_id"], ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_feedback_items_blueprint_id",
        "feedback_items",
        ["blueprint_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_feedback_items_blueprint_id", table_name="feedback_items")
    op.drop_constraint(
        "fk_feedback_items_blueprint_id_module_blueprints",
        "feedback_items",
        type_="foreignkey",
    )
    op.drop_column("feedback_items", "blueprint_id")

    op.drop_index("ix_feedback_documents_blueprint_id", table_name="feedback_documents")
    op.drop_constraint(
        "fk_feedback_documents_blueprint_id_module_blueprints",
        "feedback_documents",
        type_="foreignkey",
    )
    op.drop_column("feedback_documents", "blueprint_id")
