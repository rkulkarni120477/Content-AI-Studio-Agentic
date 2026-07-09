"""Prompt consolidation — promote prompt_versions.version_number to NOT NULL.

Deferred from Phase 3 because the live write paths did not populate the
column yet; since Phase 8 every version writer (both registry commit paths,
the PL service, seeds, and the Phase 2 carry-over) sets it, and prod was
verified to contain zero NULLs after the 2026-07-07 apply. The backfill
below is defensive only — it re-runs the Phase 3 ordering ((created_at, id)
per prompt) for any row a pre-Phase-8 writer might have left behind.

Downgrade simply drops the constraint; data is untouched either way.
"""

from alembic import op

revision = "000100000004"
down_revision = "000100000003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    bind.exec_driver_sql(
        """
        UPDATE prompt_versions pv
        SET version_number = ranked.rn
        FROM (
            SELECT id,
                   ROW_NUMBER() OVER (
                       PARTITION BY prompt_id ORDER BY created_at, id
                   ) AS rn
            FROM prompt_versions
            WHERE prompt_id IN (
                SELECT DISTINCT prompt_id FROM prompt_versions
                WHERE version_number IS NULL
            )
        ) ranked
        WHERE pv.id = ranked.id AND pv.version_number IS NULL
        """
    )
    op.alter_column("prompt_versions", "version_number", nullable=False)


def downgrade() -> None:
    op.alter_column("prompt_versions", "version_number", nullable=True)
