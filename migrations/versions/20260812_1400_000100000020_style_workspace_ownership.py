"""Add styles.project_id / styles.course_id (additive) — workspace ownership.

Before this, ``styles`` had no ownership columns: a style's only link to a
workspace was the ``active_style_id`` pointer on ``courses``/``projects``. That
made get_styles() fall back to showing a course only its *active* style, hiding
every other style the course had generated.

These nullable columns stamp the owning project/course at creation time so a
course's Generated Styles list can show all the styles it created while still
excluding sibling courses' styles.

Strictly additive: both columns are nullable with no server default; existing
rows stay NULL until the backfill below (or the runtime repair in
``_run_data_backfills``) recovers ownership from the activation pointers.

Backfill recovers ownership from the activation pointers:
  * a style a course has activated → owned by that course (and its project)
  * a style a project has set as default → owned by that project
It only fills NULLs, so it is safe to re-run and never overwrites a real owner.

Guarded so it is a no-op where the DB_AUTO_DDL escape hatch already added the
columns/indexes (the legacy ``_run_legacy_ddl`` path carries the same statements).

Revision ID: 000100000020
Revises: 000100000019
"""

import sqlalchemy as sa
from alembic import op

revision = "000100000020"
down_revision = "000100000019"
branch_labels = None
depends_on = None


def _has_column(table: str, column: str) -> bool:
    bind = op.get_bind()
    return any(c["name"] == column for c in sa.inspect(bind).get_columns(table))


def _has_index(table: str, index: str) -> bool:
    bind = op.get_bind()
    return any(ix["name"] == index for ix in sa.inspect(bind).get_indexes(table))


def upgrade() -> None:
    if not _has_column("styles", "project_id"):
        op.add_column("styles", sa.Column("project_id", sa.Integer(), nullable=True))
    if not _has_column("styles", "course_id"):
        op.add_column("styles", sa.Column("course_id", sa.Integer(), nullable=True))
    if not _has_index("styles", "idx_styles_course_id"):
        op.create_index("idx_styles_course_id", "styles", ["course_id"])
    if not _has_index("styles", "idx_styles_project_id"):
        op.create_index("idx_styles_project_id", "styles", ["project_id"])

    # Backfill ownership from the activation pointers. COALESCE keeps any owner
    # already set; the WHERE ... IS NULL guard makes the whole step idempotent.
    bind = op.get_bind()
    bind.execute(sa.text("""
        UPDATE styles
        SET course_id  = c.id,
            project_id = COALESCE(styles.project_id, c.project_id)
        FROM courses c
        WHERE c.active_style_id = styles.id
          AND styles.course_id IS NULL
    """) if bind.dialect.name == "postgresql" else sa.text("""
        UPDATE styles
        SET course_id = (
                SELECT c.id FROM courses c WHERE c.active_style_id = styles.id LIMIT 1
            ),
            project_id = COALESCE(
                styles.project_id,
                (SELECT c.project_id FROM courses c WHERE c.active_style_id = styles.id LIMIT 1)
            )
        WHERE styles.course_id IS NULL
          AND EXISTS (SELECT 1 FROM courses c WHERE c.active_style_id = styles.id)
    """))
    bind.execute(sa.text("""
        UPDATE styles
        SET project_id = p.id
        FROM projects p
        WHERE p.active_style_id = styles.id
          AND styles.project_id IS NULL
    """) if bind.dialect.name == "postgresql" else sa.text("""
        UPDATE styles
        SET project_id = (
                SELECT p.id FROM projects p WHERE p.active_style_id = styles.id LIMIT 1
            )
        WHERE styles.project_id IS NULL
          AND EXISTS (SELECT 1 FROM projects p WHERE p.active_style_id = styles.id)
    """))


def downgrade() -> None:
    if _has_index("styles", "idx_styles_project_id"):
        op.drop_index("idx_styles_project_id", table_name="styles")
    if _has_index("styles", "idx_styles_course_id"):
        op.drop_index("idx_styles_course_id", table_name="styles")
    if _has_column("styles", "course_id"):
        op.drop_column("styles", "course_id")
    if _has_column("styles", "project_id"):
        op.drop_column("styles", "project_id")
