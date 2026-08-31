"""Phase 6A: characterization and registry-driven retrieval gating (_passes_filters).

Locks business-rule gates and registry-authorized exact-match behaviour for
``registry.for_promote(PROMOTE_RETRIEVAL)`` minus documented exceptions.
"""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config.settings import get_tenant_config
from services.context_retrieval import ContextRetrievalService
from services.metadata_framework.registry import DEFAULT_REGISTRY, registry_for_tenant

_SYNTH_FIELD = "test_metadata_field"

_TENANT_RETRIEVAL_ONLY = {
    "fields": {
        _SYNTH_FIELD: {
            "type": "string",
            "tier": "structural",
            "promote": ["retrieval"],
        }
    }
}


def _payload(**meta):
    return {
        "job_id": "j1",
        "metadata": meta,
        "source_file": {"name": "doc.pdf"},
    }


@pytest.fixture
def aim_service():
    return ContextRetrievalService(get_tenant_config("aim"))


def _svc(tenant_cfg):
    svc = ContextRetrievalService.__new__(ContextRetrievalService)
    svc.tenant_cfg = tenant_cfg
    svc.role = "user"
    return svc


class TestEmptyAndExactMatch:
    def test_empty_filters_pass(self, aim_service):
        p = _payload(visibility="instructor", status="active", program_id="p1")
        assert aim_service._passes_filters({}, p, {}) is True

    def test_registry_exact_field_match(self, aim_service):
        p = _payload(visibility="instructor", status="active", program_id="p1")
        assert aim_service._passes_filters({}, p, {"program_id": "p1"}) is True

    def test_registry_exact_field_mismatch(self, aim_service):
        p = _payload(visibility="instructor", program_id="p1")
        assert aim_service._passes_filters({}, p, {"program_id": "other"}) is False

    def test_case_insensitive_exact_match(self, aim_service):
        p = _payload(visibility="Instructor", status="Active")
        assert aim_service._passes_filters({}, p, {"visibility": "instructor"}) is True
        assert aim_service._passes_filters({}, p, {"status": "active"}) is True

    def test_missing_metadata_fails_when_filter_present(self, aim_service):
        p = _payload(visibility="instructor")
        assert aim_service._passes_filters({}, p, {"program_id": "p1"}) is False


class TestModuleAlias:
    """Registry path resolves module → module_name via FieldSpec aliases."""

    def test_module_alias_matches_module_name_metadata(self, aim_service):
        p = _payload(module_name="Powerplant Systems")
        assert aim_service._passes_filters({}, p, {"module": "Powerplant Systems"}) is True

    def test_module_alias_mismatch(self, aim_service):
        p = _payload(module_name="Powerplant Systems")
        assert aim_service._passes_filters({}, p, {"module": "Airframe"}) is False

    def test_module_name_filter_key(self, aim_service):
        p = _payload(module_name="Mod A")
        assert aim_service._passes_filters({}, p, {"module_name": "Mod A"}) is True


class TestDayCalendarLogic:
    def test_day_number_match(self, aim_service):
        p = _payload(day_number=5, calendar_mapping_required=False)
        assert aim_service._passes_filters({}, p, {"day_number": 5}) is True

    def test_day_number_mismatch(self, aim_service):
        p = _payload(day_number=5, calendar_mapping_required=False)
        assert aim_service._passes_filters({}, p, {"day_number": 6}) is False

    def test_day_id_not_in_exact_registry_loop(self, aim_service):
        """day_id is handled by calendar logic, not plain registry equality."""
        p = _payload(day_id="B9D5", day_number=5, calendar_mapping_required=False)
        assert aim_service._passes_filters({}, p, {"day_id": "B9D5"}) is True


class TestBlockExactNotSameBlockString:
    """block_id / block_number use exact match; block uses business rule elsewhere."""

    def test_block_id_exact_match(self, aim_service):
        p = _payload(block_id="Block 9", block="Block 09")
        assert aim_service._passes_filters({}, p, {"block_id": "Block 9"}) is True

    def test_block_top_level_not_registry_exact_loop(self, aim_service):
        """block itself is excluded from registry exact loop (same_block lives in listing)."""
        p = _payload(block="Block 09")
        assert aim_service._passes_filters({}, p, {"block": "Block 9"}) is True


