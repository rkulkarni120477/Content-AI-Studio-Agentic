"""Client profile loader for config-driven metadata enrichment."""
from __future__ import annotations
from typing import Any, Dict

from config.settings import TenantConfig
from services.client_profiles.base import BaseClientProfile
from services.client_profiles.aim import AIMClientProfile


def get_client_profile(client_id: str, tenant_cfg: TenantConfig) -> BaseClientProfile:
    cid = (client_id or "").lower()
    if cid == "aim":
        return AIMClientProfile(tenant_cfg)
    return BaseClientProfile(tenant_cfg)


def enrich_metadata(client_id: str, filename: str, source_relative_path: str, raw_text: str, doc_type: str, current_metadata: Dict[str, Any], tenant_cfg: TenantConfig) -> Dict[str, Any]:
    return get_client_profile(client_id, tenant_cfg).enrich_metadata(
        filename=filename,
        source_relative_path=source_relative_path,
        raw_text=raw_text,
        doc_type=doc_type,
        current_metadata=current_metadata,
    )
