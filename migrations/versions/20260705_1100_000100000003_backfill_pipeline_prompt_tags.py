"""Prompt consolidation Phase 8 hygiene — backfill pipeline tags into prompt_tags.

Converges the legacy comma-string ``prompts.tags`` (live on the pipeline rows;
Phase 1 found 6 such rows on prod) into the canonical ``prompt_tags`` table:
every non-empty legacy string is parsed (split on comma, stripped, deduped)
and the resulting tags inserted for that prompt, skipping pairs that already
exist. The legacy column itself is NOT touched — the write paths now mirror
the canonical rows back into it, and it remains the read fallback for
anything not yet migrated.

Data-only and idempotent (ON CONFLICT DO NOTHING); re-running is safe.
Downgrade removes exactly the rows the upgrade would insert from the current
legacy strings — tags added through the API afterwards are preserved.
"""

from alembic import op

revision = "000100000003"
down_revision = "000100000002"
branch_labels = None
depends_on = None


def _legacy_tag_pairs(bind) -> list[tuple[int, str]]:
    """(prompt_id, tag) pairs parsed from every non-empty legacy comma-string."""
    rows = bind.exec_driver_sql(
        "SELECT id, tags FROM prompts "
        "WHERE prompt_kind = 'pipeline' AND tags IS NOT NULL AND btrim(tags) <> ''"
    ).fetchall()
    pairs: list[tuple[int, str]] = []
    for prompt_id, raw in rows:
        seen: set[str] = set()
        for part in raw.split(","):
            tag = part.strip()
            if tag and tag not in seen:
                seen.add(tag)
                pairs.append((prompt_id, tag))
    return pairs


def upgrade() -> None:
    bind = op.get_bind()
    for prompt_id, tag in _legacy_tag_pairs(bind):
        bind.exec_driver_sql(
            "INSERT INTO prompt_tags (prompt_id, tag) VALUES (%(pid)s, %(tag)s) "
            "ON CONFLICT (prompt_id, tag) DO NOTHING",
            {"pid": prompt_id, "tag": tag},
        )


def downgrade() -> None:
    bind = op.get_bind()
    for prompt_id, tag in _legacy_tag_pairs(bind):
        bind.exec_driver_sql(
            "DELETE FROM prompt_tags WHERE prompt_id = %(pid)s AND tag = %(tag)s",
            {"pid": prompt_id, "tag": tag},
        )
