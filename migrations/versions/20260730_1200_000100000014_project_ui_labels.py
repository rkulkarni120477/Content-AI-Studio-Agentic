"""Add ui_labels to projects for per-tenant UI wording overrides.

Holds a small JSON object of display-label overrides for the tenant, e.g.
{"title": "Block", "style": "Design Guide"}. Display-only — no server code
branches on it. NULL means "this tenant uses the default wording", so the
column is additive and safe to leave empty.

Revision ID: 000100000014
Revises: 000100000013
"""

import sqlalchemy as sa
from alembic import op

revision = "000100000014"
down_revision = "000100000013"
branch_labels = None
depends_on = None


def _has_column(table: str, column: str) -> bool:
    bind = op.get_bind()
    return column in {c["name"] for c in sa.inspect(bind).get_columns(table)}


def upgrade() -> None:
    # Idempotent on purpose. Some environments have this column already because
    # it was applied by hand or by the DB_AUTO_DDL path before alembic caught
    # up (alembic_version lags the real schema on at least one deployment), and
    # a bare add_column would abort the whole upgrade there.
    if not _has_column("projects", "ui_labels"):
        op.add_column(
            "projects",
            sa.Column("ui_labels", sa.Text(), nullable=True),
        )


def downgrade() -> None:
    if _has_column("projects", "ui_labels"):
        op.drop_column("projects", "ui_labels")
