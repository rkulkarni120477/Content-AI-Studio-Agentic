"""Phase 1 Field Registry unit tests (defaults, merge, acs_codes exclusion)."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.metadata_framework.adapters import (
    api_param_for_ui_key,
    project_filter_options_map,
    project_index_metadata,
    project_retrieval_metadata,
)
from services.metadata_framework.registry import (
    DEFAULT_REGISTRY,
    PROMOTE_CAS_LIST,
    PROMOTE_FILTER_OPTIONS,
    PROMOTE_INDEX,
    PROMOTE_RETRIEVAL,
    registry_from_config,
)
from tests.test_metadata_framework_phase1_characterization import (
    GOLDEN_CAS_LIST_TAXONOMY_KEYS,
    GOLDEN_COMPACT_METADATA_KEYS,
    GOLDEN_FILTER_OPTIONS_MAP,
    GOLDEN_RETRIEVAL_METADATA_KEYS,
)


def test_default_registry_index_keys_match_golden():
    assert DEFAULT_REGISTRY.keys_for(PROMOTE_INDEX) == GOLDEN_COMPACT_METADATA_KEYS


def test_default_registry_retrieval_keys_match_golden():
    assert DEFAULT_REGISTRY.keys_for(PROMOTE_RETRIEVAL) == GOLDEN_RETRIEVAL_METADATA_KEYS


def test_default_registry_filter_options_map_match_golden():
    assert project_filter_options_map(DEFAULT_REGISTRY) == GOLDEN_FILTER_OPTIONS_MAP


def test_default_registry_cas_list_keys_match_golden():
    assert DEFAULT_REGISTRY.keys_for(PROMOTE_CAS_LIST) == GOLDEN_CAS_LIST_TAXONOMY_KEYS


def test_empty_config_falls_back_to_default():
    assert registry_from_config(None) is DEFAULT_REGISTRY
    assert registry_from_config({}) is DEFAULT_REGISTRY
    assert registry_from_config({"version": "0.1"}) is DEFAULT_REGISTRY
    assert registry_from_config({"fields": {}}) is DEFAULT_REGISTRY


def test_yaml_fields_merge_does_not_admit_acs_codes():
    reg = registry_from_config({
        "fields": {
            "acs_codes": {"type": "list", "promote": ["index", "retrieval"]},
            "skill": {"type": "string", "promote": ["index", "cas_list"]},
        }
    })
    assert reg.get("acs_codes") is None
    assert "acs_codes" not in reg.keys_for(PROMOTE_INDEX)
    skill = reg.get("skill")
    assert skill is not None
    assert skill.promotes(PROMOTE_INDEX)
    assert skill.promotes(PROMOTE_CAS_LIST)


def test_module_alias_maps_to_module_name():
    assert api_param_for_ui_key("module") == "module_name"
    assert api_param_for_ui_key("module_name") == "module_name"
    assert api_param_for_ui_key("course_name") == "course_name"


def test_project_index_metadata_block_id_fallback():
    meta = {"block": "Block 3", "block_id": ""}
    out = project_index_metadata(meta, lambda k, d="": meta.get(k) or d)
    assert out["block_id"] == "Block 3"
    assert out["block"] == "Block 3"


def test_project_retrieval_doc_type_alias():
    meta = project_retrieval_metadata({"document_type": "syllabus", "title": "T"})
    assert meta["doc_type"] == "syllabus"
    assert meta["document_type"] == "syllabus"
