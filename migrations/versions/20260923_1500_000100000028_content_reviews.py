"""Add content_reviews table (CE Agent Review, Step 2 — run skeleton).

One row per review run of a single generation (lesson). Scoped to a tenant via
project_id. Records which basis the content was judged against (an uploaded
checklist, or the project's Style writing rules as a fallback) and its version,
the content fingerprint (so an identical re-run is skipped), the run lifecycle
status, and — from later steps — the verdict and counts.

Additive only. Gated by CE_REVIEW_ENABLED; unused until the feature is on.

Revision ID: 000100000028
Revises: 000100000027
"""

import sqlalchemy as sa
from alembic import op

revision = "000100000028"
down_revision = "000100000027"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "content_reviews",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("generation_id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=True),
        sa.Column("course_id", sa.Integer(), nullable=True),
        # Review basis: which rules the content was judged against.
        sa.Column("checklist_id", sa.Integer(), nullable=True),
        sa.Column("review_basis", sa.String(20), nullable=False, server_default="none"),  # checklist | style | none
        sa.Column("checklist_version", sa.String(40), nullable=True),
        # Content identity for skip-unchanged.
        sa.Column("content_fingerprint", sa.String(64), nullable=False),
        # Lifecycle.
        sa.Column("job_id", sa.String(64), nullable=True),
        sa.Column("run_status", sa.String(20), nullable=False, server_default="queued"),
        sa.Column("verdict", sa.String(30), nullable=True),          # computed in Step 6
        sa.Column("counts", sa.JSON(), nullable=True),               # findings/checklist tallies
        sa.Column("model_used", sa.String(160), nullable=True),
        sa.Column("rerun_of_id", sa.Integer(), nullable=True),       # self-ref, for Step 7
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_by", sa.String(100), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
    )
    op.create_foreign_key(
        "fk_content_reviews_generation_id_generations",
        "content_reviews", "generations", ["generation_id"], ["id"],
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        "fk_content_reviews_checklist_id_review_checklists",
        "content_reviews", "review_checklists", ["checklist_id"], ["id"],
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        "fk_content_reviews_rerun_of_id_content_reviews",
        "content_reviews", "content_reviews", ["rerun_of_id"], ["id"],
        ondelete="SET NULL",
    )
    op.create_index("ix_content_reviews_generation_id", "content_reviews", ["generation_id"])
    op.create_index("ix_content_reviews_project_id", "content_reviews", ["project_id"])
    op.create_index("ix_content_reviews_run_status", "content_reviews", ["run_status"])
    op.create_index("ix_content_reviews_content_fingerprint", "content_reviews", ["content_fingerprint"])
    op.create_index(
        "ix_content_reviews_generation_created",
        "content_reviews", ["generation_id", "created_at"],
    )
    # One active (queued|running) run per generation — enforced in the DB so two
    # concurrent Start clicks cannot both launch and double-charge LLM calls.
    op.create_index(
        "ix_content_reviews_one_active",
        "content_reviews", ["generation_id"],
        unique=True,
        postgresql_where=sa.text("run_status IN ('queued', 'running')"),
    )


def downgrade() -> None:
    op.drop_index("ix_content_reviews_one_active", table_name="content_reviews")
    op.drop_index("ix_content_reviews_generation_created", table_name="content_reviews")
    op.drop_index("ix_content_reviews_content_fingerprint", table_name="content_reviews")
    op.drop_index("ix_content_reviews_run_status", table_name="content_reviews")
    op.drop_index("ix_content_reviews_project_id", table_name="content_reviews")
    op.drop_index("ix_content_reviews_generation_id", table_name="content_reviews")
    op.drop_table("content_reviews")
