"""Unit tests for auto-provisioning a DIS client on tenant creation.

Pure filesystem/HTTP logic — no DB involved, so this lives in tests/unit/
per conftest.py's own rule. Every external touchpoint (the clients dir, the
access-config path, the DIS reload call, dis_access's own cache) is
monkeypatched so nothing here writes into the real repo or makes a real
network call.
"""

from __future__ import annotations

import json

import pytest
import yaml

from app.services import dis_provisioning
from app.services.dis_metadata_templates import PROVISIONING_STAMP_VERSION


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    monkeypatch.setattr(dis_provisioning, "_CLIENTS_DIR", tmp_path / "clients")
    monkeypatch.setattr(dis_provisioning, "_access_config_path", lambda: tmp_path / "dis_access.json")
    monkeypatch.setattr(dis_provisioning, "load_dis_access_config", lambda force_reload=False: {"default_tenant_id": "cengage"})
    return tmp_path


def test_write_client_yaml_creates_expected_file(_isolate):
    dis_provisioning._write_client_yaml("nova-publishing", "Nova Publishing")

    path = _isolate / "clients" / "nova-publishing.yaml"
    assert path.exists()
    content = path.read_text(encoding="utf-8")
    raw = yaml.safe_load(content)
    assert "client" in raw
    assert raw["client"]["client_id"] == "nova-publishing"
    assert raw["client"]["client_name"] == "Nova Publishing"
    assert raw["client"]["namespace"] == "nova-publishing"
    assert "tenant_id" not in raw
    assert raw["provisioning"]["template"] == "minimal"
    assert str(raw["provisioning"]["version"]) == PROVISIONING_STAMP_VERSION


def test_add_to_available_clients_is_idempotent(_isolate, monkeypatch):
    monkeypatch.setattr(dis_provisioning.settings, "dis_available_clients", "")

    dis_provisioning._add_to_available_clients("nova-publishing")
    dis_provisioning._add_to_available_clients("nova-publishing")  # re-run must not duplicate

    cfg = json.loads((_isolate / "dis_access.json").read_text(encoding="utf-8"))
    assert cfg["available_clients"].count("nova-publishing") == 1


def test_add_to_available_clients_preserves_existing_entries(_isolate):
    (_isolate / "dis_access.json").write_text(
        json.dumps({"available_clients": ["aim", "cengage"], "default_tenant_id": "cengage"}), encoding="utf-8",
    )

    dis_provisioning._add_to_available_clients("nova-publishing")

    cfg = json.loads((_isolate / "dis_access.json").read_text(encoding="utf-8"))
    assert cfg["available_clients"] == ["aim", "cengage", "nova-publishing"]


def test_provision_dis_client_never_raises_when_dis_unreachable(_isolate, monkeypatch):
    # Enabled explicitly: this test is about an ENABLED DIS that cannot be
    # reached, which is a different path from a DIS switched off (covered
    # below). It used to inherit the flag from whatever .env the developer
    # happened to have, so it silently asserted nothing wherever DIS was off.
    monkeypatch.setattr(dis_provisioning.settings, "dis_enabled", True)

    def _boom(*args, **kwargs):
        raise ConnectionError("dis is down")

    monkeypatch.setattr(dis_provisioning.httpx, "post", _boom)
    monkeypatch.setattr(dis_provisioning.settings, "dis_available_clients", "")

    dis_provisioning.provision_dis_client("nova-publishing", "Nova Publishing")  # must not raise

    assert (_isolate / "clients" / "nova-publishing.yaml").exists()
    cfg = json.loads((_isolate / "dis_access.json").read_text(encoding="utf-8"))
    assert "nova-publishing" in cfg["available_clients"]


def test_provision_dis_client_never_raises_on_filesystem_error(monkeypatch):
    # Same reason as above — without this the call returns at the disabled
    # guard and "did not raise" becomes true for the wrong reason.
    monkeypatch.setattr(dis_provisioning.settings, "dis_enabled", True)
    monkeypatch.setattr(dis_provisioning, "_CLIENTS_DIR", "/proc/impossible/path/for/a/directory")

    dis_provisioning.provision_dis_client("nova-publishing", "Nova Publishing")  # must not raise


