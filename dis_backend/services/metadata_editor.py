"""Phase 9 — document metadata editor (read / patch / revert).

Editable metadata lives in studio_payload/payload.json → metadata.
The AI baseline is extracted/metadata.json and must never be overwritten on save.
Compact source index is re-projected via write_source_content_and_index.
"""
from __future__ import annotations

import copy
import logging
from datetime import datetime
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from config.settings import TenantConfig, get_settings
from services.artifacts import ArtifactWriter
from services.source_library import (
    read_source_index,
    studio_payload_key,
    write_source_content_and_index,
)
from services.upload_ui_config import SYSTEM_DERIVED_FIELD_KEYS

logger = logging.getLogger(__name__)

KEYWORDS_MAX = 12

# Storage keys (snake_case in payload.metadata)
AI_STORAGE = {
    "title": "title",
    "author": "authors",
    "subject": "discipline",
    "language": "language",
    "description": "description",
    "keywords": "keywords",
}

TAXONOMY_STORAGE = {
    "subject_area": "taxonomy_subject_area",
    "domain": "taxonomy_domain",
    "subdomain": "taxonomy_subdomain",
    "blooms_level": "taxonomy_blooms_level",
    "skill_level": "taxonomy_skill_level",
    "learning_standards": "taxonomy_learning_standards",
    "skills_mapped": "taxonomy_skills_mapped",
}

RELATIONSHIP_TYPES = (
    "series_collection",
    "related_documents",
    "prerequisites",
    "cross_references",
)

# Fields restored by Revert to AI values (AI tab + taxonomy; not relationships).
REVERT_STORAGE_KEYS: Tuple[str, ...] = tuple(
    set(AI_STORAGE.values()) | set(TAXONOMY_STORAGE.values())
)

PROTECTED_METADATA_KEYS = frozenset(SYSTEM_DERIVED_FIELD_KEYS) | frozenset({
    "restricted",
    "access_level",
    "visibility",
    "document_type",
    "doc_type",
    "purpose",
    "course_id",
    "program_id",
    "tags",
    "relationships",
})


class MetadataEditorError(Exception):
    """Base metadata editor error with HTTP status hint."""

    status_code = 400

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


class DocumentNotFoundError(MetadataEditorError):
    status_code = 404


class UnauthorizedDocumentError(MetadataEditorError):
    status_code = 403


class InvalidRelationshipTargetError(MetadataEditorError):
    status_code = 400


def _resolve_payload_key(
    payload_key: str,
    *,
    writer: ArtifactWriter,
    tenant_cfg: TenantConfig,
    client_id: str,
    job_id: str,
) -> str:
    key = str(payload_key or "").replace("\\", "/").strip()
    if not key:
        return studio_payload_key(tenant_cfg, client_id, job_id)
    if not key.startswith("s3://"):
        return key
    parts = key.split("/", 3)
    if len(parts) < 4 or not parts[3]:
        return studio_payload_key(tenant_cfg, client_id, job_id)
    bucket = parts[2]
    if bucket != writer.processed_bucket:
        return studio_payload_key(tenant_cfg, client_id, job_id)
    object_key = parts[3].lstrip("/")
    bp = (writer.base_prefix or "").strip("/")
    if bp and object_key.startswith(bp + "/"):
        object_key = object_key[len(bp) + 1 :]
    return object_key or studio_payload_key(tenant_cfg, client_id, job_id)


def _find_source_record(tenant_cfg: TenantConfig, client_id: str, job_id: str) -> Dict[str, Any]:
    index = read_source_index(tenant_cfg, client_id)
    record = next(
        (r for r in index.get("sources", []) if str(r.get("job_id")) == str(job_id)),
        None,
    )
    if not record:
        raise DocumentNotFoundError(f"No source found for job_id '{job_id}'")
    if str(record.get("client_id") or client_id) != str(client_id):
        raise UnauthorizedDocumentError("Document does not belong to this tenant")
    return record


def _read_ai_baseline(writer: ArtifactWriter, tenant_cfg: TenantConfig, client_id: str, job_id: str) -> Dict[str, Any]:
    key = f"{_artifact_base(tenant_cfg, client_id, job_id)}/extracted/metadata.json"
    try:
        data = writer.read_json(key)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _artifact_base(tenant_cfg: TenantConfig, client_id: str, job_id: str) -> str:
    namespace = tenant_cfg.get_namespace(client_id)
    env = get_settings().environment
    return f"processed/{namespace}/{env}/{job_id}"


