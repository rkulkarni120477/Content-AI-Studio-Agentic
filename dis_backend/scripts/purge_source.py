"""Fully remove one ingested source so the same file can be ingested again.

``delete_source_document`` clears the raw upload, the processed artifacts, the
OpenSearch chunks and the source-index entry — but not the structure store rows and
not the client dedup manifest. Both of those survive a delete, and the manifest entry
in particular makes a re-upload of the SAME bytes stop at the deduplication agent
("same_file_hash"), so a document that was ingested with the wrong metadata cannot be
re-ingested correctly without this.

Usage:
    python scripts/purge_source.py <client_id> <job_id> [<job_id> ...]

Irreversible. Keep a copy of the raw file before running it.
"""
from __future__ import annotations

import asyncio
import sys

import psycopg

from config.settings import get_settings, get_tenant_config
from services.artifacts import ArtifactWriter
from services.source_library import delete_source_document


def _purge_structure_store(cfg, client_id: str, job_id: str) -> dict:
    url = cfg.structure_store.url
    if not (getattr(cfg.structure_store, "enabled", False) and url):
        return {"skipped": "structure_store disabled"}
    schema = getattr(cfg.structure_store, "schema_name", None) or "dis"
    counts = {}
    with psycopg.connect(url) as conn:
        with conn.cursor() as cur:
            for table in ("dis_content_units", "dis_calendar_days", "dis_course_calendars",
                          "dis_documents", "dis_jobs"):
                try:
                    if table == "dis_calendar_days":
                        cur.execute(
                            f"""DELETE FROM {schema}.dis_calendar_days
                                 WHERE calendar_id IN (SELECT calendar_id
                                                         FROM {schema}.dis_course_calendars
                                                        WHERE job_id = %s)""",
                            (job_id,),
                        )
                    else:
                        cur.execute(f"DELETE FROM {schema}.{table} WHERE job_id = %s", (job_id,))
                    counts[table] = cur.rowcount
                    # Committed per table, so one absent table cannot undo the deletes
                    # already made. Rolling back the whole transaction instead would
                    # discard them while these counts still reported them as deleted —
                    # and the caller would go on to re-upload believing the old rows
                    # were gone.
                    conn.commit()
                except Exception as exc:  # a table this tenant never created
                    counts[table] = f"skipped: {exc}"
                    conn.rollback()
    return counts


def _purge_dedup_entry(cfg, client_id: str, job_id: str) -> str:
    writer = ArtifactWriter(cfg)
    env = get_settings().environment or "development"
    key = f"processed/{cfg.get_namespace(client_id)}/{env}/_dedup/dedup_manifest.json"
    try:
        manifest = writer.read_json(key)
    except Exception as exc:
        return f"manifest unreadable ({exc})"
    if not isinstance(manifest, dict):
        return "no manifest"
    files = manifest.get("files") or {}
    doomed = [h for h, entry in files.items()
              if job_id in {entry.get("first_job_id"), entry.get("latest_job_id"), entry.get("job_id")}]
    for h in doomed:
        files.pop(h, None)
        (manifest.get("content_hashes") or {}).pop(h, None)
    if not doomed:
        return "no dedup entry"
    writer.write_json(key, manifest)
    return f"removed {len(doomed)} hash entr(y/ies)"


def main(argv: list[str]) -> int:
    if len(argv) < 3:
        print(__doc__)
        return 2
    client_id, job_ids = argv[1], argv[2:]
    cfg = get_tenant_config(client_id)
    for job_id in job_ids:
        try:
            result = asyncio.run(delete_source_document(cfg, client_id, job_id))
            print(f"{job_id}: source deleted (s3={result['s3_objects_deleted']}, "
                  f"opensearch={result.get('opensearch')})")
        except FileNotFoundError as exc:
            print(f"{job_id}: no source-index entry ({exc}) — continuing with the rest")
        print(f"{job_id}: structure store {_purge_structure_store(cfg, client_id, job_id)}")
        print(f"{job_id}: dedup {_purge_dedup_entry(cfg, client_id, job_id)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
