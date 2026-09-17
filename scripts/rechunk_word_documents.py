#!/usr/bin/env python3
"""Rechunk existing Word (.docx/.doc) sources into page-tagged content units.

WHY. New Word uploads page-chunk via ContentUnitCreationAgent (page breaks /
section breaks / lastRenderedPageBreak), but files already in the Source Library
were word-windowed (~512 words) with no page_number / tagging_status. The raw
Word files are already in S3 — this script downloads them and replaces units
without a full re-upload / re-classification.

SKIP RULES.
* Filename must end with .docx / .doc (case-insensitive).
* Document types that already have sheet units are skipped:
  course_calendar, knowledge_test_report (AKTR), and hangar_activity when units
  are hangar_sheet (xlsx workbooks). Hangar Word/PDF page docs are included.
* A job is skipped when it already looks page-chunked: majority of units carry
  ``chunking_strategy = 'page'`` or a ``page_number`` (unless ``--force``).

    python scripts/rechunk_word_documents.py                    # plan only
    python scripts/rechunk_word_documents.py --limit 1          # prove one
    python scripts/rechunk_word_documents.py --apply            # write
    python scripts/rechunk_word_documents.py --apply --skip-llm-tagging
    python scripts/rechunk_word_documents.py --job-id <id> --force --apply
    python scripts/rechunk_word_documents.py --retag-failed --apply
    python scripts/rechunk_word_documents.py --retag-failed --job-id <id> --apply
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


#: Sheet-based Word/Excel types that must not be forced into page units.
_SHEET_DOC_TYPES = frozenset({
    "course_calendar",
    "knowledge_test_report",
})

_WORD_EXTS = (".docx", ".doc")


def _meta(v: Any) -> Dict[str, Any]:
    if v is None:
        return {}
    if isinstance(v, str):
        try:
            return json.loads(v) or {}
        except Exception:
            return {}
    return dict(v or {})


def _is_word_filename(name: str) -> bool:
    n = (name or "").strip().lower()
    return n.endswith(_WORD_EXTS)


def _already_page_tagged(cur, job_id: str) -> bool:
    """True when a majority of units already look page-chunked."""
    cur.execute("""
        SELECT metadata_json, unit_type
          FROM dis.dis_content_units
         WHERE job_id = %s
    """, (job_id,))
    rows = cur.fetchall()
    if not rows:
        return False
    # Hangar / calendar sheet units are not Word page units — never "done".
    unit_types = set()
    for r in rows:
        ut = str(
            (r["unit_type"] if isinstance(r, dict) else r[1]) or ""
        ).strip().lower()
        unit_types.add(ut)
    if "hangar_sheet" in unit_types or "calendar_sheet" in unit_types:
        return True  # skip — sheet path, not a Word page rechunk candidate
    if "knowledge_test_item" in unit_types:
        return True

    tagged = 0
    for r in rows:
        md = _meta(r["metadata_json"] if isinstance(r, dict) else r[0])
        ut = str(
            (r["unit_type"] if isinstance(r, dict) else r[1]) or ""
        ).strip().lower()
        if ut in {"page", "hangar_page"} and (
            str(md.get("chunking_strategy") or "") == "page" or md.get("page_number")
        ):
            tagged += 1
    return tagged >= max(1, (len(rows) + 1) // 2)


def _has_failed_or_pending_tags(cur, job_id: str) -> int:
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


def _build_units(
    job_id: str,
    raw_bytes: bytes,
    doc_meta: Dict[str, Any],
    filename: str,
    tenant_cfg,
    skip_llm: bool,
    *,
    hangar: bool = False,
) -> List[Dict[str, Any]]:
    from services.ebook_page_chunker import build_ebook_page_units
    from services.ebook_page_tagger import tag_ebook_page_units, tagger_config_from_pipeline
    from services.pipeline.common import call_llm
    from services.pipeline.extractors import extract_docx_pages

    t0 = time.time()
    pages = extract_docx_pages(raw_bytes)
    print(f"  extracted {len(pages)} Word page(s) in {time.time() - t0:.1f}s", flush=True)
    title = doc_meta.get("title") or filename or "Source"
    unit_type = "hangar_page" if hangar else "page"
    print("  building page units…", flush=True)
    t1 = time.time()
    units = build_ebook_page_units(
        job_id=job_id,
        pages=pages,
        doc_metadata=doc_meta,
        unit_type=unit_type,
        title=title,
        outline_map={},
    )
    for u in units:
        meta = dict(u.get("metadata") or {})
        if not meta.get("page_number") and meta.get("pdf_page") is not None:
            meta["page_number"] = str(meta["pdf_page"])
        if hangar:
            meta["document_type"] = "hangar_activity"
            meta["content_type"] = "hangar_activity"
        pdf_page = meta.get("pdf_page")
        if pdf_page is not None and not meta.get("chapter"):
            u["title"] = f"{title} — p. {meta.get('page_number') or pdf_page}"[:160]
        u["metadata"] = meta
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
        if hangar:
            from services.hangar_page_tagger import tag_hangar_page_units
            tag_hangar_page_units(
                units,
                call_llm_fn=call_llm,
                model_id=tcfg["model_id"],
                batch_size=tcfg["batch_size"],
                enabled=True,
                token_guard=None,
                errors=errors,
            )
        else:
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
            "type": state.get("file_type") or "docx",
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


def _word_source_sql(extra_where: str = "") -> str:
    # Match Word filenames; exclude sheet-native document types.
    # psycopg2 needs %% for a literal % in LIKE patterns when using %s params.
    return (
        """
        SELECT job_id, source_file_name, document_type, raw_key, content_key,
               payload_key, total_units, record_json, tenant_id, client_id
          FROM dis.source_index
         WHERE client_id = %s
           AND environment = %s
           AND (
                lower(coalesce(source_file_name, '')) LIKE '%%.docx'
             OR lower(coalesce(source_file_name, '')) LIKE '%%.doc'
           )
           AND coalesce(document_type, '') NOT IN ('course_calendar', 'knowledge_test_report')
        """
        + (extra_where or "")
    )


def _run_retag_failed(args, tenant_cfg, env: str, dis_url: str) -> int:
    """Re-tag failed/pending Word pages from Postgres text — no S3 download."""
    import psycopg2
    import psycopg2.extras
    from services.ebook_page_retag import retag_ebook_pages

    retag_all = bool(getattr(args, "retag_all", False))
    conn = psycopg2.connect(dis_url)
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        sql = _word_source_sql()
        params: List[Any] = [args.client, env]
        if args.job_id:
            sql += " AND job_id = %s"
            params.append(args.job_id)
        sql += " ORDER BY created_at NULLS LAST, job_id"
        cur.execute(sql, params)
        records = [r for r in cur.fetchall() if _is_word_filename(r.get("source_file_name") or "")]

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
    print(f"\n{len(todo)} Word document(s) to re-tag:\n")
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
                         "(no Word download / re-extract)")
    ap.add_argument("--retag-all", action="store_true",
                    help="re-run LLM tagging for EVERY page unit of the job")
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
        sql = _word_source_sql()
        params: List[Any] = [args.client, env]
        if args.job_id:
            sql += " AND job_id = %s"
            params.append(args.job_id)
        sql += " ORDER BY created_at NULLS LAST, job_id"
        cur.execute(sql, params)
        records = [r for r in cur.fetchall() if _is_word_filename(r.get("source_file_name") or "")]

    todo = []
    skipped = defaultdict(int)
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        for rec in records:
            doc_type = str(rec.get("document_type") or "").strip().lower()
            if doc_type in _SHEET_DOC_TYPES:
                skipped[f"sheet doc type ({doc_type})"] += 1
                continue
            if not rec.get("raw_key"):
                skipped["no raw_key in source_index"] += 1
                continue
            if not args.force and _already_page_tagged(cur, rec["job_id"]):
                skipped["already page-tagged or sheet units"] += 1
                continue
            todo.append(rec)

    if args.limit:
        todo = todo[:args.limit]

    print(f"\n{'APPLYING' if args.apply else 'PLAN (dry run — nothing will be written)'}")
    print(f"client={args.client}  environment={env}  "
          f"llm_tagging={'off' if args.skip_llm_tagging else 'on'}")
    print(f"\n{len(todo)} Word document(s) to rechunk:\n")
    for rec in todo:
        print(f"  {rec['job_id']}  {rec.get('source_file_name')}  "
              f"type={rec.get('document_type')}  (units={rec.get('total_units')})")
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
        filename = rec.get("source_file_name") or "document.docx"
        doc_type = str(rec.get("document_type") or "other").strip().lower()
        hangar = doc_type == "hangar_activity"
        print(f"\n→ {filename} ({job_id}) type={doc_type}", flush=True)
        try:
            raw_bytes = _download_raw(tenant_cfg, rec["raw_key"])
        except Exception as exc:
            print(f"  ! download failed: {exc}")
            failed += 1
            continue

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
        doc_meta.setdefault("document_type", doc_type or "other")
        doc_meta.setdefault("content_type", doc_type or "other")

        tenant_id = (doc or {}).get("tenant_id") or rec.get("tenant_id") or args.client
        client_id = (doc or {}).get("client_id") or rec.get("client_id") or args.client
        file_type = (doc or {}).get("source_file_type") or (
            "docx" if filename.lower().endswith(".docx") else "doc"
        )

        t0 = time.time()
        print("  extracting Word pages…", flush=True)
        units = _build_units(
            job_id, raw_bytes, doc_meta, filename, tenant_cfg, args.skip_llm_tagging,
            hangar=hangar,
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
            "doc_type": doc_type,
            "doc_metadata": doc_meta,
            "content_units": units,
            "page_count": len(units),
            "namespace": tenant_cfg.get_namespace(client_id),
        }

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