def _load_payload(
    tenant_cfg: TenantConfig,
    client_id: str,
    job_id: str,
    record: Optional[Dict[str, Any]] = None,
) -> Tuple[Dict[str, Any], str, Dict[str, Any]]:
    record = record or _find_source_record(tenant_cfg, client_id, job_id)
    writer = ArtifactWriter(tenant_cfg)
    p_key = _resolve_payload_key(
        str(record.get("payload_key") or ""),
        writer=writer,
        tenant_cfg=tenant_cfg,
        client_id=client_id,
        job_id=job_id,
    )
    try:
        payload = writer.read_json(p_key)
    except Exception as exc:
        raise DocumentNotFoundError(f"Studio payload not found for job_id '{job_id}': {exc}") from exc
    if not isinstance(payload, dict):
        raise MetadataEditorError("Invalid studio payload format")
    return payload, p_key, record


def _as_string(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, list):
        if not value:
            return ""
        return ", ".join(str(v) for v in value if v not in (None, ""))
    return str(value)


def _as_string_list(value: Any) -> List[str]:
    if value is None:
        return []
    if isinstance(value, str):
        parts = [p.strip() for p in value.replace("\n", ",").split(",")]
        return [p for p in parts if p]
    if isinstance(value, list):
        return [str(v).strip() for v in value if str(v).strip()]
    s = str(value).strip()
    return [s] if s else []


def _read_title(meta: Mapping[str, Any], record: Mapping[str, Any]) -> str:
    return _as_string(
        meta.get("title")
        or meta.get("product_title")
        or record.get("title")
        or record.get("source_file_name")
    )


def _read_author(meta: Mapping[str, Any]) -> str:
    authors = meta.get("authors")
    if isinstance(authors, list):
        return ", ".join(str(a) for a in authors if a)
    return _as_string(authors or meta.get("author"))


def _normalize_relationship_item(item: Any) -> Optional[Dict[str, str]]:
    if isinstance(item, str):
        label = item.strip()
        return {"job_id": "", "label": label} if label else None
    if isinstance(item, dict):
        job_id = str(item.get("job_id") or "").strip()
        label = str(item.get("label") or item.get("name") or "").strip()
        if not label and not job_id:
            return None
        return {"job_id": job_id, "label": label or job_id}
    return None


def _read_relationships(meta: Mapping[str, Any]) -> Dict[str, Any]:
    rel = meta.get("relationships") if isinstance(meta.get("relationships"), dict) else {}
    out: Dict[str, Any] = {}
    series = rel.get("series_collection") or meta.get("series_collection") or ""
    out["series_collection"] = _as_string(series)
    for key in ("related_documents", "prerequisites", "cross_references"):
        raw = rel.get(key)
        items: List[Dict[str, str]] = []
        if isinstance(raw, list):
            for entry in raw:
                norm = _normalize_relationship_item(entry)
                if norm:
                    items.append(norm)
        out[key] = items
    return out


def _document_summary(record: Mapping[str, Any], payload: Mapping[str, Any]) -> Dict[str, Any]:
    meta = payload.get("metadata") or {}
    source = payload.get("source_file") or {}
    size_bytes = record.get("size_bytes") or source.get("size_bytes") or meta.get("file_size_bytes")
    size_label = ""
    if size_bytes:
        try:
            n = int(size_bytes)
            if n >= 1_048_576:
                size_label = f"{n / 1_048_576:.1f} MB"
            elif n >= 1024:
                size_label = f"{n / 1024:.1f} KB"
            else:
                size_label = f"{n} B"
        except (TypeError, ValueError):
            size_label = str(size_bytes)
    return {
        "job_id": record.get("job_id"),
        "name": record.get("title") or record.get("source_file_name") or "",
        "source_file_name": record.get("source_file_name") or source.get("name") or "",
        "type": record.get("document_type") or meta.get("document_type") or "",
        "size": size_label,
        "size_bytes": size_bytes,
        "pages": record.get("page_count") or payload.get("page_count") or meta.get("page_count") or "",
        "purpose": record.get("purpose") or meta.get("purpose") or "",
    }