class TestPurposeGate:
    def test_purpose_mismatch_blocks(self, aim_service):
        p = _payload(document_type="other", purpose="general_reference")
        assert aim_service._passes_filters({}, p, {"purpose": "style"}) is False

    def test_purpose_wildcard_passes(self, aim_service):
        p = _payload(document_type="other", purpose="general_reference")
        assert aim_service._passes_filters({}, p, {"purpose": "all"}) is True

    def test_explicit_selection_skips_purpose(self, aim_service):
        p = _payload(document_type="other", purpose="general_reference")
        f = {"purpose": "style", "job_ids": ["j1"]}
        assert aim_service._passes_filters({}, p, f) is True


class TestCourseSentinel:
    def test_global_course_id_matches_any_course(self, aim_service):
        p = _payload(course_id="-1")
        assert aim_service._passes_filters({}, p, {"course_id": "101"}) is True

    def test_scoped_course_mismatch(self, aim_service):
        p = _payload(course_id="48")
        assert aim_service._passes_filters({}, p, {"course_id": "101"}) is False


class TestBooleanOperationalFields:
    def test_boolean_filter_match(self, aim_service):
        p = _payload(restricted=False, calendar_mapping_required=True)
        assert aim_service._passes_filters({}, p, {"restricted": False}) is True
        assert aim_service._passes_filters({}, p, {"calendar_mapping_required": True}) is True

    def test_boolean_filter_mismatch(self, aim_service):
        p = _payload(restricted=True)
        assert aim_service._passes_filters({}, p, {"restricted": False}) is False


class TestSearchBehavior:
    def test_search_substring_in_haystack(self, aim_service):
        unit = {"title": "Hydraulic Systems Overview", "text": "Pumps and actuators."}
        p = _payload(course_name="AIM")
        assert aim_service._passes_filters(unit, p, {"search": "hydraulic"}) is True

    def test_search_miss(self, aim_service):
        unit = {"title": "Hydraulic Systems", "text": "Pumps."}
        p = _payload()
        assert aim_service._passes_filters(unit, p, {"search": "powerplant"}) is False


class TestUnknownFilters:
    def test_unknown_top_level_key_does_not_error(self, aim_service):
        p = _payload(visibility="instructor")
        assert aim_service._passes_filters({}, p, {"not_a_real_field": "x"}) is True

    def test_metadata_filters_still_work(self, aim_service):
        p = _payload(visibility="instructor")
        assert aim_service._passes_filters({}, p, {"metadata_filters": {"custom_key": "v"}}) is False
        p2 = _payload(visibility="instructor", custom_key="v")
        assert aim_service._passes_filters({}, p2, {"metadata_filters": {"custom_key": "v"}}) is True


class TestTenantDynamicField:
    def test_synth_field_absent_from_default_registry(self):
        assert DEFAULT_REGISTRY.get(_SYNTH_FIELD) is None

    def test_tenant_registry_promotes_synth_to_retrieval(self):
        cfg = SimpleNamespace(tenant_id="phase6a", metadata_framework=_TENANT_RETRIEVAL_ONLY)
        spec = registry_for_tenant(cfg).get(_SYNTH_FIELD)
        assert spec is not None
        assert spec.promotes("retrieval")
        assert not spec.promotes("index")

    def test_passes_filters_uses_tenant_retrieval_field(self):
        cfg = SimpleNamespace(
            tenant_id="phase6a",
            metadata_framework=_TENANT_RETRIEVAL_ONLY,
            retrieval=SimpleNamespace(restricted_content_filter=False),
        )
        svc = _svc(cfg)
        p = _payload(**{_SYNTH_FIELD: "synth-value", "visibility": "instructor"})
        assert svc._passes_filters({}, p, {_SYNTH_FIELD: "synth-value"}) is True
        assert svc._passes_filters({}, p, {_SYNTH_FIELD: "other"}) is False

    def test_default_tenant_does_not_activate_synth_field(self, aim_service):
        p = _payload(visibility="instructor")
        assert aim_service._passes_filters({}, p, {_SYNTH_FIELD: "synth-value"}) is True


class TestDefaultRegistryFallback:
    def test_empty_metadata_framework_uses_default_registry_fields(self, aim_service):
        """AIM has no metadata_framework; visibility/status remain filterable."""
        reg = registry_for_tenant(get_tenant_config("aim"))
        assert reg is DEFAULT_REGISTRY or registry_for_tenant(
            SimpleNamespace(metadata_framework=None)
        ) is DEFAULT_REGISTRY
        p = _payload(visibility="instructor", status="ready")
        assert aim_service._passes_filters({}, p, {"visibility": "instructor", "status": "ready"}) is True