def test_provision_is_skipped_entirely_when_dis_is_disabled(_isolate, monkeypatch):
    monkeypatch.setattr(dis_provisioning.settings, "dis_enabled", False)

    dis_provisioning.provision_dis_client("nova-publishing", "Nova Publishing")

    assert not (_isolate / "clients" / "nova-publishing.yaml").exists()
    assert not (_isolate / "dis_access.json").exists()


def test_add_to_available_clients_raises_rather_than_destroy_malformed_file(_isolate):
    """Finding #8: a parse failure must abort, never fall back to {} and
    overwrite default_client_id/super_admin_usernames/client_admin_map with
    a bare {"available_clients": [...]} skeleton."""
    config_path = _isolate / "dis_access.json"
    original = "{not valid json"
    config_path.write_text(original, encoding="utf-8")

    with pytest.raises(Exception):
        dis_provisioning._add_to_available_clients("nova-publishing")

    assert config_path.read_text(encoding="utf-8") == original


def test_provision_dis_client_never_raises_and_never_destroys_malformed_file(_isolate, monkeypatch):
    """The outer best-effort wrapper still doesn't raise, but the malformed
    file must survive intact rather than being silently replaced."""
    monkeypatch.setattr(dis_provisioning.settings, "dis_available_clients", "")
    config_path = _isolate / "dis_access.json"
    original = "{not valid json"
    config_path.write_text(original, encoding="utf-8")

    dis_provisioning.provision_dis_client("nova-publishing", "Nova Publishing")  # must not raise

    assert config_path.read_text(encoding="utf-8") == original


def test_add_to_available_clients_write_is_atomic_no_tmp_file_left_behind(_isolate):
    dis_provisioning._add_to_available_clients("nova-publishing")

    assert not (_isolate / "dis_access.json.tmp").exists()
    cfg = json.loads((_isolate / "dis_access.json").read_text(encoding="utf-8"))
    assert "nova-publishing" in cfg["available_clients"]


# ---------------------------------------------------------------------------
# Phase 8 — templates, overrides, CREATE ONLY, validation
# ---------------------------------------------------------------------------


def _write(tmp_path, slug, display, template="minimal", overrides=None):
    return dis_provisioning.write_new_client_yaml(
        slug,
        display,
        template=template,
        overrides=overrides,
        clients_dir=tmp_path / "clients",
    )


def test_minimal_academic_publishing_generation(_isolate):
    clients = _isolate / "clients"
    for family, slug in (("minimal", "t_min"), ("academic", "t_acad"), ("publishing", "t_pub")):
        path = _write(_isolate, slug, f"Client {slug}", template=family)
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        assert raw["client"]["client_id"] == slug
        assert raw["provisioning"]["template"] == family
        tenant = dis_provisioning.load_via_tenant_config(raw)
        assert tenant.tenant_id == slug
        assert tenant.namespace == slug
        if family == "minimal":
            assert "metadata_schemas" not in raw
            assert "retrieval" not in raw
            assert "metadata_framework" not in raw
        if family == "academic":
            schema = raw["metadata_schemas"][slug]
            required_names = [f["name"] for f in schema["required_fields"]]
            assert "topic" not in required_names
            assert "course_name" in required_names
            tax_keys = [t["key"] for t in raw["retrieval"]["source_ui"]["taxonomy_filters"]]
            assert "topic" not in tax_keys
            assert "metadata_framework" not in raw
        if family == "publishing":
            schema = raw["metadata_schemas"][slug]
            required_names = [f["name"] for f in schema["required_fields"]]
            assert "document_type" in required_names
            assert "file_sha256" not in required_names
            assert "password" not in path.read_text(encoding="utf-8").lower()
            assert "postgresql://" not in path.read_text(encoding="utf-8")


def test_explicit_template_required_and_invalid_rejected(_isolate):
    with pytest.raises(dis_provisioning.DisProvisioningValidationError, match="required"):
        dis_provisioning.build_client_document("x", "X", template="")
    with pytest.raises(dis_provisioning.DisProvisioningValidationError, match="required"):
        dis_provisioning.build_client_document("x", "X", template=None)  # type: ignore[arg-type]
    with pytest.raises(dis_provisioning.DisProvisioningValidationError, match="invalid template"):
        dis_provisioning.build_client_document("x", "X", template="aviation")


