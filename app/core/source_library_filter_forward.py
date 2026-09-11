"""Phase 3 CAS: Source Library list forwards registry-eligible dynamic query keys.

Named params stay intact. Extra query keys (e.g. test_metadata_field) must reach
DIS params so DIS can authorize them against the tenant registry.
"""
from __future__ import annotations

from typing import Any, Dict, Mapping, MutableMapping


# CAS-local control params for GET /source-library/documents — not DIS filters.
CAS_DOCUMENTS_CONTROL_PARAMS = frozenset({
    "client_id",
    "project_id",
    "course_id",
    "all_courses",
    "limit",
    "offset",
})


def build_cas_documents_library_params(
    *,
    purpose: str = "",
    document_type: str = "",
    visibility: str = "",
    status: str = "",
    search: str = "",
    block: str = "",
    day: str = "",
    chapter: str = "",
    module_name: str = "",
    learning_objective: str = "",
    course_name: str = "",
    course_id: str = "",
    limit: int = 200,
    offset: int = 0,
    query_params: Mapping[str, Any] | None = None,
) -> Dict[str, Any]:
    """Mirror list_source_documents params construction + dynamic forwarding."""
    params: Dict[str, Any] = {
        "purpose": purpose,
        "document_type": document_type,
        "visibility": visibility,
        "status": status,
        "search": search,
        "block": block,
        "day": day,
        "chapter": chapter,
        "module_name": module_name,
        "learning_objective": learning_objective,
        "course_name": course_name,
        "course_id": course_id,
        "limit": limit,
        "offset": offset,
    }
    merge_cas_forwarded_query_params(params, query_params or {})
    return params


def merge_cas_forwarded_query_params(
    params: MutableMapping[str, Any],
    query_params: Mapping[str, Any],
) -> MutableMapping[str, Any]:
    """Forward non-control extras to DIS. Authorization stays on the DIS side."""
    items = (
        query_params.multi_items()
        if hasattr(query_params, "multi_items")
        else query_params.items()
    )
    for raw_key, value in items:
        key = str(raw_key or "").strip()
        if not key or key in CAS_DOCUMENTS_CONTROL_PARAMS:
            continue
        if value in (None, ""):
            continue
        # Named params already bound — do not override.
        if key in params:
            continue
        params[key] = value
    return params
