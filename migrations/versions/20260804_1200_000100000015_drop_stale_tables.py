"""Drop stale/dead tables that no live code references.

Removes six tables confirmed dead by audit (see stale_removal_backup/):
  - tenants              orphaned remnant, no ORM model / FK / query
  - course_imports       superseded by reverse_course_imports (2026-07-19)
  - import_provenance    superseded by reverse_import_provenance
  - import_item_issues   child of course_imports, same abandoned batch
  - document_chunks      orphaned ORM model, never queried (0 rows)
  - ab_test_runs         orphaned ORM model, never queried (0 rows)

The matching ORM models / re-exports were removed from the codebase in the
same change, so the DB_AUTO_DDL create_all path will not recreate them.

Row data was backed up to stale_removal_backup/db/*.restore.sql before this
migration. The downgrade recreates the table STRUCTURE only (idempotently);
reload data with those .restore.sql files after downgrading if needed.

FK note: import_provenance and import_item_issues both reference
course_imports(id), so they are dropped first and recreated last.

Revision ID: 000100000015
Revises: 000100000014
"""

import sqlalchemy as sa
from alembic import op

revision = "000100000015"
down_revision = "000100000014"
branch_labels = None
depends_on = None


def _has_table(table: str) -> bool:
    bind = op.get_bind()
    return sa.inspect(bind).has_table(table)


# ---------------------------------------------------------------------------
# upgrade: drop the stale tables (children before parents), idempotently
# ---------------------------------------------------------------------------
def upgrade() -> None:
    # DROP TABLE IF EXISTS is idempotent and tolerant of environments where a
    # table was already removed by hand. Order matters: drop FK children first.
    for table in (
        "import_item_issues",   # FK -> course_imports
        "import_provenance",    # FK -> course_imports
        "course_imports",       # parent
        "document_chunks",      # FK -> documents (documents stays)
        "tenants",
        "ab_test_runs",
    ):
        op.execute(f'DROP TABLE IF EXISTS "{table}"')


