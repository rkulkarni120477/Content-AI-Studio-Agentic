"""CurriculumProfile — the per-tenant strategy the shared digest pipeline calls
so it never sees tenant specifics (plan §5.5 / decision D8).

The block-wide enumerate→map→reduce→verify orchestration, caching, attribution,
flag assembly, security gates and observability are 100% shared and
tenant-agnostic. Everything that is genuinely tenant-shaped — *where* the ordered
coverage units come from (AIM: calendar Days; Cengage: Modules), *what* the
declared coverage set is (AIM: ACS codes; Cengage: none), and the digest/reduce
schema extensions — lives behind this seam.

**Hard rule (plan §5.5):** no ``dis_calendar_days``-direct or
``declared_acs_set``-direct access may live in the shared pipeline; both go
through a profile, even while AIM is the only full implementation. That is what
prevents day/ACS calcification when a second tenant is added — a new tenant is
"add a profile + templates," never "refactor the pipeline."

AIM ships as ``CurriculumProfile`` #1 (``aim.py``). Tenants whose enumeration
source / coverage set are still undefined (Cengage, Academian) get the base
implementation, which raises a clear "not configured" error rather than silently
mis-enumerating — the design is *ready*, not *works-today*, for them.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List


@dataclass
class ScopeData:
    """The tenant-specific inventory the shared assembler needs. ``days`` is the
    generic name for the ordered coverage units (Days for AIM, Modules/Sections
    for a module-based tenant); each is a dict carrying at least ``day_number``,
    ``topic``, and the item/assessment fields attribution consumes."""

    calendar_id: str
    total_days: int
    days: List[Dict[str, Any]] = field(default_factory=list)
    units: List[Dict[str, Any]] = field(default_factory=list)
    duplicate_calendar_ids: List[str] = field(default_factory=list)
    #: Profile-level observations about the scope it just read, surfaced verbatim
    #: as enumerate flags (e.g. a block whose stored tags disagree on spelling and
    #: were merged by key). A profile that has nothing to say leaves this empty.
    notes: List[str] = field(default_factory=list)


class CurriculumProfile:
    """Base profile. Subclasses implement the tenant specifics; the shared
    pipeline only ever calls these methods, never tenant tables directly."""

    #: What VERIFY reconciles, surfaced in telemetry (coverage_semantics()).
    coverage_label: str = "coverage"

    def __init__(self, tenant_cfg: Any):
        self.tenant_cfg = tenant_cfg

    # -- enumerate(scope) -----------------------------------------------------
    def load_scope(self, cur, schema: str, client_id: str, block: str) -> ScopeData:
        """Read the ordered coverage units + in-scope content units for a block.

        SELECT-only (the caller opens a read-only connection). Must raise
        ``LookupError`` when the block/scope does not exist, and
        ``RuntimeError`` when the tenant's enumeration source is not defined.
        """
        # Names the client and block it was actually asked for. Without them this
        # reads as "some tenant is misconfigured" and gives the reader no way to see
        # that the request simply arrived under the wrong client — which is how it
        # reaches this branch in practice.
        raise RuntimeError(
            f"Curriculum enumeration is not configured for client "
            f"{(client_id or '?')!r} (block={block!r}, profile={type(self).__name__}). "
            f"Either the request reached the wrong client, or this one needs a "
            f"CurriculumProfile (plan §5.5 / D8) before its digest pipeline is enabled."
        )

    # -- coverage_semantics() -------------------------------------------------
    def coverage_codes(self, unit: Dict[str, Any]) -> List[str]:
        """The declared-coverage-set elements this unit contributes (AIM: ACS
        codes). Tenants with no coverage set (e.g. a textbook) return []."""
        return []

    # -- digest_schema() ------------------------------------------------------
    def digest_extension(self) -> Dict[str, Any]:
        """Tenant-specific fields layered onto the generic digest core (e.g. AIM
        ``high_miss_acs``; Cengage ``chapter``/``section``). Empty by default."""
        return {}

    # -- deliverable_spec() ---------------------------------------------------
    def deliverable_spec(self, deliverable: str) -> Dict[str, Any]:
        """Worksheet/section structure hints for REDUCE. Empty ⇒ the shared
        deterministic day-anchored skeleton is used as-is."""
        return {}
