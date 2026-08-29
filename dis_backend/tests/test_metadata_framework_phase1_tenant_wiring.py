"""Prove migrated consumers use registry_for_tenant, not DEFAULT_REGISTRY alone.

A synthetic field present only in tenant metadata_framework (never in
DEFAULT_REGISTRY) must flow through index, retrieval, filter_options, and
cas_list. If any consumer reverts to DEFAULT_REGISTRY, this fails.
"""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.context_retrieval import ContextRetrievalService
from services.metadata_framework.registry import DEFAULT_REGISTRY, registry_for_tenant
from services.source_library import compact_source_record, source_filter_options

_SYNTH_FIELD = "test_metadata_field"

_TENANT_FRAMEWORK = {
    "fields": {
        _SYNTH_FIELD: {
            "type": "string",
            "tier": "structural",
            "promote": ["index", "retrieval", "filter_options", "cas_list"],
        }
    }
}


def _tenant_cfg():
    return SimpleNamespace(
        tenant_id="wiring_demo",
        metadata_framework=_TENANT_FRAMEWORK,
    )


def test_synth_field_absent_from_default_registry():
    assert DEFAULT_REGISTRY.get(_SYNTH_FIELD) is None


def test_registry_for_tenant_discovers_synth_field():
    reg = registry_for_tenant(_tenant_cfg())
    spec = reg.get(_SYNTH_FIELD)
    assert spec is not None
    assert set(spec.promote) == {"index", "retrieval", "filter_options", "cas_list"}


def test_four_consumers_use_tenant_registry_not_default():
    """End-to-end: configured field reaches all four migrated destinations."""
    cfg = _tenant_cfg()
    assert DEFAULT_REGISTRY.get(_SYNTH_FIELD) is None

    payload = {
        "job_id": "job-wiring",
        "tenant_id": "wiring_demo",
        "client_id": "wiring_demo",
        "created_at": "2026-01-01T00:00:00",
        "source_file": {"name": "x.pdf", "type": "pdf", "size_bytes": 1},
        "metadata": {
            "title": "Wiring Doc",
            "document_type": "other",
            "purpose": "general_reference",
            "visibility": "instructor",
            _SYNTH_FIELD: "synth-value",
        },
        "content_units": [],
    }

    with patch("services.source_library.datetime") as dt:
        dt.utcnow.return_value.isoformat.return_value = "2026-08-29T12:00:00"
        # index
        rec = compact_source_record(payload, "p", "c", tenant_cfg=cfg)
    assert _SYNTH_FIELD in rec
    assert rec[_SYNTH_FIELD] == "synth-value"
    # Without tenant_cfg the field must NOT appear (DEFAULT_REGISTRY path).
    with patch("services.source_library.datetime") as dt:
        dt.utcnow.return_value.isoformat.return_value = "2026-08-29T12:00:00"
        default_rec = compact_source_record(payload, "p", "c")
    assert _SYNTH_FIELD not in default_rec

    # retrieval
    svc = ContextRetrievalService.__new__(ContextRetrievalService)
    svc.tenant_cfg = cfg
    meta = svc._metadata_from_record(rec)
    assert meta.get(_SYNTH_FIELD) == "synth-value"

    # filter_options (module function used by list_sources)
    opts = source_filter_options([rec], tenant_cfg=cfg)
    assert _SYNTH_FIELD in opts
    assert opts[_SYNTH_FIELD] == ["synth-value"]
    assert _SYNTH_FIELD not in source_filter_options([rec])

    # cas_list via list_sources taxonomy projection
    def _fake_index(_tenant, _client):
        return {"sources": [rec]}

    with patch("services.context_retrieval.read_source_index", _fake_index), \
         patch.object(ContextRetrievalService, "_attach_indexed_units", lambda self, page: None):
        listed = svc.list_sources("wiring_demo", filters={}, limit=10, offset=0)
    assert listed["sources"][0].get(_SYNTH_FIELD) == "synth-value"
    assert _SYNTH_FIELD in listed["filter_options"]
    assert listed["filter_options"][_SYNTH_FIELD] == ["synth-value"]
