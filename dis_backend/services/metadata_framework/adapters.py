"""Compatibility adapters for Field Registry consumers.

Preserves existing rename contracts without changing AIM transforms:
  - module (UI / filter_options) ↔ module_name (storage)
  - blocks / days (filter_options plurals) ↔ block / day
  - block_id fallback to block on index write
  - day_number / quiz_number unit-metadata promote
"""
from __future__ import annotations

from typing import Any, Callable, Dict, Mapping

from services.metadata_framework.registry import (
    EXTRACT_BOOL_META,
    EXTRACT_DOC_TYPE_ALIAS,
    EXTRACT_META_OR_BLOCK,
    EXTRACT_META_OR_EMPTY,
    EXTRACT_PROMOTE_EMPTY,
    EXTRACT_PROMOTE_NONE,
    EXTRACT_RAW_META,
    PROMOTE_CAS_LIST,
    PROMOTE_INDEX,
    PROMOTE_RETRIEVAL,
    DEFAULT_REGISTRY,
    FieldRegistry,
    FieldSpec,
)


def storage_key(spec: FieldSpec) -> str:
    """Canonical key written on the compact source record."""
    return spec.key


def filter_options_response_key(spec: FieldSpec) -> str:
    return spec.filter_options_key or spec.key


def api_param_for_ui_key(ui_key: str, registry: FieldRegistry = DEFAULT_REGISTRY) -> str:
    """Map a UI / YAML taxonomy key onto the compact-record / API storage key.

    Mirrors frontend TAXONOMY_API_KEY_MAP: ``module`` → ``module_name``.
    """
    k = str(ui_key or "").strip()
    if not k:
        return k
    for spec in registry.all():
        if spec.key == k:
            return spec.key
        if k == (spec.filter_options_key or ""):
            return spec.key
        if k in spec.aliases:
            return spec.key
    return k


def extract_index_value(
    spec: FieldSpec,
    meta: Mapping[str, Any],
    promote_fn: Callable[[str, Any], Any],
) -> Any:
    """Extract one index-promoted field value (mirrors compact_source_record)."""
    key = spec.key
    mode = spec.extract
    if mode == EXTRACT_META_OR_EMPTY:
        return meta.get(key) or ""
    if mode == EXTRACT_META_OR_BLOCK:
        return meta.get(key) or meta.get("block") or ""
    if mode == EXTRACT_PROMOTE_EMPTY:
        return promote_fn(key, "")
    if mode == EXTRACT_PROMOTE_NONE:
        return promote_fn(key, None)
    if mode == EXTRACT_BOOL_META:
        return bool(meta.get(key))
    if mode == EXTRACT_RAW_META:
        return meta.get(key)
    # Index scaffolding owns operational writes; ignore other modes.
    return meta.get(key) or ""


def project_index_metadata(
    meta: Mapping[str, Any],
    promote_fn: Callable[[str, Any], Any],
    registry: FieldRegistry = DEFAULT_REGISTRY,
) -> Dict[str, Any]:
    """Build the metadata-filter portion of a compact source record."""
    out: Dict[str, Any] = {}
    for spec in registry.for_promote(PROMOTE_INDEX):
        out[storage_key(spec)] = extract_index_value(spec, meta, promote_fn)
    return out


def project_retrieval_metadata(
    rec: Mapping[str, Any],
    registry: FieldRegistry = DEFAULT_REGISTRY,
) -> Dict[str, Any]:
    """Rebuild doc-level metadata from a compact source-index record."""
    out: Dict[str, Any] = {}
    for spec in registry.for_promote(PROMOTE_RETRIEVAL):
        if spec.extract == EXTRACT_DOC_TYPE_ALIAS or spec.key == "doc_type":
            out[spec.key] = rec.get("document_type")
        else:
            out[spec.key] = rec.get(spec.key)
    return out


def project_filter_options_map(registry: FieldRegistry = DEFAULT_REGISTRY) -> Dict[str, str]:
    return registry.filter_options_map()


def project_cas_list_taxonomy(
    src: Mapping[str, Any],
    registry: FieldRegistry = DEFAULT_REGISTRY,
) -> Dict[str, Any]:
    """Taxonomy columns appended to the CAS Source Library list whitelist."""
    out: Dict[str, Any] = {}
    for spec in registry.for_promote(PROMOTE_CAS_LIST):
        out[spec.key] = src.get(spec.key, "")
    return out
