#!/usr/bin/env python3
"""Record how much text each document actually yielded.

The Source Library has to answer one question before any other: can a generation
use this document at all? It could not, because nothing in the source index said
whether extraction produced any text.

total_units cannot say. build_clean_content_document falls back to a single unit
holding reading_content when chunking yields nothing, so a file the extractor
could not read still records total_units = 1. Measured on the AIM index:

    605  records
      0  with total_units = 0
    109  of 120 legacy .doc records hold exactly ONE unit of ZERO characters

Every one of those displayed like any other document — the library showed
"needs_review", the same thing a perfectly good file shows — while 40% of the
corpus was unusable and blocks were being rebuilt against content nobody could
see was missing.

write_source_content_and_index now stamps `extracted_chars` on every new record.
This gives the existing ones the same field, by reading the clean content
document each record already points at — the same file retrieval reads, so the
number describes what generation would actually get, not what the pipeline
believed it produced.

Reads one content.json per record (about 66 MB across 605 records for AIM) and
writes the index once at the end.

    python scripts/backfill_extracted_chars.py               # plan only
    python scripts/backfill_extracted_chars.py --apply
    python scripts/backfill_extracted_chars.py --apply --from-manifest <path>
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "dis_backend"))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--client", default="aim")
    ap.add_argument("--from-manifest", help="restore the index recorded in a manifest")
    ap.add_argument("--manifest-dir", default=".")
    ap.add_argument("--recount", action="store_true",
                    help="re-measure records that already carry the field")
    args = ap.parse_args()

    from config.settings import get_tenant_config
    from services.artifacts import ArtifactWriter
    from services.source_library import (
        read_source_index, write_source_index, extracted_chars,
    )

    client_id = args.client
    tenant_cfg = get_tenant_config(client_id)
    writer = ArtifactWriter(tenant_cfg)

    if args.from_manifest:
        with open(args.from_manifest) as fh:
            manifest = json.load(fh)
        if manifest.get("client_id") != client_id:
            print(f"manifest is for '{manifest.get('client_id')}' — refusing", file=sys.stderr)
            return 2
        if not args.apply:
            print(f"would restore {len(manifest['index_before'].get('sources') or [])} "
                  f"records from {manifest.get('taken_at')}. Re-run with --apply.")
            return 0
        write_source_index(tenant_cfg, client_id, manifest["index_before"])
        print("restored.")
        return 0

    index = read_source_index(tenant_cfg, client_id)
    records = index.get("sources") or []
    if not records:
        # The index path carries ENVIRONMENT; the wrong one reads an empty file,
        # and "0 records" would otherwise look like "nothing to do".
        print("the source index is empty — check ENVIRONMENT matches the deployment "
              "you mean to update. Refusing to continue.", file=sys.stderr)
        return 2

    todo = [r for r in records
            if args.recount or r.get("extracted_chars") is None]
    print(f"{len(records)} records, {len(todo)} to measure "
          f"({len(records) - len(todo)} already carry the field)\n")
    if not todo:
        print("nothing to do.")
        return 0

    measured, unreadable = {}, []
    empty, tiny = [], 0
    for i, rec in enumerate(todo, 1):
        job_id = str(rec.get("job_id"))
        key = rec.get("content_key")
        if not key:
            unreadable.append((job_id, rec.get("source_file_name"), "no content_key"))
            continue
        try:
            chars = extracted_chars(writer.read_json(key))
        except Exception as exc:
            # Left unmeasured rather than recorded as zero. Zero means "we read
            # the file and it held no text"; a failed read means we do not know,
            # and writing 0 here would mark a healthy document as unusable.
            unreadable.append((job_id, rec.get("source_file_name"), type(exc).__name__))
            continue
        measured[job_id] = chars
        if chars == 0:
            empty.append(rec)
        elif chars < 200:
            tiny += 1
        if i % 100 == 0:
            print(f"   ...{i}/{len(todo)}")

    print(f"\nmeasured {len(measured)} records")
    print(f"   yielded NO text at all : {len(empty)}")
    print(f"   under 200 characters   : {tiny}")
    print(f"   could not be read      : {len(unreadable)}")

    if empty:
        by_type = Counter(str(r.get("source_file_type") or "?") for r in empty)
        print("\n   documents with no text, by file type:")
        for ft, n in by_type.most_common():
            print(f"      {ft:<8} {n}")
        print("\n   the first 10:")
        for r in empty[:10]:
            print(f"      {str(r.get('source_file_name'))[:66]}")

    if unreadable:
        print("\n   unreadable content files (left unmeasured, NOT recorded as zero):")
        for job_id, name, why in unreadable[:10]:
            print(f"      {job_id[:8]}  {why:<22} {str(name)[:44]}")

    if not args.apply:
        print(f"\nWould set extracted_chars on {len(measured)} records. "
              f"Nothing has been changed. Re-run with --apply.")
        return 0

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = os.path.join(args.manifest_dir,
                        f"backfill_extracted_chars_{client_id}_{stamp}.json")
    with open(path, "w") as fh:
        json.dump({"client_id": client_id, "taken_at": stamp,
                   "environment": os.environ.get("ENVIRONMENT", ""),
                   "index_before": index}, fh, indent=2, default=str)
    print(f"\nrollback manifest: {path}")

    fresh = read_source_index(tenant_cfg, client_id)
    n = 0
    for rec in fresh.get("sources") or []:
        chars = measured.get(str(rec.get("job_id")))
        if chars is not None:
            rec["extracted_chars"] = int(chars)
            n += 1
    write_source_index(tenant_cfg, client_id, fresh)
    print(f"done: extracted_chars set on {n} records.")
    print("Re-run without --apply: '0 to measure' is the check.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
