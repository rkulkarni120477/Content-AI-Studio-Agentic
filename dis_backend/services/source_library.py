"""Source Library index and clean content helpers.

Product rule:
- CAS Source Library should list a compact document catalogue, not full DIS metadata.
- View should return readable extracted content only.
- The catalogue is persisted in DIS Postgres (``structure_store``) when enabled,
  otherwise as one JSON file per client/workspace on S3.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

from config.settings import TenantConfig, get_settings
from services.artifacts import ArtifactWriter
from services.locks import source_index_lock
from services.metadata_framework.adapters import (
    project_filter_options_map,
    project_index_metadata,
)
from services.metadata_framework.registry import registry_for_tenant
from services import source_index_pg

logger = logging.getLogger(__name__)


STYLE_DOC_TYPES = {
    "style_guide",
    "authoring_guide",
    "authoring_guidelines",
    "copyediting_guidelines",
    "sample_lesson",
    "sample_chapter",
    "approved_template",
}
# A knowledge-test performance report is course-design AND blueprint input: it is what
# makes remediation and quick-check decisions evidence-based. Listed here as well as in
# the tenant's retrieval.source_type_mapping because these two are read at different
# times — the mapping gates retrieval, while these sets decide the `purpose` STORED on
# the source-library record (and so what the Source Library UI filters show). A rollup
# left out of these sets was stored as "general_reference" even with the mapping
# widened, which is how it read in the UI.
CDD_DOC_TYPES = {"syllabus", "course_outline", "program_overview", "learning_objectives",
                 "knowledge_test_report"}
BLUEPRINT_DOC_TYPES = {"course_calendar", "syllabus", "chapter_outline", "module_map",
                       "block_schedule", "knowledge_test_report"}

#: Doc types whose pipeline units are already the meaningful division of the document
#: and must survive into the Source Library content file. The "full_document" policy
#: below concatenates a document into ONE unit to keep small authoritative sources
#: coherent — right for a syllabus, wrong for a whole-program knowledge-test rollup,
#: whose sixteen per-block units would be welded into one blob covering every block.
#: Retrieval reads THIS file (context_retrieval._iter_payloads), so collapsing here is
#: what a Block 6 request would have received.
PER_UNIT_DOC_TYPES = {"knowledge_test_report"}

#: ebook_reference is page-chunked: one unit per physical PDF page, each carrying
#: chapter / page_number / ACS / topics. Collapsing to full_document would erase
#: those tags and make assigned-reading page slices impossible.
PAGE_CHUNK_DOC_TYPES = {"ebook_reference"}

#: Unit-level metadata a ``per_unit`` / ``page`` document carries into the content
#: file. A short allow-list, not the whole unit metadata dict: this file is what
#: CAS prompts read, and the product rule for it is "no internal DIS metadata".
_PER_UNIT_METADATA_KEYS = frozenset({
    "block", "block_id", "block_number", "day_number", "acs_codes", "missed_codes",
    "document_type", "content_type", "visibility", "sheet_name",
})

_PAGE_CHUNK_METADATA_KEYS = frozenset({
    "chapter", "page_number", "printed_page", "pdf_page", "acs_codes",
    "topics", "summary", "chunking_strategy", "chunk_index",
    "document_type", "content_type", "visibility",
    "tagging_status", "tagging_error", "tagging_attempted_at",
})


def normalize_visibility(value: str) -> str:
    v = (value or "").strip().lower().replace("-", "_").replace(" ", "_")
    if v in {"", "content_team", "internal", "instructor_only"}:
        return "instructor"
    if v in {"admin", "restricted", "admin_only", "restricted_admin"}:
        return "restricted_admin"
    if v in {"student_safe", "student"}:
        return "student"
    if v == "instructor":
        return "instructor"
    return v


def source_base_prefix(tenant_cfg: TenantConfig, client_id: str) -> str:
    namespace = tenant_cfg.get_namespace(client_id)
    return f"processed/{namespace}/{get_settings().environment}"


def source_index_key(tenant_cfg: TenantConfig, client_id: str) -> str:
    return f"{source_base_prefix(tenant_cfg, client_id)}/source_index/source_list.json"


def source_content_key(tenant_cfg: TenantConfig, client_id: str, job_id: str) -> str:
    return f"{source_base_prefix(tenant_cfg, client_id)}/{job_id}/source_content/content.json"


def studio_payload_key(tenant_cfg: TenantConfig, client_id: str, job_id: str) -> str:
    return f"{source_base_prefix(tenant_cfg, client_id)}/{job_id}/studio_payload/payload.json"


def normalize_purpose(purpose: str, document_type: str) -> str:
    p = (purpose or "").strip().lower().replace("-", "_")
    d = (document_type or "").strip().lower()
    # If the caller did not choose a meaningful purpose, infer it from the
    # document type. This keeps Cengage style/copyediting guides from appearing
    # as generic references in CAS.
    if p in {"", "general", "general_reference", "reference"}:
        if d in STYLE_DOC_TYPES:
            return "style"
        if d in CDD_DOC_TYPES:
            return "cdd"
        if d in BLUEPRINT_DOC_TYPES:
            return "blueprint"
        return "general_reference"
    return p


def purpose_flags(purpose: str, document_type: str) -> Dict[str, bool]:
    p = normalize_purpose(purpose, document_type)
    d = (document_type or "").strip().lower()
    return {
        "use_for_style": p == "style" or d in STYLE_DOC_TYPES,
        "use_for_cdd": p == "cdd" or d in CDD_DOC_TYPES,
        "use_for_blueprint": p == "blueprint" or d in BLUEPRINT_DOC_TYPES,
        "use_for_course_generation": p == "course_generation",
    }


def chunking_strategy(purpose: str, document_type: str) -> str:
    """Return product-level chunking policy.

    Cengage/AIM style, CDD, blueprint sources are normally small authoritative
    documents and should stay coherent. Course generation source material can be
    chunked by the deeper pipeline later.
    """
    p = normalize_purpose(purpose, document_type)
    d = (document_type or "").strip().lower()
    if d in PER_UNIT_DOC_TYPES:
        return "per_unit"
    if d in PAGE_CHUNK_DOC_TYPES:
        return "page"
    if p in {"style", "cdd", "blueprint"}:
        return "full_document"
    if d in STYLE_DOC_TYPES | CDD_DOC_TYPES | BLUEPRINT_DOC_TYPES:
        return "full_document"
    return "semantic_chunk"


def build_clean_content_document(payload: Dict[str, Any]) -> Dict[str, Any]:
    meta = payload.get("metadata", {}) or {}
    source = payload.get("source_file", {}) or {}
    job_id = payload.get("job_id")
    document_type = str(meta.get("document_type") or meta.get("doc_type") or source.get("type") or "document")
    purpose = normalize_purpose(str(meta.get("purpose") or ""), document_type)
    units = payload.get("content_units") or []
    text_parts: List[str] = []
    for u in units:
        t = str(u.get("text") or "").strip()
        if t:
            text_parts.append(t)
    reading_content = str(payload.get("reading_content") or "\n\n".join(text_parts)).strip()
    title = str(meta.get("title") or source.get("name") or "Source Document")
    strategy = chunking_strategy(purpose, document_type)
    if strategy == "full_document":
        clean_units = [{
            "content_unit_id": f"{job_id}:0",
            "unit_type": "full_document",
            "unit_number": 1,
            "title": title,
            "text": reading_content,
        }]
    else:
        clean_units = []
        # Use pipeline chunks when available; otherwise keep a single fallback unit.
        for i, u in enumerate(units, start=1):
            text = str(u.get("text") or "").strip()
            if text:
                clean_unit = {
                    "content_unit_id": u.get("content_unit_id") or f"{job_id}:{i}",
                    "unit_type": u.get("unit_type") or "content_chunk",
                    "unit_number": int(u.get("unit_number") or i),
                    "title": u.get("title") or title,
                    "text": text,
                }
                if strategy == "per_unit":
                    # These units differ from each other in metadata, not just text —
                    # each carries the block it belongs to. Retrieval merges unit
                    # metadata over document metadata (_passes_filters), so without
                    # this a Block 6 request could only see the document's own
                    # (deliberately block-less) tag and would filter the rollup out
                    # entirely. Scoped to per_unit types so no other document type's
                    # filtering behaviour changes.
                    clean_unit["metadata"] = {k: v for k, v in (u.get("metadata") or {}).items()
                                              if k in _PER_UNIT_METADATA_KEYS}
                elif strategy == "page":
                    # ebook_reference page units: keep chapter / page_number / ACS /
                    # topics so assigned reading and retrieval can filter by them.
                    clean_unit["metadata"] = {k: v for k, v in (u.get("metadata") or {}).items()
                                              if k in _PAGE_CHUNK_METADATA_KEYS}
                clean_units.append(clean_unit)
        if not clean_units:
            clean_units = [{
                "content_unit_id": f"{job_id}:0",
                "unit_type": "full_document",
                "unit_number": 1,
                "title": title,
                "text": reading_content,
            }]
    from services.ebook_page_tagger import tagging_counts
    counts = tagging_counts(clean_units) if strategy == "page" else {
        "tagging_failed_count": 0, "tagging_pending_count": 0,
    }
    return {
        "schema_version": "source_content_v1",
        "job_id": job_id,
        "tenant_id": payload.get("tenant_id"),
        "client_id": payload.get("client_id"),
        "document_title": title,
        "source_file_name": source.get("name"),
        "source_file_type": source.get("type"),
        "document_type": document_type,
        "purpose": purpose,
        "visibility": normalize_visibility(meta.get("visibility") or "instructor"),
        # Security-critical: carry restriction flags into the compact index so the
        # retrieval gate can hide answer keys / instructor guides / calendars from
        # students. Without these, gating fell back to a visibility string the gate
        # did not recognize and restricted content leaked into retrieval.
        "restricted": bool(meta.get("restricted")),
        "access_level": meta.get("access_level") or ("admin_only" if meta.get("restricted") else ""),
        "status": meta.get("status") or "processed",
        "size_bytes": source.get("size_bytes") or payload.get("file_size_bytes") or meta.get("file_size_bytes"),
        "page_count": payload.get("page_count") or meta.get("page_count"),
        "chunking_strategy": strategy,
        "total_units": len(clean_units),
        "total_characters": len(reading_content),
        "content_too_large_for_single_view": len(reading_content) > LARGE_VIEW_CHAR_THRESHOLD,
        "reading_content": reading_content,
        "preview": reading_content[:12000],
        "content_units": clean_units,
        "tagging_failed_count": counts["tagging_failed_count"],
        "tagging_pending_count": counts["tagging_pending_count"],
    }


def compact_source_record(
    payload: Dict[str, Any],
    payload_key: str,
    content_key: str,
    tenant_cfg: Optional[TenantConfig] = None,
) -> Dict[str, Any]:
    meta = payload.get("metadata", {}) or {}
    source = payload.get("source_file", {}) or {}
    document_type = str(meta.get("document_type") or meta.get("doc_type") or source.get("type") or "document")
    purpose = normalize_purpose(str(meta.get("purpose") or ""), document_type)
    now = datetime.utcnow().isoformat()
    registry = registry_for_tenant(tenant_cfg)

    def _first_unit_value(key: str):
        """Fallback: first non-empty value of `key` across content-unit metadata.

        Doc-level payload metadata usually carries the calendar-join keys, but
        for some doc types they live only on the units; this keeps the promoted
        value correct without a full aggregation.
        """
        for u in (payload.get("content_units") or []):
            v = (u.get("metadata") or {}).get(key)
            if v not in (None, "", []):
                return v
        return None

    def _promote(key: str, default=""):
        v = meta.get(key)
        if v in (None, "", []):
            v = _first_unit_value(key)
        return v if v not in (None, "", []) else default

    record = {
        "document_id": payload.get("job_id"),
        "job_id": payload.get("job_id"),
        "tenant_id": payload.get("tenant_id"),
        "client_id": payload.get("client_id"),
        "title": meta.get("title") or source.get("name"),
        "source_file_name": source.get("name"),
        "source_file_type": source.get("type"),
        "document_type": document_type,
        "purpose": purpose,
        "visibility": normalize_visibility(meta.get("visibility") or "instructor"),
        # Security-critical: carry restriction flags into the compact index so the
        # retrieval gate can hide answer keys / instructor guides / calendars from
        # students. Without these, gating fell back to a visibility string the gate
        # did not recognize and restricted content leaked into retrieval.
        "restricted": bool(meta.get("restricted")),
        "access_level": meta.get("access_level") or ("admin_only" if meta.get("restricted") else ""),
        "status": meta.get("status") or "processed",
        "size_bytes": source.get("size_bytes") or payload.get("file_size_bytes") or meta.get("file_size_bytes"),
        "page_count": payload.get("page_count") or meta.get("page_count"),
        "total_units": len(payload.get("content_units") or []),
        "payload_key": payload_key,
        "content_key": content_key,
        # Reference back to the original uploaded file in S3, so retrieval can
        # cite/deep-link the source (not just its filename). These come straight
        # from the payload's source_file block written at ingestion.
        "raw_storage_url": source.get("raw_url") or "",
        "raw_key": source.get("raw_key") or "",
        "source_relative_path": source.get("relative_path") or "",
        "created_at": payload.get("created_at") or now,
        "updated_at": now,
    }
    # Optional simple filter / calendar-join keys. Driven by the Field Registry
    # (tenant metadata_framework when present; else DEFAULT_REGISTRY). Additive
    # only; absent values stay empty/None. AIM profile transforms still write
    # into payload.metadata first.
    record.update(project_index_metadata(meta, _promote, registry))
    # Ebook page-tagging retry badges (0 for non-page docs).
    from services.ebook_page_tagger import tagging_counts
    counts = tagging_counts(payload.get("content_units") or [])
    record["tagging_failed_count"] = int(
        payload.get("tagging_failed_count")
        if payload.get("tagging_failed_count") is not None
        else counts["tagging_failed_count"]
    )
    record["tagging_pending_count"] = int(
        payload.get("tagging_pending_count")
        if payload.get("tagging_pending_count") is not None
        else counts["tagging_pending_count"]
    )
    return record


def _empty_index(tenant_cfg: TenantConfig, client_id: str) -> Dict[str, Any]:
    return {
        "schema_version": "source_index_v1",
        "tenant_id": tenant_cfg.tenant_id,
        "client_id": client_id,
        "updated_at": datetime.utcnow().isoformat(),
        "sources": [],
    }


def _read_source_index_s3(tenant_cfg: TenantConfig, client_id: str) -> Dict[str, Any]:
    """S3 source_list.json path — used when structure_store is disabled."""
    writer = ArtifactWriter(tenant_cfg)
    key = source_index_key(tenant_cfg, client_id)
    # Logical key omits storage.base_prefix (e.g. DIS/); the object on S3 is
    # base_prefix + key. Log both so a wrong-prefix failure is obvious.
    bp = (getattr(writer, "base_prefix", "") or "").strip().strip("/")
    storage_key = f"{bp}/{key}" if bp else key
    try:
        data = writer.read_json(key)
        if isinstance(data, dict):
            data.setdefault("sources", [])
            return data
        logger.error(
            "source index for client_id=%s at %s is %s, not an object — returning an "
            "empty library; the file is corrupt or is not a source index",
            client_id, storage_key, type(data).__name__)
    except Exception as exc:  # noqa: BLE001 — the library must still render
        code = getattr(getattr(exc, "response", None), "get", lambda *_: None)("Error") or {}
        code = (code or {}).get("Code", "") if isinstance(code, dict) else ""
        if code in {"NoSuchKey", "404", "NoSuchBucket"} or exc.__class__.__name__ == "FileNotFoundError":
            logger.info(
                "no source index yet for client_id=%s at bucket=%s key=%s — empty library",
                client_id, getattr(writer, "processed_bucket", "?"), storage_key)
        else:
            logger.error(
                "CANNOT READ the source index for client_id=%s (bucket=%s key=%s): %s: %s. "
                "Returning an empty library — every document will look missing and every "
                "generation will run with no sources. Check the bucket, the credentials "
                "and that ENVIRONMENT matches the deployment whose data you expect.",
                client_id, getattr(writer, "processed_bucket", "?"), storage_key,
                type(exc).__name__, exc)
    return _empty_index(tenant_cfg, client_id)


def _warn_if_s3_has_unmigrated_data(tenant_cfg: TenantConfig, client_id: str) -> None:
    """Loud signal when PG is empty but S3 still has a catalogue (deploy without backfill)."""
    try:
        s3_index = _read_source_index_s3(tenant_cfg, client_id)
        s3_n = len(s3_index.get("sources") or [])
    except Exception:  # noqa: BLE001
        return
    if s3_n > 0:
        logger.error(
            "source_index table empty for client_id=%s environment=%s but S3 still has "
            "%s catalogue row(s). Run scripts/migrate_source_index_to_pg.py --apply "
            "--client %s before expecting the Source Library to list documents.",
            client_id, source_index_pg.environment_name(), s3_n, client_id,
        )


def read_source_index(tenant_cfg: TenantConfig, client_id: str) -> Dict[str, Any]:
    """The allow-set every retrieval and the Source Library are built from.

    When ``structure_store`` is enabled, rows come from Postgres ``source_index``.
    Otherwise the legacy S3 ``source_list.json`` is used.

    An empty index is returned on failure so a fresh client — one that has never
    had an upload — starts from an empty library rather than an error page. That
    is the ONLY case it is meant to cover for a missing store. Connection /
    permission failures are logged loudly.
    """
    if source_index_pg.use_pg_source_index(tenant_cfg):
        try:
            sources = source_index_pg.read_sources(tenant_cfg, client_id)
            if not sources:
                _warn_if_s3_has_unmigrated_data(tenant_cfg, client_id)
            return {
                "schema_version": "source_index_v1",
                "tenant_id": tenant_cfg.tenant_id,
                "client_id": client_id,
                "updated_at": datetime.utcnow().isoformat(),
                "sources": sources,
            }
        except Exception as exc:  # noqa: BLE001 — the library must still render
            logger.error(
                "CANNOT READ the Postgres source_index for client_id=%s "
                "(schema=%s environment=%s): %s: %s. Returning an empty library — "
                "every document will look missing and every generation will run with "
                "no sources. Check structure_store.url and that the migrate script "
                "has been applied.",
                client_id,
                getattr(tenant_cfg.structure_store, "schema_name", "dis"),
                source_index_pg.environment_name(),
                type(exc).__name__,
                exc,
            )
            return _empty_index(tenant_cfg, client_id)
    return _read_source_index_s3(tenant_cfg, client_id)


def write_source_index(tenant_cfg: TenantConfig, client_id: str, index: Dict[str, Any]) -> str:
    """Persist a full catalogue. PG path replaces all rows for client+environment."""
    index["schema_version"] = "source_index_v1"
    index["tenant_id"] = tenant_cfg.tenant_id
    index["client_id"] = client_id
    index["updated_at"] = datetime.utcnow().isoformat()
    sources = list(index.get("sources") or [])
    if source_index_pg.use_pg_source_index(tenant_cfg):
        return source_index_pg.replace_index(tenant_cfg, client_id, sources)
    return ArtifactWriter(tenant_cfg).write_json(source_index_key(tenant_cfg, client_id), index)


def update_source_record_status(
    tenant_cfg: TenantConfig, client_id: str, job_id: str, status: str,
    extra: Optional[Dict[str, Any]] = None, only_if_status: Optional[str] = None,
) -> str:
    """Reconcile one source-index record's status in place.

    Used when the background pipeline stops (duplicate) or fails: the
    ProcessedStorageAgent that would finalize the record is skipped, so a
    deferred record would otherwise stay ``status="processing"`` forever. This
    flips it to e.g. ``duplicate`` / ``failed`` (the first-pages preview text is
    left untouched). No-op if the record isn't found. When ``only_if_status`` is
    given, the update applies ONLY if the record currently has that status —
    so already-finalized ("processed") records with real content are never
    disturbed.
    """
    if source_index_pg.use_pg_source_index(tenant_cfg):
        updated, ref = source_index_pg.update_status(
            tenant_cfg, client_id, job_id, status, extra=extra, only_if_status=only_if_status,
        )
        return ref if updated else ""

    # S3 path: whole read→modify→write under a per-client lock.
    with source_index_lock(tenant_cfg.tenant_id, client_id):
        index = read_source_index(tenant_cfg, client_id)
        for rec in index.get("sources", []):
            if str(rec.get("job_id")) == str(job_id):
                if only_if_status is not None and str(rec.get("status") or "").lower() != only_if_status.lower():
                    return ""
                rec["status"] = status
                rec["updated_at"] = datetime.utcnow().isoformat()
                if extra:
                    rec.update({k: v for k, v in extra.items() if v})
                return write_source_index(tenant_cfg, client_id, index)
        return ""


def upsert_source_record(tenant_cfg: TenantConfig, client_id: str, record: Dict[str, Any]) -> str:
    """Insert or replace one compact catalogue row (ingest / metadata save hot path)."""
    if source_index_pg.use_pg_source_index(tenant_cfg):
        return source_index_pg.upsert_record(tenant_cfg, client_id, record)

    with source_index_lock(tenant_cfg.tenant_id, client_id):
        index = read_source_index(tenant_cfg, client_id)
        sources = [s for s in index.get("sources", []) if s.get("job_id") != record.get("job_id")]
        sources.append(record)
        sources.sort(key=lambda x: str(x.get("updated_at") or x.get("created_at") or ""), reverse=True)
        index["sources"] = sources
        return write_source_index(tenant_cfg, client_id, index)


async def delete_source_document(tenant_cfg: TenantConfig, client_id: str, job_id: str) -> Dict[str, Any]:
    """Permanently delete one Source Library document: its raw upload, every
    processed artifact under its job_id prefix, its OpenSearch chunks, and its
    entry in the source index. Irreversible — callers must confirm first.
    """
    from services.indexing import opensearch_delete_by_job
    from storage.provider import get_storage_provider

    index = read_source_index(tenant_cfg, client_id)
    sources = index.get("sources", [])
    record = next((r for r in sources if str(r.get("job_id")) == str(job_id)), None)
    if not record:
        raise FileNotFoundError(f"No source found for job_id '{job_id}'")

    provider = get_storage_provider(tenant_cfg)
    writer = ArtifactWriter(tenant_cfg)
    namespace = tenant_cfg.get_namespace(client_id)
    job_folder = f"processed/{namespace}/{get_settings().environment}/{job_id}/"

    keys_to_delete = list(writer.list_keys(job_folder))
    raw_key = record.get("raw_key") or ""
    if raw_key:
        keys_to_delete.append(raw_key)

    deleted, errors = [], []
    for key in keys_to_delete:
        try:
            await provider.delete(key)
            deleted.append(key)
        except Exception as exc:
            errors.append({"key": key, "error": str(exc)})

    opensearch_result = opensearch_delete_by_job(tenant_cfg, job_id)

    def _remove_index_row() -> None:
        if source_index_pg.use_pg_source_index(tenant_cfg):
            source_index_pg.delete_record(tenant_cfg, client_id, job_id)
            return
        with source_index_lock(tenant_cfg.tenant_id, client_id):
            fresh = read_source_index(tenant_cfg, client_id)
            fresh["sources"] = [r for r in fresh.get("sources", []) if str(r.get("job_id")) != str(job_id)]
            write_source_index(tenant_cfg, client_id, fresh)

    await asyncio.to_thread(_remove_index_row)

    return {
        "job_id": job_id,
        "deleted": True,
        "s3_objects_deleted": len(deleted),
        "s3_errors": errors,
        "opensearch": opensearch_result,
    }


def extracted_chars(content_doc: Dict[str, Any]) -> int:
    """How much text a document actually yielded, across its content units.

    total_units cannot answer this. build_clean_content_document falls back to a
    single unit holding reading_content when chunking produced nothing, so a file
    the extractor could not read at all still records total_units = 1 — and in
    production not one of 605 AIM records has total_units = 0, while 109 of 120
    legacy .doc records have exactly one unit containing zero characters.

    So "did anything come out of this file" is a question about characters, not
    units, and it is the question the Source Library has to answer: a document
    with no text cannot reach any generation, and until now it displayed the same
    "needs_review" as a healthy one.
    """
    return sum(len(str(u.get("text") or "")) for u in (content_doc.get("content_units") or []))


def write_source_content_and_index(tenant_cfg: TenantConfig, client_id: str, payload: Dict[str, Any], payload_key: Optional[str] = None) -> Dict[str, str]:
    writer = ArtifactWriter(tenant_cfg)
    job_id = str(payload.get("job_id"))
    p_key = payload_key or studio_payload_key(tenant_cfg, client_id, job_id)
    c_key = source_content_key(tenant_cfg, client_id, job_id)
    content_doc = build_clean_content_document(payload)
    content_url = writer.write_json(c_key, content_doc)
    record = compact_source_record(payload, p_key, c_key, tenant_cfg=tenant_cfg)
    record["total_units"] = int(content_doc.get("total_units") or 0)
    # Alongside total_units, and for the same reason it is set here rather than in
    # compact_source_record: both describe the CLEAN content document retrieval
    # reads, not the raw pipeline payload.
    record["extracted_chars"] = extracted_chars(content_doc)
    record["tagging_failed_count"] = int(content_doc.get("tagging_failed_count") or 0)
    record["tagging_pending_count"] = int(content_doc.get("tagging_pending_count") or 0)
    index_url = upsert_source_record(tenant_cfg, client_id, record)
    return {"content_key": c_key, "content_url": content_url, "index_url": index_url}


def source_filter_options(
    records: List[Dict[str, Any]],
    tenant_cfg: Optional[TenantConfig] = None,
) -> Dict[str, List[str]]:
    fields = project_filter_options_map(registry_for_tenant(tenant_cfg))
    out: Dict[str, set] = {k: set() for k in fields}
    for r in records:
        for out_key, field in fields.items():
            v = r.get(field)
            if v:
                out[out_key].add(str(v))
    return {k: sorted(v) for k, v in out.items()}

# -----------------------------------------------------------------------------
# Large-document viewing helpers
# -----------------------------------------------------------------------------
PAGE_VIEW_CHARS = 12000
LARGE_VIEW_CHAR_THRESHOLD = 50000


def _split_text_for_view(text: str, chars_per_page: int = PAGE_VIEW_CHARS) -> List[Dict[str, Any]]:
    """Split readable text into UI pages without changing retrieval chunks.

    DIS stores the full extracted content in S3. CAS should not request/display a
    70MB file in one modal, so these synthetic pages are only for human viewing.
    """
    raw = str(text or "")
    if not raw:
        return []
    pages: List[Dict[str, Any]] = []
    # Prefer real page markers if the extractor added [Page N].
    import re
    matches = list(re.finditer(r"(?m)^\[Page\s+(\d+)\]\s*$", raw))
    if matches:
        for idx, m in enumerate(matches):
            start = m.end()
            end = matches[idx + 1].start() if idx + 1 < len(matches) else len(raw)
            page_no = int(m.group(1))
            page_text = raw[start:end].strip()
            if page_text:
                pages.append({"page_number": page_no, "title": f"Page {page_no}", "text": page_text, "char_count": len(page_text)})
        if pages:
            return pages

    # Fallback synthetic pages by character count, split on paragraph boundary when possible.
    pos = 0
    page_no = 1
    n = len(raw)
    while pos < n:
        end = min(n, pos + chars_per_page)
        if end < n:
            cut = raw.rfind("\n\n", pos, end)
            if cut > pos + int(chars_per_page * 0.5):
                end = cut
        page_text = raw[pos:end].strip()
        if page_text:
            pages.append({"page_number": page_no, "title": f"Part {page_no}", "text": page_text, "char_count": len(page_text)})
            page_no += 1
        pos = max(end, pos + 1)
    return pages


def _view_units_for_content(content_doc: Dict[str, Any]) -> List[Dict[str, Any]]:
    units = content_doc.get("content_units") or []
    out: List[Dict[str, Any]] = []
    for i, u in enumerate(units, start=1):
        text = str(u.get("text") or "")
        meta = u.get("metadata") or {}
        item = {
            "unit_id": str(u.get("content_unit_id") or f"unit_{i}"),
            "unit_number": int(u.get("unit_number") or i),
            "unit_type": u.get("unit_type") or "content_unit",
            "title": u.get("title") or f"Section {i}",
            "preview": text[:800],
            "char_count": len(text),
        }
        # Surface ebook page tagging flags so Source Library can badge / retry.
        if meta.get("tagging_status"):
            item["tagging_status"] = meta.get("tagging_status")
        if meta.get("tagging_error"):
            item["tagging_error"] = meta.get("tagging_error")
        if meta.get("page_number"):
            item["page_number"] = meta.get("page_number")
        if meta.get("pdf_page") is not None:
            item["pdf_page"] = meta.get("pdf_page")
        out.append(item)
    return out


def build_view_manifest(content_doc: Dict[str, Any]) -> Dict[str, Any]:
    text = str(content_doc.get("reading_content") or "")
    pages = _split_text_for_view(text)
    units = _view_units_for_content(content_doc)
    from services.ebook_page_tagger import tagging_counts
    counts = tagging_counts(content_doc.get("content_units") or [])
    return {
        "schema_version": "source_view_manifest_v1",
        "job_id": content_doc.get("job_id"),
        "document_title": content_doc.get("document_title"),
        "source_file_name": content_doc.get("source_file_name"),
        "source_file_type": content_doc.get("source_file_type"),
        "document_type": content_doc.get("document_type"),
        "purpose": content_doc.get("purpose"),
        "status": content_doc.get("status"),
        "total_units": int(content_doc.get("total_units") or len(content_doc.get("content_units") or [])),
        "total_characters": len(text),
        "total_view_pages": len(pages),
        "content_too_large_for_single_view": len(text) > LARGE_VIEW_CHAR_THRESHOLD or len(pages) > 8,
        "preview": str(content_doc.get("preview") or text[:12000]),
        "units": units,
        "tagging_failed_count": int(
            content_doc.get("tagging_failed_count")
            if content_doc.get("tagging_failed_count") is not None
            else counts["tagging_failed_count"]
        ),
        "tagging_pending_count": int(
            content_doc.get("tagging_pending_count")
            if content_doc.get("tagging_pending_count") is not None
            else counts["tagging_pending_count"]
        ),
    }


def content_pages_response(content_doc: Dict[str, Any], page: int = 1, page_size: int = 5) -> Dict[str, Any]:
    pages = _split_text_for_view(str(content_doc.get("reading_content") or ""))
    page = max(1, int(page or 1))
    page_size = max(1, min(int(page_size or 5), 20))
    start = (page - 1) * page_size
    end = start + page_size
    return {
        "job_id": content_doc.get("job_id"),
        "page": page,
        "page_size": page_size,
        "total_pages": len(pages),
        "pages": pages[start:end],
        "has_next": end < len(pages),
        "has_previous": start > 0,
    }


def _page_units_for_content(content_doc: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Fallback view sections when extraction produced one giant unit.

    Big textbook/PDF uploads often arrive as a single content unit. For CAS View,
    expose synthetic page/part sections so the user can switch sections without
    loading the whole file into one textarea. Retrieval still uses the full content.
    """
    pages = _split_text_for_view(str(content_doc.get("reading_content") or ""))
    out: List[Dict[str, Any]] = []
    for i, pg in enumerate(pages, start=1):
        text = str(pg.get("text") or "")
        out.append({
            "unit_id": f"view_page_{i}",
            "unit_number": i,
            "unit_type": "view_section",
            "title": pg.get("title") or f"Section {i}",
            "preview": text[:800],
            "char_count": len(text),
        })
    return out


