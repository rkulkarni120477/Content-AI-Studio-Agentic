"""Add langfuse_trace_id to llm_usage_logs (P1.2 of claude_plan_platform_hardening).

Persists the mapping between a logged LLM call and the Langfuse trace created
for it — this is what P1.4's trace-detail endpoint looks up by CAS resource id
(entity_type/entity_id), never a client-supplied trace id. NULL means either
tracing was skipped (Langfuse unreachable/unconfigured) or the row predates P1 —
both are valid, non-error states, so the column is additive and nullable.

Revision ID: 000100000015
Revises: 000100000014
"""

import sqlalchemy as sa
from alembic import op

revision = "000100000015"
down_revision = "000100000014"
branch_labels = None
depends_on = None


def _has_column(table: str, column: str) -> bool:
    bind = op.get_bind()
    return column in {c["name"] for c in sa.inspect(bind).get_columns(table)}


def _has_index(table: str, index_name: str) -> bool:
    bind = op.get_bind()
    return index_name in {ix["name"] for ix in sa.inspect(bind).get_indexes(table)}


def upgrade() -> None:
    # Idempotent — this codebase has a known DB_AUTO_DDL path where columns can
    # already exist before alembic's bookkeeping catches up (see migration
    # 000100000014's own note); a bare add_column would abort the upgrade there.
    if not _has_column("llm_usage_logs", "langfuse_trace_id"):
        op.add_column(
            "llm_usage_logs",
            sa.Column("langfuse_trace_id", sa.String(length=64), nullable=True),
        )
    if not _has_index("llm_usage_logs", "ix_llm_usage_logs_langfuse_trace_id"):
        op.create_index(
            "ix_llm_usage_logs_langfuse_trace_id",
            "llm_usage_logs",
            ["langfuse_trace_id"],
        )


def downgrade() -> None:
    if _has_index("llm_usage_logs", "ix_llm_usage_logs_langfuse_trace_id"):
        op.drop_index("ix_llm_usage_logs_langfuse_trace_id", table_name="llm_usage_logs")
    if _has_column("llm_usage_logs", "langfuse_trace_id"):
        op.drop_column("llm_usage_logs", "langfuse_trace_id")
