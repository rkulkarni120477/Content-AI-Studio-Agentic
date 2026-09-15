"""Re-tag ebook_reference page units that failed (or never completed) LLM tagging.

Loads unit text from Postgres — no S3 PDF download — runs the same batched
tagger used at ingest, then writes Postgres + OpenSearch + S3 content artifacts
for only the selected units.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional, Sequence

from config.settings import TenantConfig
from services.artifacts import ArtifactWriter
from services.ebook_page_tagger import (
    TAGGING_OK,
    tag_ebook_page_units,
    tagger_config_from_pipeline,
    tagging_counts,
)
from services.indexing import generate_embeddings, opensearch_upsert, rds_upsert
from services.pipeline.common import call_llm
from services.source_library import (
    read_source_index,
    source_content_key,
    studio_payload_key,
    write_source_index,
)
from services import source_index_pg

log = logging.getLogger(__name__)

#: Metadata keys merged from a retagged unit into content.json / payload units.
_RETAG_META_KEYS = frozenset({
    "acs_codes", "topics", "summary",
    "tagging_status", "tagging_error", "tagging_attempted_at",
    "chapter", "page_number", "printed_page", "pdf_page",
    "chunking_strategy", "chunk_index",
})


def _parse_meta(v: Any) -> Dict[str, Any]:
    if v is None:
        return {}
    if isinstance(v, str):
        try:
            return json.loads(v) or {}
        except Exception:
            return {}
    return dict(v or {})


def _load_units_from_pg(
    tenant_cfg: TenantConfig,
    job_id: str,
    *,
    unit_ids: Optional[Sequence[str]] = None,
    all_failed: bool = False,
    all_units: bool = False,
) -> List[Dict[str, Any]]:
    cfg = tenant_cfg.structure_store
    if not cfg.enabled or not cfg.url:
        raise RuntimeError("structure_store is required to re-tag ebook pages")
    import psycopg
    from psycopg.rows import dict_row

    dsn = cfg.url
    schema = cfg.schema_name or "dis"
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT content_unit_id, document_id, job_id, tenant_id, client_id,
                       unit_type, unit_number, title, text_content, visual_summary,
                       keywords_json, topics_json, metadata_json
                  FROM {schema}.dis_content_units
                 WHERE job_id = %s
                 ORDER BY unit_number
                """,
                (job_id,),
            )
            rows = cur.fetchall()

    units: List[Dict[str, Any]] = []
    wanted = {str(u) for u in (unit_ids or []) if u}
    for r in rows:
        meta = _parse_meta(r.get("metadata_json"))
        status = str(meta.get("tagging_status") or "").lower()
        uid = str(r.get("content_unit_id") or "")
        if all_units:
            pass
        elif all_failed:
            # Anything not successfully tagged — includes unset status after
            # --skip-llm-tagging / stale content banners that say "failed".
            if status == TAGGING_OK:
                continue
        elif wanted:
            if uid not in wanted:
                continue
        else:
            continue
        kw = r.get("keywords_json")
        tp = r.get("topics_json")
        if isinstance(kw, str):
            try:
                kw = json.loads(kw)
            except Exception:
                kw = []
        if isinstance(tp, str):
            try:
                tp = json.loads(tp)
            except Exception:
                tp = []
        units.append({
            "content_unit_id": uid,
            "document_id": r.get("document_id"),
            "unit_type": r.get("unit_type") or "page",
            "unit_number": r.get("unit_number"),
            "title": r.get("title") or "",
            "text": r.get("text_content") or "",
            "visual_summary": r.get("visual_summary") or "",
            "keywords": kw if isinstance(kw, list) else [],
            "topics": tp if isinstance(tp, list) else [],
            "metadata": meta,
            "assets": [],
            "_tenant_id": r.get("tenant_id"),
            "_client_id": r.get("client_id"),
        })
    return units


