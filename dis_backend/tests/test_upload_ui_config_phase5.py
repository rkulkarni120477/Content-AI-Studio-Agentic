"""Phase 5 — upload metadata UI configuration and characterization."""
from __future__ import annotations

import copy
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config.settings import MetadataField, MetadataSchema, TenantConfig
from services.context_retrieval import ContextRetrievalService
from services.metadata_schema_validate import (
    KIND_INVALID,
    KIND_MISSING,
    validate_metadata_against_schema,
)
from services.upload_ui_config import (
    SYSTEM_DERIVED_FIELD_KEYS,
    build_upload_metadata_ui,
    resolve_document_type_upload,
)


def _schema(**kwargs) -> MetadataSchema:
    return MetadataSchema(**kwargs)


def _req(name: str, values=None, **extra) -> MetadataField:
    return MetadataField(name=name, values=values or [], **extra)


def _tenant_from_yaml_dict(raw: dict) -> TenantConfig:
    from config.settings import TenantRegistry

    registry = TenantRegistry.__new__(TenantRegistry)
    return registry._client_raw_to_tenant(raw)


def _tenant_from_yaml_text(yaml_text: str) -> TenantConfig:
    return _tenant_from_yaml_dict(yaml.safe_load(yaml_text))


# ── Upload UI config resolution ──────────────────────────────────────────────


def test_empty_upload_config_preserves_legacy_document_type_text_mode():
    schema = _schema(required_fields=[_req("document_type", type="string")])
    result = build_upload_metadata_ui({}, schema)
    assert result["fields"] == []
    assert result["configured"] is False
    assert result["document_type"]["control"] == "text"
    assert "placeholder" in result["document_type"]


def test_document_type_select_when_schema_has_controlled_values():
    schema = _schema(
        required_fields=[
            _req(
                "document_type",
                type="enum",
                values=["syllabus", "course_calendar", "other"],
            )
        ]
    )
    dt = resolve_document_type_upload(schema)
    assert dt["control"] == "select"
    assert dt["options"] == ["syllabus", "course_calendar", "other"]


def test_system_derived_field_file_sha256_not_in_upload_ui():
    schema = _schema(
        required_fields=[
            _req("file_sha256"),
            _req("document_type", values=["other"]),
        ]
    )
    source_ui = {
        "upload": {
            "fields": [
                {"key": "file_sha256", "label": "Hash", "control": "text"},
                {"key": "chapter", "label": "Chapter", "control": "text"},
            ]
        }
    }
    result = build_upload_metadata_ui(source_ui, schema)
    keys = [f["key"] for f in result["fields"]]
    assert "file_sha256" not in keys
    assert "chapter" in keys
    assert "file_sha256" in SYSTEM_DERIVED_FIELD_KEYS


def test_configured_upload_fields_resolve_with_ordering():
    schema = _schema(
        optional_fields=[
            _req("chapter"),
            _req("module_name"),
        ]
    )
    source_ui = {
        "upload": {
            "fields": [
                {"key": "module_name", "label": "Module", "control": "text", "order": 2},
                {"key": "chapter", "label": "Chapter", "control": "text", "order": 1},
            ]
        }
    }
    result = build_upload_metadata_ui(source_ui, schema)
    assert [f["key"] for f in result["fields"]] == ["chapter", "module_name"]


def test_select_control_uses_schema_values_only_when_configured():
    schema = _schema(
        optional_fields=[
            _req("difficulty_level", type="enum", values=["introductory", "advanced"]),
        ]
    )
    source_ui = {
        "upload": {
            "fields": [
                {"key": "difficulty_level", "label": "Difficulty", "control": "select"},
            ]
        }
    }
    field = build_upload_metadata_ui(source_ui, schema)["fields"][0]
    assert field["control"] == "select"
    assert field["options"] == ["introductory", "advanced"]


def test_select_without_schema_values_falls_back_to_text():
    schema = _schema(optional_fields=[_req("chapter")])
    source_ui = {
        "upload": {
            "fields": [
                {"key": "chapter", "label": "Chapter", "control": "select"},
            ]
        }
    }
    field = build_upload_metadata_ui(source_ui, schema)["fields"][0]
    assert field["control"] == "text"
    assert "options" not in field


