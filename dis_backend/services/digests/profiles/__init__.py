"""CurriculumProfile registry (plan §5.5 / D8).

Selection is config-driven: the tenant's ``structure_store.profile`` (or the
client id) picks the profile; the shared digest pipeline calls
``get_curriculum_profile`` and never branches on tenant itself. AIM is profile #1;
every other tenant currently gets the base profile, which raises a clear
"not configured" error rather than mis-enumerating (design-ready, not
works-today — see §5.5 caveat).
"""
from __future__ import annotations

from typing import Any

from services.digests.profiles.base import CurriculumProfile, ScopeData
from services.digests.profiles.aim import AIMCurriculumProfile

__all__ = ["CurriculumProfile", "ScopeData", "get_curriculum_profile"]


def get_curriculum_profile(client_id: str, tenant_cfg: Any) -> CurriculumProfile:
    """Resolve the CurriculumProfile for a tenant.

    Prefers an explicit ``structure_store.profile`` marker, else falls back to the
    client id. Unknown/undefined tenants get the base profile (raises on
    ``load_scope`` — honest failure, no silent mis-enumeration).
    """
    store = getattr(tenant_cfg, "structure_store", None)
    marker = (getattr(store, "curriculum_profile", "") or "").strip().lower()
    cid = (client_id or "").strip().lower()
    if marker == "aim" or cid == "aim":
        return AIMCurriculumProfile(tenant_cfg)
    return CurriculumProfile(tenant_cfg)
