#!/usr/bin/env python3
"""Re-index content units that exist in the structure store but not in the search index.

WHY THESE EXIST. Ingestion writes a document's text twice: to the structure store
(Postgres `dis.dis_content_units`, the system of record) and to the OpenSearch
index (what every generation actually searches). For 127 AIM documents only the
first write survives — the text is intact in Postgres and absent from the index,
so no CDD or Blueprint can reach it while the Source Library shows the document as
processed.

Measured 2026-08-27, sharply correlated with ingestion date:

    uploaded 2026-07   112 of 142 missing from the index   (79%)
    uploaded 2026-08    15 of 470 missing                  ( 3%)

i.e. the index was rebuilt or replaced around end of July and the earlier content
was never restored. Among the casualties: all 14 Block 2 lesson slide decks (653
units) — which is why the reference block has had no slides in any generation.

The step artifacts cannot be used to find these: `vector_store_upsert.json`
reports {"status":"completed","documents_indexed":36} for jobs with zero units in
the index. Only comparing the two stores tells the truth, which is what this does.

HOW IT WORKS. For each affected document it rebuilds the pipeline `state` from
Postgres and calls DIS's own `generate_embeddings` and `opensearch_upsert` — the
same functions ingestion uses — so the indexed document shape cannot drift from
what the pipeline produces. Bulk writes are create-or-replace by content_unit_id,
so re-running is safe.

COST. One Bedrock Titan embedding call per unit (amazon.titan-embed-text-v2:0).
5,841 units across the full AIM backlog; a few dollars at most, but it is a real
external call, so --limit exists to prove the path on a few documents first.

    python scripts/reindex_orphaned_units.py                    # plan only
    python scripts/reindex_orphaned_units.py --block "Block 2"  # plan, one block
    python scripts/reindex_orphaned_units.py --block "Block 2" --apply
    python scripts/reindex_orphaned_units.py --apply            # everything
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from collections import defaultdict

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "dis_backend"))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--apply", action="store_true", help="write (default: plan only)")
    ap.add_argument("--client", default="aim")
    ap.add_argument("--block", default="", help="limit to one block label, e.g. 'Block 2'")
    ap.add_argument("--limit", type=int, default=0, help="stop after N documents (proving run)")
    ap.add_argument("--batch-size", type=int, default=100,
                    help="units per bulk request (default 100)")
    ap.add_argument("--embed-max-chars", type=int, default=20000,
                    help="per-unit character cap sent to the embedding model (default 20000)")
    args = ap.parse_args()

    import psycopg2
    import psycopg2.extras
    from config.settings import get_tenant_config
    from services.indexing import generate_embeddings, opensearch_upsert, _vector_store_read_client

    tenant_cfg = get_tenant_config(args.client)
    cfg = tenant_cfg.vector_store
    # The client YAML is the only source of a store location, so a script that
    # writes to DIS cannot be repointed by an env var the service ignores — an
    # env-first read here would let a backfill edit a different database than
    # DIS reads. See settings, "Where the backing stores live".
    dis_url = tenant_cfg.structure_store.url

    # Which jobs the index already knows about. One aggregation, not N queries.
    client = _vector_store_read_client(cfg)
    res = client.search(index=cfg.index_name, body={
        "size": 0, "aggs": {"j": {"terms": {"field": "job_id", "size": 10000}}}})
    indexed_jobs = {b["key"] for b in res["aggregations"]["j"]["buckets"]}

    conn = psycopg2.connect(dis_url)
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("""
            select d.job_id, d.tenant_id, d.client_id, d.source_file_name,
                   d.source_file_type, d.document_type, d.metadata_json,
                   (select count(*) from dis.dis_content_units u where u.job_id = d.job_id) as n_units
              from dis.dis_documents d
             where d.client_id = %s
          order by d.created_at
        """, (args.client,))
        docs = cur.fetchall()

    todo = []
    skipped = defaultdict(int)
    for d in docs:
        meta = d["metadata_json"] or {}
        if isinstance(meta, str):
            meta = json.loads(meta)
        if d["job_id"] in indexed_jobs:
            skipped["already in the index"] += 1
            continue
        if not d["n_units"]:
            # Extraction produced nothing — a different failure (see the .doc /
            # antiword problem). Re-indexing cannot invent text; reported, not fixed.
            skipped["no text was ever extracted (needs re-ingestion, not re-indexing)"] += 1
            continue
        if args.block and str(meta.get("block") or "").strip() != args.block:
            skipped[f"not {args.block}"] += 1
            continue
        todo.append((d, meta))

    if args.limit:
        todo = todo[:args.limit]

    by_block = defaultdict(lambda: [0, 0])
    for d, meta in todo:
        b = str(meta.get("block") or "(no block)")
        by_block[b][0] += 1
        by_block[b][1] += d["n_units"]

    print(f"\n{'APPLYING' if args.apply else 'PLAN (dry run — nothing will be written)'}")
    print(f"client={args.client}  index={cfg.index_name}  "
          f"embedding={tenant_cfg.embedding.model_id}")
    total_units = sum(v[1] for v in by_block.values())
    print(f"\n{len(todo)} document(s) / {total_units} unit(s) to re-index:\n")
    for b in sorted(by_block):
        n, u = by_block[b]
        print(f"  {b:<16} {n:>4} documents  {u:>5} units")
    if skipped:
        print("\nskipped:")
        for reason, n in sorted(skipped.items()):
            print(f"  {n:>4}  {reason}")
    if not args.apply:
        print(f"\nWould issue ~{total_units} Bedrock embedding calls. "
              f"Re-run with --apply to write. Nothing has been changed.")
        return 0

    ok = failed = units_written = 0
    for d, meta in todo:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""
                select content_unit_id, unit_type, unit_number, title,
                       text_content, visual_summary, keywords_json, topics_json,
                       metadata_json
                  from dis.dis_content_units
                 where job_id = %s
              order by unit_number
            """, (d["job_id"],))
            rows = cur.fetchall()

        def _j(v, default):
            if v is None:
                return default
            return json.loads(v) if isinstance(v, str) else v

        units = [{
            "content_unit_id": r["content_unit_id"],
            "unit_type": r["unit_type"],
            "unit_number": r["unit_number"],
            "title": r["title"] or "",
            # The pipeline calls this `text`; the structure store column is
            # text_content. Mapping it wrong would index empty documents that
            # still report success — exactly the failure being repaired.
            "text": r["text_content"] or "",
            "visual_summary": r["visual_summary"] or "",
            "keywords": _j(r["keywords_json"], []),
            "topics": _j(r["topics_json"], []),
            "metadata": _j(r["metadata_json"], {}) or meta,
        } for r in rows]

        state = {
            "job_id": d["job_id"],
            "tenant_id": d["tenant_id"],
            "client_id": d["client_id"],
            "filename": d["source_file_name"],
            "file_type": d["source_file_type"],
            "doc_type": d["document_type"],
            "doc_metadata": meta,
            "content_units": units,
        }

        # Titan v2 accepts 8,192 TOKENS; every client config sets max_input_chars
        # 50000, which is roughly 12,500 tokens, so a long unit is rejected with
        # "Too many input tokens" — and generate_embeddings then returns [] and
        # opensearch_upsert quietly indexes the document with an EMPTY embedding
        # vector, present but unfindable by semantic search. Clamped here rather
        # than relying on that config, and anything actually clipped is reported.
        clipped = [u["content_unit_id"] for u in units
                   if len(u["title"]) + 1 + len(u["text"]) > args.embed_max_chars]
        tenant_cfg.embedding.max_input_chars = args.embed_max_chars

        doc_ok = True
        n = 0
        # Bulk in batches: a single request carrying every unit of a large
        # handbook (8083-31B is 1,233 units x 1024 float dims) is megabytes of
        # JSON, and the endpoint answered with an SSL drop or a 429 rather than
        # indexing it. Batching plus backoff is what makes the big files land.
        for i in range(0, len(units), args.batch_size):
            batch = units[i:i + args.batch_size]
            batch_state = {**state, "content_units": batch}
            for attempt in range(4):
                emb = generate_embeddings(tenant_cfg, batch_state)
                if emb.get("status") != "completed":
                    print(f"  ! embeddings failed for {d['source_file_name']} "
                          f"[units {i}-{i+len(batch)}]: {emb.get('error')}")
                    doc_ok = False
                    break
                up = opensearch_upsert(tenant_cfg, batch_state)
                if up.get("status") == "completed":
                    n += int(up.get("documents_indexed") or 0)
                    break
                if attempt == 3:
                    print(f"  ! indexing failed for {d['source_file_name']} "
                          f"[units {i}-{i+len(batch)}]: {up.get('error')}")
                    doc_ok = False
                    break
                time.sleep(2 ** attempt)   # 1s, 2s, 4s — 429s are transient
            if not doc_ok:
                break

        if not doc_ok:
            failed += 1
            continue
        units_written += n
        ok += 1
        note = f"  ({len(clipped)} unit(s) clipped to {args.embed_max_chars} chars)" if clipped else ""
        print(f"  {n:>4} units  {d['source_file_name']}{note}")

    conn.close()
    print(f"\ndone: {ok} documents re-indexed ({units_written} units), {failed} failed.")
    print("Verify with the Source Library's retrieval status, or re-run this "
          "script — an indexed document is skipped, so a clean second run is the check.")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
