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

from app.services import dis_provisioning


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
    assert 'tenant_id: "nova-publishing"' in content
    assert 'display_name: "Nova Publishing"' in content
    assert 'namespace: "nova-publishing"' in content


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
    def _boom(*args, **kwargs):
        raise ConnectionError("dis is down")

    monkeypatch.setattr(dis_provisioning.httpx, "post", _boom)
    monkeypatch.setattr(dis_provisioning.settings, "dis_available_clients", "")

    dis_provisioning.provision_dis_client("nova-publishing", "Nova Publishing")  # must not raise

    assert (_isolate / "clients" / "nova-publishing.yaml").exists()
    cfg = json.loads((_isolate / "dis_access.json").read_text(encoding="utf-8"))
    assert "nova-publishing" in cfg["available_clients"]


def test_provision_dis_client_never_raises_on_filesystem_error(monkeypatch):
    monkeypatch.setattr(dis_provisioning, "_CLIENTS_DIR", "/proc/impossible/path/for/a/directory")

    dis_provisioning.provision_dis_client("nova-publishing", "Nova Publishing")  # must not raise
