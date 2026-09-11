"""Phase 4 — metadata schema validation (report-first / non-blocking).

Characterization: schema violations must not flip structural ``valid`` or fail
the job. Golden / unit coverage: required fields, enum values, tenant isolation,
empty schema, AIM/Cengage schema load, purity.
"""
from __future__ import annotations

import copy
import sys
from pathlib import Path
from types import SimpleNamespace

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config.settings import MetadataField, MetadataSchema, TenantConfig
from services.agents.validation_report_agent import ValidationReportAgent
from services.metadata_schema_validate import (
    KIND_INVALID,
    KIND_MISSING,
    validate_metadata_against_schema,
)


# ── helpers ──────────────────────────────────────────────────────────────────


def _schema(**kwargs) -> MetadataSchema:
    return MetadataSchema(**kwargs)


def _req(name: str, values=None, **extra) -> MetadataField:
    return MetadataField(name=name, values=values or [], **extra)


def _opt(name: str, values=None, **extra) -> MetadataField:
    return MetadataField(name=name, values=values or [], **extra)


class _FakeWriter:
    def job_prefix(self, namespace, job_id):
        return f"{namespace}/{job_id}"

    def write_json(self, key, payload):
        return f"s3://fake/{key}"


def _agent_for(tenant: TenantConfig) -> ValidationReportAgent:
    agent = ValidationReportAgent.__new__(ValidationReportAgent)
    agent.ctx = SimpleNamespace(
        cfg=tenant,
        writer=_FakeWriter(),
        step_done=lambda state, name: state,
    )
    return agent


def _complete_payload(metadata: dict | None = None, client_id: str = "client_a") -> dict:
    return {
        "tenant_id": "t1",
        "client_id": client_id,
        "job_id": "job-1",
        "source_file": {
            "name": "doc.pdf",
            "type": "pdf",
            "raw_url": "s3://bucket/raw/doc.pdf",
        },
        "metadata": metadata if metadata is not None else {"title": "Doc"},
        "content_units": [{"content_unit_id": "u1"}],
    }


def _run_report(tenant: TenantConfig, metadata: dict | None = None, client_id: str = "client_a", **state_extra):
    state = {
        "job_id": "job-1",
        "tenant_id": tenant.tenant_id,
        "client_id": client_id,
        "namespace": tenant.namespace,
        "raw_text": "hello",
        "content_units": [{"content_unit_id": "u1"}],
        "studio_payload": _complete_payload(metadata=metadata, client_id=client_id),
        "artifact_urls": {},
        "completed_steps": [],
        **state_extra,
    }
    return _agent_for(tenant).run(state)["validation_report"]


def _tenant_with_schemas(schemas: dict, tenant_id: str = "t1", default_client: str = "client_a") -> TenantConfig:
    return TenantConfig(
        tenant_id=tenant_id,
        display_name=tenant_id,
        namespace=f"{tenant_id}_ns",
        default_client_id=default_client,
        metadata_schemas=schemas,
    )


# ── Characterization: schema violations do not fail structural validation ────


def test_characterization_missing_schema_required_does_not_fail_valid():
    """Pre-Phase-4 contract: missing schema required fields do not flip ``valid``.

    Phase 4 adds report findings but must keep this non-blocking behavior.
    """
    tenant = _tenant_with_schemas({
        "client_a": _schema(required_fields=[_req("course_name")]),
    })
    # Non-empty metadata object (structural check treats {} as missing "metadata").
    report = _run_report(tenant, metadata={"title": "Doc"})
    assert report["valid"] is True
    assert "course_name" not in report["missing_required_fields"]


def test_characterization_invalid_enum_does_not_fail_valid():
    """Pre-Phase-4 contract: invalid allowed values do not flip ``valid``."""
    tenant = _tenant_with_schemas({
        "client_a": _schema(required_fields=[
            _req("document_type", values=["textbook", "article"]),
        ]),
    })
    report = _run_report(tenant, metadata={"document_type": "invalid"})
    assert report["valid"] is True
    assert report["missing_required_fields"] == []


# ── Validator unit: required ─────────────────────────────────────────────────


def test_required_present_no_finding():
    schema = _schema(required_fields=[_req("course_name")])
    assert validate_metadata_against_schema(schema, {"course_name": "Physics"}) == []


def test_required_missing_finding():
    schema = _schema(required_fields=[_req("course_name")])
    findings = validate_metadata_against_schema(schema, {})
    assert findings == [{"kind": KIND_MISSING, "field": "course_name"}]


def test_required_null_and_empty_string():
    schema = _schema(required_fields=[_req("course_name")])
    assert validate_metadata_against_schema(schema, {"course_name": None}) == [
        {"kind": KIND_MISSING, "field": "course_name"}
    ]
    assert validate_metadata_against_schema(schema, {"course_name": ""}) == [
        {"kind": KIND_MISSING, "field": "course_name"}
    ]


