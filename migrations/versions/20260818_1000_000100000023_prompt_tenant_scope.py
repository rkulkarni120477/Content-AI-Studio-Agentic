"""Add prompts.project_id (additive) — tenant isolation for the Prompt Library.

Prompts had no tenant association at all: visibility was gated purely by
role (admin/reviewer see everything), which is a per-tenant credential — any
tenant's admin could browse, search, and even edit/delete every other
tenant's prompts. This column is the fix's data half;
promptops_app.services.prompt_library_service's tenant_scope_condition is the
query half.

NULL means shared/global (visible to every tenant), same convention already
used by Style.project_id. Applies uniformly to both prompt_kind values —
real data checked before writing this migration shows pipeline-kind rows
(component_type style|cdd|blueprint|generate) are NOT always shared system
templates: several are a specific tenant's own customized generation prompt
(e.g. a one-off "style" or "cdd" variant owned by that tenant's user), which
leaked to every other tenant's admin exactly like the library rows did.
System-seeded defaults (is_default=True) are always shared and stay NULL.

Backfill: for existing non-default rows, resolve `owner` (a username) to
that user's own tenant and stamp it — retroactively applies real isolation
to prompts that already have an obvious owning tenant.

Resolves via tenant_memberships, not users.project_id: the latter is
documented (database.py) as "last-active tenant", not ownership, so for a
user who belongs to several tenants it would stamp whichever org they
happened to be logged into most recently — confidently wrong, not merely
imprecise, and it would hide the prompt from its real owning tenant. Only a
user with EXACTLY ONE active membership resolves unambiguously; a user with
zero or several active memberships stays NULL (no historical record ties an
existing prompt to which tenant context created it) — safe fallback, never
silently hides a prompt nobody can already tell used to be tenant-only.

`is_default IS NOT TRUE` rather than `= FALSE`: the column has only a
Python-side default (no server_default), so a row written before the ORM
default applied can have `is_default IS NULL` — `= FALSE` silently skips
those, `IS NOT TRUE` treats NULL the same as FALSE (not a shared default).

Revision ID: 000100000023
Revises: 000100000022
"""

import sqlalchemy as sa
from alembic import op

revision = "000100000023"
down_revision = "000100000022"
branch_labels = None
depends_on = None


def _has_column(table: str, column: str) -> bool:
    bind = op.get_bind()
    return column in {c["name"] for c in sa.inspect(bind).get_columns(table)}


# Module-level so tests/unit/test_prompt_tenant_scope_backfill.py can load this
# file and exercise the exact statement rather than a hand-copied duplicate.
BACKFILL_SQL = """
    UPDATE prompts
    SET project_id = m.project_id
    FROM users u
    JOIN tenant_memberships m ON m.user_id = u.id AND m.active = TRUE
    WHERE prompts.owner = u.username
      AND (prompts.is_default IS NOT TRUE)
      AND prompts.project_id IS NULL
      AND (
          SELECT COUNT(*) FROM tenant_memberships m2
          WHERE m2.user_id = u.id AND m2.active = TRUE
      ) = 1
"""


def upgrade() -> None:
    if not _has_column("prompts", "project_id"):
        op.add_column("prompts", sa.Column("project_id", sa.Integer(), nullable=True))
    op.create_index("ix_prompts_project_id", "prompts", ["project_id"], if_not_exists=True)

    bind = op.get_bind()
    bind.execute(sa.text(BACKFILL_SQL))


def downgrade() -> None:
    op.drop_index("ix_prompts_project_id", table_name="prompts", if_exists=True)
    if _has_column("prompts", "project_id"):
        op.drop_column("prompts", "project_id")
