"""Prompt consolidation Phase 3 — additive unified-prompt schema.

Implements PROMPT_CONSOLIDATION_PLAN.md Phase 1 (as verified/signed off
2026-07-05):

* ``prompts``       — prompt_kind / title / category / visibility / variant /
                      parent_id / last_used_at / deleted_at.
* ``prompt_versions`` — version_number (NULLABLE; NOT NULL lands in Phase 4
                      once write paths populate it) backfilled by
                      ``(created_at, id)``; workflow_state;
                      UniqueConstraint(prompt_id, version_number).
* ``audit_logs``    — actor_role / user_agent / changes / summary.
* Seven new tables replacing pl_*: prompt_tags, prompt_variables,
  prompt_attachments, teams, prompt_team_links, prompt_reviews,
  prompt_requests.
* Partial unique index guaranteeing one default prompt per pipeline
  stage/variant — declared NULLS NOT DISTINCT (all current defaults have a
  NULL variant; prod is PostgreSQL 17.9). This index is intentionally NOT
  declared on the SQLAlchemy model (SQLite test DBs can't express it) —
  strip it from future autogenerate diffs.

Strictly additive: no pl_* table is touched (that is Phase 6), no existing
column is altered. Existing rows get prompt_kind='pipeline' via the column's
server_default (all pre-existing prompts are pipeline templates).
"""

import sqlalchemy as sa
from alembic import op

revision = "000100000002"
down_revision = "000100000001"
branch_labels = None
depends_on = None

_PARTIAL_UNIQUE_INDEX = "uq_prompts_default_per_component_variant"


def _preflight(bind) -> None:
    """Abort (and roll back) if prod data violates the constraints we add."""
    dupes = bind.exec_driver_sql(
        "SELECT component_type, count(*) FROM prompts "
        "WHERE is_default = true GROUP BY component_type HAVING count(*) > 1"
    ).fetchall()
    if dupes:
        raise RuntimeError(
            f"Pre-flight failed: multiple is_default prompts per component: {dupes}. "
            "Resolve duplicates before the partial unique index can be created."
        )


