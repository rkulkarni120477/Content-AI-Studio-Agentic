"""Base client profile.

Profiles are intentionally small adapters. The DIS pipeline remains common;
profiles only enrich metadata using client-specific config/rules.
"""
from __future__ import annotations
from typing import Any, Dict

from config.settings import TenantConfig


class BaseClientProfile:
    def __init__(self, tenant_cfg: TenantConfig):
        self.tenant_cfg = tenant_cfg
        self.rules: Dict[str, Any] = getattr(tenant_cfg, "client_rules", {}) or {}

    def enrich_metadata(self, filename: str, source_relative_path: str, raw_text: str, doc_type: str, current_metadata: Dict[str, Any]) -> Dict[str, Any]:
        return dict(current_metadata or {})