def test_deterministic_generation(_isolate):
    a = dis_provisioning.build_client_document("det", "Det", "academic")
    b = dis_provisioning.build_client_document("det", "Det", "academic")
    assert dis_provisioning.render_client_yaml(a) == dis_provisioning.render_client_yaml(b)


def test_valid_override_metadata_framework_topic(_isolate):
    path = _write(
        _isolate,
        "ov1",
        "Override One",
        template="academic",
        overrides={
            "metadata_framework": {
                "fields": {
                    "topic": {
                        "type": "string",
                        "tier": "structural",
                        "promote": ["index", "filter_options", "retrieval"],
                    }
                }
            }
        },
    )
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert raw["metadata_framework"]["fields"]["topic"]["promote"] == [
        "index", "filter_options", "retrieval",
    ]
    dis_provisioning.load_via_tenant_config(raw)


def test_source_ui_override(_isolate):
    path = _write(
        _isolate,
        "ov2",
        "Override Two",
        template="academic",
        overrides={
            "source_ui": {
                "taxonomy_filters": [
                    {"key": "course_name", "label": "Programme", "type": "select"},
                ]
            }
        },
    )
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    filters = raw["retrieval"]["source_ui"]["taxonomy_filters"]
    assert filters[0]["label"] == "Programme"


def test_invalid_override_rejected(_isolate):
    with pytest.raises(dis_provisioning.DisProvisioningValidationError, match="unsupported|forbidden"):
        dis_provisioning.build_client_document(
            "x", "X", "minimal", overrides={"document_processing": {"profile": "x"}},
        )
    with pytest.raises(dis_provisioning.DisProvisioningValidationError, match="unsupported|forbidden"):
        dis_provisioning.build_client_document(
            "x", "X", "minimal", overrides={"namespace": "hijack"},
        )


def test_create_only_existing_file_unchanged(_isolate, monkeypatch):
    monkeypatch.setattr(dis_provisioning.settings, "dis_enabled", True)
    monkeypatch.setattr(dis_provisioning.settings, "dis_available_clients", "")
    monkeypatch.setattr(dis_provisioning.httpx, "post", lambda *a, **k: type("R", (), {"raise_for_status": lambda self: None})())

    path = _write(_isolate, "exists1", "Exists One", template="minimal")
    original = path.read_text(encoding="utf-8")

    with pytest.raises(dis_provisioning.DisProvisioningExistsError):
        _write(_isolate, "exists1", "Exists One", template="academic")

    assert path.read_text(encoding="utf-8") == original

    dis_provisioning.provision_dis_client("exists1", "Exists One", template="academic")
    assert path.read_text(encoding="utf-8") == original
    assert not (_isolate / "dis_access.json").exists()


def test_failed_validation_does_not_write_or_touch_allowlist(_isolate, monkeypatch):
    monkeypatch.setattr(dis_provisioning.settings, "dis_enabled", True)
    monkeypatch.setattr(dis_provisioning.settings, "dis_available_clients", "")

    dis_provisioning.provision_dis_client("badfam", "Bad Fam", template="not-a-family")

    assert not (_isolate / "clients" / "badfam.yaml").exists()
    assert not (_isolate / "dis_access.json").exists()


def test_invalid_promote_target(_isolate):
    with pytest.raises(dis_provisioning.DisProvisioningValidationError, match="promote"):
        _write(
            _isolate,
            "bad_promote",
            "Bad Promote",
            template="minimal",
            overrides={
                "metadata_framework": {
                    "fields": {"topic": {"type": "string", "promote": ["not_a_target"]}}
                }
            },
        )


def test_invalid_source_ui_reference(_isolate):
    with pytest.raises(dis_provisioning.DisProvisioningValidationError, match="unknown field"):
        _write(
            _isolate,
            "bad_ui",
            "Bad UI",
            template="minimal",
            overrides={
                "source_ui": {
                    "taxonomy_filters": [
                        {"key": "not_a_real_field", "label": "Nope", "type": "select"},
                    ]
                }
            },
        )


def test_invalid_control(_isolate):
    with pytest.raises(dis_provisioning.DisProvisioningValidationError, match="control"):
        _write(
            _isolate,
            "bad_ctrl",
            "Bad Ctrl",
            template="academic",
            overrides={
                "source_ui": {
                    "upload": {
                        "fields": [
                            {"key": "module_name", "label": "Module", "control": "checkbox"},
                        ]
                    }
                }
            },
        )


