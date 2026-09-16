"""Phase 7: AIM Topic Source Library filter — registry, listing, retrieval, isolation."""
from __future__ import annotations

import importlib
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config.settings import get_tenant_config
from services.context_retrieval import ContextRetrievalService
from services.metadata_framework.filter_query import (
    build_documents_library_filters,
    resolve_filter_options_storage_key,
)
from services.metadata_framework.registry import (
    DEFAULT_REGISTRY,
    PROMOTE_FILTER_OPTIONS,
    PROMOTE_INDEX,
    PROMOTE_RETRIEVAL,
    registry_for_tenant,
)
from services.source_library import compact_source_record, source_filter_options


def _aim():
    import config.settings as settings

    importlib.reload(settings)
    return settings.get_tenant_config("aim")


def _cengage():
    import config.settings as settings

    importlib.reload(settings)
    return settings.get_tenant_config("cengage")


def _payload(**meta):
    return {
        "job_id": "j1",
        "tenant_id": "aim",
        "client_id": "aim",
        "created_at": "2026-01-01T00:00:00",
        "source_file": {
            "name": "Engines.pdf",
            "type": "pdf",
            "size_bytes": 10,
            "raw_url": "",
            "raw_key": "",
            "relative_path": "Engines.pdf",
        },
        "metadata": {
            "title": "Engines",
            "document_type": "lesson_pdf",
            "purpose": "course_generation",
            "visibility": "instructor",
            "status": "processed",
            **meta,
        },
        "content_units": [],
    }


def _svc(tenant_cfg):
    svc = ContextRetrievalService.__new__(ContextRetrievalService)
    svc.tenant_cfg = tenant_cfg
    svc.role = "user"
    return svc


class TestRegistryPromotion:
    def test_default_registry_has_no_topic(self):
        assert DEFAULT_REGISTRY.get("topic") is None

    def test_aim_promotes_topic_index_filter_options_retrieval(self):
        reg = registry_for_tenant(_aim())
        spec = reg.get("topic")
        assert spec is not None
        assert set(spec.promote) == {"index", "filter_options", "retrieval"}
        assert not spec.promotes("cas_list")
        assert "topic" in reg.keys_for(PROMOTE_INDEX)
        assert "topic" in reg.keys_for(PROMOTE_FILTER_OPTIONS)
        assert "topic" in reg.keys_for(PROMOTE_RETRIEVAL)

    def test_cengage_registry_has_no_topic(self):
        assert registry_for_tenant(_cengage()).get("topic") is None

    def test_empty_framework_has_no_topic(self):
        cfg = SimpleNamespace(metadata_framework=None)
        assert registry_for_tenant(cfg).get("topic") is None


class TestNewRecordIndexing:
    def test_aim_compact_record_includes_topic(self):
        with patch("services.source_library.datetime") as dt:
            dt.utcnow.return_value.isoformat.return_value = "2026-08-31T12:00:00"
            rec = compact_source_record(
                _payload(topic="Engines"), "p", "c", tenant_cfg=_aim()
            )
        assert rec["topic"] == "Engines"

    def test_default_compact_record_omits_topic(self):
        with patch("services.source_library.datetime") as dt:
            dt.utcnow.return_value.isoformat.return_value = "2026-08-31T12:00:00"
            rec = compact_source_record(_payload(topic="Engines"), "p", "c")
        assert "topic" not in rec


