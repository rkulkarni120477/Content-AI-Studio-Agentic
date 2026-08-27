#!/usr/bin/env python3
"""Give a document the course scope that makes it reachable.

DIS isolates documents per CAS course: a retrieval for course 101 accepts only
documents tagged with course_id "101" or the global sentinel "-1"
(context_retrieval._source_matches). A document carrying anything else is
invisible to every course — including "general_science_i", which is not a course
at all but the ``default_course_id`` placeholder from aim.yaml.

111 AIM documents / 2,889 units are stranded on that placeholder. Among them:

    8083-31B.pdf        1,233 units   the FAA Airframe handbook Block 9's calendar
                                      assigns as reading on nearly every day
    AC43.13-2025.pdf      590 units
    20 Block 2 slide decks 630 units  the teaching content for the reference block

They are indexed and carry embeddings. They are simply addressed to a course that
does not exist, so nothing asks for them. Re-indexing could not have fixed this
and did not: a document can be perfectly searchable and still unreachable.

THE RULE, and it is deliberately narrow:

  * A document with a block tag belongs to that block's course. "Block 02" and
    "2" resolve to the same course as "Block 2".
  * A document with NO block and a reference document_type (ebook_reference) is
    programme-wide material — a handbook serves every block — so it becomes the
    global sentinel "-1".
  * Anything else is reported and left alone. Guessing a scope is how a document
    ends up addressed to the wrong course, which is worse than being unaddressed:
    it turns up in a generation it does not belong to.

Writes all three stores that carry the value: the structure store, the OpenSearch
units, and the S3 source index the Source Library lists from. Dry run by default.

    python scripts/backfill_course_scope.py            # plan only
    python scripts/backfill_course_scope.py --apply
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections import defaultdict

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "dis_backend"))

GLOBAL_COURSE_ID = "-1"
#: Not a course — aim.yaml's default_course_id, applied when ingestion had no
#: better answer. Every document wearing it is unreachable.
PLACEHOLDER_COURSE_IDS = {"general_science_i", "", "None", "-1"}
#: Doc types that serve the whole programme rather than one block.
GLOBAL_DOC_TYPES = {"ebook_reference"}

BLOCK_RE = re.compile(r"(?:block\s*)?0*(\d+)\s*$", re.IGNORECASE)


def block_key(value) -> str:
    """'Block 02' / 'Block 2' / '2' -> 'block 2'. '' when there is no block."""
    text = " ".join(str(value or "").strip().split()).lower()
    if not text:
        return ""
    m = BLOCK_RE.match(text)
    return f"block {int(m.group(1))}" if m else text



def _sync_source_index(tenant_cfg, client_id, conn, read_source_index, write_source_index):
    """Make the S3 source index agree with the structure store on course_id.

    The Source Library lists from this file, not from Postgres or OpenSearch, so a
    stale copy shows the old scope while retrieval uses the new one — two answers
    to the same question, which is the shape of every defect in this area.

    Reconciles EVERY record rather than only the ones this run changed: a previous
    run that updated the stores and stopped short of the index would otherwise stay
    broken forever, and the whole point is that a second run is a valid repair.

    Note the file lives under the environment name (…/aim/<environment>/…), so
    running with the wrong ENVIRONMENT silently reads an empty index and reports
    0 records — which is exactly what happened the first time this ran.
    """
    with conn.cursor() as cur:
        cur.execute("""
            select job_id, metadata_json->>'course_id'
              from dis.dis_documents where client_id = %s
        """, (client_id,))
        truth = {j: c for j, c in cur.fetchall()}

    index = read_source_index(tenant_cfg, client_id)
    records = index.get("sources") or []
    if not records:
        print("  source index:    0 records found — check ENVIRONMENT matches the "
              "deployment whose index you mean to update")
        return
    touched = 0
    for rec in records:
        want = truth.get(rec.get("job_id"))
        if want is not None and str(rec.get("course_id") or "") != str(want):
            rec["course_id"] = want
            touched += 1
    if touched:
        write_source_index(tenant_cfg, client_id, index)
    print(f"  source index:    {touched} of {len(records)} records corrected")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--client", default="aim")
    ap.add_argument("--sync-source-index-only", action="store_true",
                    help="skip re-scoping; only make the S3 source index agree "
                         "with the structure store")
    args = ap.parse_args()

    import psycopg2
    import psycopg2.extras
    from config.settings import get_tenant_config
    from services.indexing import _vector_store_read_client
    from services.source_library import read_source_index, write_source_index

    tenant_cfg = get_tenant_config(args.client)
    cfg = tenant_cfg.vector_store
    dis_url = os.environ.get("DIS_STRUCTURE_STORE_URL") or tenant_cfg.structure_store.url
    cas_url = os.environ.get("DATABASE_URL_PROD", "").replace("+psycopg2", "")
    if not cas_url:
        print("DATABASE_URL_PROD is not set — needed to map a block to its course",
              file=sys.stderr)
        return 2

    conn = psycopg2.connect(dis_url)

    # Which CAS project owns this client's content, derived from the documents that
    # ARE correctly scoped rather than from a flag or a name match.
    #
    # This is not defensive padding. "Block 1" is the name of course 94 in project
    # 23 (AIM) AND of course 10 in project 8 (Online Student Material). A global
    # name lookup picks whichever row comes back first, and an AIM handbook
    # scoped into another tenant's course is worse than one scoped nowhere: it
    # would surface in that tenant's generations.
    with conn.cursor() as cur:
        cur.execute("""
            select distinct metadata_json->>'course_id'
              from dis.dis_documents
             where client_id = %s and metadata_json->>'course_id' ~ '^[0-9]+$'
        """, (args.client,))
        known_course_ids = [r[0] for r in cur.fetchall()]

    with psycopg2.connect(cas_url) as cas, cas.cursor() as cur:
        owner_projects = set()
        if known_course_ids:
            cur.execute("select distinct project_id from courses where id = any(%s::int[])",
                        (known_course_ids,))
            owner_projects = {r[0] for r in cur.fetchall() if r[0] is not None}
        if len(owner_projects) != 1:
            print(f"cannot determine which project owns client '{args.client}': "
                  f"its documents point at projects {sorted(owner_projects) or '[]'}. "
                  f"Refusing to guess — a wrong course scope leaks content into "
                  f"another tenant's generations.", file=sys.stderr)
            return 2
        project_id = owner_projects.pop()

        cur.execute("select id, name from courses where project_id = %s", (project_id,))
        block_course = {}
        for cid, name in cur.fetchall():
            k = block_key(name)
            if k.startswith("block "):
                if k in block_course:
                    print(f"ambiguous: project {project_id} has two courses named "
                          f"like '{name}' — refusing to guess", file=sys.stderr)
                    return 2
                block_course[k] = str(cid)
    print(f"resolved client '{args.client}' -> CAS project {project_id} "
          f"({len(block_course)} block courses)")
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("""
            select job_id, source_file_name, document_type, metadata_json
              from dis.dis_documents where client_id = %s
        """, (args.client,))
        docs = cur.fetchall()

    if args.sync_source_index_only:
        if not args.apply:
            print("--sync-source-index-only needs --apply; nothing was written.")
            return 0
        print("reconciling the S3 source index with the structure store...")
        _sync_source_index(tenant_cfg, args.client, conn,
                           read_source_index, write_source_index)
        conn.close()
        return 0

    plan, skipped = [], defaultdict(int)
    for d in docs:
        meta = d["metadata_json"] or {}
        if isinstance(meta, str):
            meta = json.loads(meta)
        current = str(meta.get("course_id") or "")
        if current not in PLACEHOLDER_COURSE_IDS:
            skipped["already scoped to a real course"] += 1
            continue
        if current == GLOBAL_COURSE_ID:
            skipped["already global (-1)"] += 1
            continue

        blk = block_key(meta.get("block"))
        if blk and blk in block_course:
            plan.append((d, block_course[blk], f"{meta.get('block')} -> course {block_course[blk]}"))
        elif not blk and (d["document_type"] or "") in GLOBAL_DOC_TYPES:
            plan.append((d, GLOBAL_COURSE_ID, "programme-wide reference -> global"))
        elif blk:
            skipped[f"block '{meta.get('block')}' has no CAS course"] += 1
        else:
            skipped[f"no block and type '{d['document_type']}' is not a known "
                    f"programme-wide type — needs a human decision"] += 1

    by_target = defaultdict(list)
    for d, target, why in plan:
        by_target[why].append(d["source_file_name"])

    print(f"\n{'APPLYING' if args.apply else 'PLAN (dry run — nothing will be written)'}")
    print(f"client={args.client}  index={cfg.index_name}\n")
    print(f"{len(plan)} document(s) would be re-scoped:\n")
    for why in sorted(by_target):
        names = by_target[why]
        print(f"  {why}  ({len(names)} documents)")
        for n in sorted(names)[:6]:
            print(f"      {n}")
        if len(names) > 6:
            print(f"      … and {len(names) - 6} more")
    if skipped:
        print("\nleft alone:")
        for reason, n in sorted(skipped.items()):
            print(f"  {n:>4}  {reason}")
    if not args.apply:
        print("\nRe-run with --apply to write. Nothing has been changed.")
        return 0

    os.makedirs("backups", exist_ok=True)
    manifest = f"backups/course_scope_backfill_{len(plan)}.json"
    with open(manifest, "w") as fh:
        json.dump([{"job_id": d["job_id"], "source_file_name": d["source_file_name"],
                    "was": str((d["metadata_json"] or {}).get("course_id")
                               if not isinstance(d["metadata_json"], str)
                               else json.loads(d["metadata_json"]).get("course_id")),
                    "now": target}
                   for d, target, _ in plan], fh, indent=2)
    print(f"\nrollback manifest: {manifest}\nwriting...")

    with conn.cursor() as cur:
        for d, target, _ in plan:
            cur.execute("""
                update dis.dis_documents
                   set metadata_json = jsonb_set(metadata_json, '{course_id}', to_jsonb(%s::text))
                 where job_id = %s
            """, (target, d["job_id"]))
    conn.commit()
    print(f"  structure store: {len(plan)} documents")

    client = _vector_store_read_client(cfg)
    units = 0
    for d, target, _ in plan:
        res = client.update_by_query(
            index=cfg.index_name, params={"conflicts": "proceed", "refresh": "true"},
            body={"query": {"term": {"job_id": d["job_id"]}},
                  "script": {"source": "ctx._source.metadata.course_id = params.c",
                             "params": {"c": target}}})
        units += int(res.get("updated") or 0)
    print(f"  search index:    {units} units")

    _sync_source_index(tenant_cfg, args.client, conn,
                       read_source_index, write_source_index)

    conn.close()
    print("\ndone. Re-running is safe — a document already scoped is skipped.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
