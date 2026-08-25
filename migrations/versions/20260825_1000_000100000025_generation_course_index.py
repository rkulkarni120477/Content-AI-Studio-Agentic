"""Index generations.course_id.

_empty_shell_course_ids (course_repository.py) runs a correlated
``NOT EXISTS (SELECT 1 FROM generations WHERE course_id = courses.id)`` per
candidate course on every Titles-page load (list_courses_for_cluster).
``generations`` holds one row per generated block — the highest-volume table
in the schema — and its only existing index is on ``job_id``, so that
NOT EXISTS was a full table scan per call. course_modules.course_id already
has an index (its column is declared ``index=True``); this brings
generations.course_id to parity.

Revision ID: 000100000025
Revises: 000100000024
"""

from alembic import op

revision = "000100000025"
down_revision = "000100000024"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index("ix_generations_course_id", "generations", ["course_id"], if_not_exists=True)


def downgrade() -> None:
    op.drop_index("ix_generations_course_id", table_name="generations", if_exists=True)
