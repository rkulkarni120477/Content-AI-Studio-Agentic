# Phase 7 — AIM Topic Source Library Filter Approval Record

**Status:** Approval recording only — no implementation authorized by this document.  
**Approval-recording date:** 2026-08-31  
**Discovery document:** `docs/phase7-topic-filter-discovery.md`

---

## 1. Baseline

| Item | Value |
|---|---|
| **branch** | `feature/dis_metadata` |
| **HEAD at approval recording** | `25088bf4fd5a1c0a0c50256898db8db2f162e6fd` |
| **Phase 7 discovery commit** | `25088bf4fd5a1c0a0c50256898db8db2f162e6fd` |
| **Discovery document** | `docs/phase7-topic-filter-discovery.md` |
| **working_tree at start** | CLEAN |

This record is based on `docs/phase7-topic-filter-discovery.md` (Phase 7 discovery).

---

## 2. Decision Table

| ID | Question (short) | Decision |
|---|---|---|
| **DECISION-001** | AIM topic via AIM tenant Field Registry overlay? | **APPROVE** |
| **DECISION-002** | Which promote targets? | **APPROVE** — `index`, `filter_options`, `retrieval` (no `cas_list`) |
| **DECISION-003** | Keep topic outside `DEFAULT_REGISTRY`? | **APPROVE** |
| **DECISION-004** | Legacy `_RETRIEVAL_LEGACY_EXACT` topic handling? | **APPROVE** — Option A (promote retrieval + remove legacy) |
| **DECISION-005** | Remove topic from `DEFERRED_TAXONOMY_KEYS`? | **APPROVE** |
| **DECISION-006** | Topic UI control type? | **APPROVE** — keep `type: text` |
| **DECISION-007** | Source Library matching behavior? | **APPROVE** — case-insensitive exact match |
| **DECISION-008** | Source Library UI treatment of `"all"`? | **APPROVE** — `"all"` = no topic constraint at UI/filtering layer |
| **DECISION-009** | Compact S3 source-index backfill required? | **APPROVE** |
| **DECISION-010** | Keep existing metadata.topic sourcing? | **APPROVE** |
| **DECISION-011** | Topic upload-collectable in Phase 7? | **REJECT** |
| **DECISION-012** | Cengage Topic filtering? | **REJECT** |
| **DECISION-013** | OpenSearch changes? | **REJECT** |
| **DECISION-014** | Add topic to `DEFAULT_REGISTRY`? | **REJECT** |
| **DECISION-015** | New filtering architecture? | **REJECT** |

### DECISION-001

**Question:** Should AIM topic become a normal Source Library filter through an AIM tenant-specific Field Registry overlay?

**DECISION-001: APPROVE**

Yes. Topic must be configured only for AIM.

Do **NOT** add topic to `DEFAULT_REGISTRY`.

---

### DECISION-002

**Question:** Which Field Registry promotion targets should AIM topic receive?

**DECISION-002: APPROVE**

```
promote:
  - index
  - filter_options
  - retrieval
```

Do **NOT** add `cas_list` unless a concrete CAS list requirement is identified.

---

### DECISION-003

**Question:** Should topic remain outside `DEFAULT_REGISTRY`?

**DECISION-003: APPROVE**

Yes. The implementation must use AIM tenant YAML configuration only. Other tenants must remain unchanged.

---

### DECISION-004

**Question:** How should Phase 6A's legacy topic retrieval exception be handled?

**DECISION-004: APPROVE** — Option A

Promote topic to `PROMOTE_RETRIEVAL` and remove `_RETRIEVAL_LEGACY_EXACT` topic handling.

The Field Registry becomes the normal retrieval authority for AIM topic. No duplicate legacy topic gate should remain after migration.

---

### DECISION-005

**Question:** Should topic be removed from the frontend deferred taxonomy list?

**DECISION-005: APPROVE**

Yes. Remove topic from `DEFERRED_TAXONOMY_KEYS`. The existing config-driven taxonomy UI should render Topic. No new frontend filtering architecture.

---

### DECISION-006

**Question:** What UI control should Topic use?

**DECISION-006: APPROVE**