def _build_ui_response(
    *,
    record: Mapping[str, Any],
    payload: Mapping[str, Any],
    ai_baseline: Mapping[str, Any],
) -> Dict[str, Any]:
    meta = payload.get("metadata") or {}
    ai_meta = {
        "title": _read_title(meta, record),
        "author": _read_author(meta),
        "subject": _as_string(meta.get("discipline") or meta.get("subject")),
        "language": _as_string(meta.get("language") or "en-US"),
        "description": _as_string(meta.get("description")),
        "keywords": _as_string_list(meta.get("keywords") or meta.get("key_terms")),
    }
    taxonomy = {
        "subject_area": _as_string(meta.get(TAXONOMY_STORAGE["subject_area"])),
        "domain": _as_string(meta.get(TAXONOMY_STORAGE["domain"])),
        "subdomain": _as_string(meta.get(TAXONOMY_STORAGE["subdomain"])),
        "blooms_level": _as_string(meta.get(TAXONOMY_STORAGE["blooms_level"])),
        "skill_level": _as_string(meta.get(TAXONOMY_STORAGE["skill_level"])),
        "learning_standards": _as_string_list(meta.get(TAXONOMY_STORAGE["learning_standards"])),
        "skills_mapped": _as_string_list(meta.get(TAXONOMY_STORAGE["skills_mapped"])),
    }
    relationships = _read_relationships(meta)
    extracted_at = (
        meta.get("metadata_extracted_at")
        or payload.get("metadata_extracted_at")
        or ai_baseline.get("metadata_extracted_at")
        or ""
    )
    return {
        "job_id": record.get("job_id"),
        "document_summary": _document_summary(record, payload),
        "ai_metadata": ai_meta,
        "taxonomy_standards": taxonomy,
        "relationships": relationships,
        "ai_baseline_available": bool(ai_baseline),
        "provenance": {
            "ai_extracted_at": extracted_at,
            "taxonomy_source": meta.get("taxonomy_source") or "Cengage taxonomy",
        },
        "limits": {"keywords_max": KEYWORDS_MAX},
    }


def get_document_metadata(tenant_cfg: TenantConfig, client_id: str, job_id: str) -> Dict[str, Any]:
    record = _find_source_record(tenant_cfg, client_id, job_id)
    payload, _, _ = _load_payload(tenant_cfg, client_id, job_id, record)
    writer = ArtifactWriter(tenant_cfg)
    ai_baseline = _read_ai_baseline(writer, tenant_cfg, client_id, job_id)
    return _build_ui_response(record=record, payload=payload, ai_baseline=ai_baseline)


def _validate_relationship_targets(
    tenant_cfg: TenantConfig,
    client_id: str,
    relationships: Mapping[str, Any],
) -> None:
    index = read_source_index(tenant_cfg, client_id)
    known = {str(r.get("job_id")) for r in index.get("sources", [])}
    for rel_type in ("related_documents", "prerequisites", "cross_references"):
        for item in relationships.get(rel_type) or []:
            if not isinstance(item, dict):
                continue
            target = str(item.get("job_id") or "").strip()
            if target and target not in known:
                raise InvalidRelationshipTargetError(
                    f"Relationship target '{target}' is not accessible in this tenant"
                )


