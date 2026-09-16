#!/usr/bin/env python3
"""Remove S3 leftovers for Source Library rows already gone from Postgres.

Postgres ``source_index`` is the source of truth. After a Source Library delete
(or a failed delete that only dropped the PG row), raw objects, processed
artifacts, and S3 ``source_list.json`` rows can remain. This script finds those
orphans and deletes them.

Safety
  - Dry-run by default. Pass ``--apply`` to delete.
  - Refuses ``--apply`` when Postgres has zero rows but S3 still has job
    folders/list entries (wrong ENVIRONMENT / empty DB would wipe the bucket).
    Override with ``--allow-empty-pg``.

    python scripts/cleanup_orphaned_sources.py
    python scripts/cleanup_orphaned_sources.py --client aim
    python scripts/cleanup_orphaned_sources.py --apply --client aim
    python scripts/cleanup_orphaned_sources.py --apply --clients aim,cengage
    python scripts/cleanup_orphaned_sources.py --apply --client aim --allow-empty-pg
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "dis_backend"))


def _load_dis_env() -> None:
    """Load dis_backend/.env when the script is run from the repo root."""
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    dis_env = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "dis_backend", ".env")
    if os.path.isfile(dis_env):
        load_dotenv(dis_env, override=False)


def _print_plan(plan: dict) -> None:
    orphans = plan.get("orphan_job_ids") or []
    print(
        f"[{plan['client_id']}] env={plan['environment']} "
        f"pg={plan['pg_count']} s3_list={plan['s3_list_count']} "
        f"processed_folders={plan['processed_folder_count']} "
        f"raw_folders={plan['raw_folder_count']} orphans={len(orphans)}"
    )
    if not orphans:
        print(f"[{plan['client_id']}] nothing to clean up.")
        return
    print(f"[{plan['client_id']}] processed orphans: {len(plan['processed_orphan_job_ids'])}")
    print(f"[{plan['client_id']}] raw orphans: {len(plan['raw_orphan_job_ids'])}")
    print(f"[{plan['client_id']}] source_list.json orphans: {len(plan['s3_list_orphan_job_ids'])}")
    preview = orphans[:20]
    for job_id in preview:
        print(f"  {job_id}")
    if len(orphans) > 20:
        print(f"  ... {len(orphans) - 20} more")


def main() -> int:
    _load_dis_env()
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--apply", action="store_true", help="delete S3 orphans (default: plan only)")
    ap.add_argument("--client", default="aim", help="DIS client id (default: aim)")
    ap.add_argument(
        "--clients",
        default="",
        help="comma-separated client ids (overrides --client). Example: aim,cengage",
    )
    ap.add_argument(
        "--allow-empty-pg",
        action="store_true",
        help="allow --apply when Postgres has zero rows (would delete every S3 job folder)",
    )
    ap.add_argument(
        "--skip-opensearch",
        action="store_true",
        help="do not delete OpenSearch chunks for orphan job_ids",
    )
    args = ap.parse_args()

    from config.settings import get_settings, get_tenant_config
    from services import source_index_pg
    from services.source_library import apply_orphaned_source_cleanup, plan_orphaned_source_cleanup

    client_ids = [c.strip() for c in args.clients.split(",") if c.strip()] or [args.client]
    env = get_settings().environment or "development"
    print(f"ENVIRONMENT={env}  apply={args.apply}  allow_empty_pg={args.allow_empty_pg}")

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
                "(Postgres is not the source of truth for this client)."
            )
            continue

        try:
            plan = plan_orphaned_source_cleanup(tenant_cfg, client_id)
        except Exception as exc:  # noqa: BLE001
            print(f"[{client_id}] cannot plan cleanup: {exc}", file=sys.stderr)
            overall_rc = 2
            continue

        _print_plan(plan)
        if not args.apply:
            if plan["orphan_job_ids"]:
                print(f"[{client_id}] Re-run with --apply to delete these S3 leftovers.")
            continue

        try:
            result = asyncio.run(
                apply_orphaned_source_cleanup(
                    tenant_cfg,
                    client_id,
                    plan,
                    allow_empty_pg=args.allow_empty_pg,
                    delete_opensearch=not args.skip_opensearch,
                )
            )
        except RuntimeError as exc:
            print(f"[{client_id}] {exc}", file=sys.stderr)
            overall_rc = 2
            continue
        except Exception as exc:  # noqa: BLE001
            print(f"[{client_id}] cleanup failed: {exc}", file=sys.stderr)
            overall_rc = 2
            continue

        deleted_objects = sum(j["s3_objects_deleted"] for j in result["jobs"])
        s3_errors = sum(len(j["s3_errors"]) for j in result["jobs"])
        print(
            f"[{client_id}] done: jobs={len(result['jobs'])} "
            f"s3_objects_deleted={deleted_objects} s3_errors={s3_errors} "
            f"source_list_rows_removed={result['s3_list_rows_removed']}"
        )
        for job in result["jobs"]:
            if job["s3_errors"]:
                print(f"  {job['job_id']}: errors={job['s3_errors']}", file=sys.stderr)

    return overall_rc


if __name__ == "__main__":
    raise SystemExit(main())