Keep Topic as `type: text`. Do not introduce a select/control redesign in Phase 7.

---

### DECISION-007

**Question:** What matching behavior should Source Library Topic filtering use?

**DECISION-007: APPROVE**

Case-insensitive exact matching.

Do **not** introduce fuzzy matching.  
Do **not** introduce substring matching.  
Do **not** introduce search semantics.

---

### DECISION-008

**Question:** How should `"all"` be treated by the Source Library UI?

**DECISION-008: APPROVE**

`"all"` means **NO TOPIC CONSTRAINT** at the UI/filtering layer.

Do not send `"all"` as a literal topic constraint when the user means “all topics”.

Existing literal exact-match semantics must remain available at the lower-level retrieval layer if explicitly supplied by an internal caller, but normal Source Library UI behavior must treat `"all"` as unconstrained.

Do not change unrelated wildcard behavior.

---

### DECISION-009

**Question:** Is a compact S3 source-index backfill/rewrite required?

**DECISION-009: APPROVE**

Yes. Existing compact source records do not contain topic.

The implementation must include a safe backfill/rebuild mechanism or an explicitly documented operational backfill step.

Do **NOT** modify OpenSearch.  
Do **NOT** modify DB schema.  
Do **NOT** invent a new storage system.

---

### DECISION-010

**Question:** Should existing AIM `metadata.topic` remain sourced from existing metadata extraction/profile inference?

**DECISION-010: APPROVE**

Yes.

Do not redesign metadata extraction.  
Do not redesign AIM transforms.  
Do not change `metadata_schemas`.  
Do not change how topic is generated.

---

### DECISION-011

**Question:** Should Topic become upload-collectable in Phase 7?

**DECISION-011: REJECT**

Do not add topic to `retrieval.source_ui.upload`. Topic remains inferred/extracted.

---

### DECISION-012

**Question:** Should Cengage receive Topic filtering?

**DECISION-012: REJECT**

Cengage remains unchanged unless separately configured in a future decision.

---

### DECISION-013

**Question:** Should OpenSearch be changed?

**DECISION-013: REJECT**

Source Library Topic filtering remains on the existing S3 compact-index + Python path.

---

### DECISION-014

**Question:** Should Topic be added to `DEFAULT_REGISTRY`?

**DECISION-014: REJECT**

AIM-only configuration is required.

---

### DECISION-015

**Question:** Should a new filtering architecture be introduced?

**DECISION-015: REJECT**

Use the existing:

- Field Registry
- Phase 3 dynamic API forwarding
- existing `source_filter_options`
- existing `_source_matches`
- existing frontend taxonomy filter infrastructure

---

## 3. Approved Architecture

```
AIM metadata.topic
        ↓
AIM tenant metadata_framework.fields.topic
        ↓
Field Registry
        ├── index
        ├── filter_options
        └── retrieval
        ↓
S3 compact source index
        ↓
Source Library
        ↓
Topic filter

Frontend:

retrieval.source_ui.taxonomy_filters
        ↓
Topic (type: text)
        ↓
existing dynamic filter API
```

No new filtering architecture.

**Architectural acceptance**

| Concern | Approved approach |
|---|---|
| Tenant scope | AIM YAML `metadata_framework.fields.topic` only |
| Default registry | Unchanged — no `topic` |
| Index | Compact S3 record carries `topic` |
| Filter options / listing match | `PROMOTE_FILTER_OPTIONS` + existing `_source_matches` |
| Retrieval | `PROMOTE_RETRIEVAL`; remove `_RETRIEVAL_LEGACY_EXACT` topic |
| Frontend | Undeffer `topic`; keep `type: text` |
| Matching | Case-insensitive exact |
| UI `"all"` | No topic constraint (do not send literal `"all"`) |
| Cengage | Unchanged |
| OpenSearch | Unchanged / out of scope |

---

## 4. Explicit Non-Goals

Do **NOT** implement:

- `DEFAULT_REGISTRY` topic promotion
- Cengage topic promotion
- upload topic collection
- OpenSearch changes
- metadata schema redesign
- AIM transform redesign
- TaxonomyRegistry
- new filter engine
- new storage system
- fuzzy topic search
- substring topic search
- topic vocabulary redesign
- PipelineState changes
- validation hard-fail
- provisioning redesign
- `cas_list` promotion (unless a concrete CAS list requirement is identified later)

