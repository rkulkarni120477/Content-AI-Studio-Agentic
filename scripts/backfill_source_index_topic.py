#!/usr/bin/env python3
"""Backfill AIM compact source-index ``topic`` from studio payload metadata.

Phase 7B: compact records whose ``payload_key`` was stored as an ``s3://`` URI
(from ``ProcessedStorageAgent`` / ``write_json`` return value) are normalized
before ``ArtifactWriter.read_json()``. Relative logical keys are unchanged.
``topic`` to Field Registry ``index``. Newly ingested records pick up ``topic``
automatically via ``compact_source_record`` + AIM ``metadata_framework``. This
script rewrites **existing** compact rows only.

Source of truth
  Compact record ``payload_key`` → studio ``payload.json`` → ``metadata.topic``

What it writes
  Sets/updates ``topic`` on each compact source-index record. Does not modify
  studio payloads, content files, raw uploads, OpenSearch, or DB schema.

Safety
  - Dry-run by default (plan only). Pass ``--apply`` to write.
  - Tenant/client scoped via ``--client`` (default: ``aim``).
  - Idempotent: records whose ``topic`` already equals payload metadata are skipped.
  - Writes a rollback manifest before the first index write.
  - Refuses an empty source index (wrong ENVIRONMENT).

Do NOT run against production from CI. Operators run manually after deploy.

    python scripts/backfill_source_index_topic.py                 # plan only
    python scripts/backfill_source_index_topic.py --client aim
    python scripts/backfill_source_index_topic.py --apply
    python scripts/backfill_source_index_topic.py --apply --from-manifest <path>
    python scripts/backfill_source_index_topic.py --recount        # re-copy even if set

Rollback
    python scripts/backfill_source_index_topic.py --apply --from-manifest <manifest>

Verification
    Re-run without ``--apply``: ``0 to update`` is the clean check.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "dis_backend"))


def _topic_from_payload(payload: dict) -> str:
    meta = (payload or {}).get("metadata") or {}
    value = meta.get("topic")
    if value in (None, "", [], {}):
        return ""
    return str(value)


def resolve_payload_key(
    payload_key: str,
    *,
    processed_bucket: str,
    source_prefix: str,
    base_prefix: str = "",
) -> str | None:
    """Return a logical S3 key for ``ArtifactWriter.read_json()``, or None.

    Relative logical keys are returned unchanged (Phase 7 pre-7B behavior).
    ``s3://`` URIs are parsed using the same convention as dedup/finalize
    agents: extract bucket + object key, validate bucket, strip ``base_prefix``,
    then require the logical key to stay within ``source_prefix``.
    """
    key = str(payload_key or "").replace("\\", "/").strip()
    if not key:
        return None

    if not key.startswith("s3://"):
        return key

    parts = key.split("/", 3)
    if len(parts) < 4 or not parts[3]:
        return None

    bucket = parts[2]
    if bucket != processed_bucket:
        return None

    object_key = parts[3].lstrip("/")
    bp = (base_prefix or "").strip("/")
    if bp and object_key.startswith(bp + "/"):
        object_key = object_key[len(bp) + 1 :]
    object_key = object_key.lstrip("/")

    prefix = source_prefix.strip("/").rstrip("/")
    if not object_key.startswith(prefix + "/"):
        return None
    if not object_key.endswith("/studio_payload/payload.json"):
        return None

    return object_key


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--apply", action="store_true",
                    help="write; without it nothing is changed")
    ap.add_argument("--client", default="aim",
                    help="DIS client id (default: aim). Other tenants are out of scope.")
    ap.add_argument("--from-manifest",
                    help="restore the source index recorded in a manifest and exit")
    ap.add_argument("--manifest-dir", default=".",
                    help="where to write the rollback manifest (default: cwd)")
    ap.add_argument("--recount", action="store_true",
                    help="re-copy topic even when the compact record already has a value")
    args = ap.parse_args()

    from config.settings import get_tenant_config
    from services.artifacts import ArtifactWriter
    from services.source_library import (
        read_source_index,
        source_base_prefix,
        write_source_index,
    )

    client_id = str(args.client or "").strip()
    if not client_id:
        print("client id is required", file=sys.stderr)
        return 2

    tenant_cfg = get_tenant_config(client_id)
    writer = ArtifactWriter(tenant_cfg)
    source_prefix = source_base_prefix(tenant_cfg, client_id)
    base_prefix = getattr(tenant_cfg.storage, "base_prefix", "") or ""

    if args.from_manifest:
        with open(args.from_manifest) as fh:
            manifest = json.load(fh)
        if manifest.get("client_id") != client_id:
            print(
                f"manifest is for client '{manifest.get('client_id')}', not "
                f"'{client_id}' — refusing",
                file=sys.stderr,
            )
            return 2
        before = manifest.get("index_before")
        if not before:
            print("manifest carries no index_before snapshot — cannot restore",
                  file=sys.stderr)
            return 2
        if not args.apply:
            print(
                f"would restore the source index to its state at "
                f"{manifest.get('taken_at')} "
                f"({len(before.get('sources') or [])} records). "
                f"Re-run with --apply."
            )
            return 0
        write_source_index(tenant_cfg, client_id, before)
        print(f"restored {len(before.get('sources') or [])} records from "
              f"{args.from_manifest}")
        return 0

    index = read_source_index(tenant_cfg, client_id)
    records = list(index.get("sources") or [])
    if not records:
        print(
            "the source index is empty — check ENVIRONMENT matches the deployment "
            "you mean to update. Refusing to continue.",
            file=sys.stderr,
        )
        return 2

    planned: list[tuple[str, str, str]] = []  # job_id, old, new
    missing_payload = []
    unreadable = []
    already_ok = 0

    for rec in records:
        job_id = str(rec.get("job_id") or "")
        payload_key = rec.get("payload_key") or ""
        if not payload_key:
            missing_payload.append((job_id, rec.get("source_file_name"), "no payload_key"))
            continue
        resolved_key = resolve_payload_key(
            payload_key,
            processed_bucket=writer.processed_bucket,
            source_prefix=source_prefix,
            base_prefix=base_prefix,
        )
        if not resolved_key:
            unreadable.append((job_id, rec.get("source_file_name"), "invalid_payload_key"))
            continue
        try:
            payload = writer.read_json(resolved_key)
        except Exception as exc:  # noqa: BLE001 — leave record untouched
            unreadable.append((job_id, rec.get("source_file_name"), type(exc).__name__))
            continue
        if not isinstance(payload, dict):
            unreadable.append((job_id, rec.get("source_file_name"), "payload_not_object"))
            continue

        new_topic = _topic_from_payload(payload)
        old_topic = rec.get("topic")
        old_norm = "" if old_topic in (None, "") else str(old_topic)
        if not args.recount and "topic" in rec and old_norm == new_topic:
            already_ok += 1
            continue
        if not args.recount and "topic" not in rec and new_topic == "" and old_norm == "":
            # No payload topic and no compact key — nothing useful to write.
            already_ok += 1
            continue
        planned.append((job_id, old_norm, new_topic))

    print(f"client={client_id}  records={len(records)}")
    print(f"   already matching payload.topic : {already_ok}")
    print(f"   to update                      : {len(planned)}")
    print(f"   missing payload_key            : {len(missing_payload)}")
    print(f"   unreadable payload             : {len(unreadable)}")

    if planned:
        print("\n   first updates (job_id  old → new):")
        for job_id, old, new in planned[:15]:
            print(f"      {job_id[:8]}  {old!r} → {new!r}")
        if len(planned) > 15:
            print(f"      … {len(planned) - 15} more")

    if missing_payload:
        print("\n   missing payload_key (skipped):")
        for job_id, name, why in missing_payload[:10]:
            print(f"      {job_id[:8]}  {why:<18} {str(name)[:44]}")

    if unreadable:
        print("\n   unreadable payloads (skipped):")
        for job_id, name, why in unreadable[:10]:
            print(f"      {job_id[:8]}  {why:<22} {str(name)[:44]}")

    if not args.apply:
        print(
            f"\nWould set topic on {len(planned)} compact records. "
            f"Nothing has been changed. Re-run with --apply."
        )
        return 0

    if not planned:
        print("\nnothing to do.")
        return 0

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    manifest_path = os.path.join(
        args.manifest_dir, f"backfill_source_index_topic_{client_id}_{stamp}.json"
    )
    with open(manifest_path, "w") as fh:
        json.dump(
            {
                "client_id": client_id,
                "taken_at": stamp,
                "environment": os.environ.get("ENVIRONMENT", ""),
                "updated_jobs": [j for j, _, _ in planned],
                "index_before": index,
            },
            fh,
            indent=2,
            default=str,
        )
    print(f"\nrollback manifest: {manifest_path}")
    print(
        "   restore with:  python scripts/backfill_source_index_topic.py "
        f"--apply --client {client_id} --from-manifest {manifest_path}\n"
    )

    by_job = {j: new for j, _, new in planned}
    fresh = read_source_index(tenant_cfg, client_id)
    n = 0
    for rec in fresh.get("sources") or []:
        job_id = str(rec.get("job_id") or "")
        if job_id not in by_job:
            continue
        rec["topic"] = by_job[job_id]
        n += 1
    write_source_index(tenant_cfg, client_id, fresh)
    print(f"done: topic set on {n} compact records.")
    print("Re-run without --apply: '0 to update' is the check.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