def _load_doc_context(tenant_cfg: TenantConfig, job_id: str) -> Dict[str, Any]:
    cfg = tenant_cfg.structure_store
    import psycopg
    from psycopg.rows import dict_row

    with psycopg.connect(cfg.url, row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT document_id, tenant_id, client_id, document_type,
                       source_file_name, source_file_type, source_relative_path,
                       raw_storage_url, metadata_json
                  FROM {cfg.schema_name or 'dis'}.dis_documents
                 WHERE job_id = %s
                 LIMIT 1
                """,
                (job_id,),
            )
            doc = cur.fetchone()
    if not doc:
        raise FileNotFoundError(f"document for job_id={job_id} not found")
    return doc


def _merge_retag_into_unit(unit: Dict[str, Any], src: Dict[str, Any], *, replace_metadata: bool = False) -> None:
    """Apply retag/edit fields onto one unit dict in place."""
    if src.get("title") is not None:
        unit["title"] = src.get("title")
    if src.get("topics") is not None:
        unit["topics"] = src.get("topics")
    if src.get("keywords") is not None:
        unit["keywords"] = src.get("keywords")
    src_meta = src.get("metadata") or {}
    if replace_metadata:
        unit["metadata"] = dict(src_meta)
    else:
        unit["metadata"] = {
            **(unit.get("metadata") or {}),
            **{k: v for k, v in src_meta.items() if k in _RETAG_META_KEYS},
        }


def merge_retag_units_into_list(
    units: List[Dict[str, Any]],
    updated_by_id: Dict[str, Dict[str, Any]],
    *,
    replace_metadata: bool = False,
) -> List[Dict[str, Any]]:
    """Merge retagged units into an existing list without dropping other units.

    Units already in ``units`` are updated in place (order preserved). Updated
    ids missing from the list are appended. Returns the same list object.
    """
    if not isinstance(units, list):
        units = []
    seen = set()
    for u in units:
        uid = u.get("content_unit_id")
        if uid in updated_by_id:
            _merge_retag_into_unit(u, updated_by_id[uid], replace_metadata=replace_metadata)
            seen.add(uid)
    for uid, src in updated_by_id.items():
        if uid in seen:
            continue
        units.append({
            "content_unit_id": uid,
            "unit_type": src.get("unit_type"),
            "unit_number": src.get("unit_number"),
            "title": src.get("title"),
            "text": src.get("text"),
            "topics": src.get("topics"),
            "keywords": src.get("keywords"),
            "metadata": dict(src.get("metadata") or {}),
        })
    return units


def _update_source_index_tagging_counts(
    tenant_cfg: TenantConfig,
    client_id: str,
    job_id: str,
    counts: Dict[str, int],
) -> None:
    """Persist recount on the compact source-index row (0 is a valid value)."""
    extra = {
        "tagging_failed_count": int(counts.get("tagging_failed_count") or 0),
        "tagging_pending_count": int(counts.get("tagging_pending_count") or 0),
    }
    if source_index_pg.use_pg_source_index(tenant_cfg):
        # update_status skips falsy extra values (``if v``); write counts via upsert merge.
        index = read_source_index(tenant_cfg, client_id)
        rec = next((r for r in index.get("sources", []) if str(r.get("job_id")) == str(job_id)), None)
        if not rec:
            return
        rec = dict(rec)
        rec.update(extra)
        from datetime import datetime
        rec["updated_at"] = datetime.utcnow().isoformat()
        source_index_pg.upsert_record(tenant_cfg, client_id, rec)
        return

    from datetime import datetime
    from services.locks import source_index_lock
    with source_index_lock(tenant_cfg.tenant_id, client_id):
        index = read_source_index(tenant_cfg, client_id)
        for rec in index.get("sources", []):
            if str(rec.get("job_id")) == str(job_id):
                rec.update(extra)
                rec["updated_at"] = datetime.utcnow().isoformat()
                write_source_index(tenant_cfg, client_id, index)
                return


def _patch_s3_artifacts(
    tenant_cfg: TenantConfig,
    client_id: str,
    job_id: str,
    updated_units: List[Dict[str, Any]],
    doc: Dict[str, Any],
) -> Dict[str, Any]:
    """Merge updated unit metadata into S3 content.json / payload / extracted units.

    content.json is the View UI source of truth. We patch it in place and must
    NOT rebuild it from a sparse studio payload (that wiped sibling page tags
    when Retry-this-page only passed one unit).
    """
    writer = ArtifactWriter(tenant_cfg)
    index = read_source_index(tenant_cfg, client_id)
    record = next((r for r in index.get("sources", []) if str(r.get("job_id")) == str(job_id)), None) or {}
    content_key = record.get("content_key") or source_content_key(tenant_cfg, client_id, job_id)
    payload_key = record.get("payload_key") or studio_payload_key(tenant_cfg, client_id, job_id)

    by_id = {u["content_unit_id"]: u for u in updated_units if u.get("content_unit_id")}

    # --- content.json (authoritative for View / Sections) ---
    try:
        content_doc = writer.read_json(content_key)
    except Exception:
        content_doc = None

    counts: Dict[str, int]
    if isinstance(content_doc, dict):
        units_list = list(content_doc.get("content_units") or [])
        merge_retag_units_into_list(units_list, by_id, replace_metadata=False)
        content_doc["content_units"] = units_list
        counts = tagging_counts(units_list)
        content_doc["tagging_failed_count"] = counts["tagging_failed_count"]
        content_doc["tagging_pending_count"] = counts["tagging_pending_count"]
        writer.write_json(content_key, content_doc)
    else:
        counts = tagging_counts(updated_units)
        content_doc = None

    # --- studio payload: merge tags; seed from content.json when sparse ---
    try:
        payload = writer.read_json(payload_key)
    except Exception:
        payload = None
    if not isinstance(payload, dict):
        payload = {
            "payload_version": "dis-studio-context-v1",
            "tenant_id": doc.get("tenant_id"),
            "client_id": client_id,
            "job_id": job_id,
            "source_file": {
                "name": doc.get("source_file_name"),
                "type": doc.get("source_file_type") or "pdf",
                "raw_key": record.get("raw_key") or "",
                "raw_url": doc.get("raw_storage_url") or "",
            },
            "metadata": _parse_meta(doc.get("metadata_json")),
            "content_units": [],
        }

    payload_units = list(payload.get("content_units") or [])
    content_units = list((content_doc or {}).get("content_units") or [])
    # If payload is missing most page units, seed from content.json so a later
    # rebuild cannot collapse the handbook to the single retagged page.
    if content_units and len(payload_units) < max(1, len(content_units) // 2):
        log.warning(
            "retag payload sparse (%s units) vs content.json (%s); seeding payload from content",
            len(payload_units), len(content_units),
        )
        payload_units = []
        for u in content_units:
            payload_units.append({
                "content_unit_id": u.get("content_unit_id"),
                "unit_type": u.get("unit_type"),
                "unit_number": u.get("unit_number"),
                "title": u.get("title"),
                "text": u.get("text"),
                "topics": u.get("topics"),
                "keywords": u.get("keywords"),
                "metadata": dict(u.get("metadata") or {}),
            })

    merge_retag_units_into_list(payload_units, by_id, replace_metadata=True)
    payload["content_units"] = payload_units
    # Prefer content.json recount when available (full handbook); else payload.
    if content_doc is not None:
        payload["tagging_failed_count"] = counts["tagging_failed_count"]
        payload["tagging_pending_count"] = counts["tagging_pending_count"]
    else:
        counts = tagging_counts(payload_units)
        payload["tagging_failed_count"] = counts["tagging_failed_count"]
        payload["tagging_pending_count"] = counts["tagging_pending_count"]

    writer.write_json(payload_key, payload)

    try:
        _update_source_index_tagging_counts(tenant_cfg, client_id, job_id, counts)
    except Exception:
        log.exception("failed to update source_index tagging counts job_id=%s", job_id)

    try:
        ns = payload.get("namespace") or tenant_cfg.get_namespace(client_id)
        prefix = writer.job_prefix(ns, job_id)
        writer.write_json(
            f"{prefix}/extracted/content_units.json",
            content_units or payload_units,
        )
    except Exception:
        log.exception("failed to write extracted content_units.json job_id=%s", job_id)

    return counts


def retag_ebook_pages(
    tenant_cfg: TenantConfig,
    client_id: str,
    job_id: str,
    *,
    unit_ids: Optional[Sequence[str]] = None,
    all_failed: bool = False,
    all_units: bool = False,
) -> Dict[str, Any]:
    """Re-run LLM content tagging for selected (or all failed/pending) page units."""
    if not all_failed and not all_units and not unit_ids:
        return {
            "status": "failed",
            "error": "provide unit_ids or set all_failed/all_units=true",
            "retagged": 0,
        }

    doc = _load_doc_context(tenant_cfg, job_id)
    units = _load_units_from_pg(
        tenant_cfg, job_id,
        unit_ids=unit_ids, all_failed=all_failed, all_units=all_units,
    )
    if not units:
        return {
            "status": "completed",
            "retagged": 0,
            "message": "no matching units to re-tag",
            "tagging_failed_count": 0,
            "tagging_pending_count": 0,
            "unit_ids": [],
        }

    tcfg = tagger_config_from_pipeline(tenant_cfg.pipeline)
    use_llm = (
        tenant_cfg.pipeline.llm_provider != "mock"
        and (tenant_cfg.pipeline.bedrock_enabled or tenant_cfg.pipeline.anthropic_enabled)
    )
    errors: List[str] = []
    if use_llm and tcfg["enabled"]:
        tag_ebook_page_units(
            units,
            call_llm_fn=call_llm,
            model_id=tcfg["model_id"],
            batch_size=tcfg["batch_size"],
            enabled=True,
            token_guard=None,
            errors=errors,
        )
    else:
        return {
            "status": "failed",
            "error": "LLM tagging is disabled (pipeline.llm_provider=mock or ebook_page_tagging_enabled=false)",
            "retagged": 0,
        }

    tenant_id = doc.get("tenant_id") or units[0].get("_tenant_id") or client_id
    # Strip helper keys before persistence.
    for u in units:
        u.pop("_tenant_id", None)
        u.pop("_client_id", None)

    state = {
        "job_id": job_id,
        "tenant_id": tenant_id,
        "client_id": client_id,
        "filename": doc.get("source_file_name") or "ebook.pdf",
        "file_type": doc.get("source_file_type") or "pdf",
        "doc_type": doc.get("document_type") or "ebook_reference",
        "doc_metadata": _parse_meta(doc.get("metadata_json")),
        "content_units": units,
    }
    rds = rds_upsert(tenant_cfg, state)
    if rds.get("status") not in {"completed", "skipped"}:
        return {"status": "failed", "error": f"rds_upsert: {rds}", "retagged": 0, "errors": errors}

    emb = generate_embeddings(tenant_cfg, state)
    if emb.get("status") == "completed":
        up = opensearch_upsert(tenant_cfg, state)
        if up.get("status") not in {"completed", "skipped"}:
            log.warning("retag opensearch upsert failed: %s", up)
    else:
        log.warning("retag embeddings failed: %s", emb)

    counts = _patch_s3_artifacts(tenant_cfg, client_id, job_id, units, doc)
    return {
        "status": "completed",
        "retagged": len(units),
        "unit_ids": [u["content_unit_id"] for u in units],
        "tagging_failed_count": counts.get("tagging_failed_count", 0),
        "tagging_pending_count": counts.get("tagging_pending_count", 0),
        "errors": errors,
        "units": [
            {
                "content_unit_id": u["content_unit_id"],
                "tagging_status": (u.get("metadata") or {}).get("tagging_status"),
                "tagging_error": (u.get("metadata") or {}).get("tagging_error"),
            }
            for u in units
        ],
    }

class UnitMetadataPatchError(Exception):
    """Raised when a unit metadata patch cannot be applied."""

    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.message = message
        self.status_code = status_code


def _as_string_list(value: Any, *, limit: Optional[int] = None) -> List[str]:
    if value is None:
        return []
    if isinstance(value, str):
        items = [value.strip()] if value.strip() else []
    elif isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray)):
        items = [str(v).strip() for v in value if str(v).strip()]
    else:
        items = [str(value).strip()] if str(value).strip() else []
    if limit is not None:
        return items[:limit]
    return items


def patch_unit_metadata(
    tenant_cfg: TenantConfig,
    client_id: str,
    job_id: str,
    unit_id: str,
    *,
    title: Optional[str] = None,
    summary: Optional[str] = None,
    topics: Optional[Sequence[Any]] = None,
    acs_codes: Optional[Sequence[Any]] = None,
) -> Dict[str, Any]:
    """Manually edit per-section tags (title / summary / topics / ACS codes).

    Synthetic `view_page_*` slices are not persisted units and cannot be patched.
    """
    wanted = str(unit_id or "").strip()
    if not wanted:
        raise UnitMetadataPatchError("unit_id is required", status_code=400)
    if wanted.startswith("view_page_"):
        raise UnitMetadataPatchError(
            "Synthetic view sections cannot be edited; select a real content unit",
            status_code=400,
        )
    if all(v is None for v in (title, summary, topics, acs_codes)):
        raise UnitMetadataPatchError("No unit metadata fields provided", status_code=400)

    doc = _load_doc_context(tenant_cfg, job_id)
    units = _load_units_from_pg(tenant_cfg, job_id, unit_ids=[wanted])
    if not units:
        raise UnitMetadataPatchError(f"Content unit '{wanted}' not found", status_code=404)

    unit = units[0]
    meta = dict(unit.get("metadata") or {})

    if title is not None:
        unit["title"] = str(title or "").strip()
    if summary is not None:
        cleaned = str(summary or "").strip()[:600]
        if cleaned:
            meta["summary"] = cleaned
        else:
            meta.pop("summary", None)
    if topics is not None:
        cleaned_topics = _as_string_list(topics, limit=8)
        unit["topics"] = cleaned_topics
        if cleaned_topics:
            meta["topics"] = cleaned_topics
        else:
            meta.pop("topics", None)
    if acs_codes is not None:
        from services.ebook_page_tagger import validate_acs_codes
        cleaned_acs = validate_acs_codes(acs_codes)
        if cleaned_acs:
            meta["acs_codes"] = cleaned_acs
        else:
            meta.pop("acs_codes", None)

    unit["metadata"] = meta
    unit.pop("_tenant_id", None)
    unit.pop("_client_id", None)

    tenant_id = doc.get("tenant_id") or client_id
    state = {
        "job_id": job_id,
        "tenant_id": tenant_id,
        "client_id": client_id,
        "filename": doc.get("source_file_name") or "ebook.pdf",
        "file_type": doc.get("source_file_type") or "pdf",
        "doc_type": doc.get("document_type") or "ebook_reference",
        "doc_metadata": _parse_meta(doc.get("metadata_json")),
        "content_units": [unit],
    }
    rds = rds_upsert(tenant_cfg, state)
    if rds.get("status") not in {"completed", "skipped"}:
        raise UnitMetadataPatchError(f"rds_upsert failed: {rds}", status_code=500)

    emb = generate_embeddings(tenant_cfg, state)
    if emb.get("status") == "completed":
        up = opensearch_upsert(tenant_cfg, state)
        if up.get("status") not in {"completed", "skipped"}:
            log.warning("unit metadata opensearch upsert failed: %s", up)
    else:
        log.warning("unit metadata embeddings failed: %s", emb)

    _patch_s3_artifacts(tenant_cfg, client_id, job_id, [unit], doc)

    return {
        "job_id": job_id,
        "unit": {
            "unit_id": unit["content_unit_id"],
            "content_unit_id": unit["content_unit_id"],
            "unit_number": unit.get("unit_number"),
            "unit_type": unit.get("unit_type"),
            "title": unit.get("title") or "",
            "text": unit.get("text") or "",
            "topics": unit.get("topics") or [],
            "metadata": unit.get("metadata") or {},
        },
    }
