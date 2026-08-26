"""Make ix_generations_course_id self-healing if a prior CONCURRENTLY build failed.

000100000025 built ix_generations_course_id with ``postgresql_concurrently=True,
if_not_exists=True``. CONCURRENTLY needs autocommit and can fail part-way (lock
timeout, deadlock, cancellation), which leaves an *invalid* index behind — one
the planner ignores entirely, silently reintroducing the full table scan on
``generations`` (the highest-volume table in the schema; see 000100000025's
own docstring) that the index exists to remove.

``IF NOT EXISTS`` resolves by relation name, and an invalid index still holds
its name, so a rerun of 000100000025 as originally written would silently
skip rebuilding it and report success. Fixing that in place in 000100000025
would not help anywhere that migration has already run — Alembic tracks
applied revisions by ID, not content, so an environment already stamped at
000100000025 would never re-execute an edited version of it. This migration
is a new, separate step so it actually runs everywhere, dropping the index
before recreating it — self-healing on a rerun; a no-op drop on a first run
or one that already succeeded.

Revision ID: 000100000026
Revises: 000100000025
"""

from alembic import op

revision = "000100000026"
down_revision = "000100000025"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.drop_index(
            "ix_generations_course_id", table_name="generations",
            if_exists=True, postgresql_concurrently=True,
        )
        op.create_index(
            "ix_generations_course_id", "generations", ["course_id"],
            postgresql_concurrently=True,
        )


def downgrade() -> None:
    # Leaves the index in place — reverting to 000100000025's shape would
    # mean dropping and recreating with if_not_exists again, which is not a
    # meaningful downgrade of THIS migration (it changes nothing about the
    # index's final definition, only how a failed build recovers).
    pass
