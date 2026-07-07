"""Prompt consolidation Phase 6 — drop the legacy pl_* tables.

The one-way door. Preconditions (verified before this revision was applied
anywhere, per PROMPT_CONSOLIDATION_PLAN.md Phase 6 / MIGRATION_RUNBOOK.md):

* The Phase 2 carry-over ran on the target and self-verified 126-row parity
  (20 prompts + 20 versions + 46 tags + 32 variables + 6 teams + 2 requests
  accounted for in the native tables) — applied to prod 2026-07-07.
* Plain-SQL dumps of the pl_* tables exist in ``backups/`` and are retained
  off-box (``pl_tables_pre_migration_*.sql``). They are the ONLY automated
  recovery path for this revision.

Explicit DROP ... IF EXISTS because these tables were created by
``create_all()`` (never Alembic-managed) and would otherwise persist as
orphans; IF EXISTS keeps the revision green on fresh databases where the
baseline replay is the only thing that ever created them.

Downgrade is deliberately NOT implemented: recreating empty pl_* shells
would masquerade as recovery while every row stayed lost. Restore from the
pre-migration dump instead (MIGRATION_RUNBOOK.md, "Restore procedures").
"""

from alembic import op

revision = "000100000005"
down_revision = "000100000004"
branch_labels = None
depends_on = None

PL_TABLES = (
    "pl_attachments",
    "pl_reviews",
    "pl_prompt_requests",
    "pl_prompt_teams",
    "pl_prompt_tags",
    "pl_prompt_variables",
    "pl_prompt_versions",
    "pl_audit_events",
    "pl_prompts",
    "pl_teams",
)


def upgrade() -> None:
    bind = op.get_bind()
    bind.exec_driver_sql(
        "DROP TABLE IF EXISTS " + ", ".join(PL_TABLES) + " CASCADE"
    )


def downgrade() -> None:
    raise NotImplementedError(
        "Phase 6 is one-way: pl_* data cannot be recreated by a schema "
        "downgrade. Restore backups/pl_tables_pre_migration_<stamp>.sql "
        "per MIGRATION_RUNBOOK.md instead."
    )
