"""Report-level metadata schema validation (Phase 4).

Pure checker: ``MetadataSchema`` + metadata dict → findings.
Does not mutate metadata, fail the job, or touch the Field Registry.
"""
from __future__ import annotations

from typing import Any, Dict, List, Mapping, Sequence

from config.settings import MetadataField, MetadataSchema

# Same emptiness semantics as validation_report_agent payload checks.
_EMPTY = (None, "", [], {})

KIND_MISSING = "missing_required_field"
KIND_INVALID = "invalid_value"


def _is_empty(value: Any) -> bool:
    return value in _EMPTY


def _as_value_list(value: Any) -> List[Any]:
    """Normalize a metadata value for enum membership checks.

    List/tuple values are checked member-by-member (schema supports ``type: list``).
    Scalars are checked as a single member. Does not invent nested semantics.
    """
    if isinstance(value, (list, tuple)):
        return list(value)
    return [value]


def validate_metadata_against_schema(
    schema: MetadataSchema | None,
    metadata: Mapping[str, Any] | None,
) -> List[Dict[str, Any]]:
    """Return deterministic schema findings for *metadata*.

    Findings kinds:
    - ``missing_required_field`` — required field absent or empty
    - ``invalid_value`` — present value not in the field's configured ``values``

    Empty / absent schema → no findings.
    Optional fields never produce missing-field findings.
    Fields with empty/absent ``values`` skip enum checks.
    Does not enforce ``MetadataField.type`` or ``applies_to``.
    """
    if schema is None:
        return []
    meta: Mapping[str, Any] = metadata if isinstance(metadata, Mapping) else {}
    findings: List[Dict[str, Any]] = []
    seen_missing: set[str] = set()

    for field in schema.required_fields or []:
        name = getattr(field, "name", None)
        if not name:
            continue
        value = meta.get(name) if name in meta else None
        if name not in meta or _is_empty(value):
            findings.append({"kind": KIND_MISSING, "field": name})
            seen_missing.add(name)

    for field in _all_fields(schema):
        name = getattr(field, "name", None)
        if not name or name in seen_missing:
            continue
        allowed = list(getattr(field, "values", None) or [])
        if not allowed:
            continue
        if name not in meta:
            continue
        value = meta.get(name)
        if _is_empty(value):
            # Empty optional (or already-reported required) — no enum finding.
            continue
        allowed_set = set(allowed)
        for member in _as_value_list(value):
            if _is_empty(member):
                continue
            if member not in allowed_set:
                findings.append({
                    "kind": KIND_INVALID,
                    "field": name,
                    "value": member,
                    "allowed": list(allowed),
                })

    return findings


def _all_fields(schema: MetadataSchema) -> Sequence[MetadataField]:
    return list(schema.required_fields or []) + list(schema.optional_fields or [])


def findings_to_report_section(findings: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Small additive report block; does not alter top-level ``valid`` semantics."""
    return {"findings": list(findings)}
