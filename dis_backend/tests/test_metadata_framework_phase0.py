"""Phase 0: metadata_framework survives client YAML → TenantConfig.

Unsupported top-level YAML keys used to be dropped by
TenantRegistry._client_raw_to_tenant. These tests pin that
metadata_framework is no longer discarded, while clients without the
block continue to load unchanged.
"""
from __future__ import annotations

import importlib
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def _raw_client(*, with_framework: bool = False) -> dict:
    raw = {
        "client": {
            "client_id": "phase0_demo",
            "client_name": "Phase0 Demo",
            "namespace": "phase0_ns",
            "enabled": True,
        },
        "auth": {"client_admins": [], "users": []},
        "ingestion": {},
        "processing": {},
        "pipeline": {"models": {}},
        "storage": {
            "provider": "s3",
            "raw_bucket": "test-bucket",
            "processed_bucket": "test-bucket",
        },
        "retrieval": {
            "max_results": 10,
            "source_ui": {
                "taxonomy_filters": [
                    {"key": "module", "label": "Module / Section", "type": "select"},
                ],
            },
        },
    }
    if with_framework:
        raw["metadata_framework"] = {
            "version": "0.1",
            "fields": {"skill": {"type": "string"}},
            "taxonomies": {"bloom": ["remember", "apply"]},
        }
    return raw


def test_metadata_framework_survives_client_raw_to_tenant():
    from config.settings import TenantRegistry

    registry = TenantRegistry.__new__(TenantRegistry)
    tenant = TenantRegistry._client_raw_to_tenant(registry, _raw_client(with_framework=True))
    dumped = tenant.metadata_framework.model_dump()
    assert dumped.get("version") == "0.1"
    assert dumped.get("fields", {}).get("skill", {}).get("type") == "string"
    assert dumped.get("taxonomies", {}).get("bloom") == ["remember", "apply"]


def test_client_without_metadata_framework_still_loads():
    from config.settings import TenantRegistry

    registry = TenantRegistry.__new__(TenantRegistry)
    tenant = TenantRegistry._client_raw_to_tenant(registry, _raw_client(with_framework=False))
    assert tenant.tenant_id == "phase0_demo"
    assert tenant.metadata_framework is not None
    # Empty default — no framework required for existing clients.
    assert tenant.metadata_framework.model_dump() == {}


def test_existing_retrieval_config_unaffected_when_framework_present():
    from config.settings import TenantRegistry

    raw = _raw_client(with_framework=True)
    raw["retrieval"]["max_results"] = 17
    registry = TenantRegistry.__new__(TenantRegistry)
    tenant = TenantRegistry._client_raw_to_tenant(registry, raw)
    assert tenant.retrieval.max_results == 17
    assert tenant.retrieval.source_ui["taxonomy_filters"][0]["key"] == "module"
    assert tenant.metadata_framework.model_dump().get("version") == "0.1"


def test_real_aim_client_loads_with_topic_metadata_framework():
    """Phase 7: AIM YAML overlays metadata_framework.fields.topic (tenant-only)."""
    import config.settings as settings

    importlib.reload(settings)
    tenant = settings.get_tenant_config("aim")
    assert tenant.tenant_id == "aim"
    assert tenant.retrieval.source_ui.get("taxonomy_filters")
    assert tenant.metadata_framework is not None
    fields = tenant.metadata_framework.model_dump().get("fields") or {}
    topic = fields.get("topic") or {}
    assert topic.get("type") == "string"
    assert set(topic.get("promote") or []) == {"index", "filter_options", "retrieval"}
    assert "cas_list" not in (topic.get("promote") or [])


def test_yaml_roundtrip_via_safe_load():
    """Prove YAML → loader dict → TenantConfig carries the block."""
    from config.settings import TenantRegistry

    text = yaml.dump(_raw_client(with_framework=True))
    raw = yaml.safe_load(text)
    registry = TenantRegistry.__new__(TenantRegistry)
    tenant = TenantRegistry._client_raw_to_tenant(registry, raw)
    assert "metadata_framework" in raw
    assert tenant.metadata_framework.model_dump()["version"] == "0.1"
