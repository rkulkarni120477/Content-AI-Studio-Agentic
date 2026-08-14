"""Add deleted_at/deleted_by to course_design_documents and module_blueprints.

Design documents had no delete of any kind, so every generation — including
duplicates, failed runs and abandoned experiments — stayed in the picker
forever. One course had accumulated 55 CDDs, 9 of them from a single afternoon
of retries.

Archive rather than delete, for the same reason courses archive before they
purge: ``CourseDesignDocument.blueprints`` carries ``cascade="all, delete-orphan"``,
so a hard delete of a CDD takes every blueprint derived from it and all of their
version history with it. Reversibility is the whole point of the column.

Strictly additive: both columns are nullable with no server default, so every
existing row reads as live (``deleted_at IS NULL``) without a backfill.

The indexes are partial. List queries only ever ask for live rows, so indexing
``WHERE deleted_at IS NULL`` keeps them at the size of the *working set* rather
than the size of the table — the archive can grow without slowing down reads.

Guarded so it is a no-op where the DB_AUTO_DDL escape hatch already added the
columns/indexes (``_run_legacy_ddl`` carries the same statements).

Revision ID: 000100000021
Revises: 000100000020
"""

import sqlalchemy as sa
from alembic import op

revision = "000100000021"
down_revision = "000100000020"
branch_labels = None
depends_on = None

_TABLES = ("course_design_documents", "module_blueprints")
_INDEXES = {
    "course_design_documents": "idx_cdd_live",
    "module_blueprints": "idx_blueprint_live",
}


def _has_column(table: str, column: str) -> bool:
    bind = op.get_bind()
    return any(c["name"] == column for c in sa.inspect(bind).get_columns(table))


def _has_index(table: str, index: str) -> bool:
    bind = op.get_bind()
    return any(ix["name"] == index for ix in sa.inspect(bind).get_indexes(table))


def upgrade() -> None:
    for table in _TABLES:
        if not _has_column(table, "deleted_at"):
            op.add_column(table, sa.Column("deleted_at", sa.DateTime(), nullable=True))
        if not _has_column(table, "deleted_by"):
            op.add_column(table, sa.Column("deleted_by", sa.String(length=100), nullable=True))

        index = _INDEXES[table]
        if not _has_index(table, index):
            op.create_index(
                index, table, ["course_id"],
                postgresql_where=sa.text("deleted_at IS NULL"),
                sqlite_where=sa.text("deleted_at IS NULL"),
            )


def downgrade() -> None:
    for table in _TABLES:
        if _has_index(table, _INDEXES[table]):
            op.drop_index(_INDEXES[table], table_name=table)
        if _has_column(table, "deleted_by"):
            op.drop_column(table, "deleted_by")
        if _has_column(table, "deleted_at"):
            op.drop_column(table, "deleted_at")
