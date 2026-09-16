# Phase 6A — Registry-Complete Retrieval Gating Approval Record

**Status:** Approval recording only — no implementation authorized by this document.  
**Approval-recording date:** 2026-08-31

---

## Baseline

| Item | Value |
|---|---|
| **branch** | `feature/dis_metadata` |
| **HEAD at approval recording** | `210723c38ec5beedb55425d4f337d7b2faafce2e` |
| **Phase 5 implementation commit** | `91000cc7674134f1998d6f6ffa1600cf1d3f002d` |
| **Phase 6 discovery commit** | `210723c38ec5beedb55425d4f337d7b2faafce2e` |
| **Discovery document** | `docs/phase6-metadata-architecture-discovery.md` |
| **working_tree at start** | CLEAN |

This record is based on `docs/phase6-metadata-architecture-discovery.md` (Phase 6 discovery).

---

## Decision Record

### DECISION-001

**Question:** Should AIM `topic` be added to `DEFAULT_REGISTRY` index/filter promotion now?

**Discovery context:** `topic` is AIM schema-required, present in taxonomy filters and hardcoded `_passes_filters exact_fields`, but not registry-promoted. Discovery recommended deferring until tenant registry overrides are authored.

**Stakeholder decision:**

**DECISION-001: REJECT FOR PHASE 6A**

Defer topic promotion to a separate future decision.

**Do NOT in Phase 6A:**

- add `topic` to `DEFAULT_REGISTRY`
- modify topic indexing
- modify topic filters
- modify AIM topic configuration
- modify AIM transforms

---

### DECISION-002

**Question:** Should Phase 6A migrate exact-match retrieval fields only, or also boolean fields?

**Discovery context:** `_passes_filters` uses hardcoded `exact_fields` and separate `bool_fields`. Discovery recommended migrating exact fields only; leaving bool fields as permanent business rules.

**Stakeholder decision:**

**DECISION-002: APPROVE — EXACT_FIELDS ONLY**

Phase 6A will migrate the metadata exact-match field selection in `_passes_filters` to use:

```
registry_for_tenant(current tenant)
    →
registry.for_promote(PROMOTE_RETRIEVAL)
    →
exact-match metadata fields
```

Boolean fields and operational/business-rule logic remain unchanged.

**Do NOT migrate boolean fields in Phase 6A.**

---

### DECISION-003

**Question:** Is metadata configuration template provisioning required before Phase 6A?

**Discovery context:** `dis_provisioning.py` creates minimal tenant YAML without metadata templates. Existing clients (AIM, Cengage, Academian) are manually configured.

**Stakeholder decision:**

**DECISION-003: REJECT FOR PHASE 6A**

Existing manually configured tenants are sufficient.

**Do NOT modify:**

- provisioning
- tenant creation
- config templates
- onboarding

Provisioning remains future/optional work.

---

### DECISION-004

**Question:** Should OpenSearch receive registry-driven top-level metadata promotion now?

**Discovery context:** Field Registry drives S3 source index; OpenSearch uses a separate hardcoded top-level subset plus dynamic `metadata.*`. Discovery verdict: NOT REQUIRED NOW.

**Stakeholder decision:**

**DECISION-004: REJECT FOR PHASE 6A**

Current architecture does not require OpenSearch changes to complete metadata framework retrieval gating.

**Do NOT modify:**

- OpenSearch mappings
- templates
- indexing
- queries
- reindexing
- provisioning

Revisit only if a measured production requirement emerges.

---

## Phase 6A Approved Implementation Scope

**PHASE 6A — Registry-Complete Retrieval Gating**

### Objective

Remove the remaining hardcoded metadata exact-match field selection from `_passes_filters` and make it tenant-registry-driven.

### Implementation target

```
_passes_filters
    →
registry_for_tenant(current tenant)
    →
registry.for_promote(PROMOTE_RETRIEVAL)
    →
exact-match metadata fields
```

Preserve all existing business-rule exceptions documented below.

### Architectural acceptance

**BEFORE:**

```
_passes_filters
    ↓
hardcoded metadata field list
```

**AFTER:**