def upgrade() -> None:
    bind = op.get_bind()
    _preflight(bind)

    # -- prompts: unified-model columns -------------------------------------
    op.add_column("prompts", sa.Column("prompt_kind", sa.String(20),
                                       nullable=False, server_default="pipeline"))
    op.add_column("prompts", sa.Column("title", sa.String(300), nullable=True))
    op.add_column("prompts", sa.Column("category", sa.String(100), nullable=True))
    op.add_column("prompts", sa.Column("visibility", sa.String(20),
                                       nullable=False, server_default="draft"))
    op.add_column("prompts", sa.Column("variant", sa.String(50), nullable=True))
    op.add_column("prompts", sa.Column(
        "parent_id", sa.Integer(),
        sa.ForeignKey("prompts.id", ondelete="CASCADE",
                      name="fk_prompts_parent_id_prompts"),
        nullable=True))
    op.add_column("prompts", sa.Column("last_used_at", sa.DateTime(), nullable=True))
    op.add_column("prompts", sa.Column("deleted_at", sa.DateTime(), nullable=True))
    op.create_index("ix_prompts_parent_id", "prompts", ["parent_id"])
    op.create_index("ix_prompts_deleted_at", "prompts", ["deleted_at"])

    # One default per pipeline stage/variant. NULLS NOT DISTINCT is required:
    # every current default has variant NULL, and default index semantics
    # would let two NULL-variant defaults coexist.
    op.execute(
        f"CREATE UNIQUE INDEX {_PARTIAL_UNIQUE_INDEX} "
        "ON prompts (component_type, variant) NULLS NOT DISTINCT "
        "WHERE is_default = true AND prompt_kind = 'pipeline'"
    )

    # -- prompt_versions: numeric ordering + approval state ------------------
    op.add_column("prompt_versions",
                  sa.Column("version_number", sa.Integer(), nullable=True))
    op.add_column("prompt_versions",
                  sa.Column("workflow_state", sa.String(20),
                            nullable=False, server_default="active"))
    # Backfill ordered by (created_at, id): created_at alone is ambiguous
    # (prompt 1's two seeded versions share a timestamp; id order matches
    # their v1/v2 labels).
    op.execute(
        "UPDATE prompt_versions pv SET version_number = t.rn "
        "FROM (SELECT id, row_number() OVER "
        "      (PARTITION BY prompt_id ORDER BY created_at, id) AS rn "
        "      FROM prompt_versions) t "
        "WHERE pv.id = t.id"
    )
    op.create_unique_constraint(
        "uq_prompt_versions_prompt_id_version_number",
        "prompt_versions", ["prompt_id", "version_number"])

    # -- audit_logs: additive governance columns ----------------------------
    op.add_column("audit_logs", sa.Column("actor_role", sa.String(64), nullable=True))
    op.add_column("audit_logs", sa.Column("user_agent", sa.String(512), nullable=True))
    op.add_column("audit_logs", sa.Column("changes", sa.JSON(), nullable=True))
    op.add_column("audit_logs", sa.Column("summary", sa.Text(), nullable=True))

    # -- new tables (native replacements for pl_*) ---------------------------
    op.create_table(
        "prompt_tags",
        sa.Column("prompt_id", sa.Integer(),
                  sa.ForeignKey("prompts.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("tag", sa.String(100), primary_key=True),
    )
    op.create_table(
        "prompt_variables",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("prompt_id", sa.Integer(),
                  sa.ForeignKey("prompts.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("label", sa.String(200)),
        sa.Column("hint", sa.Text()),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
    )
    op.create_index("ix_prompt_variables_prompt_id", "prompt_variables", ["prompt_id"])
    op.create_table(
        "prompt_attachments",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("prompt_id", sa.Integer(),
                  sa.ForeignKey("prompts.id", ondelete="CASCADE"), nullable=False),
        sa.Column("original_name", sa.String(300)),
        sa.Column("stored_name", sa.String(500)),
        sa.Column("size_bytes", sa.BigInteger()),
        sa.Column("uploaded_by", sa.String(100)),
        sa.Column("uploaded_at", sa.DateTime()),
    )
    op.create_index("ix_prompt_attachments_prompt_id", "prompt_attachments", ["prompt_id"])
    op.create_table(
        "teams",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("name", sa.String(100), nullable=False, unique=True),
        sa.Column("created_by", sa.String(100)),
        sa.Column("created_at", sa.DateTime()),
    )
    op.create_table(
        "prompt_team_links",
        sa.Column("prompt_id", sa.Integer(),
                  sa.ForeignKey("prompts.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("team_id", sa.Integer(),
                  sa.ForeignKey("teams.id", ondelete="CASCADE"), primary_key=True),
    )
    op.create_table(
        "prompt_reviews",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("prompt_id", sa.Integer(),
                  sa.ForeignKey("prompts.id", ondelete="CASCADE"), nullable=False),
        sa.Column("username", sa.String(100), nullable=False),
        sa.Column("rating", sa.SmallInteger(), nullable=False),
        sa.Column("feedback", sa.Text()),
        sa.Column("created_at", sa.DateTime()),
        sa.Column("updated_at", sa.DateTime()),
        sa.UniqueConstraint("prompt_id", "username",
                            name="uq_prompt_reviews_prompt_id_username"),
    )
    op.create_index("ix_prompt_reviews_prompt_id", "prompt_reviews", ["prompt_id"])
    op.create_table(
        "prompt_requests",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("title", sa.String(300), nullable=False),
        sa.Column("description", sa.Text()),
        sa.Column("type", sa.String(20), nullable=False, server_default="new"),
        sa.Column("prompt_id", sa.Integer(),
                  sa.ForeignKey("prompts.id", ondelete="SET NULL"), nullable=True),
        sa.Column("requested_by", sa.String(100), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="open"),
        sa.Column("admin_notes", sa.Text()),
        sa.Column("created_at", sa.DateTime()),
        sa.Column("updated_at", sa.DateTime()),
    )
    op.create_index("ix_prompt_requests_status", "prompt_requests", ["status"])


def downgrade() -> None:
    # New tables (children of teams first).
    op.drop_table("prompt_requests")
    op.drop_table("prompt_reviews")
    op.drop_table("prompt_team_links")
    op.drop_table("teams")
    op.drop_table("prompt_attachments")
    op.drop_table("prompt_variables")
    op.drop_table("prompt_tags")

    # audit_logs additions.
    op.drop_column("audit_logs", "summary")
    op.drop_column("audit_logs", "changes")
    op.drop_column("audit_logs", "user_agent")
    op.drop_column("audit_logs", "actor_role")

    # prompt_versions additions.
    op.drop_constraint("uq_prompt_versions_prompt_id_version_number",
                       "prompt_versions", type_="unique")
    op.drop_column("prompt_versions", "workflow_state")
    op.drop_column("prompt_versions", "version_number")

    # prompts additions.
    op.execute(f"DROP INDEX IF EXISTS {_PARTIAL_UNIQUE_INDEX}")
    op.drop_index("ix_prompts_deleted_at", table_name="prompts")
    op.drop_index("ix_prompts_parent_id", table_name="prompts")
    op.drop_constraint("fk_prompts_parent_id_prompts", "prompts", type_="foreignkey")
    op.drop_column("prompts", "deleted_at")
    op.drop_column("prompts", "last_used_at")
    op.drop_column("prompts", "parent_id")
    op.drop_column("prompts", "variant")
    op.drop_column("prompts", "visibility")
    op.drop_column("prompts", "category")
    op.drop_column("prompts", "title")
    op.drop_column("prompts", "prompt_kind")
