"""Phase 3: registry-authorized dynamic Source Library HTTP filters.

Tenant-only filter_options fields must reach _source_matches via the filters
dict built for documents_library. Unknown keys are ignored. Tenant B without
the field must not accept Tenant A's filter key.
"""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.context_retrieval import ContextRetrievalService
from services.metadata_framework.filter_query import (
    build_documents_library_filters,
    resolve_filter_options_storage_key,
)
from services.metadata_framework.registry import DEFAULT_REGISTRY, registry_for_tenant
from services.source_library import source_filter_options

_SYNTH_FIELD = "test_metadata_field"

_TENANT_FRAMEWORK = {
    "fields": {
        _SYNTH_FIELD: {
            "type": "string",
            "tier": "structural",
            "promote": ["filter_options"],
        }
    }
}


def _tenant_a():
    return SimpleNamespace(tenant_id="tenant_a", metadata_framework=_TENANT_FRAMEWORK)


def _tenant_b():
    return SimpleNamespace(tenant_id="tenant_b", metadata_framework=None)


def _svc(tenant_cfg):
    svc = ContextRetrievalService.__new__(ContextRetrievalService)
    svc.tenant_cfg = tenant_cfg
    return svc


def test_synth_absent_from_default_registry():
    assert DEFAULT_REGISTRY.get(_SYNTH_FIELD) is None


def test_tenant_a_filter_options_includes_synth():
    cfg = _tenant_a()
    assert registry_for_tenant(cfg).get(_SYNTH_FIELD) is not None
    rec = {_SYNTH_FIELD: "ABC", "document_type": "other"}
    opts = source_filter_options([rec], tenant_cfg=cfg)
    assert _SYNTH_FIELD in opts
    assert "ABC" in opts[_SYNTH_FIELD]


def test_tenant_b_filter_options_excludes_synth():
    rec = {_SYNTH_FIELD: "ABC", "document_type": "other"}
    opts = source_filter_options([rec], tenant_cfg=_tenant_b())
    assert _SYNTH_FIELD not in opts
    opts_default = source_filter_options([rec])
    assert _SYNTH_FIELD not in opts_default


def test_resolve_authorizes_tenant_synth_storage_key():
    assert resolve_filter_options_storage_key(_SYNTH_FIELD, _tenant_a()) == _SYNTH_FIELD
    assert resolve_filter_options_storage_key(_SYNTH_FIELD, _tenant_b()) is None
    assert resolve_filter_options_storage_key(_SYNTH_FIELD, None) is None


def test_resolve_authorizes_default_named_filter_keys():
    for key in ("module_name", "learning_objective", "block", "day", "chapter", "course_name"):
        assert resolve_filter_options_storage_key(key, None) == key
    # UI alias module → module_name
    assert resolve_filter_options_storage_key("module", None) == "module_name"
    # Registry alias day_number → day
    assert resolve_filter_options_storage_key("day_number", None) == "day"


def test_unknown_query_key_ignored():
    """Option A: unknown/unregistered filters are ignored (pre-Phase-2 contract)."""
    filters = build_documents_library_filters(
        query_params={"unknown_random_filter": "x", "tenant_id": "evil", "limit": "1"},
        tenant_cfg=_tenant_a(),
    )
    assert "unknown_random_filter" not in filters
    assert "tenant_id" not in filters
    assert filters["metadata_filters"] == {}


def test_tenant_a_merges_synth_from_query_params():
    filters = build_documents_library_filters(
        module_name="Section 2",
        query_params={_SYNTH_FIELD: "ABC", "unknown_random_filter": "nope"},
        tenant_cfg=_tenant_a(),
    )
    assert filters["module_name"] == "Section 2"
    assert filters[_SYNTH_FIELD] == "ABC"
    assert "unknown_random_filter" not in filters


def test_tenant_b_ignores_synth_query_param():
    filters = build_documents_library_filters(
        query_params={_SYNTH_FIELD: "ABC"},
        tenant_cfg=_tenant_b(),
    )
    assert _SYNTH_FIELD not in filters


def test_named_param_not_overridden_by_duplicate_query_key():
    filters = build_documents_library_filters(
        block="Block 9",
        query_params={"block": "Block 1"},
        tenant_cfg=None,
    )
    assert filters["block"] == "Block 9"


def test_empty_metadata_framework_falls_back_to_default_authorization():
    cfg = SimpleNamespace(tenant_id="x", metadata_framework={})
    filters = build_documents_library_filters(
        module_name="Section 2",
        query_params={_SYNTH_FIELD: "ABC"},
        tenant_cfg=cfg,
    )
    assert filters["module_name"] == "Section 2"
    assert _SYNTH_FIELD not in filters


def test_http_filters_to_source_matches_e2e():
    """tenant config → filter builder → _source_matches match / non-match."""
    cfg = _tenant_a()
    svc = _svc(cfg)
    src = {_SYNTH_FIELD: "ABC", "document_type": "other", "module_name": "Section 2"}

    match_filters = build_documents_library_filters(
        query_params={_SYNTH_FIELD: "ABC"},
        tenant_cfg=cfg,
    )
    miss_filters = build_documents_library_filters(
        query_params={_SYNTH_FIELD: "XYZ"},
        tenant_cfg=cfg,
    )
    assert svc._source_matches(src, match_filters) is True
    assert svc._source_matches(src, miss_filters) is False

    # Tenant B cannot filter on synth even with the same query string.
    b_filters = build_documents_library_filters(
        query_params={_SYNTH_FIELD: "ABC"},
        tenant_cfg=_tenant_b(),
    )
    assert _SYNTH_FIELD not in b_filters
    # Without the filter key, matching does not require the field (ignored).
    assert svc._source_matches(src, b_filters) is True
