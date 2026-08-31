# Phase 5 — Upload Metadata Approval Record

**Status:** Approval recording only — no implementation authorized by this document.  
**Approval-recording date:** 2026-08-31

---

## Baseline

| Item | Value |
|---|---|
| **branch** | `feature/dis_metadata` |
| **HEAD at approval recording** | `3838b2537cd525ded45e67c1c1d64721fba99164` |
| **Phase 4 commit** | `14fd727b73ca7d24f4dec858e8b728360867d5bd` |
| **Phase 5 discovery commit** | `3838b2537cd525ded45e67c1c1d64721fba99164` |
| **Discovery document** | `docs/phase5-upload-metadata-discovery.md` |
| **working_tree at start** | CLEAN |

This record is based on `docs/phase5-upload-metadata-discovery.md` (Phase 5 discovery).

---

## Decision Record

### DECISION-001

**Question:** Where should upload UI configuration live in tenant YAML?

**Discovery recommendation:** Option B′ — dedicated upload UI configuration under `retrieval.source_ui`, parallel to `retrieval.source_ui.taxonomy_filters`, referencing `metadata_schemas` field names.

**Stakeholder decision:**

**DECISION-001: APPROVE**

Upload UI configuration will live under `retrieval.source_ui` (not inside Field Registry, not as a frontend-only transformation of `metadata_schemas`).

---

### DECISION-002

**Question:** Which fields are collectable by the upload UI versus system-derived?

**Discovery context:** Upload currently involves `purpose`, `document_type`, `project_id`, `course_id`, folder/path information, taxonomy hint fields (`block`, `day`, `chapter`, etc.), and other metadata accepted downstream. Some `metadata_schemas` required fields (e.g. `file_sha256`, `content_hash`) are pipeline-generated and unsuitable for user entry.

**Stakeholder decision:**

**DECISION-002: APPROVE**

The upload UI may collect tenant/user-entered metadata fields explicitly designated as upload-collectable.

System-derived fields must remain system-derived and must NOT be exposed as mandatory user-entry fields merely because they appear in `metadata_schemas`.

In particular, the following categories remain system-derived:

- `file_sha256` and similar hash/digest fields
- Derived course/block information (e.g. CAS `_block_from_course()`)
- Storage/path-derived information (`source_relative_path`, `source_root`, S3 keys)
- Other pipeline-generated metadata

---

### DECISION-003

**Question:** Should metadata validation remain report-only?

**Discovery recommendation:** Keep Phase 4 behavior unchanged.

**Stakeholder decision:**

**DECISION-003: APPROVE**

`metadata_schemas` validation remains report-only. Invalid or missing metadata must not automatically hard-fail ingestion as part of this architectural direction. Phase 4 validation behavior is unchanged.

---

### DECISION-004

**Question:** Should `document_type` become schema-driven?

**Discovery context:** Current duplication between YAML-defined document type enums (`metadata_schemas`) and frontend free-text `document_type` input.

**Stakeholder decision:**

**DECISION-004: APPROVE**

Where tenant `metadata_schemas` provides an explicit controlled set of `document_type` values, the upload UI should use those values as the selectable options.

The architecture must retain a safe fallback for tenants/configurations where no controlled `document_type` values are defined.

---

### DECISION-005

**Question:** How should `purpose` be handled?

**Discovery context:** `purpose` is currently a structural upload field with hardcoded frontend options; it is not a `metadata_schemas` field.

**Stakeholder decision:**

**DECISION-005: APPROVE**

Keep `purpose` as a structural upload field. Tenant configuration may provide presentation/labeling behavior where appropriate (e.g. `purpose_labels`), but `purpose` must NOT be treated as a generic `metadata_schemas` field.

---

### DECISION-006

**Question:** What is the relationship between upload fields and retrieval/filter taxonomy configuration?

**Discovery context:** Taxonomy concepts appear across CAS upload form, DIS `metadata_hints`, Field Registry, and `retrieval.source_ui.taxonomy_filters`.

**Stakeholder decision:**

**DECISION-006: APPROVE**