class TestFilterOptionsAndListing:
    def test_aim_source_filter_options_includes_topic(self):
        opts = source_filter_options(
            [{"topic": "Engines", "document_type": "lesson_pdf"}],
            tenant_cfg=_aim(),
        )
        assert "topic" in opts
        assert opts["topic"] == ["Engines"]

    def test_cengage_source_filter_options_excludes_topic(self):
        opts = source_filter_options(
            [{"topic": "Engines", "document_type": "textbook_pdf"}],
            tenant_cfg=_cengage(),
        )
        assert "topic" not in opts

    def test_aim_source_matches_case_insensitive_exact(self):
        svc = _svc(_aim())
        src = {"topic": "Engines", "document_type": "lesson_pdf"}
        assert svc._source_matches(src, {"topic": "Engines"}) is True
        assert svc._source_matches(src, {"topic": "engines"}) is True
        assert svc._source_matches(src, {"topic": "Hydraulics"}) is False

    def test_aim_source_matches_empty_and_wildcard(self):
        svc = _svc(_aim())
        src = {"topic": "Engines", "document_type": "lesson_pdf"}
        assert svc._source_matches(src, {}) is True
        assert svc._source_matches(src, {"topic": ""}) is True
        assert svc._source_matches(src, {"topic": "all"}) is True

    def test_aim_source_matches_missing_topic_fails(self):
        svc = _svc(_aim())
        src = {"document_type": "lesson_pdf"}
        assert svc._source_matches(src, {"topic": "Engines"}) is False

    def test_cengage_ignores_topic_query(self):
        assert resolve_filter_options_storage_key("topic", _cengage()) is None
        filters = build_documents_library_filters(
            query_params={"topic": "Engines"},
            tenant_cfg=_cengage(),
        )
        assert "topic" not in filters

    def test_aim_api_forwards_topic(self):
        assert resolve_filter_options_storage_key("topic", _aim()) == "topic"
        filters = build_documents_library_filters(
            query_params={"topic": "Engines"},
            tenant_cfg=_aim(),
        )
        assert filters["topic"] == "Engines"


class TestRetrievalPromotion:
    def test_no_legacy_exact_constant(self):
        import services.context_retrieval as cr

        assert not hasattr(cr, "_RETRIEVAL_LEGACY_EXACT")

    def test_aim_retrieval_topic_match(self):
        svc = _svc(_aim())
        p = {"job_id": "j1", "metadata": {"topic": "Engines", "visibility": "instructor"},
             "source_file": {"name": "x.pdf"}}
        assert svc._passes_filters({}, p, {}) is True
        assert svc._passes_filters({}, p, {"topic": ""}) is True
        assert svc._passes_filters({}, p, {"topic": "Engines"}) is True
        assert svc._passes_filters({}, p, {"topic": "engines"}) is True
        assert svc._passes_filters({}, p, {"topic": "Hydraulics"}) is False

    def test_aim_retrieval_missing_topic_fails(self):
        svc = _svc(_aim())
        p = {"job_id": "j1", "metadata": {"visibility": "instructor"},
             "source_file": {"name": "x.pdf"}}
        assert svc._passes_filters({}, p, {"topic": "Engines"}) is False

    def test_aim_retrieval_all_is_literal_not_wildcard(self):
        svc = _svc(_aim())
        p = {"job_id": "j1", "metadata": {"topic": "Engines", "visibility": "instructor"},
             "source_file": {"name": "x.pdf"}}
        assert svc._passes_filters({}, p, {"topic": "all"}) is False

    def test_default_tenant_topic_filter_ignored(self):
        """Without registry promotion, unknown top-level topic does not gate."""
        svc = _svc(SimpleNamespace(
            metadata_framework=None,
            retrieval=SimpleNamespace(restricted_content_filter=False),
        ))
        p = {"job_id": "j1", "metadata": {"visibility": "instructor"},
             "source_file": {"name": "x.pdf"}}
        assert svc._passes_filters({}, p, {"topic": "Engines"}) is True


class TestExistingRecordBackfillContract:
    def test_existing_compact_without_topic_fails_filter_until_backfilled(self):
        svc = _svc(_aim())
        legacy = {"document_type": "lesson_pdf", "title": "Engines"}
        assert "topic" not in legacy
        assert svc._source_matches(legacy, {"topic": "Engines"}) is False

    def test_backfilled_compact_topic_matches(self):
        svc = _svc(_aim())
        backfilled = {"document_type": "lesson_pdf", "topic": "Engines"}
        assert svc._source_matches(backfilled, {"topic": "engines"}) is True

    def test_backfill_helper_reads_payload_metadata_topic(self):
        import importlib.util

        path = Path(__file__).resolve().parents[2] / "scripts" / "backfill_source_index_topic.py"
        spec = importlib.util.spec_from_file_location("backfill_source_index_topic", path)
        mod = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(mod)
        assert mod._topic_from_payload({"metadata": {"topic": "Engines"}}) == "Engines"
        assert mod._topic_from_payload({"metadata": {}}) == ""
        assert mod._topic_from_payload({}) == ""
