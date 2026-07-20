"""Reverse pipeline — dedicated import tables (decoupled).

An unrelated effort created a richer ``course_imports`` / ``import_provenance``
schema on the shared dev DB under the SAME Alembic revision (000100000009), so
this feature's original ``000100000009`` migration never actually created its
tables (the revision was already recorded). To stay fully decoupled and never
touch that other table, the reverse pipeline now owns its own tables:

  * reverse_course_imports    — one row per import attempt.
  * reverse_import_provenance — Canvas-item ↔ CAS-entity map for round-trip.

Strictly additive & reversible. Guarded so it is a no-op if the tables already
exist. Existing ``courses.source_type`` / ``courses.import_id`` columns (added
by 000100000009) are left as-is.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision = "000100000010"
down_revision = "000100000009"
branch_labels = None
depends_on = None


def _existing_tables() -> set:
    return set(inspect(op.get_bind()).get_table_names())


def upgrade() -> None:
    existing = _existing_tables()

    if "reverse_course_imports" not in existing:
        op.create_table(
            "reverse_course_imports",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("course_id", sa.Integer(), nullable=True, index=True),
            sa.Column("project_id", sa.Integer(), nullable=True, index=True),
            sa.Column("uploaded_by", sa.String(100), nullable=True),
            sa.Column("package_name", sa.String(255), nullable=True),
            sa.Column("package_size", sa.Integer(), nullable=True),
            sa.Column("status", sa.String(30), nullable=False, server_default="pending"),
            sa.Column("structure_counts_json", sa.Text(), nullable=True),
            sa.Column("warnings_json", sa.Text(), nullable=True),
            sa.Column("provenance_ready", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("created_at", sa.DateTime(), nullable=True),
            sa.Column("completed_at", sa.DateTime(), nullable=True),
        )

    if "reverse_import_provenance" not in existing:
        op.create_table(
            "reverse_import_provenance",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("import_id", sa.Integer(), nullable=False, index=True),
            sa.Column("canvas_identifier", sa.String(255), nullable=False),
            sa.Column("canvas_type", sa.String(20), nullable=False),        # page | quiz | module
            sa.Column("cas_entity_type", sa.String(20), nullable=False),    # block | module
            sa.Column("cas_entity_id", sa.Integer(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=True),
        )


def downgrade() -> None:
    existing = _existing_tables()
    if "reverse_import_provenance" in existing:
        op.drop_table("reverse_import_provenance")
    if "reverse_course_imports" in existing:
        op.drop_table("reverse_course_imports")