Upload UI configuration and retrieval/filter UI configuration are related but separate concerns.

They may reference the same canonical metadata field names, but upload configuration must NOT be implemented by reusing or mutating Field Registry configuration.

The following must NOT be merged into a single registry:

- upload UI config
- `taxonomy_filters`
- Field Registry

---

### DECISION-007

**Question:** Should CAS continue hardcoding `visibility: "internal"`?

**Discovery context:** CAS currently sets `"visibility": "internal"` in `form_fields` when forwarding uploads to DIS.

**Stakeholder decision:**

**DECISION-007: APPROVE**

Leave CAS visibility behavior unchanged for this phase. Do not expose `visibility` as a new tenant-configurable upload field unless explicitly approved in a later decision.

---

### DECISION-008

**Question:** Should `metadata_schemas.applies_to` be implemented/enforced now?

**Discovery recommendation:** No — keep deferred (Phase 4 established no enforcement).

**Stakeholder decision:**

**DECISION-008: APPROVE**

Keep `applies_to` deferred. Do not introduce new `applies_to` semantics. Do not enforce it in upload UI. Do not enforce it in validation. Phase 4 behavior is unchanged.

---

## Architectural Boundaries (Preserved)

### metadata_schemas

**Purpose:** Tenant/client domain metadata definition and validation.

**Boundary:** NOT automatically a frontend form schema.

### retrieval.source_ui

**Purpose:** Presentation/configuration of UI behavior.

**Boundary:** Upload UI configuration may be added here in a future implementation phase (per DECISION-001).

### Field Registry

**Purpose:** Canonical projection/index/filter/listing metadata behavior.

**Boundary:** Remains separate from `metadata_schemas` (no merge).

### Phase 4 validation

**Behavior:** REPORT-ONLY.

**Boundary:** No ingestion hard-fail is introduced by this approval record.

---

## Approved Future Architecture

```
metadata_schemas
    |
    | defines domain metadata fields
    v
tenant metadata model

retrieval.source_ui
    |
    | defines upload presentation/control behavior
    v
upload UI

Field Registry
    |
    | independent canonical projection/filter/listing concern
    v
search / index / retrieval
```

Future upload implementation may allow:

```
retrieval.source_ui.upload
        |
        | references field names
        v
metadata_schemas
```

Future upload implementation must NOT allow:

```
metadata_schemas  -->  Field Registry          (merge forbidden)
metadata_schemas  -->  automatic frontend form (without explicit UI configuration)
```

---

## Future Implementation Scope

The **next** implementation phase may address:

### IN SCOPE

1. Define the approved upload UI configuration structure under `retrieval.source_ui`.
2. Add tenant YAML configuration for upload fields.
3. Build schema/config-driven upload metadata UI.
4. Connect configured fields to existing metadata submission paths (`metadata_hints` / CAS Form).
5. Use `metadata_schemas` controlled values for `document_type` where applicable (with fallback).
6. Preserve system-derived metadata (per DECISION-002).
7. Preserve Phase 4 report-only validation (per DECISION-003).
8. Preserve Field Registry separation (per DECISION-006).
9. Add appropriate backend/frontend tests.

### OUT OF SCOPE

- Field Registry redesign
- Field Registry merge with `metadata_schemas`
- OpenSearch redesign
- Reindexing
- Ingestion hard-fail
- `applies_to` enforcement
- CAS visibility redesign
- AIM transform changes
- Cengage transform changes
- Unrelated metadata refactoring
- Unrelated frontend redesign

---

## Implementation Gate

**No implementation is authorized by this commit.**

This approval record only records architectural decisions. A separate implementation prompt/phase must be created after this approval record is reviewed.

---

## Decision Summary

| ID | Decision |
|---|---|
| DECISION-001 | APPROVE |
| DECISION-002 | APPROVE |
| DECISION-003 | APPROVE |
| DECISION-004 | APPROVE |
| DECISION-005 | APPROVE |
| DECISION-006 | APPROVE |
| DECISION-007 | APPROVE |
| DECISION-008 | APPROVE |
