"""Phase 2: tenant-only filter_options fields drive _source_matches.

A synthetic field present only in tenant metadata_framework (never in
DEFAULT_REGISTRY) must be matchable by _source_matches when promoted to
filter_options. If matching reverts to a hardcoded list / DEFAULT_REGISTRY,
these fail.
"""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.context_retrieval import ContextRetrievalService
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


def _tenant_cfg(framework=None):
    return SimpleNamespace(
        tenant_id="phase2_filter_demo",
        metadata_framework=framework if framework is not None else _TENANT_FRAMEWORK,
    )


def _svc(tenant_cfg=None):
    svc = ContextRetrievalService.__new__(ContextRetrievalService)
    svc.tenant_cfg = tenant_cfg
    return svc


def test_synth_field_absent_from_default_registry():
    assert DEFAULT_REGISTRY.get(_SYNTH_FIELD) is None


def test_tenant_registry_promotes_synth_to_filter_options_only():
    reg = registry_for_tenant(_tenant_cfg())
    spec = reg.get(_SYNTH_FIELD)
    assert spec is not None
    assert spec.promotes("filter_options")
    assert not spec.promotes("index")
    assert not spec.promotes("retrieval")
    assert not spec.promotes("cas_list")


def test_source_matches_uses_tenant_filter_options_field():
    """Tenant config → registry → filterable field → _source_matches match/miss."""
    cfg = _tenant_cfg()
    assert DEFAULT_REGISTRY.get(_SYNTH_FIELD) is None

    svc = _svc(cfg)
    match = {_SYNTH_FIELD: "synth-value", "document_type": "other"}
    other = {_SYNTH_FIELD: "other-value", "document_type": "other"}
    missing = {"document_type": "other"}

    assert svc._source_matches(match, {_SYNTH_FIELD: "synth-value"}) is True
    assert svc._source_matches(match, {_SYNTH_FIELD: "SYNTH-VALUE"}) is True
    assert svc._source_matches(other, {_SYNTH_FIELD: "synth-value"}) is False
    assert svc._source_matches(missing, {_SYNTH_FIELD: "synth-value"}) is False


def test_default_registry_ignores_unknown_synth_filter_key():
    """Without tenant YAML, unknown filter keys remain ignored (pre-Phase-2 contract)."""
    svc = _svc(None)
    assert DEFAULT_REGISTRY.get(_SYNTH_FIELD) is None
    src = {"document_type": "other"}  # no synth field on record
    # Filter key not in DEFAULT_REGISTRY filter_options → ignored → still matches
    assert svc._source_matches(src, {_SYNTH_FIELD: "synth-value"}) is True


def test_empty_metadata_framework_falls_back_to_default_matching():
    svc = _svc(_tenant_cfg(framework={}))
    src = {
        "module_name": "Section 2",
        "document_type": "syllabus",
        "block": "Block 9",
        "day": "Day 1",
        "chapter": "Ch 1",
        "course_name": "Course A",
        "status": "processed",
        "source_file_type": "pdf",
        "visibility": "instructor",
        "lesson_name": "L1",
    }
    assert svc._source_matches(src, {"module_name": "Section 2"}) is True
    assert svc._source_matches(src, {"module_name": "Section 9"}) is False
    # Synth field still ignored under empty framework
    assert svc._source_matches(src, {_SYNTH_FIELD: "x"}) is True


def test_absent_metadata_framework_falls_back_to_default_matching():
    svc = _svc(SimpleNamespace(tenant_id="x", metadata_framework=None))
    src = {"module_name": "Section 2", "document_type": "syllabus"}
    assert svc._source_matches(src, {"module_name": "Section 2"}) is True
    assert svc._source_matches(src, {"module_name": "Nope"}) is False


def test_filter_options_includes_tenant_synth_field():
    cfg = _tenant_cfg()
    records = [{_SYNTH_FIELD: "synth-value", "module_name": "M1"}]
    opts = source_filter_options(records, tenant_cfg=cfg)
    assert _SYNTH_FIELD in opts
    assert opts[_SYNTH_FIELD] == ["synth-value"]
    assert _SYNTH_FIELD not in source_filter_options(records)


def test_learning_objective_top_level_and_metadata_filters_agree():
    """Both API paths (top-level after Phase 2, metadata_filters historically) match."""
    svc = _svc(None)
    src = {"learning_objective": "LO-1", "document_type": "other"}
    assert svc._source_matches(src, {"learning_objective": "LO-1"}) is True
    assert svc._source_matches(src, {"metadata_filters": {"learning_objective": "LO-1"}}) is True
    assert svc._source_matches(src, {"learning_objective": "LO-2"}) is False
    assert svc._source_matches(src, {"metadata_filters": {"learning_objective": "LO-2"}}) is False
