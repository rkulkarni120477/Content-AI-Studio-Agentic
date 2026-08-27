#!/usr/bin/env python3
"""Repair documents that were ingested without a block tag.

WHY THESE EXIST. Until the fix in app/api/v1/routers/source_library.py, DIS
derived a document's ``block`` by regex over its path, filename and first 1,500
characters. A folder named for its subject rather than its block matched nothing,
so the document was stored with ``block: ""`` and became invisible to every
block-scoped retrieval — while the Source Library still listed it as "Processed".

Measured on the AIM corpus 2026-08-27: 148 documents across six courses, 59-79%
of each. It is why Block 9's CDD reported "Total Projects: 0" while eleven
landing-gear project files sat in the store, and why Blocks 8 and 10 had no
reachable teaching content at all.

WHAT THIS DOES. For every AIM document with no block whose course maps to a
"Block N" course, set the block in both stores that retrieval reads:
the DIS structure store (dis.dis_documents.metadata_json) and the OpenSearch
content units (block / block_id / block_number).

WHAT IT DELIBERATELY WILL NOT DO
  * Global uploads (course_id "-1") — visible in every course by design.
  * Placeholder course ids ("general_science_i") — the scope is genuinely unknown,
    and guessing would file a document under a block it may not belong to.
  * Documents flagged ``spans_multiple_blocks`` (AKTR knowledge-test rollups) —
    blanked on purpose; attribution is per content unit, one unit per sheet.
  * Anything already carrying a block. Re-running is a no-op.

Dry run by default. Prints every document it would touch and writes nothing
until --apply is passed.

    python scripts/backfill_block_tags.py                 # plan only
    python scripts/backfill_block_tags.py --course 101    # plan, one course
    python scripts/backfill_block_tags.py --apply         # write
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections import defaultdict

BLOCK_RE = re.compile(r"\b(?:Block|BLK)\s*0*(\d+)\b", re.IGNORECASE)
#: Course ids that carry no single block scope. "-1" is the Source Library's
#: "Upload as Global" sentinel; the string form is DIS's default placeholder.
UNSCOPED_COURSE_IDS = {"-1", "", "general_science_i", "None"}


def block_label(value) -> str:
    """Canonical form to write: ``Block 9``, never ``Block 09``.

    Mirrors dis_backend.services.blocks.block_label. Unpadded because that is what
    the course records and the UI use, and what the blocks that already work are
    stored as — writing padded here would widen the split this repairs.
    """
    m = BLOCK_RE.search(str(value or ""))
    return f"Block {int(m.group(1))}" if m else ""


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--apply", action="store_true", help="write changes (default: plan only)")
    ap.add_argument("--client", default="aim", help="DIS client id (default: aim)")
    ap.add_argument("--course", default="", help="limit to one course id")
    ap.add_argument("--from-manifest", default="",
                    help="re-apply the OpenSearch half for a previous run's manifest "
                         "(use when the structure store was updated but the index was not)")
    ap.add_argument("--manifest", default="",
                    help="path to write the rollback manifest (default: backups/block_tag_backfill_<n>.json)")
    args = ap.parse_args()

    try:
        import psycopg2
        import requests
        from requests.auth import HTTPBasicAuth
        import yaml
    except ImportError as exc:
        print(f"missing dependency: {exc}", file=sys.stderr)
        return 2

    cfg_path = f"dis_backend/config/clients/{args.client}.yaml"
    if not os.path.exists(cfg_path):
        print(f"no client config at {cfg_path}", file=sys.stderr)
        return 2
    cfg = yaml.safe_load(open(cfg_path))
    dis_url = os.environ.get("DIS_STRUCTURE_STORE_URL") or cfg["structure_store"]["url"]
    vs = cfg["vector_store"]
    os_endpoint = os.environ.get("DIS_VECTOR_STORE_ENDPOINT") or vs["endpoint"]
    os_index = os.environ.get(f"DIS_VECTOR_STORE_INDEX_{args.client.upper()}") or vs["index_name"]
    os_auth = HTTPBasicAuth(vs["username"], vs["password"])

    cas_url = os.environ.get("DATABASE_URL_PROD", "").replace("+psycopg2", "")
    if not cas_url:
        print("DATABASE_URL_PROD is not set — needed to map course_id -> block name",
              file=sys.stderr)
        return 2

    # course_id -> block label, from the course records CAS owns.
    with psycopg2.connect(cas_url) as cas, cas.cursor() as cur:
        cur.execute("select id, name from courses")
        course_block = {str(cid): block_label(name) for cid, name in cur.fetchall()}

    with psycopg2.connect(dis_url) as dis, dis.cursor() as cur:
        cur.execute("""
            select document_id, source_file_name,
                   metadata_json::json->>'course_id',
                   coalesce(metadata_json::json->>'spans_multiple_blocks','false')
            from dis.dis_documents
            where client_id = %s
              and coalesce(metadata_json::json->>'block','') = ''
        """, (args.client,))
        rows = cur.fetchall()

    if args.from_manifest:
        prior = json.load(open(args.from_manifest))
        plan = [(d["document_id"], d["source_file_name"], d["course_id"], d["block_set_to"])
                for d in prior["documents"]]
        skipped = defaultdict(int)
        rows = []
    plan_from_manifest = bool(args.from_manifest)

    plan, skipped = (plan, skipped) if plan_from_manifest else ([], defaultdict(int))
    for doc_id, fname, course_id, spans in rows:
        course_id = str(course_id or "")
        if args.course and course_id != args.course:
            continue
        if str(spans).lower() == "true":
            skipped["spans multiple blocks (AKTR rollup)"] += 1
            continue
        if course_id in UNSCOPED_COURSE_IDS:
            skipped[f"no single course scope (course_id={course_id or 'null'})"] += 1
            continue
        label = course_block.get(course_id, "")
        if not label:
            skipped[f"course {course_id} is not a block course"] += 1
            continue
        plan.append((doc_id, fname, course_id, label))

    by_block = defaultdict(list)
    for doc_id, fname, course_id, label in plan:
        by_block[label].append(fname)

    print(f"\n{'APPLYING' if args.apply else 'PLAN (dry run — nothing will be written)'}")
    print(f"client={args.client}  structure_store={dis_url.split('@')[-1]}  index={os_index}")
    print(f"\n{len(plan)} document(s) would be tagged:\n")
    for label in sorted(by_block, key=lambda x: int(x.split()[-1])):
        names = by_block[label]
        print(f"  {label}  ({len(names)} documents)")
        for n in sorted(names):
            print(f"      {n}")
    if skipped:
        print("\ndeliberately skipped:")
        for reason, n in sorted(skipped.items()):
            print(f"  {n:>4}  {reason}")

    if not args.apply:
        print("\nRe-run with --apply to write. Nothing has been changed.")
        return 0

    # Rollback manifest, written BEFORE any write. Every document in the plan had
    # an empty block (that is the selection criterion), so reverting is exactly
    # "set these document_ids back to empty" — but only if we know which they were,
    # and after the write they are indistinguishable from documents that were always
    # tagged. Written first so a crash mid-write still leaves a complete record.
    manifest_path = args.manifest or f"backups/block_tag_backfill_{len(plan)}.json"
    os.makedirs(os.path.dirname(manifest_path) or ".", exist_ok=True)
    with open(manifest_path, "w") as fh:
        json.dump({
            "client": args.client,
            "index": os_index,
            "note": "these document_ids had block=='' before the backfill; "
                    "to revert, set block/block_id/block_number back to ''/''/null",
            "documents": [{"document_id": d, "source_file_name": f,
                           "course_id": c, "block_set_to": lb}
                          for d, f, c, lb in plan],
        }, fh, indent=2)
    print(f"\nrollback manifest written: {manifest_path}")

    print("writing...")
    updated_docs = updated_units = 0
    with psycopg2.connect(dis_url) as dis:
        with dis.cursor() as cur:
            for doc_id, _fname, _cid, label in plan:
                num = int(label.split()[-1])
                # metadata_json is jsonb; jsonb_set returns jsonb, so no cast either side.
                cur.execute("""
                    update dis.dis_documents
                       set metadata_json = jsonb_set(jsonb_set(jsonb_set(
                             metadata_json,
                             '{block}',        to_jsonb(%s::text)),
                             '{block_id}',     to_jsonb(%s::text)),
                             '{block_number}', to_jsonb(%s::int))
                     where document_id = %s
                       and coalesce(metadata_json->>'block','') = ''
                """, (label, f"B{num}", num, doc_id))
                updated_docs += cur.rowcount
        dis.commit()

    unindexed = []
    for doc_id, fname, _cid, label in plan:
        num = int(label.split()[-1])
        # The content units carry job_id, not document_id: `document_id` is in the
        # index mapping but written on 0 of 2,878 units. dis_documents.document_id
        # is literally "doc_" + job_id, so that is the join.
        job_id = doc_id[4:] if doc_id.startswith("doc_") else doc_id
        r = requests.post(
            f"{os_endpoint}/{os_index}/_update_by_query",
            auth=os_auth, timeout=180,
            params={"conflicts": "proceed", "refresh": "true"},
            json={
                # must_not wildcard, NOT must_not exists: an empty-string keyword
                # still "exists", so an exists check matches every untagged unit and
                # excludes exactly the ones needing repair.
                "query": {"bool": {
                    "filter": [{"term": {"job_id": job_id}}],
                    "must_not": [{"wildcard": {"block": "?*"}}],
                }},
                "script": {
                    "source": ("ctx._source.block = params.label; "
                               "ctx._source.block_id = params.bid; "
                               "ctx._source.block_number = params.num;"),
                    "params": {"label": label, "bid": f"B{num}", "num": num},
                },
            },
        )
        if r.status_code >= 300:
            print(f"  ! OpenSearch update failed for {fname}: {r.status_code} {r.text[:200]}")
            continue
        n = r.json().get("updated", 0)
        updated_units += n
        if n == 0:
            # Tagged in the structure store but with no searchable content units —
            # extraction never produced any. Retrieval reads OpenSearch, so this
            # document stays invisible no matter how it is tagged. Reported rather
            # than passed over: a silent zero here is how the original defect hid.
            unindexed.append(fname)

    if unindexed:
        print(f"\n  !! {len(unindexed)} document(s) are now tagged but have NO content units "
              f"in the index — they were never successfully extracted, so tagging cannot "
              f"make them retrievable. These need re-ingestion:")
        for f in sorted(unindexed):
            print(f"       {f}")

    print(f"\ndone: {updated_docs} structure-store documents, {updated_units} content units.")
    print("Re-running is safe — both writes skip anything already tagged.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
