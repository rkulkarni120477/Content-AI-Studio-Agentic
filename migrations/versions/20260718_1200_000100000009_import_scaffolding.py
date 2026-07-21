"""Reverse pipeline (IMSCC import) scaffolding — additive only.

Adds the metadata + provenance surface the course-import subsystem needs,
WITHOUT touching any existing column or table:

  * courses.source_type  (nullable)  — "scratch" (None) | "imscc"; display/analytics only.
  * courses.import_id     (nullable)  — soft link to course_imports.id.
  * course_imports        (new table) — one row per import attempt.
  * import_provenance     (new table) — Canvas-item ↔ CAS-entity map for round-trip.

Strictly additive & reversible: only ADD COLUMN (nullable, no backfill) and
CREATE TABLE. Existing rows read as scratch. Nothing downstream branches on
these — see reverse_cas.md "same rows, different origin".
"""

import sqlalchemy as sa
from alembic import op

revision = "000100000009"
down_revision = "000100000008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── courses: additive metadata columns (nullable, no default backfill) ──────
    op.add_column("courses", sa.Column("source_type", sa.String(20), nullable=True))
    op.add_column("courses", sa.Column("import_id", sa.Integer(), nullable=True))

    # ── course_imports: one row per import attempt ──────────────────────────────
    op.create_table(
        "course_imports",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("course_id", sa.Integer(), nullable=True, index=True),
        sa.Column("project_id", sa.Integer(), nullable=True, index=True),
        sa.Column("uploaded_by", sa.String(100), nullable=True),
        sa.Column("package_name", sa.String(255), nullable=True),
        sa.Column("package_size", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(30), nullable=False, server_default="pending"),
        sa.Column("structure_counts_json", sa.Text(), nullable=True),
        sa.Column("warnings_json", sa.Text(), nullable=True),
        sa.Column("provenance_ready", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
    )

    # ── import_provenance: Canvas-item ↔ CAS-entity map (round-trip fidelity) ────
    op.create_table(
        "import_provenance",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("import_id", sa.Integer(), nullable=False, index=True),
        sa.Column("canvas_identifier", sa.String(255), nullable=False),
        sa.Column("canvas_type", sa.String(20), nullable=False),        # page | quiz | module
        sa.Column("cas_entity_type", sa.String(20), nullable=False),    # block | module
        sa.Column("cas_entity_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("import_provenance")
    op.drop_table("course_imports")
    op.drop_column("courses", "import_id")
    op.drop_column("courses", "source_type")
