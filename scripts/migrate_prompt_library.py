"""
One-off data migration: standalone Prompt Library DB  ->  Content AI Studio (pl_* tables).

Copies prompts, follow-ups, tags, variables, versions, team links, attachments,
reviews, and requests from the standalone ``promptlibrary`` Postgres database into
the host's ``pl_*`` tables. Multi-tenancy is dropped (``tenant_id`` columns are
ignored). ``created_by`` / ``requested_by`` / review ``username`` are copied verbatim
as host usernames; unmatched names are reported (not blocked).

Idempotent: rows whose primary key already exists in the target are skipped, so the
script can be re-run safely.

Usage
-----
    # source = standalone DB, target = host DB (defaults to the app's DATABASE_URL)
    export PROMPT_LIBRARY_SOURCE_URL="postgresql+psycopg://postgres:postgres@localhost:5432/promptlibrary"
    export PROMPT_LIBRARY_SOURCE_ATTACHMENTS="C:/path/to/prompt-library/backend/attachments"

    python -m scripts.migrate_prompt_library --dry-run   # preview counts only
    python -m scripts.migrate_prompt_library             # perform the migration

The target connection is taken from the host settings (``settings.db_url`` /
``DATABASE_URL``) unless ``PROMPT_LIBRARY_TARGET_URL`` is set.
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

# Ensure the host models are importable and their table metadata is registered.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from promptops_app import pl_models  # noqa: E402  (registers pl_* tables on Base)
from promptops_app.database import Base  # noqa: E402

# Order matters: parents before children / FK targets before referrers.
# Each entry: (source_table, target_model, columns_to_copy, pk_attr)
_PROMPT_COLS = [
    "id", "parent_id", "title", "content", "description", "category",
    "visibility", "team_id", "created_by", "created_at", "updated_at",
    "last_used_at", "deleted_at",
]


def _source_url() -> str:
    url = os.environ.get("PROMPT_LIBRARY_SOURCE_URL")
    if not url:
        sys.exit("ERROR: set PROMPT_LIBRARY_SOURCE_URL to the standalone Prompt Library DB URL.")
    return url


def _target_url() -> str:
    url = os.environ.get("PROMPT_LIBRARY_TARGET_URL")
    if url:
        return url
    try:
        from promptops_app.database import engine  # host engine already configured
        return str(engine.url)
    except Exception as exc:  # pragma: no cover
        sys.exit(f"ERROR: could not resolve target DB URL ({exc}); set PROMPT_LIBRARY_TARGET_URL.")


def _rows(src: Session, table: str, cols: list[str]) -> list[dict]:
    col_sql = ", ".join(cols)
    result = src.execute(text(f"SELECT {col_sql} FROM {table}"))
    return [dict(zip(cols, row)) for row in result.fetchall()]


def _existing_ids(dst: Session, table: str, pk: str = "id") -> set:
    return {r[0] for r in dst.execute(text(f"SELECT {pk} FROM {table}")).fetchall()}


def _insert(dst: Session, table: str, rows: list[dict], existing: set, pk: str = "id") -> int:
    inserted = 0
    for row in rows:
        if row.get(pk) in existing:
            continue
        cols = list(row.keys())
        placeholders = ", ".join(f":{c}" for c in cols)
        dst.execute(text(f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({placeholders})"), row)
        inserted += 1
    return inserted


def migrate(dry_run: bool = False) -> None:
    src_engine = create_engine(_source_url())
    dst_engine = create_engine(_target_url())

    # Make sure the target pl_* tables exist.
    Base.metadata.create_all(bind=dst_engine, tables=[
        t for name, t in Base.metadata.tables.items() if name.startswith("pl_")
    ])

    src = Session(src_engine)
    dst = Session(dst_engine)

    report: dict[str, int] = {}
    try:
        # 1) Teams (FK target for prompts / prompt_teams)
        teams = _rows(src, "teams", ["id", "name", "created_at", "created_by"])
        report["pl_teams"] = _insert(dst, "pl_teams", teams, _existing_ids(dst, "pl_teams"))

        # 2) Prompts — roots first, then children (self-referential parent_id FK)
        prompts = _rows(src, "prompts", _PROMPT_COLS)
        existing_prompts = _existing_ids(dst, "pl_prompts")
        roots = [p for p in prompts if not p.get("parent_id")]
        children = [p for p in prompts if p.get("parent_id")]
        n = _insert(dst, "pl_prompts", roots, existing_prompts)
        n += _insert(dst, "pl_prompts", children, existing_prompts)
        report["pl_prompts"] = n

        # 3) Prompt child tables
        report["pl_prompt_tags"] = _insert_composite(
            dst, "pl_prompt_tags", _rows(src, "prompt_tags", ["prompt_id", "tag"]), ("prompt_id", "tag"),
        )

        report["pl_prompt_variables"] = _insert(
            dst, "pl_prompt_variables",
            _rows(src, "prompt_variables", ["id", "prompt_id", "name", "label", "hint", "sort_order"]),
            _existing_ids(dst, "pl_prompt_variables"),
        )
        report["pl_prompt_versions"] = _insert(
            dst, "pl_prompt_versions",
            _rows(src, "prompt_versions", ["id", "prompt_id", "version_number", "content", "note", "created_by", "created_at"]),
            _existing_ids(dst, "pl_prompt_versions"),
        )
        report["pl_prompt_teams"] = _insert_composite(
            dst, "pl_prompt_teams", _rows(src, "prompt_teams", ["prompt_id", "team_id"]), ("prompt_id", "team_id"),
        )
        report["pl_attachments"] = _insert(
            dst, "pl_attachments",
            _rows(src, "attachments", ["id", "prompt_id", "original_name", "stored_name", "size_bytes", "uploaded_by", "uploaded_at"]),
            _existing_ids(dst, "pl_attachments"),
        )

        # 4) Reviews and requests
        report["pl_reviews"] = _insert(
            dst, "pl_reviews",
            _rows(src, "reviews", ["id", "prompt_id", "username", "rating", "feedback", "created_at", "updated_at"]),
            _existing_ids(dst, "pl_reviews"),
        )
        report["pl_prompt_requests"] = _insert(
            dst, "pl_prompt_requests",
            _rows(src, "prompt_requests", ["id", "title", "description", "type", "prompt_id", "requested_by", "status", "admin_notes", "created_at", "updated_at"]),
            _existing_ids(dst, "pl_prompt_requests"),
        )

        # Report usernames present in the source that are missing from host users.
        _report_unmatched_users(src, dst)

        if dry_run:
            dst.rollback()
            print("DRY RUN — no changes committed.")
        else:
            dst.commit()
            _copy_attachment_files()
    finally:
        src.close()
        dst.close()

    print("\nMigration summary (rows inserted):")
    for table, count in report.items():
        print(f"  {table:24} {count}")


def _insert_composite(dst: Session, table: str, rows: list[dict], key: tuple[str, str]) -> int:
    existing = {
        (r[0], r[1])
        for r in dst.execute(text(f"SELECT {key[0]}, {key[1]} FROM {table}")).fetchall()
    }
    inserted = 0
    for row in rows:
        if (row[key[0]], row[key[1]]) in existing:
            continue
        cols = list(row.keys())
        placeholders = ", ".join(f":{c}" for c in cols)
        dst.execute(text(f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({placeholders})"), row)
        inserted += 1
    return inserted


def _report_unmatched_users(src: Session, dst: Session) -> None:
    try:
        host_users = {r[0] for r in dst.execute(text("SELECT username FROM users")).fetchall()}
    except Exception:
        return
    source_names: set[str] = set()
    for table, col in (("prompts", "created_by"), ("prompt_requests", "requested_by"), ("reviews", "username")):
        try:
            for r in src.execute(text(f"SELECT DISTINCT {col} FROM {table} WHERE {col} IS NOT NULL")).fetchall():
                source_names.add(r[0])
        except Exception:
            pass
    unmatched = sorted(n for n in source_names if n not in host_users)
    if unmatched:
        print("\nWARNING: these Prompt Library usernames have no matching host user "
              "(kept as-is on rows):")
        for name in unmatched:
            print(f"  - {name}")


def _copy_attachment_files() -> None:
    src_dir = os.environ.get("PROMPT_LIBRARY_SOURCE_ATTACHMENTS")
    if not src_dir or not os.path.isdir(src_dir):
        print("\nNOTE: PROMPT_LIBRARY_SOURCE_ATTACHMENTS not set or not found — "
              "skipping attachment file copy. Set it to the standalone backend/attachments dir "
              "(files may be nested per-tenant).")
        return
    dst_dir = os.environ.get(
        "PL_ATTACHMENTS_DIR",
        os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "pl_attachments"),
    )
    os.makedirs(dst_dir, exist_ok=True)
    copied = 0
    # Attachments may be stored flat or nested (per-tenant subdirs) in the source.
    for root, _dirs, files in os.walk(src_dir):
        for fname in files:
            dst_path = os.path.join(dst_dir, fname)
            if not os.path.exists(dst_path):
                shutil.copy2(os.path.join(root, fname), dst_path)
                copied += 1
    print(f"\nCopied {copied} attachment file(s) into {dst_dir}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Migrate standalone Prompt Library data into Content AI Studio.")
    parser.add_argument("--dry-run", action="store_true", help="Preview row counts without committing.")
    args = parser.parse_args()
    migrate(dry_run=args.dry_run)


if __name__ == "__main__":
    main()
