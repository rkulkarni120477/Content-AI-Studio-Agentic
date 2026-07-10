"""Add course_modules table + blocks.module_id (Canvas-style export modules).

Enables grouping published blocks into ordered modules on the Export screen so
the IMSCC package is generated with a proper multi-module Table of Contents.

Strictly additive: the new column is nullable (unassigned blocks fall into a
default module at export time).
"""

import sqlalchemy as sa
from alembic import op

revision = "000100000008"
down_revision = "000100000007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "course_modules",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("course_id", sa.Integer(), nullable=False, index=True),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
    )
    op.add_column("blocks", sa.Column("module_id", sa.Integer(), nullable=True))
    op.create_foreign_key(
        "fk_blocks_module_id_course_modules",
        "blocks", "course_modules",
        ["module_id"], ["id"],
        ondelete="SET NULL",
    )
    op.create_index("ix_blocks_module_id", "blocks", ["module_id"])


def downgrade() -> None:
    op.drop_index("ix_blocks_module_id", table_name="blocks")
    op.drop_constraint("fk_blocks_module_id_course_modules", "blocks", type_="foreignkey")
    op.drop_column("blocks", "module_id")
    op.drop_table("course_modules")