def test_invalid_profile(_isolate):
    with pytest.raises(dis_provisioning.DisProvisioningValidationError, match="unknown client profile"):
        _write(
            _isolate,
            "prof1",
            "Prof One",
            template="minimal",
            overrides={"profile": "does-not-exist"},
        )
    with pytest.raises(dis_provisioning.DisProvisioningValidationError, match="bound to client id"):
        _write(
            _isolate,
            "prof2",
            "Prof Two",
            template="minimal",
            overrides={"profile": "aim"},
        )


def test_invalid_identity_and_namespace_collision(_isolate):
    with pytest.raises(dis_provisioning.DisProvisioningValidationError, match="display name"):
        dis_provisioning.build_client_document("x", "  ", "minimal")

    clients = _isolate / "clients"
    clients.mkdir(parents=True, exist_ok=True)
    # Another tenant already owns the namespace that "newbie" would use (namespace == slug).
    (clients / "other.yaml").write_text(
        "client:\n  client_id: other\n  client_name: Other\n  namespace: newbie\n",
        encoding="utf-8",
    )
    with pytest.raises(dis_provisioning.DisProvisioningValidationError, match="already owned"):
        _write(_isolate, "newbie", "Newbie", template="minimal")


def test_duplicate_metadata_fields(_isolate):
    with pytest.raises(dis_provisioning.DisProvisioningValidationError, match="duplicate"):
        _write(
            _isolate,
            "dup",
            "Dup",
            template="minimal",
            overrides={
                "metadata_schemas": {
                    "required_fields": [
                        {"name": "course_name", "type": "string"},
                        {"name": "course_name", "type": "string"},
                    ]
                }
            },
        )


def test_generated_yaml_loads_through_real_tenant_config(_isolate):
    for family in ("minimal", "academic", "publishing"):
        path = _write(_isolate, f"load_{family}", f"Load {family}", template=family)
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        tenant = dis_provisioning.load_via_tenant_config(raw)
        assert tenant.tenant_id == f"load_{family}"
        assert tenant.get_client() is not None


def test_no_secrets_and_does_not_copy_existing_client_credentials(_isolate):
    for family in ("minimal", "academic", "publishing"):
        path = _write(_isolate, f"sec_{family}", f"Sec {family}", template=family)
        text = path.read_text(encoding="utf-8")
        lowered = text.lower()
        assert "password" not in lowered
        assert "postgresql://" not in lowered
        assert "opensearch" not in lowered
        assert "aws_secret" not in lowered
        raw = yaml.safe_load(text)
        assert "structure_store" not in raw
        assert "vector_store" not in raw


def test_allowlist_idempotent_and_skipped_when_create_stops(_isolate, monkeypatch):
    monkeypatch.setattr(dis_provisioning.settings, "dis_enabled", True)
    monkeypatch.setattr(dis_provisioning.settings, "dis_available_clients", "")
    monkeypatch.setattr(
        dis_provisioning.httpx,
        "post",
        lambda *a, **k: type("R", (), {"raise_for_status": lambda self: None})(),
    )

    dis_provisioning.provision_dis_client("allow1", "Allow One", template="minimal")
    dis_provisioning.provision_dis_client("allow1", "Allow One", template="minimal")

    cfg = json.loads((_isolate / "dis_access.json").read_text(encoding="utf-8"))
    assert cfg["available_clients"].count("allow1") == 1
    yaml_path = _isolate / "clients" / "allow1.yaml"
    original = yaml_path.read_text(encoding="utf-8")
    dis_provisioning.provision_dis_client("allow1", "Allow One", template="academic")
    assert yaml_path.read_text(encoding="utf-8") == original


def test_acs_codes_rejected_in_framework_overlay(_isolate):
    with pytest.raises(dis_provisioning.DisProvisioningValidationError, match="acs_codes"):
        _write(
            _isolate,
            "acs1",
            "ACS One",
            template="minimal",
            overrides={
                "metadata_framework": {
                    "fields": {"acs_codes": {"type": "list", "promote": ["index"]}}
                }
            },
        )
