"""Prompt consolidation Phase 9 — shared fragment library (additive).

Two new tables: ``prompt_fragments`` (one row per stable fragment key) and
``prompt_fragment_versions`` (append-only snapshots, one active per fragment).
No seeds here — ``seed_prompt_fragments()`` (called from ``seed_data()`` and
``scripts/seed_prompt_fragments.py``) populates persona_tone / style_guide
from the legacy constants, keeping the constants as the code fallback tier.

Purely additive: nothing reads these tables until the fragment tier resolves
(flag-gated like the Phase 8 registry tiers), and with identical seeded text
even flag-on output is unchanged.
"""

import sqlalchemy as sa
from alembic import op

revision = "000100000006"
down_revision = "000100000005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "prompt_fragments",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("fragment_key", sa.String(50), nullable=False, unique=True),
        sa.Column("description", sa.Text()),
        sa.Column("active_version", sa.String(50)),
        sa.Column("created_by", sa.String(100)),
        sa.Column("created_at", sa.DateTime()),
        sa.Column("updated_at", sa.DateTime()),
    )
    op.create_table(
        "prompt_fragment_versions",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("fragment_id", sa.Integer(),
                  sa.ForeignKey("prompt_fragments.id", ondelete="CASCADE"),
                  nullable=False, index=True),
        sa.Column("version", sa.String(50), nullable=False),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("change_reason", sa.Text()),
        sa.Column("is_active", sa.Boolean()),
        sa.Column("created_by", sa.String(100)),
        sa.Column("created_at", sa.DateTime()),
        sa.UniqueConstraint(
            "fragment_id", "version_number",
            name="uq_prompt_fragment_versions_fragment_id_version_number",
        ),
    )


def downgrade() -> None:
    op.drop_table("prompt_fragment_versions")
    op.drop_table("prompt_fragments")
