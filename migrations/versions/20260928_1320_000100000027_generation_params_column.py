"""Add generations.generation_params (additive) — records which DIS source
units actually grounded a generation.

PR review (CAS_Findings_v0.1.docx findings 2/12): the column was added to
the ORM model (promptops_app/database.py) but the only DDL for it lived in
_run_legacy_ddl(), which only runs when DB_AUTO_DDL is true (default false).
Prod runs ``alembic upgrade head``, which would find nothing to apply --
SQLAlchemy selects every mapped column, so any ORM load of Generation
(detail, export, the idempotency guard, new inserts) would fail with
UndefinedColumn once deployed.

Strictly additive: nullable, no server default, so a generation created
before this column existed just reads back None. Guarded so it is a no-op
where DB_AUTO_DDL already added the column via the legacy path.

Revision ID: 000100000027
Revises: 000100000026
"""

import sqlalchemy as sa
from alembic import op

revision = "000100000027"
down_revision = "000100000026"
branch_labels = None
depends_on = None


def _has_column(table: str, column: str) -> bool:
    bind = op.get_bind()
    return any(c["name"] == column for c in sa.inspect(bind).get_columns(table))


def upgrade() -> None:
    if not _has_column("generations", "generation_params"):
        op.add_column("generations", sa.Column("generation_params", sa.Text(), nullable=True))


def downgrade() -> None:
    if _has_column("generations", "generation_params"):
        op.drop_column("generations", "generation_params")
