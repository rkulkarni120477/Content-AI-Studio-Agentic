"""Rename llm_usage_logs.langfuse_trace_id to trace_id.

The column now holds a Phoenix trace_id (hex string), not the previous
tracer's — the values are just a generic external-tracer reference either
way, so existing rows are preserved as-is under the new name rather than
dropped and re-added.

Revision ID: 000100000021
Revises: 000100000020
"""

import sqlalchemy as sa
from alembic import op

revision = "000100000021"
down_revision = "000100000020"
branch_labels = None
depends_on = None


def _has_column(table: str, column: str) -> bool:
    bind = op.get_bind()
    return column in {c["name"] for c in sa.inspect(bind).get_columns(table)}


def _has_index(table: str, index_name: str) -> bool:
    bind = op.get_bind()
    return index_name in {ix["name"] for ix in sa.inspect(bind).get_indexes(table)}


def upgrade() -> None:
    # Idempotent — see 000100000017's own note on the DB_AUTO_DDL escape hatch.
    if _has_column("llm_usage_logs", "langfuse_trace_id") and not _has_column("llm_usage_logs", "trace_id"):
        op.alter_column("llm_usage_logs", "langfuse_trace_id", new_column_name="trace_id")
    if _has_index("llm_usage_logs", "ix_llm_usage_logs_langfuse_trace_id"):
        op.drop_index("ix_llm_usage_logs_langfuse_trace_id", table_name="llm_usage_logs")
    if not _has_index("llm_usage_logs", "ix_llm_usage_logs_trace_id"):
        op.create_index("ix_llm_usage_logs_trace_id", "llm_usage_logs", ["trace_id"])


def downgrade() -> None:
    if _has_index("llm_usage_logs", "ix_llm_usage_logs_trace_id"):
        op.drop_index("ix_llm_usage_logs_trace_id", table_name="llm_usage_logs")
    if not _has_index("llm_usage_logs", "ix_llm_usage_logs_langfuse_trace_id"):
        op.create_index("ix_llm_usage_logs_langfuse_trace_id", "llm_usage_logs", ["langfuse_trace_id"])
    if _has_column("llm_usage_logs", "trace_id") and not _has_column("llm_usage_logs", "langfuse_trace_id"):
        op.alter_column("llm_usage_logs", "trace_id", new_column_name="langfuse_trace_id")