---

## 5. Backfill Requirement

**DECISION-009: APPROVE** — compact S3 source-index backfill/rewrite is required.

| Item | Requirement |
|---|---|
| Why | Existing compact source records do not contain `topic` |
| Source of truth | Existing AIM `metadata.topic` (extraction / profile inference) |
| Mechanism | Safe backfill/rebuild **or** explicitly documented operational backfill step |
| OpenSearch | Do **not** modify |
| DB schema | Do **not** modify |
| New storage | Do **not** invent |

---

## 6. Compatibility Requirements

| Scenario | Expected behavior |
|---|---|
| AIM after Phase 7 | Topic is a normal Source Library filter via tenant registry overlay |
| Cengage / default tenants | Unchanged; `topic=` unauthorized unless separately configured later |
| Empty / absent `metadata_framework` (non-AIM or pre-overlay) | Continue using `DEFAULT_REGISTRY` without `topic` |
| Source Library matching | Case-insensitive exact match via existing `_source_matches` path |
| Source Library UI `"all"` | Treated as unconstrained; do not send literal `"all"` as a topic filter |
| Lower-level retrieval | Literal exact-match remains available if an internal caller explicitly supplies a topic value (including literal `"all"` if deliberately sent) |
| Frontend | Config-driven taxonomy UI; no new filter architecture |
| Topic generation | Unchanged extraction / AIM profile inference |
| Upload | Topic not collectable |
| Phase 6A legacy topic gate | Removed after `PROMOTE_RETRIEVAL` migration (Option A) |
| Phase 3 API forwarding | Unchanged; authorizes `topic=` only when AIM registry promotes `filter_options` |

---

## 7. Implementation Gate

**NO IMPLEMENTATION IS AUTHORIZED BY THIS COMMIT.**

This approval record only records architectural decisions. A separate Phase 7 implementation prompt must be issued after this approval record is reviewed.

### Authorized future implementation (when explicitly instructed)

**PHASE 7: AIM Topic Source Library Filter**

Scope outline (recording only — not authorization to start now):

1. AIM YAML `metadata_framework.fields.topic` with `promote: [index, filter_options, retrieval]`
2. Compact S3 source-index backfill/rewrite (or documented operational step)
3. Remove frontend `DEFERRED_TAXONOMY_KEYS` entry for `topic`
4. Remove `_RETRIEVAL_LEGACY_EXACT` topic handling after registry retrieval promotion
5. Tests covering registry promotion, tenant isolation, filter options, matching, API forwarding, frontend rendering, backfill/empty behavior, and legacy retrieval removal

Do **not** start implementation until an explicit implementation instruction is issued.

---

## Decision Summary

| ID | Decision |
|---|---|
| DECISION-001 | **APPROVE** — AIM tenant Field Registry overlay |
| DECISION-002 | **APPROVE** — `index` + `filter_options` + `retrieval` (no `cas_list`) |
| DECISION-003 | **APPROVE** — outside `DEFAULT_REGISTRY` |
| DECISION-004 | **APPROVE** — Option A (remove legacy topic exception) |
| DECISION-005 | **APPROVE** — undeffer frontend `topic` |
| DECISION-006 | **APPROVE** — keep `type: text` |
| DECISION-007 | **APPROVE** — case-insensitive exact match |
| DECISION-008 | **APPROVE** — UI `"all"` = unconstrained |
| DECISION-009 | **APPROVE** — compact-index backfill required |
| DECISION-010 | **APPROVE** — keep existing topic sourcing |
| DECISION-011 | **REJECT** — no upload topic |
| DECISION-012 | **REJECT** — no Cengage Topic |
| DECISION-013 | **REJECT** — no OpenSearch changes |
| DECISION-014 | **REJECT** — no `DEFAULT_REGISTRY` topic |
| DECISION-015 | **REJECT** — no new filtering architecture |

---

## Document Control

| Version | Date | Notes |
|---|---|---|
| 1.0 | 2026-08-31 | Initial Phase 7 approval record |