def content_units_response(content_doc: Dict[str, Any]) -> Dict[str, Any]:
    units = _view_units_for_content(content_doc)
    text = str(content_doc.get("reading_content") or "")
    # If the extractor only produced a single giant unit, show page/part sections
    # in the UI instead of one huge section.
    if len(units) <= 1 and len(text) > PAGE_VIEW_CHARS:
        units = _page_units_for_content(content_doc)
    from services.ebook_page_tagger import tagging_counts
    counts = tagging_counts(content_doc.get("content_units") or [])
    return {
        "job_id": content_doc.get("job_id"),
        "units": units,
        "total_units": len(units),
        "tagging_failed_count": int(
            content_doc.get("tagging_failed_count")
            if content_doc.get("tagging_failed_count") is not None
            else counts["tagging_failed_count"]
        ),
        "tagging_pending_count": int(
            content_doc.get("tagging_pending_count")
            if content_doc.get("tagging_pending_count") is not None
            else counts["tagging_pending_count"]
        ),
    }


def content_unit_detail_response(content_doc: Dict[str, Any], unit_id: str) -> Dict[str, Any]:
    wanted = str(unit_id)
    if wanted.startswith("view_page_"):
        try:
            page_num = int(wanted.replace("view_page_", ""))
        except ValueError:
            page_num = 1
        pages = _split_text_for_view(str(content_doc.get("reading_content") or ""))
        if 1 <= page_num <= len(pages):
            pg = pages[page_num - 1]
            return {
                "job_id": content_doc.get("job_id"),
                "unit": {
                    "unit_id": wanted,
                    "unit_number": page_num,
                    "unit_type": "view_section",
                    "title": pg.get("title") or f"Section {page_num}",
                    "text": pg.get("text") or "",
                },
            }
    units = content_doc.get("content_units") or []
    for i, u in enumerate(units, start=1):
        uid = str(u.get("content_unit_id") or f"unit_{i}")
        if uid == wanted or str(u.get("unit_number") or "") == wanted:
            return {"job_id": content_doc.get("job_id"), "unit": {**u, "unit_id": uid}}
    raise FileNotFoundError(f"Content unit '{unit_id}' not found")


def content_search_response(content_doc: Dict[str, Any], q: str, limit: int = 20) -> Dict[str, Any]:
    query = str(q or "").strip().lower()
    if not query:
        return {"job_id": content_doc.get("job_id"), "query": q, "matches": [], "total": 0}
    matches: List[Dict[str, Any]] = []
    # Search pages to give user meaningful locations.
    for page in _split_text_for_view(str(content_doc.get("reading_content") or "")):
        text = page.get("text") or ""
        idx = text.lower().find(query)
        if idx >= 0:
            start = max(0, idx - 180)
            end = min(len(text), idx + len(query) + 220)
            matches.append({
                "location_type": "page",
                "page_number": page.get("page_number"),
                "title": page.get("title"),
                "snippet": text[start:end].strip(),
            })
            if len(matches) >= limit:
                break
    return {"job_id": content_doc.get("job_id"), "query": q, "matches": matches, "total": len(matches)}