def test_required_empty_list_and_dict_are_missing():
    schema = _schema(required_fields=[_req("keywords"), _req("extra")])
    findings = validate_metadata_against_schema(
        schema, {"keywords": [], "extra": {}}
    )
    assert {f["field"] for f in findings} == {"keywords", "extra"}
    assert all(f["kind"] == KIND_MISSING for f in findings)


def test_required_zero_and_false_are_not_missing():
    """Match validation_report_agent emptiness: only None/''/[]/{} are empty."""
    schema = _schema(required_fields=[_req("count"), _req("flag")])
    assert validate_metadata_against_schema(schema, {"count": 0, "flag": False}) == []


def test_optional_missing_no_finding():
    schema = _schema(optional_fields=[_opt("learning_objective")])
    assert validate_metadata_against_schema(schema, {}) == []


def test_empty_required_fields_config_no_findings():
    assert validate_metadata_against_schema(_schema(), {}) == []
    assert validate_metadata_against_schema(_schema(required_fields=[]), {}) == []


# ── Validator unit: enum / values ────────────────────────────────────────────


def test_enum_allowed_value():
    schema = _schema(required_fields=[
        _req("document_type", values=["textbook", "article"]),
    ])
    assert validate_metadata_against_schema(schema, {"document_type": "textbook"}) == []


def test_enum_invalid_value():
    schema = _schema(required_fields=[
        _req("document_type", values=["textbook", "article"]),
    ])
    findings = validate_metadata_against_schema(schema, {"document_type": "video"})
    assert len(findings) == 1
    assert findings[0]["kind"] == KIND_INVALID
    assert findings[0]["field"] == "document_type"
    assert findings[0]["value"] == "video"
    assert findings[0]["allowed"] == ["textbook", "article"]


def test_enum_case_sensitive():
    schema = _schema(optional_fields=[
        _opt("document_type", values=["Article"]),
    ])
    findings = validate_metadata_against_schema(schema, {"document_type": "article"})
    assert len(findings) == 1
    assert findings[0]["kind"] == KIND_INVALID


def test_empty_values_configuration_skips_enum():
    schema = _schema(required_fields=[_req("document_type", values=[])])
    assert validate_metadata_against_schema(schema, {"document_type": "anything"}) == []


def test_missing_values_configuration_skips_enum():
    schema = _schema(required_fields=[_req("document_type")])
    assert validate_metadata_against_schema(schema, {"document_type": "anything"}) == []


def test_optional_enum_invalid_still_reported():
    schema = _schema(optional_fields=[
        _opt("difficulty_level", values=["introductory", "advanced"]),
    ])
    findings = validate_metadata_against_schema(
        schema, {"difficulty_level": "expert"}
    )
    assert len(findings) == 1
    assert findings[0]["kind"] == KIND_INVALID


def test_multi_value_list_validates_each_member():
    schema = _schema(optional_fields=[
        _opt("tags", values=["a", "b"]),
    ])
    findings = validate_metadata_against_schema(schema, {"tags": ["a", "x", "b"]})
    assert findings == [{
        "kind": KIND_INVALID,
        "field": "tags",
        "value": "x",
        "allowed": ["a", "b"],
    }]


def test_missing_required_does_not_also_emit_invalid():
    schema = _schema(required_fields=[
        _req("document_type", values=["textbook", "article"]),
    ])
    findings = validate_metadata_against_schema(schema, {})
    assert findings == [{"kind": KIND_MISSING, "field": "document_type"}]


# ── Purity / safety ──────────────────────────────────────────────────────────


def test_validator_does_not_mutate_metadata():
    schema = _schema(required_fields=[_req("course_name")])
    meta = {"title": "x"}
    snapshot = copy.deepcopy(meta)
    validate_metadata_against_schema(schema, meta)
    assert meta == snapshot


def test_none_schema_and_none_metadata():
    assert validate_metadata_against_schema(None, None) == []
    assert validate_metadata_against_schema(_schema(), None) == []


# ── Tenant / client isolation ────────────────────────────────────────────────


def test_tenant_client_isolation_required_field():
    tenant = _tenant_with_schemas({
        "client_a": _schema(required_fields=[_req("field_A")]),
        "client_b": _schema(required_fields=[_req("field_B")]),
    })
    report_a = _run_report(tenant, metadata={"title": "A"}, client_id="client_a")
    report_b = _run_report(tenant, metadata={"title": "B"}, client_id="client_b")

    fields_a = [f["field"] for f in report_a["metadata_validation"]["findings"]]
    fields_b = [f["field"] for f in report_b["metadata_validation"]["findings"]]
    assert fields_a == ["field_A"]
    assert fields_b == ["field_B"]
    assert report_a["valid"] is True
    assert report_b["valid"] is True


# ── Golden: empty schema / required / enum via report ────────────────────────


def test_golden_empty_schema_no_metadata_findings():
    tenant = _tenant_with_schemas({})
    report = _run_report(tenant, metadata={"title": "Doc"})
    assert report["metadata_validation"]["findings"] == []
    assert report["valid"] is True