```
_passes_filters
    ↓
registry_for_tenant()
    ↓
PROMOTE_RETRIEVAL
    ↓
registry-defined exact fields
```

while:

```
business rules
    ↓
remain explicit and unchanged
```

---

## Business Rules That Must Remain Unchanged

The implementation must **NOT** migrate or alter:

- purpose gates
- day/calendar normalization logic
- course sentinel behavior (`course_scope_matches`, `GLOBAL_COURSE_ID = "-1"`)
- boolean operational flags (`use_for_style`, `use_for_cdd`, `use_for_blueprint`, `use_for_course_generation`, `is_generation_candidate`, `restricted`, `calendar_mapping_required`, `is_archive_or_working_version`)
- search behavior
- `same_block` behavior
- other explicitly documented normalization/business rules in `_passes_filters`

The registry replaces **only** the metadata exact-field list.

---

## Tenant Dynamic Field Test Requirement

Approve a test using a synthetic field: **`test_metadata_field`**

The field must:

- exist only in tenant `metadata_framework.fields`
- **NOT** exist in `DEFAULT_REGISTRY`

The test must prove:

| Tenant | Expected behavior |
|---|---|
| **Tenant A** | field is promoted for retrieval → `_passes_filters` can match it |
| **Default/other tenant** | field is not in registry → unknown field does not unexpectedly become globally active |

---

## Backward Compatibility Requirements

| Scenario | Expected behavior |
|---|---|
| Empty or absent `metadata_framework` | Continue using `DEFAULT_REGISTRY` |
| Existing retrieval behavior | Unchanged for all current clients |
| Existing hardcoded fields | Continue matching through the default registry field set |

---

## Characterization Test Requirement

Before implementation, lock current `_passes_filters` behavior.

Tests must cover at minimum:

- empty filters
- exact field match
- case-insensitive match (if currently supported)
- mismatch
- missing metadata
- `module` / `module_name` compatibility
- `day` / `day_number` compatibility
- block / `same_block` behavior
- purpose behavior
- course sentinel
- boolean fields
- existing operational filters

Do not weaken existing tests.

---

## Explicit Non-Goals

Phase 6A **MUST NOT** include:

- OpenSearch changes
- TaxonomyRegistry
- Provisioning changes
- PipelineState changes
- `metadata_schemas` redesign
- upload UI changes
- frontend changes
- AIM topic promotion
- `acs_codes` changes
- schema versioning
- metadata provenance
- AIM transform changes
- Cengage transform changes
- boolean registry migration
- hard-fail validation

---

## Acceptance Criteria (from Phase 6 Discovery)

1. `_passes_filters` exact-match field set is derived from `registry_for_tenant(tenant_cfg).for_promote(PROMOTE_RETRIEVAL)` with an explicit, tested allowlist of business-rule fields that remain hardcoded
2. Characterization test proves a synthetic tenant `metadata_framework` field promoted to `retrieval` participates in retrieval gating
3. Existing AIM/Cengage retrieval behavior unchanged when `metadata_framework` is empty (`DEFAULT_REGISTRY`)
4. No changes to OpenSearch, `metadata_schemas`, upload UI, CAS, pipeline agents, or tenant YAML
5. Phase 1–3 characterization tests continue to pass

---

## Implementation Gate

**NO IMPLEMENTATION IS AUTHORIZED BY THIS COMMIT.**

This approval record only records architectural decisions. A separate Phase 6A implementation prompt must be issued after this approval record is reviewed.

### Authorized future implementation

**PHASE 6A: Registry-Complete Retrieval Gating — exact-match fields only.**

---

## Decision Summary

| ID | Decision |
|---|---|
| DECISION-001 | REJECT FOR PHASE 6A — defer topic promotion |
| DECISION-002 | APPROVE — exact_fields only |
| DECISION-003 | REJECT FOR PHASE 6A — provisioning not required |
| DECISION-004 | REJECT FOR PHASE 6A — OpenSearch not required |

---

## Document Control

| Version | Date | Notes |
|---|---|---|
| 1.0 | 2026-08-31 | Initial Phase 6A approval record |
