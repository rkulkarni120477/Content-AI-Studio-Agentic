#!/usr/bin/env python3
"""Backfill DIS Postgres ``source_index`` from S3 ``source_list.json``.

After deploy, the Source Library catalogue is read/written from Postgres when
``structure_store.enabled`` is true. Existing clients still have their compact
rows only in S3 until this script runs.

Safety
  - Dry-run by default. Pass ``--apply`` to write.
  - Refuses ``--apply`` when S3 is empty but Postgres already has rows
    (wrong ENVIRONMENT / empty bucket would wipe nothing, but refuse to
    pretend a migration succeeded).
  - Creates the table idempotently (same DDL as ensure_schema).
  - Upserts by (client_id, environment, job_id); does not delete PG rows that
    are absent from S3 unless ``--replace`` is passed.

    python scripts/migrate_source_index_to_pg.py
    python scripts/migrate_source_index_to_pg.py --client aim
    python scripts/migrate_source_index_to_pg.py --apply
    python scripts/migrate_source_index_to_pg.py --apply --client cengage
    python scripts/migrate_source_index_to_pg.py --apply --replace
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "dis_backend"))


def _load_dis_env() -> None:
    """Load dis_backend/.env when the script is run from the repo root.

    pydantic Settings looks for ``.env`` in cwd; operators usually run this from
    the repo root, so DIS_S3_* / AWS_* from dis_backend/.env would be skipped.
    """
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    dis_env = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "dis_backend", ".env")
    if os.path.isfile(dis_env):
        load_dotenv(dis_env, override=False)


def main() -> int:
    _load_dis_env()
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--apply", action="store_true", help="write to Postgres (default: plan only)")
    ap.add_argument("--client", default="aim", help="DIS client id (default: aim)")
    ap.add_argument(
        "--replace",
        action="store_true",
        help="after upserting S3 rows, delete PG rows for this client+env whose job_id is not in S3",
    )
    ap.add_argument(
        "--clients",
        default="",
        help="comma-separated client ids (overrides --client). Example: aim,cengage",
    )
    args = ap.parse_args()

    from config.settings import get_settings, get_tenant_config
    from services import source_index_pg
    from services.source_library import _read_source_index_s3

    client_ids = [c.strip() for c in args.clients.split(",") if c.strip()] or [args.client]
    env = get_settings().environment or "development"
    print(f"ENVIRONMENT={env}  apply={args.apply}  replace={args.replace}")

    overall_rc = 0
    for client_id in client_ids:
        try:
            tenant_cfg = get_tenant_config(client_id)
        except Exception as exc:  # noqa: BLE001
            print(f"[{client_id}] cannot load tenant config: {exc}", file=sys.stderr)
            overall_rc = 2
            continue

        if not source_index_pg.use_pg_source_index(tenant_cfg):
            print(
                f"[{client_id}] structure_store disabled or no URL — skipping "
                "(S3 catalogue remains the source of truth for this client)."
            )
            continue

        s3_index = _read_source_index_s3(tenant_cfg, client_id)
        s3_sources = list(s3_index.get("sources") or [])
        s3_n = len(s3_sources)
        print(f"[{client_id}] S3 source_list.json: {s3_n} row(s)")

        try:
            pg_before = source_index_pg.count_sources(tenant_cfg, client_id)
        except Exception as exc:  # noqa: BLE001
            print(f"[{client_id}] cannot count Postgres rows (will create table on apply): {exc}")
            pg_before = -1
        else:
            print(f"[{client_id}] Postgres source_index before: {pg_before} row(s)")

        if s3_n == 0:
            if pg_before > 0:
                print(
                    f"[{client_id}] REFUSING: S3 catalogue is empty but Postgres has "
                    f"{pg_before} row(s). Check ENVIRONMENT / bucket before re-running.",
                    file=sys.stderr,
                )
                overall_rc = 2
                continue
            print(f"[{client_id}] nothing to migrate (empty S3, empty PG).")
            continue

        job_ids = [str(r.get("job_id") or "") for r in s3_sources]
        missing_job = sum(1 for j in job_ids if not j)
        if missing_job:
            print(f"[{client_id}] WARNING: {missing_job} S3 row(s) lack job_id — they will be skipped.")

        if not args.apply:
            print(
                f"[{client_id}] would upsert {s3_n - missing_job} row(s)"
                + (" and delete PG-only rows (--replace)" if args.replace else "")
                + ". Re-run with --apply."
            )
            continue

        usable = [
            rec for rec in s3_sources
            if str(rec.get("job_id") or rec.get("document_id") or "").strip()
        ]
        skipped = s3_n - len(usable)

        if args.replace:
            source_index_pg.replace_index(tenant_cfg, client_id, usable)
            mode = "replace"
        else:
            for rec in usable:
                source_index_pg.upsert_record(tenant_cfg, client_id, rec)
            mode = "upsert"

        pg_after = source_index_pg.count_sources(tenant_cfg, client_id)
        print(
            f"[{client_id}] done: mode={mode} wrote={len(usable)} skipped={skipped} "
            f"pg_after={pg_after} s3={s3_n}"
        )
        if mode == "upsert" and pg_after != len(usable):
            print(
                f"[{client_id}] note: PG count ({pg_after}) differs from S3 usable rows "
                f"({len(usable)}). Pass --replace to drop PG-only job_ids."
            )

    return overall_rc


if __name__ == "__main__":
    raise SystemExit(main())
