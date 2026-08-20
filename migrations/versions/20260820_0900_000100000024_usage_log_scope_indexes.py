"""Composite (scope, created_at) indexes on llm_usage_logs.

current_period_usage()'s SUM() — the Platform Admin dashboard's query, and
now also the post-call usage-summary toast's (see
_build_usage_summary in budget_service.py) — filters on one of
project_id/course_id/user_id plus a created_at >= window_start range. Each
column already has its own single-column index, but a range condition on
created_at combined with an equality on the scope column is what a
composite index is for; Postgres can bitmap-AND the two single-column
indexes instead, but that's strictly more work per query.

Only worth doing now because the toast made this a per-completed-job-poll
query instead of an admin-dashboard-only one (jobs.py only calls it once per
job, but that endpoint is polled) — per the module's own note, a rollup
table is for if this is ever measured slow; a plain index is cheaper than
that and safe to add regardless.

Revision ID: 000100000024
Revises: 000100000023
"""

from alembic import op

revision = "000100000024"
down_revision = "000100000023"
branch_labels = None
depends_on = None

_INDEXES = (
    ("ix_llm_usage_logs_project_created", "project_id"),
    ("ix_llm_usage_logs_course_created", "course_id"),
    ("ix_llm_usage_logs_user_created", "user_id"),
)


def upgrade() -> None:
    for name, column in _INDEXES:
        op.create_index(name, "llm_usage_logs", [column, "created_at"], if_not_exists=True)


def downgrade() -> None:
    for name, _column in _INDEXES:
        op.drop_index(name, table_name="llm_usage_logs", if_exists=True)