# ---------------------------------------------------------------------------
# downgrade: recreate the table structures (parents before children)
# ---------------------------------------------------------------------------
def downgrade() -> None:
    if not _has_table("course_imports"):
        op.create_table(
            "course_imports",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("project_id", sa.Integer(), nullable=False),
            sa.Column("cluster_id", sa.Integer(), nullable=True),
            sa.Column("course_id", sa.Integer(), nullable=True),
            sa.Column("uploaded_by", sa.String(100), nullable=True),
            sa.Column("original_filename", sa.String(512), nullable=True),
            sa.Column("storage_key", sa.String(512), nullable=True),
            sa.Column("package_size", sa.BigInteger(), nullable=True),
            sa.Column("package_checksum", sa.String(128), nullable=True),
            sa.Column("source_format", sa.String(30), server_default="canvas_imscc", nullable=True),
            sa.Column("cartridge_version", sa.String(30), nullable=True),
            sa.Column("status", sa.String(30), server_default="uploaded", nullable=True),
            sa.Column("progress", sa.Integer(), server_default="0", nullable=True),
            sa.Column("current_stage", sa.String(80), nullable=True),
            sa.Column("validation_summary_json", sa.Text(), nullable=True),
            sa.Column("structure_summary_json", sa.Text(), nullable=True),
            sa.Column("warning_count", sa.Integer(), server_default="0", nullable=True),
            sa.Column("failure_count", sa.Integer(), server_default="0", nullable=True),
            sa.Column("error_message", sa.Text(), nullable=True),
            sa.Column("job_id", sa.String(64), nullable=True),
            sa.Column("cancel_requested", sa.Boolean(), server_default=sa.false(), nullable=True),
            sa.Column("editor_ready_at", sa.DateTime(), nullable=True),
            sa.Column("completed_at", sa.DateTime(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=True),
            sa.Column("updated_at", sa.DateTime(), nullable=True),
        )
        op.create_index("ix_course_imports_status", "course_imports", ["status"])
        op.create_index("ix_course_imports_course_id", "course_imports", ["course_id"])
        op.create_index("ix_course_imports_project_id", "course_imports", ["project_id"])

    if not _has_table("import_provenance"):
        op.create_table(
            "import_provenance",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("project_id", sa.Integer(), nullable=False),
            sa.Column("course_id", sa.Integer(), nullable=False),
            sa.Column("import_id", sa.Integer(), nullable=True),
            sa.Column("source_format", sa.String(30), server_default="canvas_imscc", nullable=True),
            sa.Column("source_external_id", sa.String(255), nullable=True),
            sa.Column("source_resource_identifier", sa.String(255), nullable=True),
            sa.Column("source_manifest_path", sa.String(512), nullable=True),
            sa.Column("source_item_type", sa.String(60), nullable=True),
            sa.Column("source_parent_external_id", sa.String(255), nullable=True),
            sa.Column("cas_entity_type", sa.String(50), nullable=True),
            sa.Column("cas_entity_id", sa.Integer(), nullable=True),
            sa.Column("cas_parent_entity_id", sa.Integer(), nullable=True),
            sa.Column("original_checksum", sa.String(128), nullable=True),
            sa.Column("current_checksum", sa.String(128), nullable=True),
            sa.Column("original_metadata_json", sa.Text(), nullable=True),
            sa.Column("mapping_status", sa.String(20), server_default="mapped", nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=True),
            sa.Column("updated_at", sa.DateTime(), nullable=True),
            sa.ForeignKeyConstraint(
                ["import_id"], ["course_imports.id"],
                name="fk_import_provenance_import_id", ondelete="CASCADE",
            ),
        )
        op.create_index("ix_import_provenance_cas_entity_id", "import_provenance", ["cas_entity_id"])
        op.create_index("ix_import_provenance_source_external_id", "import_provenance", ["source_external_id"])
        op.create_index("ix_import_provenance_import_id", "import_provenance", ["import_id"])
        op.create_index("ix_import_provenance_project_id", "import_provenance", ["project_id"])
        op.create_index("ix_import_provenance_course_id", "import_provenance", ["course_id"])
        op.create_index("ix_import_provenance_mapping_status", "import_provenance", ["mapping_status"])

    if not _has_table("import_item_issues"):
        op.create_table(
            "import_item_issues",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("import_id", sa.Integer(), nullable=False),
            sa.Column("course_id", sa.Integer(), nullable=True),
            sa.Column("source_external_id", sa.String(255), nullable=True),
            sa.Column("source_path", sa.String(512), nullable=True),
            sa.Column("item_type", sa.String(60), nullable=True),
            sa.Column("severity", sa.String(20), server_default="warning", nullable=True),
            sa.Column("issue_code", sa.String(80), nullable=True),
            sa.Column("message", sa.Text(), nullable=True),
            sa.Column("resolution_status", sa.String(20), server_default="open", nullable=True),
            sa.Column("resolution_note", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=True),
            sa.Column("resolved_at", sa.DateTime(), nullable=True),
            sa.ForeignKeyConstraint(
                ["import_id"], ["course_imports.id"],
                name="fk_import_item_issues_import_id", ondelete="CASCADE",
            ),
        )
        op.create_index("ix_import_item_issues_severity", "import_item_issues", ["severity"])
        op.create_index("ix_import_item_issues_course_id", "import_item_issues", ["course_id"])
        op.create_index("ix_import_item_issues_import_id", "import_item_issues", ["import_id"])

    if not _has_table("document_chunks"):
        op.create_table(
            "document_chunks",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("document_id", sa.Integer(), nullable=False),
            sa.Column("chunk_index", sa.Integer(), nullable=False),
            sa.Column("content", sa.Text(), nullable=False),
            sa.Column("token_estimate", sa.Integer(), nullable=True),
            sa.Column("embedding_json", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=True),
            sa.ForeignKeyConstraint(
                ["document_id"], ["documents.id"],
                name="document_chunks_document_id_fkey",
            ),
        )
        op.create_index("idx_document_chunks_document_id", "document_chunks", ["document_id"])

    if not _has_table("tenants"):
        op.create_table(
            "tenants",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("slug", sa.String(64), nullable=False),
            sa.Column("name", sa.String(200), nullable=False),
            sa.Column("max_users", sa.Integer(), nullable=False),
            sa.Column("status", sa.String(20), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=True),
            sa.Column("created_by", sa.String(100), nullable=True),
        )
        op.create_index("ix_tenants_slug", "tenants", ["slug"], unique=True)

    if not _has_table("ab_test_runs"):
        op.create_table(
            "ab_test_runs",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("prompt_name", sa.String(150), nullable=False),
            sa.Column("variant_a", sa.String(50), nullable=False),
            sa.Column("variant_b", sa.String(50), nullable=False),
            sa.Column("topic", sa.String(255), nullable=False),
            sa.Column("output_a", sa.Text(), nullable=False),
            sa.Column("output_b", sa.Text(), nullable=False),
            sa.Column("created_by", sa.String(100), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=True),
        )
