"""Source Library HTTP filter merging (Phase 3).

Named query parameters stay compatible. Extra query keys are accepted only when
``registry_for_tenant`` authorizes them as ``filter_options``-promoted fields
(storage key, filter_options_key, or alias). Unknown keys are ignored.
"""
from __future__ import annotations

from typing import Any, Dict, Iterable, Mapping, MutableMapping, Optional

from services.metadata_framework.adapters import api_param_for_ui_key
from services.metadata_framework.registry import (
    PROMOTE_FILTER_OPTIONS,
    registry_for_tenant,
)

# Control / pagination / auth-adjacent query keys for documents/library.
# Built from the actual FastAPI signature — not a guessed denylist of metadata.
DOCUMENTS_LIBRARY_CONTROL_PARAMS = frozenset({
    "purpose",
    "document_type",
    "visibility",
    "status",
    "search",
    "block",
    "day",
    "chapter",
    "module_name",
    "learning_objective",
    "course_name",
    "course_id",
    "limit",
    "offset",
})


def resolve_filter_options_storage_key(
    query_key: str,
    tenant_cfg: Any = None,
) -> Optional[str]:
    """Map a query key to a filter_options storage key, or None if unauthorized."""
    k = str(query_key or "").strip()
    if not k:
        return None
    registry = registry_for_tenant(tenant_cfg)
    storage = api_param_for_ui_key(k, registry)
    spec = registry.get(storage)
    if spec is None or not spec.promotes(PROMOTE_FILTER_OPTIONS):
        return None
    return storage


def build_documents_library_filters(
    *,
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
    query_params: Optional[Mapping[str, Any]] = None,
    tenant_cfg: Any = None,
) -> Dict[str, Any]:
    """Build the filters dict for ``documents_library``.

    Named parameters always populate the dict (Phase 0–2 contract). Additional
    keys from ``query_params`` are merged only when registry-authorized.
    """
    filters: Dict[str, Any] = {
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
        "metadata_filters": {},
    }
    merge_registry_authorized_query_filters(
        filters,
        query_params or {},
        tenant_cfg=tenant_cfg,
        reserved=DOCUMENTS_LIBRARY_CONTROL_PARAMS,
    )
    return filters


def merge_registry_authorized_query_filters(
    filters: MutableMapping[str, Any],
    query_params: Mapping[str, Any],
    *,
    tenant_cfg: Any = None,
    reserved: Iterable[str] = (),
) -> MutableMapping[str, Any]:
    """Merge registry-authorized extras into ``filters``. Unknown keys ignored."""
    reserved_set = frozenset(str(x) for x in reserved)
    items: Iterable[tuple[str, Any]]
    if hasattr(query_params, "multi_items"):
        items = query_params.multi_items()  # type: ignore[assignment]
    else:
        items = query_params.items()

    for raw_key, value in items:
        key = str(raw_key or "").strip()
        if not key or key in reserved_set:
            continue
        if value in (None, ""):
            continue
        # Never override an already-bound named / control filter.
        if key in filters and filters.get(key) not in (None, "", {}, []):
            continue
        storage = resolve_filter_options_storage_key(key, tenant_cfg)
        if storage is None:
            continue
        if storage in filters and filters.get(storage) not in (None, "", {}, []):
            continue
        filters[storage] = value
    return filters
