"""Add generations.job_id (additive) — background-job idempotency key.

Stamped with the originating GenerationJob.id so a generation task retried
after a worker crash (Celery at-least-once delivery, P4.1) can detect its own
prior output and adopt it instead of creating a duplicate Generation/Block set
or double-charging LLM usage.

Strictly additive: the column is nullable with no server default; existing rows
and any generation created outside the background-job path keep NULL. An index
backs the retry-time lookup (``WHERE job_id = :job_id``).

Guarded so it is a no-op where the DB_AUTO_DDL escape hatch already added the
column/index (the legacy ``_run_legacy_ddl`` path carries the same statements).

Revision ID: 000100000016
Revises: 000100000015
"""

import sqlalchemy as sa
from alembic import op

revision = "000100000016"
down_revision = "000100000015"
branch_labels = None
depends_on = None


def _has_column(table: str, column: str) -> bool:
    bind = op.get_bind()
    return any(c["name"] == column for c in sa.inspect(bind).get_columns(table))


def _has_index(table: str, index: str) -> bool:
    bind = op.get_bind()
    return any(ix["name"] == index for ix in sa.inspect(bind).get_indexes(table))


def upgrade() -> None:
    if not _has_column("generations", "job_id"):
        op.add_column("generations", sa.Column("job_id", sa.String(64), nullable=True))
    if not _has_index("generations", "idx_generations_job_id"):
        op.create_index("idx_generations_job_id", "generations", ["job_id"])


def downgrade() -> None:
    if _has_index("generations", "idx_generations_job_id"):
        op.drop_index("idx_generations_job_id", table_name="generations")
    if _has_column("generations", "job_id"):
        op.drop_column("generations", "job_id")