def test_golden_required_missing_finding_non_blocking():
    tenant = _tenant_with_schemas({
        "client_a": _schema(required_fields=[_req("course_name")]),
    })
    report = _run_report(tenant, metadata={"title": "Doc"})
    assert report["metadata_validation"]["findings"] == [
        {"kind": KIND_MISSING, "field": "course_name"}
    ]
    assert report["valid"] is True


def test_golden_enum_invalid_finding_non_blocking():
    tenant = _tenant_with_schemas({
        "client_a": _schema(required_fields=[
            _req("document_type", values=["textbook", "article"]),
        ]),
    })
    report = _run_report(tenant, metadata={"document_type": "video"})
    findings = report["metadata_validation"]["findings"]
    assert len(findings) == 1
    assert findings[0]["kind"] == KIND_INVALID
    assert findings[0]["value"] == "video"
    assert report["valid"] is True


def test_report_does_not_change_structural_missing_fields():
    tenant = _tenant_with_schemas({
        "client_a": _schema(required_fields=[_req("course_name")]),
    })
    # Incomplete payload → structural invalid; schema findings still additive.
    state = {
        "job_id": "job-1",
        "tenant_id": "t1",
        "client_id": "client_a",
        "namespace": "t1_ns",
        "raw_text": "hello",
        "studio_payload": {"metadata": {}},
        "artifact_urls": {},
        "completed_steps": [],
    }
    report = _agent_for(tenant).run(state)["validation_report"]
    assert report["valid"] is False
    assert "tenant_id" in report["missing_required_fields"]
    assert any(f["field"] == "course_name" for f in report["metadata_validation"]["findings"])


# ── applies_to deferred ──────────────────────────────────────────────────────


def test_applies_to_is_ignored():
    """applies_to appears only on MetadataField model; unused in AIM/Cengage YAML.

    Phase 4 leaves it unenforced — field still validates regardless of doc_type.
    """
    schema = _schema(required_fields=[
        _req("special", applies_to=["syllabus"]),
    ])
    findings = validate_metadata_against_schema(
        schema, {"document_type": "other"}
    )
    assert findings == [{"kind": KIND_MISSING, "field": "special"}]


# ── AIM / Cengage golden (schema load; transforms untouched) ─────────────────


def _tenant_from_client_yaml(name: str) -> TenantConfig:
    """Load real client YAML via the same conversion as TenantRegistry."""
    from config.settings import TenantRegistry

    raw = yaml.safe_load(
        (Path(__file__).resolve().parents[1] / "config" / "clients" / f"{name}.yaml")
        .read_text(encoding="utf-8")
    )
    registry = TenantRegistry.__new__(TenantRegistry)
    return registry._client_raw_to_tenant(raw)


def test_aim_schema_loads_and_validates_required():
    tenant = _tenant_from_client_yaml("aim")
    schema = tenant.get_metadata_schema("aim")
    assert any(f.name == "course_name" for f in schema.required_fields)
    assert any(f.name == "document_type" and f.values for f in schema.required_fields)

    findings = validate_metadata_against_schema(schema, {})
    missing = {f["field"] for f in findings if f["kind"] == KIND_MISSING}
    assert "course_name" in missing
    assert "document_type" in missing

    # Allowed AIM enum value produces no invalid finding for that field.
    ok = validate_metadata_against_schema(
        schema,
        {
            "course_name": "Systems",
            "document_type": "syllabus",
            "topic": "Landing gear",
            "unit_type": "page",
        },
    )
    assert not any(f["kind"] == KIND_INVALID for f in ok)


def test_cengage_schema_loads_and_validates_enum():
    tenant = _tenant_from_client_yaml("cengage")
    schema = tenant.get_metadata_schema("cengage")
    doc_type = next(f for f in schema.required_fields if f.name == "document_type")
    assert "textbook_pdf" in doc_type.values

    findings = validate_metadata_against_schema(
        schema,
        {
            "client_id": "cengage",
            "source_file_name": "x.pdf",
            "document_type": "not_a_real_type",
            "file_sha256": "abc",
            "content_hash": "def",
            "access_level": "internal",
        },
    )
    invalid = [f for f in findings if f["kind"] == KIND_INVALID]
    assert len(invalid) == 1
    assert invalid[0]["field"] == "document_type"
    assert invalid[0]["value"] == "not_a_real_type"


def test_aim_cengage_extraction_prompt_fields_unchanged():
    """Schema still feeds extraction field lists; Phase 4 does not alter agents."""
    from services.agents.metadata_extraction_agent import MetadataExtractionAgent

    # Importability / class surface unchanged (transforms live elsewhere).
    assert MetadataExtractionAgent.step_name == "metadata_extraction"
    schema = _tenant_from_client_yaml("aim").get_metadata_schema("aim")
    names = [f.name for f in schema.required_fields + schema.optional_fields]
    assert "course_name" in names
    assert "block" in names