def test_document_type_in_upload_config_uses_schema_options():
    schema = _schema(
        required_fields=[
            _req("document_type", type="enum", values=["syllabus", "quiz_exam"]),
        ]
    )
    source_ui = {
        "upload": {
            "fields": [
                {"key": "document_type", "label": "Doc Type", "control": "select"},
            ]
        }
    }
    result = build_upload_metadata_ui(source_ui, schema)
    assert result["document_type"]["control"] == "select"
    assert result["document_type"]["options"] == ["syllabus", "quiz_exam"]
    assert result["document_type"]["label"] == "Doc Type"
    assert not any(f["key"] == "document_type" for f in result["fields"])


def test_tenant_isolation_in_ui_config():
    yaml_a = """
tenant_id: tenant_a
display_name: Tenant A
default_client_id: client_a
namespace: ns-a
metadata_schemas:
  client_a:
    optional_fields:
      - name: chapter
        type: string
retrieval:
  source_ui:
    upload:
      fields:
        - key: chapter
          label: Chapter A
          control: text
"""
    yaml_b = """
tenant_id: tenant_b
display_name: Tenant B
default_client_id: client_b
namespace: ns-b
metadata_schemas:
  client_b:
    optional_fields:
      - name: module_name
        type: string
retrieval:
  source_ui:
    upload:
      fields:
        - key: module_name
          label: Module B
          control: text
"""
    tenant_a = _tenant_from_yaml_text(yaml_a)
    tenant_b = _tenant_from_yaml_text(yaml_b)
    cfg_a = ContextRetrievalService(tenant_a).ui_config("client_a")["upload_metadata"]
    cfg_b = ContextRetrievalService(tenant_b).ui_config("client_b")["upload_metadata"]
    assert [f["key"] for f in cfg_a["fields"]] == ["chapter"]
    assert [f["key"] for f in cfg_b["fields"]] == ["module_name"]


def test_ui_config_includes_upload_metadata_without_touching_taxonomy_filters():
    yaml_text = """
tenant_id: t1
display_name: Test Tenant
default_client_id: client_a
namespace: ns
retrieval:
  source_ui:
    taxonomy_filters:
      - key: block
        label: Block
        type: select
    upload:
      fields:
        - key: chapter
          label: Chapter
          control: text
metadata_schemas:
  client_a:
    optional_fields:
      - name: chapter
        type: string
"""
    tenant = _tenant_from_yaml_text(yaml_text)
    ui = ContextRetrievalService(tenant).ui_config("client_a")
    assert ui["source_library"]["taxonomy_filters"][0]["key"] == "block"
    assert ui["upload_metadata"]["fields"][0]["key"] == "chapter"


def test_aim_yaml_loads_upload_config_when_present():
    from config.settings import TenantRegistry

    aim_path = Path(__file__).resolve().parents[1] / "config" / "clients" / "aim.yaml"
    if not aim_path.exists():
        return
    raw = yaml.safe_load(aim_path.read_text(encoding="utf-8"))
    registry = TenantRegistry.__new__(TenantRegistry)
    tenant = registry._client_raw_to_tenant(raw)
    ui = ContextRetrievalService(tenant).ui_config("aim")
    upload = ui.get("upload_metadata") or {}
    raw_upload = (tenant.retrieval.source_ui or {}).get("upload") or {}
    if raw_upload.get("fields"):
        assert upload.get("configured") is True
        assert isinstance(upload.get("fields"), list)
    assert upload["document_type"]["control"] == "select"
    assert upload["document_type"]["options"]


def test_validation_integration_missing_required_is_finding_only():
    schema = _schema(
        required_fields=[
            _req("course_name"),
            _req("document_type", values=["syllabus"]),
        ]
    )
    meta = {"document_type": "syllabus"}
    findings = validate_metadata_against_schema(schema, meta)
    assert findings == [{"kind": KIND_MISSING, "field": "course_name"}]
    assert not any(f["kind"] == KIND_INVALID for f in findings)
