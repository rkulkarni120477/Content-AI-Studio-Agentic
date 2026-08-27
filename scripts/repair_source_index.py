#!/usr/bin/env python3
"""Put a searchable document back into the set retrieval is allowed to return.

DIS answers a query in two steps (context_retrieval.retrieve). First it builds an
allow-set of job_ids from the S3 source index ALONE — the doc-level gate
(restricted / visibility / purpose / course / day) is evaluated from those records,
with no content-file reads. Only then does it run the vector search, and it
restricts the OpenSearch hits to that allow-set.

The consequence is easy to miss and was missed here: a document can be extracted,
chunked, embedded and indexed, and still be returned by nothing at all, because
its job_id is not in the source index. Being "indexed" is not being "reachable".

Found in production 2026-08-27, after a re-index that reported success:

    594  job_ids in the source index (the allow-set)
    494  job_ids in OpenSearch
     23  indexed but absent from the allow-set  (1,339 units)

Those 23 are two different problems wearing the same shape, and only one of them
is a loss:

  STRANDED (13 docs / 1,299 units)
      No source-index record exists for this document under any job_id. The
      content is real and unreachable. The largest is 8083-31B.pdf — the FAA
      Airframe handbook, 1,233 page-level units — which Block 9's calendar
      assigns as reading on nearly every day.

  SUPERSEDED (10 docs / 40 units)
      The same file was ingested more than once; a record already in the index
      serves it with at least as many units. These are duplicate copies in
      OpenSearch, and adding records for them would make retrieval return the
      same passage several times. They are reported and deliberately left out.

The repair rebuilds each stranded document's payload from dis.dis_content_units —
the structure store is the surviving system of record; the July artifacts for
several of these jobs are gone from S3 entirely — and then hands it to the
pipeline's own write_source_content_and_index. Using production's writer rather
than a hand-built dict is the point: compact_source_record decides which fields
the gate can read, and a record assembled here would drift from it the first time
that function changes.

  python scripts/repair_source_index.py                    # plan only
  python scripts/repair_source_index.py --apply
  python scripts/repair_source_index.py --apply --retire-stubs
  python scripts/repair_source_index.py --from-manifest <path>   # undo

--retire-stubs is separate and opt-in. Six records in the allow-set are whole
reference textbooks collapsed into ONE 1.9-3.8 MB unit, with nothing in
OpenSearch: they were classified as a generic type instead of ebook_reference, so
they were never chunked, and a unit that size cannot be embedded. Exactly one of
them (8083-31B) has a correctly chunked twin, so exactly one can be retired here.
The other five need re-ingestion and are reported, never touched.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "dis_backend"))


#: What context_retrieval._passes_filters treats as "matches every course".
GLOBAL_COURSE_ID = "-1"


def _identity(relative_path: str, file_name: str) -> str:
    """What counts as 'the same document' when deciding stranded vs superseded.

    The relative path, because the same filename legitimately appears in several
    folders — 'Block 2 Teacher Calendar.xlsx' exists under three of them — and
    keying on the name alone would call a distinct document a duplicate and drop
    it. Falls back to the name only when a row has no path at all.
    """
    return (str(relative_path or "") or str(file_name or "")).strip().lower()


def _jload(value, default):
    if value is None:
        return default
    return json.loads(value) if isinstance(value, str) else value


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--apply", action="store_true",
                    help="write; without it nothing is changed")
    ap.add_argument("--client", default="aim")
    ap.add_argument("--retire-stubs", action="store_true",
                    help="also remove an unchunked stub record once a properly "
                         "chunked record for the same file is in the allow-set")
    ap.add_argument("--allow-new-global", action="store_true",
                    help="also add a stranded document whose scope is the global "
                         "sentinel -1 when nothing in the allow-set already grants "
                         "it that reach (see WIDENS SCOPE in the plan)")
    ap.add_argument("--from-manifest",
                    help="restore the source index recorded in a manifest and exit")
    ap.add_argument("--manifest-dir", default=".",
                    help="where to write the rollback manifest (default: cwd)")
    args = ap.parse_args()

    import psycopg2
    import psycopg2.extras
    from config.settings import get_tenant_config
    from services.artifacts import ArtifactWriter
    from services.indexing import _vector_store_read_client
    from services.source_library import (
        read_source_index, write_source_index, studio_payload_key,
        write_source_content_and_index,
    )

    client_id = args.client
    tenant_cfg = get_tenant_config(client_id)
    writer = ArtifactWriter(tenant_cfg)

    # ---- restore path -------------------------------------------------------
    if args.from_manifest:
        with open(args.from_manifest) as fh:
            manifest = json.load(fh)
        if manifest.get("client_id") != client_id:
            print(f"manifest is for client '{manifest.get('client_id')}', not "
                  f"'{client_id}' — refusing", file=sys.stderr)
            return 2
        before = manifest.get("index_before")
        if not before:
            print("manifest carries no index_before snapshot — cannot restore",
                  file=sys.stderr)
            return 2
        if not args.apply:
            print(f"would restore the source index to its state at "
                  f"{manifest.get('taken_at')} ({len(before.get('sources') or [])} "
                  f"records). Re-run with --apply.")
            return 0
        write_source_index(tenant_cfg, client_id, before)
        print(f"restored {len(before.get('sources') or [])} records from "
              f"{args.from_manifest}")
        return 0

    # ---- read all three stores ---------------------------------------------
    osc = _vector_store_read_client(tenant_cfg.vector_store)
    index_name = tenant_cfg.vector_store.index_name
    buckets = osc.search(index=index_name, body={
        "size": 0,
        "aggs": {"j": {"terms": {"field": "job_id", "size": 10000}}},
    })["aggregations"]["j"]["buckets"]
    os_units = {b["key"]: b["doc_count"] for b in buckets}

    index = read_source_index(tenant_cfg, client_id)
    records = index.get("sources") or []
    if not records:
        # The index lives under the environment name (…/aim/<environment>/…), so
        # the wrong ENVIRONMENT reads an empty file and this script would then
        # cheerfully "repair" every document in the corpus into a fresh index.
        print("the source index is empty — check ENVIRONMENT matches the "
              "deployment you mean to repair. Refusing to continue.",
              file=sys.stderr)
        return 2
    recorded_jobs = {str(r.get("job_id")) for r in records}

    dis_url = os.environ.get("DIS_STRUCTURE_STORE_URL") or tenant_cfg.structure_store.url
    conn = psycopg2.connect(dis_url)
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("""
            select d.job_id, d.document_id, d.tenant_id, d.client_id,
                   d.document_title, d.document_type, d.source_file_name,
                   d.source_file_type, d.source_relative_path,
                   d.raw_storage_url, d.payload_storage_url,
                   d.metadata_json, d.created_at
              from dis.dis_documents d
             where d.client_id = %s
        """, (client_id,))
        docs = {r["job_id"]: r for r in cur.fetchall()}

    # identity -> the records already in the allow-set for that document
    by_identity: dict = {}
    for rec in records:
        row = docs.get(str(rec.get("job_id")))
        ident = _identity(
            (row or {}).get("source_relative_path") or rec.get("source_relative_path"),
            (row or {}).get("source_file_name") or rec.get("source_file_name"),
        )
        by_identity.setdefault(ident, []).append(rec)

    # A record already in the allow-set at global scope, keyed by file name.
    # Restoring a document to a reach something else already grants it is a
    # repair; granting that reach for the first time is a decision.
    already_global = {
        str(r.get("source_file_name") or "").strip().lower()
        for r in records if str(r.get("course_id") or "").strip() == GLOBAL_COURSE_ID
    }

    stranded, superseded, no_row, widens = [], [], [], []
    for job_id, n_units in sorted(os_units.items(), key=lambda kv: -kv[1]):
        if job_id in recorded_jobs:
            continue
        row = docs.get(job_id)
        if not row:
            # Indexed content with no structure-store row: nothing to rebuild a
            # payload from. Reported, never invented.
            no_row.append((job_id, n_units))
            continue
        ident = _identity(row["source_relative_path"], row["source_file_name"])
        twins = by_identity.get(ident) or []
        twin_units = sum(os_units.get(str(t.get("job_id")), 0) for t in twins)
        if twins and twin_units >= n_units:
            superseded.append((job_id, n_units, row, twins, twin_units))
            continue
        meta = _jload(row["metadata_json"], {}) or {}
        scope = str(meta.get("course_id") or "").strip()
        name = str(row["source_file_name"] or "").strip().lower()
        if scope == GLOBAL_COURSE_ID and name not in already_global and not args.allow_new_global:
            # context_retrieval._passes_filters accepts a document when its
            # course_id is the requested course OR "-1". So adding this record
            # does not restore a document to one course — it publishes it to
            # every course this client has. That is how a lesson on public-safety
            # careers ends up cited in an aircraft-systems CDD. Held back.
            widens.append((job_id, n_units, row))
            continue
        stranded.append((job_id, n_units, row))

    print(f"source index: {len(records)} records     OpenSearch: {len(os_units)} jobs")
    print(f"indexed but not in the allow-set: "
          f"{len(stranded) + len(superseded) + len(no_row)} docs / "
          f"{sum(n for _, n, *_ in stranded) + sum(n for _, n, *_ in superseded) + sum(n for _, n in no_row)} units\n")

    print(f"STRANDED — no record for this document, content is unreachable: "
          f"{len(stranded)} docs / {sum(n for _, n, _ in stranded)} units")
    for job_id, n, row in stranded:
        print(f"   {n:>5}u  {job_id[:8]}  {(row['source_relative_path'] or row['source_file_name'])[:64]}")

    print(f"\nSUPERSEDED — a record already serves this file; left out on purpose: "
          f"{len(superseded)} docs / {sum(n for _, n, *_ in superseded)} units")
    for job_id, n, row, twins, tu in superseded:
        print(f"   {n:>5}u  {job_id[:8]}  serving record {str(twins[0].get('job_id'))[:8]}"
              f" ({tu}u)  {(row['source_relative_path'] or row['source_file_name'])[:48]}")

    if widens:
        print(f"\nWIDENS SCOPE — held back, needs a decision: {len(widens)} docs / "
              f"{sum(n for _, n, _ in widens)} units")
        print( "   These carry course_id -1, which retrieval reads as 'every course'.")
        print( "   Nothing in the allow-set grants them that reach today, so adding")
        print( "   them would not restore a document — it would publish it programme-")
        print( "   wide. Scope each one properly, or pass --allow-new-global.")
        for job_id, n, row in widens:
            meta = _jload(row["metadata_json"], {}) or {}
            print(f"   {n:>5}u  {job_id[:8]}  block={str(meta.get('block') or '-'):<14}"
                  f"type={str(row['document_type']):<18}"
                  f"{(row['source_relative_path'] or row['source_file_name'])[:44]}")

    if no_row:
        print(f"\nNO STRUCTURE-STORE ROW — indexed content with nothing to rebuild "
              f"from: {len(no_row)} docs / {sum(n for _, n in no_row)} units")
        for job_id, n in no_row:
            print(f"   {n:>5}u  {job_id[:8]}")

    # ---- records in the allow-set that can never return anything ------------
    empty = [r for r in records if os_units.get(str(r.get("job_id")), 0) == 0]
    huge = [r for r in empty
            if int(r.get("total_units") or 0) <= 1 and str(r.get("job_id")) not in docs]
    print(f"\nALSO IN THE ALLOW-SET BUT UNSEARCHABLE: {len(empty)} of {len(records)} "
          f"records return nothing from OpenSearch")
    if huge:
        print(f"   {len(huge)} are whole documents collapsed into a single unit — "
              f"never chunked, so never embeddable:")
        for r in huge:
            print(f"      {str(r.get('source_file_name'))[:44]:<46} "
                  f"total_units={r.get('total_units')} status={r.get('status')}")

    # ---- stubs that a repaired record makes redundant -----------------------
    # Keyed on FILE NAME, not path: the stub was uploaded loose while the good
    # copy sits in a folder, so their relative paths never match.
    will_serve_name = {}
    for job_id, n, row in stranded:
        will_serve_name.setdefault(str(row["source_file_name"]).strip().lower(), []).append((job_id, n))
    retire = []
    for rec in records:
        name = str(rec.get("source_file_name") or "").strip().lower()
        if os_units.get(str(rec.get("job_id")), 0):
            continue                       # it still serves units — keep it
        if int(rec.get("total_units") or 0) > 1:
            continue                       # a real chunked record, just unindexed
        replacement = will_serve_name.get(name)
        if replacement:
            retire.append((rec, replacement))
    if retire:
        print(f"\nSTUBS MADE REDUNDANT BY THIS REPAIR: {len(retire)}"
              f"{'' if args.retire_stubs else '  (pass --retire-stubs to remove)'}")
        for rec, repl in retire:
            print(f"   {str(rec.get('source_file_name'))[:44]:<46} "
                  f"stub {str(rec.get('job_id'))[:8]} ({rec.get('total_units')}u, 0 indexed)"
                  f"  ->  {repl[0][0][:8]} ({repl[0][1]}u)")

    unscoped = [(j, n, r) for j, n, r in stranded
                if not str((_jload(r["metadata_json"], {}) or {}).get("course_id") or "").strip()]
    if unscoped:
        print(f"\nNOTE — {len(unscoped)} of the records this will add carry no "
              f"course_id, so they")
        print( "   stay unreachable until they are scoped (the gate accepts only the")
        print( "   requested course or -1). The record has to exist first; scoping it")
        print( "   is backfill_course_scope.py's job.")
        for job_id, n, row in unscoped:
            print(f"   {n:>5}u  {job_id[:8]}  {row['source_file_name'][:52]}")

    if not args.apply:
        print(f"\nWould add {len(stranded)} records"
              f"{f' and retire {len(retire)} stubs' if args.retire_stubs and retire else ''}. "
              f"Nothing has been changed. Re-run with --apply.")
        return 0

    if not stranded and not (args.retire_stubs and retire):
        print("\nnothing to do.")
        return 0

    # ---- rollback manifest, written BEFORE the first write ------------------
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    manifest_path = os.path.join(args.manifest_dir,
                                 f"repair_source_index_{client_id}_{stamp}.json")
    with open(manifest_path, "w") as fh:
        json.dump({
            "client_id": client_id,
            "taken_at": stamp,
            "environment": os.environ.get("ENVIRONMENT", ""),
            "added_jobs": [j for j, _, _ in stranded],
            "retired_jobs": [str(r.get("job_id")) for r, _ in retire] if args.retire_stubs else [],
            "index_before": index,
        }, fh, indent=2, default=str)
    print(f"\nrollback manifest: {manifest_path}")
    print("   restore with:  python scripts/repair_source_index.py "
          f"--apply --from-manifest {manifest_path}\n")

    # ---- rebuild each stranded document and write it through production -----
    added = failed = 0
    for job_id, n_units, row in stranded:
        meta = _jload(row["metadata_json"], {}) or {}
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""
                select content_unit_id, unit_type, unit_number, title,
                       text_content, visual_summary, keywords_json, topics_json,
                       metadata_json
                  from dis.dis_content_units
                 where job_id = %s
              order by unit_number
            """, (job_id,))
            unit_rows = cur.fetchall()

        units = [{
            "content_unit_id": u["content_unit_id"],
            "unit_type": u["unit_type"],
            "unit_number": u["unit_number"],
            "title": u["title"] or "",
            # The pipeline calls this `text`; the column is text_content. Getting
            # this wrong writes a content file full of empty units that still
            # reports the right count.
            "text": u["text_content"] or "",
            "visual_summary": u["visual_summary"] or "",
            "keywords": _jload(u["keywords_json"], []),
            "topics": _jload(u["topics_json"], []),
            "metadata": _jload(u["metadata_json"], {}) or meta,
        } for u in unit_rows]

        if not any(str(u["text"]).strip() for u in units):
            print(f"   SKIP {job_id[:8]} {row['source_file_name'][:40]}: "
                  f"{len(units)} units, none with text — a record here would put an "
                  f"empty document into the allow-set")
            failed += 1
            continue

        payload = {
            "job_id": job_id,
            "tenant_id": row["tenant_id"],
            "client_id": row["client_id"],
            "schema_version": "1.0",
            "created_at": (row["created_at"] or datetime.now(timezone.utc)).isoformat(),
            "metadata": meta,
            "source_file": {
                "name": row["source_file_name"],
                "type": row["source_file_type"],
                "relative_path": row["source_relative_path"] or "",
                "raw_url": row["raw_storage_url"] or "",
                "raw_key": meta.get("raw_key") or "",
            },
            "content_units": units,
            "reading_content": "\n\n".join(
                str(u["text"]).strip() for u in units if str(u["text"]).strip()),
        }

        p_key = studio_payload_key(tenant_cfg, client_id, job_id)
        try:
            # Rewrite the payload too. For several of these jobs every S3
            # artifact was deleted, so the record's payload_key would otherwise
            # point at nothing — a record that lies about where its source is.
            writer.write_json(p_key, payload)
            result = write_source_content_and_index(tenant_cfg, client_id, payload, p_key)
            added += 1
            print(f"   added {job_id[:8]}  {len(units):>5}u  "
                  f"{(row['source_relative_path'] or row['source_file_name'])[:52]}")
        except Exception as exc:
            failed += 1
            print(f"   FAILED {job_id[:8]} {row['source_file_name'][:40]}: "
                  f"{type(exc).__name__}: {exc}", file=sys.stderr)

    # ---- retire stubs, only after their replacement actually landed ---------
    retired = 0
    if args.retire_stubs and retire:
        fresh = read_source_index(tenant_cfg, client_id)
        live = {str(r.get("job_id")) for r in (fresh.get("sources") or [])}
        keep = []
        for rec in fresh.get("sources") or []:
            match = next((r for r, _ in retire if str(r.get("job_id")) == str(rec.get("job_id"))), None)
            if match is not None:
                repl = will_serve_name.get(str(rec.get("source_file_name") or "").strip().lower()) or []
                if any(j in live for j, _ in repl):
                    retired += 1
                    continue
                print(f"   kept stub {str(rec.get('job_id'))[:8]} — its replacement "
                      f"was not written, so removing it would lose the document")
            keep.append(rec)
        if retired:
            fresh["sources"] = keep
            write_source_index(tenant_cfg, client_id, fresh)

    conn.close()
    print(f"\ndone: {added} records added, {failed} skipped/failed"
          f"{f', {retired} stubs retired' if args.retire_stubs else ''}.")
    print("Re-run without --apply: a clean plan (0 stranded) is the check.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