def _apply_patch_to_metadata(
    meta: Dict[str, Any],
    *,
    ai_metadata: Optional[Mapping[str, Any]] = None,
    taxonomy_standards: Optional[Mapping[str, Any]] = None,
    relationships: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    out = dict(meta)
    if ai_metadata is not None:
        if "title" in ai_metadata:
            title = str(ai_metadata.get("title") or "").strip()
            out["title"] = title
            if title and not out.get("product_title"):
                out["product_title"] = title
        if "author" in ai_metadata:
            author = str(ai_metadata.get("author") or "").strip()
            out["authors"] = _as_string_list(author) if author else []
        if "subject" in ai_metadata:
            out["discipline"] = str(ai_metadata.get("subject") or "").strip()
        if "language" in ai_metadata:
            out["language"] = str(ai_metadata.get("language") or "").strip()
        if "description" in ai_metadata:
            out["description"] = str(ai_metadata.get("description") or "").strip()
        if "keywords" in ai_metadata:
            kws = _as_string_list(ai_metadata.get("keywords"))
            if len(kws) > KEYWORDS_MAX:
                raise MetadataEditorError(f"Keywords exceed maximum of {KEYWORDS_MAX}")
            out["keywords"] = kws

    if taxonomy_standards is not None:
        for ui_key, storage_key in TAXONOMY_STORAGE.items():
            if ui_key not in taxonomy_standards:
                continue
            val = taxonomy_standards[ui_key]
            if ui_key in ("learning_standards", "skills_mapped"):
                out[storage_key] = _as_string_list(val)
            else:
                out[storage_key] = str(val or "").strip()

    if relationships is not None:
        rel_block: Dict[str, Any] = dict(out.get("relationships") or {})
        if "series_collection" in relationships:
            rel_block["series_collection"] = str(relationships.get("series_collection") or "").strip()
        for rel_type in ("related_documents", "prerequisites", "cross_references"):
            if rel_type in relationships:
                items = []
                for entry in relationships.get(rel_type) or []:
                    norm = _normalize_relationship_item(entry)
                    if norm:
                        items.append(norm)
                rel_block[rel_type] = items
        out["relationships"] = rel_block

    return out


def patch_document_metadata(
    tenant_cfg: TenantConfig,
    client_id: str,
    job_id: str,
    *,
    ai_metadata: Optional[Mapping[str, Any]] = None,
    taxonomy_standards: Optional[Mapping[str, Any]] = None,
    relationships: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    if not any(v is not None for v in (ai_metadata, taxonomy_standards, relationships)):
        raise MetadataEditorError("No metadata fields provided")

    record = _find_source_record(tenant_cfg, client_id, job_id)
    payload, p_key, _ = _load_payload(tenant_cfg, client_id, job_id, record)

    if relationships is not None:
        _validate_relationship_targets(tenant_cfg, client_id, relationships)

    meta = dict(payload.get("metadata") or {})
    updated = _apply_patch_to_metadata(
        meta,
        ai_metadata=ai_metadata,
        taxonomy_standards=taxonomy_standards,
        relationships=relationships,
    )
    payload = copy.deepcopy(payload)
    payload["metadata"] = updated
    payload["updated_at"] = datetime.utcnow().isoformat()

    writer = ArtifactWriter(tenant_cfg)
    writer.write_json(p_key, payload)
    write_source_content_and_index(tenant_cfg, client_id, payload, payload_key=p_key)

    ai_baseline = _read_ai_baseline(writer, tenant_cfg, client_id, job_id)
    fresh_record = _find_source_record(tenant_cfg, client_id, job_id)
    return _build_ui_response(record=fresh_record, payload=payload, ai_baseline=ai_baseline)


def _baseline_value_for_storage(ai_baseline: Mapping[str, Any], storage_key: str) -> Any:
    if storage_key == "authors":
        val = ai_baseline.get("authors") or ai_baseline.get("author")
        if isinstance(val, list):
            return val
        return _as_string_list(val)
    if storage_key == "discipline":
        return ai_baseline.get("discipline") or ai_baseline.get("subject") or ""
    if storage_key == "keywords":
        return _as_string_list(ai_baseline.get("keywords") or ai_baseline.get("key_terms"))
    if storage_key == "title":
        return ai_baseline.get("title") or ai_baseline.get("product_title") or ""
    if storage_key.startswith("taxonomy_"):
        return ai_baseline.get(storage_key, "")
    return ai_baseline.get(storage_key, "")


def revert_document_metadata_to_ai(
    tenant_cfg: TenantConfig,
    client_id: str,
    job_id: str,
) -> Dict[str, Any]:
    record = _find_source_record(tenant_cfg, client_id, job_id)
    payload, p_key, _ = _load_payload(tenant_cfg, client_id, job_id, record)
    writer = ArtifactWriter(tenant_cfg)
    ai_baseline = _read_ai_baseline(writer, tenant_cfg, client_id, job_id)
    if not ai_baseline:
        raise MetadataEditorError("AI baseline not available for this document")

    meta = dict(payload.get("metadata") or {})
    for storage_key in REVERT_STORAGE_KEYS:
        if storage_key in ("taxonomy_learning_standards", "taxonomy_skills_mapped", "keywords", "authors"):
            meta[storage_key] = _baseline_value_for_storage(ai_baseline, storage_key)
        else:
            val = _baseline_value_for_storage(ai_baseline, storage_key)
            if storage_key == "title" and val:
                meta["title"] = val
                if ai_baseline.get("product_title"):
                    meta["product_title"] = ai_baseline.get("product_title")
            else:
                meta[storage_key] = val

    payload = copy.deepcopy(payload)
    payload["metadata"] = meta
    payload["updated_at"] = datetime.utcnow().isoformat()
    writer.write_json(p_key, payload)
    write_source_content_and_index(tenant_cfg, client_id, payload, payload_key=p_key)

    fresh_record = _find_source_record(tenant_cfg, client_id, job_id)
    return _build_ui_response(record=fresh_record, payload=payload, ai_baseline=ai_baseline)
