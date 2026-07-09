"""Baseline — capture the full production schema as of 2026-07-05.

This is the first Alembic revision. It brings a database under Alembic
control in one of two ways:

1. **Empty database** (fresh dev / rehearsal / CI): executes
   ``baseline_schema.sql`` — a schema-only ``pg_dump`` of production taken
   2026-07-05 — reproducing today's schema exactly (all 47 tables, including
   the 10 ``pl_*`` tables and the orphaned ``tenants``/``tenant_id`` remnants;
   those are removed by *later* explicit migrations, not silently dropped here).

2. **Already-provisioned database** (prod RDS, long-lived dev DBs): detects
   the existing schema and does nothing — Alembic simply records this
   revision as applied ("self-stamping"). This makes the deploy pipeline's
   ``alembic upgrade head`` safe even if it runs before anyone manually
   stamps production.

Notes
-----
- The schema was historically created by ``Base.metadata.create_all()`` plus
  the raw ``ALTER``/``CREATE INDEX`` statements in ``init_db()``
  (promptops_app/database.py). From this revision on, Alembic owns the
  schema; ``init_db()``'s DDL is gated behind ``DB_AUTO_DDL`` and off by
  default.
- Postgres-only (the SQL file is a pg_dump). Tests use SQLite via
  ``create_all`` and never run Alembic.
- ``downgrade`` is deliberately not implemented: there is no meaningful
  schema "below" the baseline.

Revision ID: 000100000001
Revises:
Create Date: 2026-07-05 07:00:00+00:00

"""

from collections.abc import Sequence
from pathlib import Path

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "000100000001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_SQL_FILE = Path(__file__).parent / "baseline_schema.sql"

# A table that has existed since the first release — its presence means the
# schema was already provisioned (by create_all) before Alembic took over.
_SENTINEL_TABLE = "users"


def upgrade() -> None:
    bind = op.get_bind()
    if sa.inspect(bind).has_table(_SENTINEL_TABLE):
        # Existing install: schema already present via create_all()/init_db().
        # Do nothing — Alembic records the baseline as applied.
        print(
            f"[baseline] table '{_SENTINEL_TABLE}' already exists — "
            "existing install detected, skipping DDL (self-stamp only)."
        )
        return

    sql = _SQL_FILE.read_text()
    # psycopg2 executes the multi-statement dump in a single call; the whole
    # migration runs inside Alembic's transaction (transactional DDL).
    bind.exec_driver_sql(sql)


def downgrade() -> None:
    raise RuntimeError(
        "The baseline revision cannot be downgraded — there is no schema "
        "state below it. Restore from a backup instead."
    )
