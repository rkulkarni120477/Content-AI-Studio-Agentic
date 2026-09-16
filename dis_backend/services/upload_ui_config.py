"""Upload metadata UI configuration (Phase 5).

Resolves ``retrieval.source_ui.upload`` against ``metadata_schemas`` for ui-config.
Presentation only — does not touch Field Registry or validation semantics.
"""
from __future__ import annotations

from typing import Any, Dict, List, Mapping, Optional, Sequence

from config.settings import MetadataField, MetadataSchema

# DECISION-002: never offer these as upload inputs.
SYSTEM_DERIVED_FIELD_KEYS = frozenset({
    "file_sha256",
    "content_hash",
    "client_id",
    "source_file_name",
    "tenant_id",
    "job_id",
    "namespace",
    "raw_storage_url",
    "s3_key",
    "status",
})

SUPPORTED_CONTROLS = frozenset({"text", "select", "textarea"})


def _schema_field_map(schema: MetadataSchema | None) -> Dict[str, MetadataField]:
    if schema is None:
        return {}
    out: Dict[str, MetadataField] = {}
    for field in (schema.required_fields or []) + (schema.optional_fields or []):
        if field.name:
            out[field.name] = field
    return out


def _document_type_options(schema_map: Mapping[str, MetadataField]) -> List[str]:
    field = schema_map.get("document_type")
    if not field:
        return []
    values = list(getattr(field, "values", None) or [])
    if values:
        return values
    return []


def _resolve_control(
    control: Any,
    field_key: str,
    options: List[str],
) -> str:
    requested = str(control or "").strip().lower()
    if requested in SUPPORTED_CONTROLS:
        if requested == "select" and not options:
            return "text"
        return requested
    if field_key == "document_type" and options:
        return "select"
    return "text"


def _resolve_upload_field(
    entry: Mapping[str, Any],
    schema_map: Mapping[str, MetadataField],
    *,
    order: int,
) -> Dict[str, Any]:
    key = str(entry.get("key") or "").strip()
    schema_field = schema_map.get(key)
    options: List[str] = []
    control_hint = str(entry.get("control") or "").strip().lower()
    if schema_field and list(getattr(schema_field, "values", None) or []):
        if key == "document_type" or control_hint == "select":
            options = list(schema_field.values or [])

    control = _resolve_control(entry.get("control"), key, options)

    resolved: Dict[str, Any] = {
        "key": key,
        "label": str(entry.get("label") or key.replace("_", " ").title()),
        "control": control,
        "order": int(entry.get("order", order)),
    }
    if entry.get("required") is not None:
        resolved["required_ui"] = bool(entry.get("required"))
    if entry.get("placeholder"):
        resolved["placeholder"] = str(entry.get("placeholder"))
    if control == "select" and options:
        resolved["options"] = options
    return resolved


def resolve_document_type_upload(schema: MetadataSchema | None) -> Dict[str, Any]:
    """Structural document_type control derived from metadata_schemas when configured."""
    schema_map = _schema_field_map(schema)
    options = _document_type_options(schema_map)
    control = "select" if options else "text"
    out: Dict[str, Any] = {
        "key": "document_type",
        "label": "Document Type",
        "control": control,
    }
    if control == "select":
        out["options"] = options
    else:
        out["placeholder"] = "Optional, auto-detect if blank"
    return out


def build_upload_metadata_ui(
    source_ui: Mapping[str, Any] | None,
    schema: MetadataSchema | None,
) -> Dict[str, Any]:
    """Resolve upload presentation config for ui-config consumers."""
    ui = dict(source_ui or {})
    upload_raw = dict(ui.get("upload") or {})
    raw_fields: Sequence[Any] = upload_raw.get("fields") or []

    schema_map = _schema_field_map(schema)
    resolved_fields: List[Dict[str, Any]] = []
    seen: set[str] = set()
    document_type_from_config: Optional[Dict[str, Any]] = None

    for order, entry in enumerate(raw_fields):
        if not isinstance(entry, Mapping):
            continue
        key = str(entry.get("key") or "").strip()
        if not key or key in SYSTEM_DERIVED_FIELD_KEYS or key in seen:
            continue
        seen.add(key)
        resolved = _resolve_upload_field(entry, schema_map, order=order)
        if key == "document_type":
            document_type_from_config = resolved
            continue
        resolved_fields.append(resolved)

    resolved_fields.sort(key=lambda f: (f.get("order", 0), f.get("key", "")))

    document_type = document_type_from_config or resolve_document_type_upload(schema)

    return {
        "fields": resolved_fields,
        "document_type": document_type,
        "configured": bool(raw_fields),
    }
