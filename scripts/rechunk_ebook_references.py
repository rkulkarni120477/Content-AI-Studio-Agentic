#!/usr/bin/env python3
"""Rechunk existing ebook_reference PDFs into page-tagged content units.

WHY. New uploads page-chunk via ContentUnitCreationAgent, but handbooks already
in the Source Library were word-windowed (~512 words) with no page_number /
chapter tags. Assigned reading and retrieval need those tags. The raw PDFs are
already in S3 — this script downloads them and replaces units without a full
re-upload / re-classification.

SKIP RULE. A job is skipped when it already looks like a finished page-chunked
ebook: at least ``_MIN_PAGE_UNITS_FOR_SKIP`` content units AND a majority of
those units carry ``chunking_strategy = 'page'`` or a ``page_number`` (unless
``--force``). A single unit with a ``page_number`` like ``1-24`` is NOT skipped
— that is the broken handbook shape this script exists to repair.

    python scripts/rechunk_ebook_references.py                    # plan only
    python scripts/rechunk_ebook_references.py --limit 1          # prove one
    python scripts/rechunk_ebook_references.py --apply            # write
    python scripts/rechunk_ebook_references.py --apply --skip-llm-tagging
    python scripts/rechunk_ebook_references.py --job-id <id> --force --apply
    python scripts/rechunk_ebook_references.py --retag-failed --apply
    python scripts/rechunk_ebook_references.py --retag-failed --job-id <id> --apply
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from collections import defaultdict
from typing import Any, Dict, List, Optional

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "dis_backend"))


def _meta(v: Any) -> Dict[str, Any]:
    if v is None:
        return {}
    if isinstance(v, str):
        try:
            return json.loads(v) or {}
        except Exception:
            return {}
    return dict(v or {})


#: Incomplete / preview rows (e.g. one unit with page_number ``1-24``) must not
#: count as finished page-chunked ebooks — otherwise rechunk skips the repair.
_MIN_PAGE_UNITS_FOR_SKIP = 10


def _already_page_tagged(cur, job_id: str) -> bool:
    """True when enough units already look page-chunked (majority + min count)."""
    cur.execute("""
        SELECT metadata_json
          FROM dis.dis_content_units
         WHERE job_id = %s
    """, (job_id,))
    rows = cur.fetchall()
    if len(rows) < _MIN_PAGE_UNITS_FOR_SKIP:
        return False
    tagged = 0
    for r in rows:
        md = _meta(r["metadata_json"] if isinstance(r, dict) else r[0])
        if str(md.get("chunking_strategy") or "") == "page" or md.get("page_number"):
            tagged += 1
    return tagged >= max(1, (len(rows) + 1) // 2)


def _has_failed_or_pending_tags(cur, job_id: str) -> int:
    """Count units that still need LLM tagging (failed, pending, or unset)."""
    cur.execute("""
        SELECT metadata_json
          FROM dis.dis_content_units
         WHERE job_id = %s
    """, (job_id,))
    n = 0
    for r in cur.fetchall():
        md = _meta(r["metadata_json"] if isinstance(r, dict) else r[0])
        status = str(md.get("tagging_status") or "").lower()
        if status != "ok":
            n += 1
    return n


def _download_raw(tenant_cfg, raw_key: str) -> bytes:
    from storage.provider import get_storage_provider
    provider = get_storage_provider(tenant_cfg)
    print(f"  downloading s3://…/{raw_key} …", flush=True)
    t0 = time.time()
    data = asyncio.run(provider.download(raw_key))
    print(f"  downloaded {len(data):,} bytes in {time.time() - t0:.1f}s", flush=True)
    return data


def _build_units(job_id: str, raw_bytes: bytes, doc_meta: Dict[str, Any],
                 filename: str, tenant_cfg, skip_llm: bool) -> List[Dict[str, Any]]:
    from services.ebook_page_chunker import (
        build_ebook_page_units, extract_pdf_pages, outline_chapter_map,
    )
    from services.ebook_page_tagger import tag_ebook_page_units, tagger_config_from_pipeline
    from services.pipeline.common import call_llm

    t0 = time.time()
    pages = extract_pdf_pages(raw_bytes, max_chars=0)
    print(f"  extracted {len(pages)} PDF pages in {time.time() - t0:.1f}s", flush=True)
    title = doc_meta.get("title") or filename or "Source"
    unit_type = tenant_cfg.document_processing.unit_type_map.get("ebook_reference", "page")
    print("  reading PDF outline / building units…", flush=True)
    t1 = time.time()
    units = build_ebook_page_units(
        job_id=job_id,
        pages=pages,
        doc_metadata=doc_meta,
        unit_type=unit_type,
        title=title,
        outline_map=outline_chapter_map(raw_bytes),
    )
    print(f"  unit build done ({len(units)} units) in {time.time() - t1:.1f}s", flush=True)
    if skip_llm or not units:
        return units

    tcfg = tagger_config_from_pipeline(tenant_cfg.pipeline)
    use_llm = (
        tenant_cfg.pipeline.llm_provider != "mock"
        and (tenant_cfg.pipeline.bedrock_enabled or tenant_cfg.pipeline.anthropic_enabled)
    )
    if use_llm and tcfg["enabled"]:
        errors: List[str] = []
        tag_ebook_page_units(
            units,
            call_llm_fn=call_llm,
            model_id=tcfg["model_id"],
            batch_size=tcfg["batch_size"],
            enabled=True,
            token_guard=None,
            errors=errors,
        )
        for e in errors:
            print(f"    ! tagging: {e}")
    return units


def _refresh_artifacts(tenant_cfg, client_id: str, state: Dict[str, Any],
                       raw_key: str, content_key: str, payload_key: str) -> None:
    from services.artifacts import ArtifactWriter
    from services.source_library import (
        studio_payload_key, write_source_content_and_index,
    )

    writer = ArtifactWriter(tenant_cfg)
    p_key = payload_key or studio_payload_key(tenant_cfg, client_id, state["job_id"])
    payload = {
        "payload_version": "dis-studio-context-v1",
        "tenant_id": state.get("tenant_id"),
        "client_id": state.get("client_id"),
        "job_id": state.get("job_id"),
        "namespace": state.get("namespace") or "",
        "source_file": {
            "name": state.get("filename"),
            "relative_path": state.get("source_relative_path") or state.get("filename"),
            "type": state.get("file_type") or "pdf",
            "raw_key": raw_key,
            "raw_url": state.get("raw_storage_url") or "",
            "size_bytes": len(state.get("raw_bytes") or b""),
        },
        "metadata": state.get("doc_metadata") or {},
        "content_units": state.get("content_units") or [],
        "page_count": state.get("page_count") or 0,
        "reading_content": "\n\n".join(
            u.get("text") or "" for u in (state.get("content_units") or [])
        ),
    }
    # Preserve existing payload fields (calendar etc.) when the prior payload exists.
    try:
        prior = writer.read_json(p_key)
        if isinstance(prior, dict):
            for k in ("structure", "calendar_structure", "syllabus_structure",
                      "quiz_structure", "project_structure", "quality_report",
                      "namespace"):
                if k in prior and k not in payload:
                    payload[k] = prior[k]
            if prior.get("namespace"):
                payload["namespace"] = prior["namespace"]
    except Exception:
        pass

    writer.write_json(p_key, payload)
    prefix = writer.job_prefix(
        payload.get("namespace") or tenant_cfg.get_namespace(client_id),
        state["job_id"],
    )
    writer.write_json(f"{prefix}/extracted/content_units.json", state.get("content_units") or [])
    write_source_content_and_index(tenant_cfg, client_id, payload, payload_key=p_key)


def _tagging_status_histogram(cur, job_id: str) -> Dict[str, int]:
    cur.execute("""
        SELECT metadata_json
          FROM dis.dis_content_units
         WHERE job_id = %s
    """, (job_id,))
    hist: Dict[str, int] = defaultdict(int)
    for r in cur.fetchall():
        md = _meta(r["metadata_json"] if isinstance(r, dict) else r[0])
        status = str(md.get("tagging_status") or "").lower() or "(unset)"
        hist[status] += 1
    return dict(hist)


def _unit_count(cur, job_id: str) -> int:
    cur.execute(
        "SELECT count(*) AS n FROM dis.dis_content_units WHERE job_id = %s",
        (job_id,),
    )
    row = cur.fetchone()
    if isinstance(row, dict):
        return int(row.get("n") or 0)
    return int(row[0] if row else 0)


def _run_retag_failed(args, tenant_cfg, env: str, dis_url: str) -> int:
    """Re-tag failed/pending pages from Postgres text — no S3 PDF download."""
    import psycopg2
    import psycopg2.extras
    from services.ebook_page_retag import retag_ebook_pages

    retag_all = bool(getattr(args, "retag_all", False))
    conn = psycopg2.connect(dis_url)
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        sql = """
            SELECT job_id, source_file_name, document_type, client_id, total_units
              FROM dis.source_index
             WHERE client_id = %s
               AND environment = %s
               AND document_type = 'ebook_reference'
        """
        params: List[Any] = [args.client, env]
        if args.job_id:
            sql += " AND job_id = %s"
            params.append(args.job_id)
        sql += " ORDER BY created_at NULLS LAST, job_id"
        cur.execute(sql, params)
        records = cur.fetchall()

    todo = []
    skipped = defaultdict(int)
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        if args.job_id and not records:
            print(f"\n! job_id {args.job_id} not found in source_index "
                  f"(client={args.client} env={env})")
            return 2
        for rec in records:
            total = _unit_count(cur, rec["job_id"])
            hist = _tagging_status_histogram(cur, rec["job_id"])
            if retag_all:
                if total <= 0:
                    skipped["no content units in Postgres"] += 1
                    print(f"  ! {rec['job_id']}: 0 PG units  hist={hist}")
                    continue
                todo.append((rec, total))
                continue
            n = _has_failed_or_pending_tags(cur, rec["job_id"])
            if n <= 0:
                skipped["no units needing tagging (all ok or empty)"] += 1
                print(f"  skip {rec['job_id']}: pg_units={total} hist={hist}")
                continue
            todo.append((rec, n))

    if args.limit:
        todo = todo[:args.limit]

    mode = "--retag-all" if retag_all else "--retag-failed"
    print(f"\n{'APPLYING' if args.apply else 'PLAN (dry run — nothing will be written)'}  {mode}")
    print(f"client={args.client}  environment={env}")
    print(f"\n{len(todo)} ebook_reference document(s) to re-tag:\n")
    for rec, n in todo:
        print(f"  {rec['job_id']}  {rec.get('source_file_name')}  ({n} pages to re-tag)")
    if skipped:
        print("\nskipped:")
        for reason, n in sorted(skipped.items()):
            print(f"  {n:>4}  {reason}")
    if not args.apply:
        print("\nRe-run with --apply to write. Nothing has been changed.")
        return 0

    ok = failed = 0
    for rec, n in todo:
        job_id = rec["job_id"]
        client_id = rec.get("client_id") or args.client
        print(f"\n→ {rec.get('source_file_name')} ({job_id}) — {n} pages", flush=True)
        try:
            result = retag_ebook_pages(
                tenant_cfg, client_id, job_id,
                all_failed=not retag_all,
                all_units=retag_all,
            )
        except Exception as exc:
            print(f"  ! {exc}")
            failed += 1
            continue
        if result.get("status") != "completed":
            print(f"  ! {result.get('error') or result}")
            failed += 1
            continue
        print(f"  ok — retagged={result.get('retagged')} "
              f"failed_left={result.get('tagging_failed_count')} "
              f"pending_left={result.get('tagging_pending_count')}")
        ok += 1
    print(f"\nDone. ok={ok} failed={failed}")
    return 0 if failed == 0 else 1


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--apply", action="store_true", help="write (default: plan only)")
    ap.add_argument("--client", default="aim")
    ap.add_argument("--limit", type=int, default=0, help="stop after N documents")
    ap.add_argument("--job-id", default="", help="rechunk only this job_id")
    ap.add_argument("--force", action="store_true",
                    help="rechunk even when page tags already present")
    ap.add_argument("--skip-llm-tagging", action="store_true",
                    help="location tags only (no Bedrock content-tagging calls)")
    ap.add_argument("--retag-failed", action="store_true",
                    help="only re-run LLM tagging for failed/pending/unset pages "
                         "(no PDF download / re-extract)")
    ap.add_argument("--retag-all", action="store_true",
                    help="re-run LLM tagging for EVERY page unit of the job "
                         "(ignores tagging_status; use when the UI banner is stale)")
    ap.add_argument("--batch-size", type=int, default=100,
                    help="units per OpenSearch bulk request (default 100)")
    args = ap.parse_args()

    import psycopg2
    import psycopg2.extras
    from config.settings import get_settings, get_tenant_config

    tenant_cfg = get_tenant_config(args.client)
    env = get_settings().environment or "development"
    dis_url = tenant_cfg.structure_store.url
    if not dis_url:
        print("structure_store.url is empty in client YAML — refusing to guess.")
        return 2

    if args.retag_failed or args.retag_all:
        return _run_retag_failed(args, tenant_cfg, env, dis_url)

    from services.indexing import (
        delete_content_units_by_job,
        generate_embeddings,
        opensearch_delete_by_job,
        opensearch_upsert,
        rds_upsert,
    )

    conn = psycopg2.connect(dis_url)
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        sql = """
            SELECT job_id, source_file_name, document_type, raw_key, content_key,
                   payload_key, total_units, record_json, tenant_id, client_id
              FROM dis.source_index
             WHERE client_id = %s
               AND environment = %s
               AND document_type = 'ebook_reference'
        """
        params: List[Any] = [args.client, env]
        if args.job_id:
            sql += " AND job_id = %s"
            params.append(args.job_id)
        sql += " ORDER BY created_at NULLS LAST, job_id"
        cur.execute(sql, params)
        records = cur.fetchall()

    todo = []
    skipped = defaultdict(int)
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        for rec in records:
            if not rec.get("raw_key"):
                skipped["no raw_key in source_index"] += 1
                continue
            if not args.force and _already_page_tagged(cur, rec["job_id"]):
                skipped["already page-tagged"] += 1
                continue
            todo.append(rec)

    if args.limit:
        todo = todo[:args.limit]

    print(f"\n{'APPLYING' if args.apply else 'PLAN (dry run — nothing will be written)'}")
    print(f"client={args.client}  environment={env}  "
          f"llm_tagging={'off' if args.skip_llm_tagging else 'on'}")
    print(f"\n{len(todo)} ebook_reference document(s) to rechunk:\n")
    for rec in todo:
        print(f"  {rec['job_id']}  {rec.get('source_file_name')}  "
              f"(units={rec.get('total_units')})")
    if skipped:
        print("\nskipped:")
        for reason, n in sorted(skipped.items()):
            print(f"  {n:>4}  {reason}")
    if not args.apply:
        print("\nRe-run with --apply to write. Nothing has been changed.")
        return 0
    if not todo:
        return 0

    ok = failed = 0
    for rec in todo:
        job_id = rec["job_id"]
        filename = rec.get("source_file_name") or "ebook.pdf"
        print(f"\n→ {filename} ({job_id})", flush=True)
        try:
            raw_bytes = _download_raw(tenant_cfg, rec["raw_key"])
        except Exception as exc:
            print(f"  ! download failed: {exc}")
            failed += 1
            continue

        # Document metadata from dis_documents (system of record for doc-level tags).
        print("  loading document row from Postgres…", flush=True)
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""
                SELECT document_id, tenant_id, client_id, document_type,
                       source_file_name, source_file_type, source_relative_path,
                       raw_storage_url, metadata_json
                  FROM dis.dis_documents
                 WHERE job_id = %s
                 LIMIT 1
            """, (job_id,))
            doc = cur.fetchone()
        print("  document row loaded", flush=True)

        doc_meta = _meta(doc["metadata_json"] if doc else None)
        if not doc_meta:
            doc_meta = _meta((rec.get("record_json") or {}).get("metadata")
                             if isinstance(rec.get("record_json"), dict)
                             else None)
        doc_meta.setdefault("document_type", "ebook_reference")
        doc_meta.setdefault("content_type", "ebook_reference")

        tenant_id = (doc or {}).get("tenant_id") or rec.get("tenant_id") or args.client
        client_id = (doc or {}).get("client_id") or rec.get("client_id") or args.client
        file_type = (doc or {}).get("source_file_type") or "pdf"

        t0 = time.time()
        print("  extracting PDF pages (this can take several minutes on large files)…", flush=True)
        units = _build_units(
            job_id, raw_bytes, doc_meta, filename, tenant_cfg, args.skip_llm_tagging,
        )
        print(f"  built {len(units)} page units in {time.time() - t0:.1f}s", flush=True)
        if not units:
            print("  ! no text pages extracted — skipping write")
            failed += 1
            continue

        state = {
            "job_id": job_id,
            "tenant_id": tenant_id,
            "client_id": client_id,
            "filename": filename,
            "file_type": file_type,
            "source_relative_path": (doc or {}).get("source_relative_path") or filename,
            "s3_key": rec["raw_key"],
            "raw_storage_url": (doc or {}).get("raw_storage_url") or "",
            "raw_bytes": raw_bytes,
            "doc_type": "ebook_reference",
            "doc_metadata": doc_meta,
            "content_units": units,
            "page_count": len(units),
            "namespace": tenant_cfg.get_namespace(client_id),
        }

        # Replace old unit_N ids before writing page_N.
        print("  writing Postgres units…", flush=True)
        del_pg = delete_content_units_by_job(tenant_cfg, job_id)
        del_os = opensearch_delete_by_job(tenant_cfg, job_id)
        print(f"  deleted old units: pg={del_pg.get('deleted')} os={del_os.get('deleted')}", flush=True)

        rds = rds_upsert(tenant_cfg, state)
        if rds.get("status") != "completed":
            print(f"  ! rds_upsert failed: {rds}")
            failed += 1
            continue
        print(f"  rds_upsert ok ({len(units)} units)", flush=True)

        # Embed + index in batches (handbooks are large).
        indexed = 0
        doc_ok = True
        for i in range(0, len(units), args.batch_size):
            batch = units[i:i + args.batch_size]
            batch_state = {**state, "content_units": batch}
            print(f"  embed+index [{i}:{i + len(batch)}]…", flush=True)
            for attempt in range(4):
                emb = generate_embeddings(tenant_cfg, batch_state)
                if emb.get("status") != "completed":
                    print(f"  ! embeddings failed [{i}:{i+len(batch)}]: {emb.get('error')}")
                    doc_ok = False
                    break
                up = opensearch_upsert(tenant_cfg, batch_state)
                if up.get("status") == "completed":
                    indexed += int(up.get("documents_indexed") or 0)
                    print(f"  indexed {indexed}/{len(units)}", flush=True)
                    break
                if attempt == 3:
                    print(f"  ! opensearch failed [{i}:{i+len(batch)}]: {up.get('error')}")
                    doc_ok = False
                    break
                time.sleep(2 ** attempt)
            if not doc_ok:
                break

        try:
            print("  refreshing source-content artifacts…", flush=True)
            _refresh_artifacts(
                tenant_cfg, client_id, state,
                raw_key=rec["raw_key"],
                content_key=rec.get("content_key") or "",
                payload_key=rec.get("payload_key") or "",
            )
            print("  artifacts refreshed", flush=True)
        except Exception as exc:
            print(f"  ! artifact refresh failed: {exc}")
            doc_ok = False

        if doc_ok:
            print(f"  ok — {len(units)} units, {indexed} indexed")
            ok += 1
        else:
            failed += 1

    print(f"\nDone. ok={ok} failed={failed}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
