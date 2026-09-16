# Phase 6 — Metadata Architecture Discovery

**Status:** Discovery / verification only — no implementation in this phase.  
**Branch baseline:** `feature/dis_metadata` @ `91000cc7674134f1998d6f6ffa1600cf1d3f002d` (Phase 5 complete).  
**Investigation date:** 2026-08-31.

---

## 1. Baseline

| Check | Result |
|---|---|
| **branch** | `feature/dis_metadata` |
| **HEAD** | `91000cc7674134f1998d6f6ffa1600cf1d3f002d` |
| **working tree (start)** | CLEAN |
| **Phase 5 commit present** | YES — `91000cc feat: add schema driven upload metadata` |
| **Remote `feature/dis_metadata`** | `91000cc7674134f1998d6f6ffa1600cf1d3f002d` (matches local HEAD) |

**FACT:** All Phase 0–5 commits are present on the branch in order:

```
91000cc feat: add schema driven upload metadata          (Phase 5)
7528dba docs: record phase 5 upload metadata approvals
3838b25 docs: add phase 5 upload metadata discovery
14fd727 feat: add metadata schema validation report      (Phase 4)
a6387bf feat: generalize source library filter api       (Phase 3)
f550b0d feat: complete registry-driven source filtering    (Phase 2)
7e10421 fix: wire tenant metadata registry to consumers
2128e78 feat: add DIS metadata Field Registry (phase 1)
255b4ff feat: implement metadata framework phase 0
```

---

## 2. Phase 0–5 Status Matrix

| Capability | Original Requirement | Current Status | Implemented In | Remaining Work | Recommendation |
|---|---|---|---|---|---|
| metadata framework configuration | Tenant YAML `metadata_framework` survives load; optional per-tenant field overrides | **Implemented** — passthrough in `TenantConfig`; empty block falls back to defaults | Phase 0 (`255b4ff`) | No production client YAML defines `metadata_framework` yet | **Optional future** — tenant YAML authoring when needed |
| tenant config passthrough | Client YAML → `TenantConfig` without discarding framework blocks | **Implemented** | Phase 0 | None | **Complete** |
| Field Registry | Central definition of metadata field promotion targets | **Implemented** — `FieldRegistry`, `DEFAULT_FIELDS`, `registry_for_tenant()` | Phase 1 (`2128e78`) | Retrieval gating not fully registry-driven | **Phase 6A candidate** |
| default registry fallback | Empty/missing `metadata_framework` → `DEFAULT_REGISTRY` | **Implemented** | Phase 1 | None | **Complete** |
| registry adapters | `project_index/retrieval/filter_options/cas_list` | **Implemented** | Phase 1 | None | **Complete** |
| index promotion | Registry fields written to S3 compact source records | **Implemented** — `compact_source_record` → `project_index_metadata` | Phase 1–2 | None for framework scope | **Complete** |
| retrieval promotion | Registry fields rebuilt in `_metadata_from_record` | **Implemented** | Phase 1–2 | `_passes_filters` still hardcoded | **Phase 6A candidate** |
| filter_options promotion | Dropdown maps + listing filter matching | **Implemented** — `source_filter_options`, `_source_matches` | Phase 1–2 | `/context/sources` lacks Phase 3 dynamic passthrough | **Optional future** |
| cas_list promotion | Source Library listing taxonomy columns | **Implemented** — `list_sources` → `project_cas_list_taxonomy` | Phase 1–2 | None | **Complete** |
| source filtering | Registry-driven `_source_matches` for Source Library | **Implemented** | Phase 2 (`f550b0d`) | Compat fields `lesson_name`, `visibility` remain hardcoded | **Acceptable compat** |
| dynamic API filter forwarding | Registry-authorized extra query params on documents/library | **Implemented** | Phase 3 (`a6387bf`) | Not on `/context/sources` endpoint | **Optional future** |
| frontend taxonomy filters | Config-driven filter sidebar from ui-config | **Implemented** — `taxonomyFilters.js` | Phase 0 | None | **Complete** |
| metadata_schemas | Per-client domain field definitions in tenant YAML | **Implemented** — `MetadataSchema`, `get_metadata_schema()` | Pre-existing + Phase 4 wiring | No schema versioning | **Future governance** |
| metadata validation | Validate extracted metadata against schema | **Implemented** — report-only findings | Phase 4 (`14fd727`) | No type/`applies_to` enforcement | **By design (DECISION-003/008)** |
| report-only validation | Non-blocking pipeline findings | **Implemented** — `validation_report_agent` | Phase 4 | None | **Complete** |
| upload UI configuration | Separate presentation config under `retrieval.source_ui` | **Implemented** — `upload_ui_config.py`, AIM YAML | Phase 5 (`91000cc`) | Cengage has no upload block | **Optional tenant YAML** |
| dynamic upload fields | Frontend renders config-driven fields | **Implemented** — `uploadFields.js`, `SourceLibraryPage.jsx` | Phase 5 | CAS Form params fixed list | **Future if new fields added** |
| document_type controlled values | Schema enum → select when defined | **Implemented** — `resolve_document_type_upload()` | Phase 5 | Free-text fallback when no enum | **Complete per DECISION-004** |
| purpose | Structural upload routing field | **Implemented** — hardcoded values, tenant `purpose_labels` | Pre-existing + Phase 5 preserved | Not schema-driven | **Complete per DECISION-005** |
| system-derived metadata | Pipeline/CAS-derived fields not in upload UI | **Implemented** — `SYSTEM_DERIVED_FIELD_KEYS` | Phase 5 | None | **Complete per DECISION-002** |
| tenant isolation | Per-tenant YAML, client_id routing | **Implemented** | Pre-existing | Provisioning creates empty YAML | **See provisioning** |
| AIM configuration | AIM YAML schemas, content rules, source_ui | **Implemented** — `aim.yaml`, `AIMClientProfile` | Pre-existing | Upload fields ⊄ full schema | **Acceptable gap** |
| Cengage configuration | Cengage YAML schemas | **Implemented** — `cengage.yaml` | Pre-existing | No upload UI config | **Optional YAML authoring** |
| AIM topic | Required schema field; filter dimension; profile inference | **Partial** — schema + enrich + filter; not registry-promoted or upload-collectable | Pre-existing | Not in Field Registry index/cas_list | **Optional future** |
| acs_codes | Calendar unit structural IDs | **Implemented outside registry** — content-unit pipeline | Pre-existing | Intentionally excluded from Field Registry | **Complete by design** |
| OpenSearch top-level metadata promotion | Promote metadata to OS queryable top-level fields | **Not implemented** — hardcoded subset in `_build_bulk_actions` | N/A | Dual path vs Field Registry | **NOT REQUIRED NOW** |
| OpenSearch mapping changes | Registry-driven mapping updates | **Not implemented** — create-only `ensure_index` | N/A | Dynamic `metadata.*` templates absorb new keys | **NOT REQUIRED NOW** |
| provisioning | Auto-create DIS client on tenant creation | **Minimal** — writes `tenant_id`, `display_name`, `namespace` only | Pre-existing | No metadata template inheritance | **Future if multi-tenant self-service** |
| PipelineState | Pipeline step state tracking | **Unchanged** — no metadata validation state | Pre-existing | Report-only validation sufficient | **NOT REQUIRED** |
| TaxonomyRegistry | Separate taxonomy authority | **Not present** — zero references in repo | N/A | Covered by existing layers | **NOT JUSTIFIED** |
| metadata normalization | Consistent field naming across pipeline | **Partial** — client profiles + hints merge | Pre-existing | No unified normalization layer | **Optional future** |
| metadata lifecycle | Provenance tracking (user/system/AI/validated) | **Not implemented** — flat merged dict | N/A | No origin metadata on fields | **Future governance** |
| metadata versioning | Schema/config version tracking | **Not implemented** | N/A | `metadata_framework.version` in model only | **Future governance** |
| schema evolution | Safe schema change + migration | **Not implemented** | N/A | Manual YAML edits | **Future governance** |
| metadata migration/reindexing | Backfill on registry/schema change | **Scripts exist** — `repair_source_index.py`, `reindex_orphaned_units.py` | Pre-existing ops | Not automated on config change | **Operational, not framework** |

---

## 3. Current Metadata Architecture

### Intended end state (from repository evidence)

**FACT:** The original metadata framework work (Phases 0–5) established a **three-layer separation**:

```
metadata_schemas          → domain definition + LLM extraction + report-only validation
retrieval.source_ui       → UI presentation (taxonomy_filters, upload)
Field Registry            → index / retrieval projection / filter_options / cas_list
client profiles           → tenant-specific inference transforms (AIM/Cengage)
```

**FACT:** Phase 5 approval record (`docs/phase5-upload-metadata-approval.md`) explicitly forbids merging these layers.

**FACT:** The AIM briefing document (`docs/AIM_DIS_Metadata_Tagging_Briefing.md`) describes the **operational tagging model** (purpose, course scope, block/day, visibility) as live, and **skills/competency taxonomy** as explicitly not present — a separate product decision.

**INFERENCE:** The metadata framework Phases 0–5 targeted **operational metadata configurability** for DIS Source Library and retrieval — not a full taxonomy/skills platform.

### Architecture diagram (current state)

```
Upload (CAS → DIS)
  │  metadata_hints (user form + CAS derivation)
  ▼
Pipeline
  │  metadata_extraction  ← metadata_schemas (LLM prompt)
  │  metadata_tagging     ← client profile enrich_metadata + apply_metadata_hints
  │  validation_report    ← metadata_schemas (report-only)
  ▼
Storage
  │  S3 compact source record  ← Field Registry PROMOTE_INDEX
  │  S3 content units          ← full metadata dict
  │  OpenSearch chunks         ← hardcoded top-level + metadata.* blob
  ▼
Retrieval / UI
  │  Source Library listing    ← Field Registry filter_options + cas_list
  │  Context retrieval         ← _passes_filters (partially hardcoded) + vector search
  │  ui-config                 ← source_ui + upload_metadata + purpose_labels
```

### What Phases 0–5 delivered

| Phase | Deliverable |
|---|---|
| 0 | `metadata_framework` YAML passthrough; frontend taxonomy filter rendering |
| 1 | Field Registry with four promotion targets; default fallback |
| 2 | Registry-driven source filtering (`_source_matches`, `compact_source_record`) |
| 3 | Dynamic filter API forwarding for `/documents/library` |
| 4 | Report-only metadata schema validation in pipeline |
| 5 | Schema/config-driven upload metadata UI under `retrieval.source_ui.upload` |

---

## 4. Field Registry Audit

### What the registry controls

**FACT:** `DEFAULT_FIELDS` defines 30+ `FieldSpec` entries with promotion to one or more of: `index`, `retrieval`, `filter_options`, `cas_list` (`dis_backend/services/metadata_framework/registry.py`).

**FACT:** `acs_codes` is explicitly excluded from the registry (module docstring lines 12–13; merge logic lines 238–240).

### Promotion targets and consumers

| Target | Write path | Read path | Status |
|---|---|---|---|
| `index` | `compact_source_record` → `project_index_metadata(registry_for_tenant)` | S3 `source_list.json` | **Complete** |
| `retrieval` | `_metadata_from_record` → `project_retrieval_metadata(registry_for_tenant)` | Returned in retrieval payloads | **Projection complete** |
| `filter_options` | `source_filter_options` → `project_filter_options_map` | `_source_matches`, `build_documents_library_filters` | **Mostly complete** |
| `cas_list` | N/A (read from index) | `list_sources` → `project_cas_list_taxonomy` | **Complete** |

### Hardcoded metadata lists (classification)

| Item | Location | Classification |
|---|---|---|
| `DEFAULT_FIELDS` / `DEFAULT_REGISTRY` | `registry.py` | **REGISTRY-DRIVEN** |
| `project_*` adapters | `adapters.py` | **REGISTRY-DRIVEN** |
| `_LISTING_COMPAT_FILTER_FIELDS` (`lesson_name`, `visibility`) | `context_retrieval.py:33` | **COMPATIBILITY** |
| `_FILTER_OPTIONS_SKIP_EQ` (`purpose`) | `context_retrieval.py:37` | **BUSINESS RULE** |
| `_passes_filters exact_fields` / `bool_fields` | `context_retrieval.py:824–874` | **LEGACY / SUSPICIOUS** — bypasses registry |
| `DOCUMENTS_LIBRARY_CONTROL_PARAMS` | `filter_query.py` | **COMPATIBILITY** — Phase 0–2 API contract |
| `TAXONOMY_API_KEY_MAP` | `frontend/.../taxonomyFilters.js` | **COMPATIBILITY** — UI alias map |
| `SYSTEM_DERIVED_FIELD_KEYS` | `upload_ui_config.py` | **BUSINESS RULE** — upload exclusion |
| `_PER_UNIT_METADATA_KEYS` (incl. `acs_codes`) | `source_library.py` | **BUSINESS RULE** — content unit export |
| `STYLE/CDD/BLUEPRINT_DOC_TYPES` | `source_library.py` | **BUSINESS RULE** — purpose inference |
| `_build_bulk_actions` top-level fields | `indexing.py:306–326` | **LEGACY / SUSPICIOUS** — separate from registry |
| Search haystack keys | `context_retrieval.py` | **BUSINESS RULE** — free-text search |
| `metadata_filters` arbitrary keys in `_source_matches` | `context_retrieval.py:461–466` | **UNKNOWN** — accepts any key |

### Registry bypass summary

**FACT:** All four primary consumers call `registry_for_tenant(tenant_cfg)` in production paths.

**FACT:** `_passes_filters()` (retrieval unit gating) does **not** use the registry — it uses hardcoded `exact_fields` including `topic`, `quiz_number`, etc. (`context_retrieval.py:824–832`).

**INFERENCE:** A tenant YAML field promoted only to `retrieval` via `metadata_framework.fields` will appear in rebuilt metadata but will **not** participate in retrieval gating unless listed in `exact_fields` or passed via `metadata_filters`.

**FACT:** No production client YAML defines `metadata_framework`; all tenants use `DEFAULT_REGISTRY` today (`test_metadata_framework_phase0.py:88–97`).

---

## 5. metadata_schemas Audit

### Where loaded

**FACT:** `TenantConfig.metadata_schemas: Dict[str, MetadataSchema]` loaded from client YAML via `TenantRegistry._client_raw_to_tenant()` (`dis_backend/config/settings.py`).

**FACT:** `get_metadata_schema(client_id)` returns per-client schema or empty `MetadataSchema()`.

### Consumers

| Consumer | Use |
|---|---|
| `MetadataExtractionAgent` | LLM prompt field list (required + optional) |
| `ValidationReportAgent` / `validate_metadata_against_schema()` | Report-only findings (required presence + enum membership) |
| `build_upload_metadata_ui()` | Enum options for `document_type` and select controls |
| Admin `_client_config_summary()` | Super-admin schema dump |

### Relationships

| Path | Relationship to metadata_schemas |
|---|---|
| Extraction | **Reads** — schema guides LLM field extraction |
| Tagging | **Indirect** — hints and profile transforms merge into flat `doc_metadata`; schemas do not drive tagging rules |
| Validation | **Reads** — report-only; does not gate pipeline |
| Upload UI | **Reads enum values only** — presentation from `retrieval.source_ui.upload`; schemas are not the form authority |

### Duplication

**FACT:** Field names overlap across `metadata_schemas`, Field Registry `DEFAULT_FIELDS`, `retrieval.source_ui.taxonomy_filters`, and `retrieval.source_ui.upload` — maintained manually per tenant.

**FACT:** AIM upload includes `chapter` and `learning_objective` which are **not** in `metadata_schemas.aim` required/optional lists.

### Schema evolution / versioning

**FACT:** No `version` field on `MetadataSchema`.

**FACT:** `metadata_framework` model supports optional `version` in tests; committed AIM/Cengage YAML have no `metadata_framework` block.

**FACT:** Validator does not enforce `MetadataField.type` or `applies_to` (`metadata_schema_validate.py` docstring).

**INFERENCE:** Schema evolution is **manual YAML editing** with no migration tooling or version negotiation.

---

## 6. Upload UI Architecture

### Phase 5 verification

**FACT:** Phase 5 implemented the approved architecture:

| Layer | Authority | Evidence |
|---|---|---|
| `metadata_schemas` | Domain / validation | `upload_ui_config.py` reads enum values; does not generate form from schema alone |
| `retrieval.source_ui.upload` | Upload presentation | AIM YAML lines 252–268; `build_upload_metadata_ui()` |
| Field Registry | Index / filter / listing | Explicitly not touched (`upload_ui_config.py` module docstring) |

**FACT:** `ContextRetrievalService.ui_config()` exposes `upload_metadata` alongside `source_library` (`context_retrieval.py:193`).

**FACT:** Frontend reads `uiConfig.upload_metadata` via `uploadFields.js`; tests in `SourceLibraryPage.upload.test.jsx`.

**FACT:** `purpose` remains structural/hardcoded in the form (DECISION-005).

**FACT:** CAS upload endpoint declares fixed Form params including AIM upload fields (`app/api/v1/routers/source_library.py:303–317`).

### Separation sufficiency

**INFERENCE:** The three-layer separation is **sufficient for the intended architecture** as approved in Phase 5. Upload UI did not create a new metadata authority.

**FACT (residual gap):** CAS Form params are a fixed allow-list. New upload field keys added to tenant YAML but not declared in CAS `Form()` parameters would not reach DIS (FastAPI ignores undeclared form fields). Current AIM configured fields are covered.

---

## 7. OpenSearch Assessment

### Current architecture

**FACT:** DIS uses **two metadata stores**:

1. **S3 source index** — Source Library catalogue + retrieval allow-set (Field Registry drives projection)
2. **OpenSearch** — per-content-unit semantic/hybrid search + day digests

**FACT:** `_build_bulk_actions()` writes fixed top-level fields: `content_unit_id`, `job_id`, `tenant_id`, `client_id`, `source_file_name`, `source_file_type`, `document_type`, `course_name`, `block`, `day_number`, `unit_type`, `unit_number`, `title`, `text`, `visual_summary`, `keywords`, `topics`, `metadata` (full dict), `embedding` (`indexing.py:306–326`).

**FACT:** All other metadata lives under `metadata.*` via dynamic templates (`dynamic: true`, string/number/boolean templates).

**FACT:** `vector_search` filters only by `client_id` and `job_id` allow-list — not by block/day/course/topic (`indexing.py`).

**FACT:** Source Library and retrieval metadata filtering runs in **Python against S3 source records**, not OpenSearch query filters.

### Field Registry → OpenSearch relationship

**FACT:** Field Registry `PROMOTE_INDEX` affects S3 `compact_source_record` only — **not** OpenSearch document construction.

**INFERENCE:** Registry promotions to `index` do **not** require OpenSearch mapping changes — those fields are already stored in OpenSearch `metadata.*` on next unit upsert via dynamic mapping.

**INFERENCE:** Top-level OpenSearch promotion would require code changes to `_build_bulk_actions` and possibly `ensure_index` properties — only needed if server-side OS pre-filtering by those fields is required.

### Reindex necessity

| Scenario | Reindex needed? |
|---|---|
| New registry `PROMOTE_INDEX` field | S3 backfill; OS gets field in `metadata.*` on next upsert — **no full OS reindex** |
| New top-level OS field for querying | **Yes** for existing docs |
| Mapping change on existing index | **Yes** — `ensure_index` is create-only |
| Postgres ↔ OpenSearch drift | **Yes** — `reindex_orphaned_units.py` (operational) |

### Verdict

**OpenSearch redesign: NOT REQUIRED NOW**

**Why (evidence):**
- Metadata framework Phases 0–5 intentionally did not touch OpenSearch
- Current retrieval gating uses S3 source index, not OS metadata filters
- Dynamic `metadata.*` templates absorb registry field additions without mapping changes
- Documented OS pain points (Postgres/OS drift, post-ANN filtering) are **operational reliability** and **performance optimization** — not blockers for metadata framework completion
- `aim.yaml` comment referencing "tag filtering" in OpenSearch is **not implemented** as OS query filters — filtering is Python-side

**Classification:** **REQUIRED LATER** only if product requires server-side metadata pre-filtering inside kNN at scale, or unified registry→OS top-level promotion for cross-system consistency.

---

## 8. TaxonomyRegistry Assessment

### Current equivalents

| Need | Current solution |
|---|---|
| Field promotion / projection | Field Registry |
| Domain field definitions | `metadata_schemas` |
| Filter UI dimensions | `retrieval.source_ui.taxonomy_filters` |
| Upload form presentation | `retrieval.source_ui.upload` |
| Controlled vocabularies (enums) | `metadata_schemas` `values` lists |
| AIM inference rules | `AIMClientProfile` + `aim_content_rules` in YAML |

**FACT:** Zero references to `TaxonomyRegistry` or `taxonomy_registry` anywhere in the repository.

### Would TaxonomyRegistry solve a real current problem?

**INFERENCE:** No current runtime path fails because a TaxonomyRegistry is absent. Existing layers cover operational tagging, UI filters, schema validation, and upload presentation.

**INFERENCE:** A separate TaxonomyRegistry would **duplicate** Field Registry (projection), metadata_schemas (domain), and source_ui (presentation).

### Verdict

**TaxonomyRegistry: NOT JUSTIFIED**

**Why:** Repository evidence shows the deferred TaxonomyRegistry concept is superseded by the three-layer architecture established in Phases 0–5. Skills/competency taxonomy (per AIM briefing) is explicitly **future product work**, not a missing registry class.

**Classification:** **FUTURE** — only if stakeholders require a dedicated skills/competency ontology service separate from operational metadata.

---

## 9. Provisioning Assessment

**FACT:** `dis_provisioning.py` writes minimal YAML on tenant creation:

```yaml
tenant_id: ...
display_name: ...
namespace: ...
```

**FACT:** Does **not** copy `metadata_schemas`, `retrieval.source_ui`, or `metadata_framework` from a template tenant.

**FACT:** Adds slug to `config/dis_access.json` `available_clients`; best-effort DIS config reload.

**INFERENCE:** New auto-provisioned tenants have **empty/default metadata behavior** until YAML is hand-edited.

**INFERENCE:** Provisioning redesign is **not required** for metadata framework completion on existing clients (AIM, Cengage, Academian) which have committed YAML.

### Verdict

**Provisioning redesign: OPTIONAL / FUTURE**

Required only if product goal is **self-service tenant onboarding** with pre-configured metadata schemas and UI. Not justified as immediate metadata framework continuation.

---

## 10. PipelineState Assessment

**FACT:** `PipelineState` TypedDict includes `metadata_hints`, `doc_metadata`, `doc_type`, `classification`, step tracking — no metadata validation state (`dis_backend/services/pipeline/common.py`).

**FACT:** `validation_report` is written by `ValidationReportAgent` but is **not declared** in `PipelineState` TypedDict (LangGraph may drop undeclared keys — known limitation).

**FACT:** Validation findings live under `validation_report.metadata_validation.findings`; top-level `valid` reflects structural payload completeness, not schema compliance.

**FACT:** Pipeline continues through validation regardless of schema findings (report-only per DECISION-003).

### Verdict

**PipelineState changes: NOT REQUIRED**

No evidence that metadata framework work needs new states (metadata_validation_pending, metadata_approval, schema_version tracking). Phase 4 report-only validation is sufficient for current architecture.

---

## 11. Metadata Lifecycle

### Current lifecycle

```
source upload → metadata_hints (USER)
     ↓
metadata_extraction → doc_metadata (AI-EXTRACTED, schema-guided)
     ↓
metadata_tagging → enrich_metadata (AI-TAGGED / profile rules) + apply_metadata_hints (hints win)
     ↓
validation_report → findings (VALIDATED, report-only)
     ↓
compact_source_record (Field Registry projection) → S3 index
     ↓
content units + OpenSearch upsert
     ↓
retrieval / Source Library UI
```

### Metadata origin categories

| Category | Implementation | Origin tracked? |
|---|---|---|
| USER-PROVIDED | `metadata_hints` from upload → `apply_metadata_hints` | **No** — merged flat |
| SYSTEM-DERIVED | `file_sha256`, `job_id`, CAS block derivation, path hints | **No** — blocked from upload UI |
| AI-EXTRACTED | `MetadataExtractionAgent` → `doc_metadata` | **No** |
| AI-TAGGED / profile | `enrich_metadata`, structure agents, calendar parsers | **No** |
| VALIDATED | `validation_report.metadata_validation` | **Report-only** — does not mark fields or gate ingestion |

### Gaps

1. **No field-level provenance** — cannot distinguish user hint vs LLM extraction vs profile inference after merge
2. **No unified lifecycle model** — three config systems without sync enforcement
3. **Validation non-blocking** — schema findings do not affect job status
4. **AIM upload fields ⊄ metadata_schemas** — `chapter`, `learning_objective` collected at upload but not in AIM schema lists
5. **`acs_codes` outside registry/schemas** — calendar unit pipeline only; by design

**INFERENCE:** Lifecycle gaps are **governance/observability** concerns, not blockers for operational metadata retrieval.

---

## 12. Source-of-Truth / Duplication Audit

| Concept | Locations | Classification |
|---|---|---|
| `document_type` | metadata_schemas enum; upload UI; Field Registry; client profile remap; frontend fallback free-text | **SOURCE OF TRUTH:** metadata_schemas (domain); **DERIVED:** upload select options; **UI PRESENTATION:** upload control |
| `chapter` | upload UI (AIM); Field Registry; taxonomy potential | **SOURCE OF TRUTH:** Field Registry (storage key); **UI PRESENTATION:** upload config; **GAP:** not in AIM metadata_schemas |
| `module` / `module_name` | Registry (alias `module`); metadata_schemas; upload UI; taxonomy_filters | **SOURCE OF TRUTH:** Field Registry key `module_name`; **COMPATIBILITY:** alias map |
| `day` | Registry; metadata_schemas optional `block`; AIM profile; taxonomy_filters | **SOURCE OF TRUTH:** Field Registry; **BUSINESS RULE:** calendar-first day logic in `_passes_filters` |
| `block` | Registry; metadata_schemas; AIM profile; CAS derivation; taxonomy_filters | **SOURCE OF TRUTH:** multiple — profile inference + upload hints + CAS `_block_from_course` |
| `learning_objective` | Registry; Cengage schema (`learning_objective_text`); upload UI | **LEGACY:** naming split between clients |
| `purpose` | Frontend hardcoded values; `_purpose_flags()`; purpose_labels YAML | **SOURCE OF TRUTH:** structural code values; **UI PRESENTATION:** purpose_labels |
| `topic` | metadata_schemas (AIM required); AIM profile inference; taxonomy_filters; `_passes_filters exact_fields` | **SOURCE OF TRUTH:** metadata_schemas + profile; **GAP:** not registry-promoted |
| `acs_codes` | Calendar parsers; `_PER_UNIT_METADATA_KEYS`; unit metadata | **SOURCE OF TRUTH:** structure extraction pipeline; **BY DESIGN:** excluded from registry |
| Controlled enum values | metadata_schemas `values` | **SOURCE OF TRUTH:** metadata_schemas; **DERIVED COPY:** upload select options |

### Competing authorities?

**INFERENCE:** The architecture has **intentional separation**, not unnecessary competing authorities — except where manual YAML maintenance creates drift risk (field names across schema/registry/upload/filter config).

**INFERENCE:** No silent merge occurs at runtime; boundaries are enforced in code (`upload_ui_config.py`, `metadata_schema_validate.py` docstrings).

---

## 13. AIM/Cengage Gap Analysis

### AIM

| Aspect | Status |
|---|---|
| metadata_schemas configured | YES — required: `course_name`, `document_type`, `topic`, `unit_type` |
| metadata extracted | YES — LLM + profile `enrich_metadata` |
| metadata validated | YES — report-only |
| metadata filterable | YES — registry + taxonomy_filters (incl. `topic`) |
| metadata uploadable | PARTIAL — upload UI: `document_type`, `chapter`, `module_name`, `learning_objective`; NOT `topic`, `block`, `day` |
| hardcoded AIM logic | YES — `AIMClientProfile.enrich_metadata`, content rules in YAML |
| client-specific code required | YES — profile transform; architecture supports via profiles without fork |

### Cengage

| Aspect | Status |
|---|---|
| metadata_schemas configured | YES — system-required fields (`file_sha256`, `content_hash`, etc.) |
| upload UI configured | NO — falls back to legacy free-text `document_type` |
| metadata filterable | YES — default registry |
| client-specific code | Minimal — no Cengage profile transform equivalent to AIM |

### Cross-client architecture support

**FACT:** Tenant YAML + client profiles allow different metadata behavior without code forks for filtering/indexing/upload presentation.

**FACT:** AIM-specific behavior (block/day inference, topic from filename) lives in `client_profiles/aim.py` — outside Field Registry by design.

---

## 14. acs_codes / topic Assessment

### acs_codes

**FACT:** Not in any `metadata_schemas`.

**FACT:** Explicitly excluded from Field Registry (`registry.py:12, 238–240`).

**FACT:** Populated at content-unit level from calendar parsing (`content_unit_creation_agent`, `aim_calendar.py`).

**FACT:** Included in `_PER_UNIT_METADATA_KEYS` for clean content export (`source_library.py`).

**Verdict:** **Not an architectural gap** — intentional design for calendar structural IDs at unit level, not document catalogue metadata.

### topic (AIM)

| Check | Status |
|---|---|
| In tenant metadata_schemas | YES — required string (`aim.yaml:146–147`) |
| In document metadata | YES — via LLM extraction and/or `_topic_from_name()` in AIM profile |
| Indexed (S3 compact record) | **NO** — not in Field Registry `PROMOTE_INDEX` |
| Retrievable | YES — in `doc_metadata`; `_passes_filters exact_fields` includes `topic` |
| Filterable (Source Library) | YES — `taxonomy_filters` includes `topic` as text filter |
| Uploadable | **NO** — not in `retrieval.source_ui.upload` fields |
| Required by original architecture | **INFERENCE:** Required for AIM domain schema and retrieval gating; optional for upload collection |

**Verdict:** **Optional future gap** — topic works via extraction/inference and filter UI; upload collection and index promotion are enhancements, not framework blockers.

---

## 15. Remaining Gaps

### Required (for registry architecture completeness)

1. **`_passes_filters` not registry-driven** — retrieval gating uses hardcoded `exact_fields`/`bool_fields` while listing/index use registry (`context_retrieval.py:824–874`)

### Future (product-dependent)

1. OpenSearch server-side metadata pre-filtering in hybrid search
2. Registry → OpenSearch top-level promotion unification
3. Provisioning template inheritance for metadata config
4. Schema versioning and evolution tooling
5. Field-level metadata provenance
6. Cengage upload UI YAML authoring
7. AIM `topic` upload collection / index promotion
8. `/context/sources` dynamic filter passthrough (Phase 3 parity)

### Optional

1. Tenant YAML `metadata_framework` overrides (no production usage yet)
2. `applies_to` semantics (explicitly deferred DECISION-008)
3. CAS generic upload field passthrough beyond fixed Form params
4. Shared field dictionary linking upload + taxonomy_filters (DECISION-006 left open long-term)

### Obsolete

1. TaxonomyRegistry as separate service (superseded by three-layer model)
2. Upload form generation directly from metadata_schemas (rejected in Phase 5)
3. metadata_schemas → Field Registry merge (forbidden by approval)

### Blocked

1. Upload-time schema hard-fail (rejected DECISION-003)
2. CAS visibility tenant configurability (deferred DECISION-007)
3. Skills/competency taxonomy platform (explicitly out of scope per AIM briefing)

---

## 16. Candidate Next Phases

| Candidate | Objective | Evidence support | Risk |
|---|---|---|---|
| **6A — Registry-Complete Retrieval Gating** | Wire `_passes_filters` to registry `PROMOTE_RETRIEVAL` (+ documented business-rule exceptions) | Direct Phase 1–3 incompleteness | Low |
| **6B — Metadata Config Provisioning** | Template-based tenant YAML seeding for schemas/source_ui | New tenants get empty config | Medium — ops scope |
| **6C — OpenSearch Metadata Alignment** | Unify registry promotion into `_build_bulk_actions` | Dual-path drift risk | High — unrelated to current retrieval path |
| **6D — Schema Governance** | Versioning, migration, applies_to semantics | No versioning today | Medium — needs decisions |
| **6E — Metadata Provenance** | Track field origins through pipeline | No provenance model | Medium — new data model |
| **6F — No additional implementation** | Phases 0–5 sufficient for current AIM/Cengage | All core paths work | None |

---

## 17. Recommended Next Phase

### **Phase 6A — Registry-Complete Retrieval Gating**

**Objective:** Complete the Field Registry migration by making retrieval unit gating (`_passes_filters`) derive filterable metadata fields from `registry.for_promote(PROMOTE_RETRIEVAL)` instead of hardcoded lists, preserving documented business-rule exceptions (purpose gates, day/calendar logic, course sentinel, bool flags).

**Why this phase (evidence):**

1. **Direct continuation** of Phases 1–3 — the only metadata-framework consumer not fully registry-driven
2. **Repository-proven gap** — `_metadata_from_record` uses registry; `_passes_filters` does not
3. **Bounded scope** — primarily `context_retrieval.py` + characterization tests
4. **Low risk** — no OpenSearch, YAML, upload UI, or pipeline changes
5. **Measurable** — tenant YAML `metadata_framework` retrieval-promoted fields participate in gating

**Why NOT other candidates now:**

- **OpenSearch:** NOT REQUIRED NOW — retrieval gating uses S3, not OS filters
- **TaxonomyRegistry:** NOT JUSTIFIED — existing layers sufficient
- **Provisioning:** Only needed for self-service tenant onboarding
- **PipelineState:** Report-only validation sufficient
- **Schema governance:** No versioning requirement evidenced in current operations

---

## 18. Acceptance Criteria for Recommended Phase

1. `_passes_filters` exact-match field set is derived from `registry_for_tenant(tenant_cfg).for_promote(PROMOTE_RETRIEVAL)` with an explicit, tested allowlist of **business-rule fields** that remain hardcoded (e.g. `use_for_*` bool gates, day/calendar logic, course sentinel handling)
2. Characterization test proves a synthetic tenant `metadata_framework` field promoted to `retrieval` participates in retrieval gating
3. Existing AIM/Cengage retrieval behavior unchanged when `metadata_framework` is empty (DEFAULT_REGISTRY)
4. No changes to OpenSearch, metadata_schemas, upload UI, CAS, pipeline agents, or tenant YAML
5. Phase 1–3 characterization tests continue to pass

---

## 19. Open Decisions

### DECISION-001

**Question:** Should `topic` be promoted to Field Registry `index` + `filter_options` for AIM, or remain retrieval-gating-only via hardcoded `exact_fields`?

**Evidence:** `topic` is AIM schema-required, in taxonomy_filters, in `_passes_filters exact_fields`, but not registry-promoted.

**Options:**
A. Add `topic` to DEFAULT_REGISTRY with index/retrieval/filter_options promote  
B. Leave as-is until tenant YAML override needed  
C. Add only to registry when AIM YAML defines `metadata_framework`

**Recommendation:** B — no production breakage today; address when tenant registry overrides are authored.

**Why approval is required:** Affects compact index shape and filter dropdowns for all tenants if added to DEFAULT_REGISTRY.

### DECISION-002

**Question:** Should Phase 6A include migrating `_passes_filters bool_fields` and purpose gates to registry tiers, or only `exact_fields`?

**Evidence:** Bool fields (`use_for_style`, etc.) are operational routing, not taxonomy.

**Options:**
A. Migrate exact_fields only (minimal)  
B. Also derive bool_fields from registry `tier=operational` + type=boolean  
C. Leave bool_fields hardcoded permanently as business rules

**Recommendation:** A for Phase 6A scope; C for bool_fields long-term.

**Why approval is required:** Scope boundary for Phase 6A implementation.

### DECISION-003

**Question:** Is metadata config template provisioning required before further metadata framework work?

**Evidence:** `dis_provisioning.py` creates empty YAML; existing clients manually configured.

**Options:**
A. Defer — existing clients sufficient  
B. Phase 6B — template inheritance from reference tenant (e.g. academian)  
C. Documentation-only onboarding guide

**Recommendation:** A unless product requires self-service tenant creation with Source Library metadata.

**Why approval is required:** Business/onboarding priority, not technical blocker.

### DECISION-004

**Question:** Should OpenSearch ever use registry-driven top-level promotion?

**Evidence:** Dual paths today; dynamic `metadata.*` sufficient for storage; OS queries don't filter by metadata fields.

**Options:**
A. NOT REQUIRED — maintain separation  
B. REQUIRED LATER — when server-side OS pre-filter needed  
C. REQUIRED NOW — unify immediately

**Recommendation:** A/B — not now; revisit when kNN pre-filtering becomes a measured production bottleneck.

**Why approval is required:** Significant indexing/reindex scope if chosen.

---

## 20. Explicit Non-Goals

This discovery phase and the recommended Phase 6A **do not** pursue:

- [x] OpenSearch redesign or reindexing
- [x] TaxonomyRegistry implementation
- [x] Provisioning redesign
- [x] PipelineState metadata validation states
- [x] Upload-time schema hard-fail
- [x] `applies_to` enforcement
- [x] metadata_schemas → Field Registry merge
- [x] AIM/Cengage transform changes
- [x] CAS visibility redesign
- [x] Schema versioning / migration tooling
- [x] Metadata provenance / lifecycle modeling
- [x] Skills/competency taxonomy platform

---

## 21. Evidence Index

| File | Relevance |
|---|---|
| `dis_backend/services/metadata_framework/registry.py` | Field Registry definitions, DEFAULT_FIELDS, tenant merge |
| `dis_backend/services/metadata_framework/adapters.py` | project_* projection adapters |
| `dis_backend/services/metadata_framework/filter_query.py` | Phase 3 dynamic filter forwarding |
| `dis_backend/services/source_library.py` | compact_source_record, filter_options |
| `dis_backend/services/context_retrieval.py` | _source_matches, _passes_filters, ui_config, retrieval |
| `dis_backend/services/upload_ui_config.py` | Phase 5 upload UI resolution |
| `dis_backend/services/metadata_schema_validate.py` | Phase 4 validation |
| `dis_backend/services/indexing.py` | OpenSearch bulk docs, hybrid search |
| `dis_backend/config/settings.py` | MetadataSchema, TenantConfig, get_metadata_schema |
| `dis_backend/config/clients/aim.yaml` | AIM schemas, source_ui, upload config |
| `dis_backend/config/clients/cengage.yaml` | Cengage schemas |
| `dis_backend/services/client_profiles/aim.py` | AIM enrich_metadata, topic inference |
| `app/api/v1/routers/source_library.py` | CAS upload proxy, Form params |
| `app/services/dis_provisioning.py` | Minimal tenant YAML provisioning |
| `frontend/src/features/sourceLibrary/utils/uploadFields.js` | Phase 5 frontend upload config |
| `frontend/src/features/sourceLibrary/pages/SourceLibraryPage/SourceLibraryPage.jsx` | Dynamic upload form |
| `frontend/src/features/sourceLibrary/utils/taxonomyFilters.js` | Filter UI config |
| `docs/phase5-upload-metadata-discovery.md` | Phase 5 discovery baseline |
| `docs/phase5-upload-metadata-approval.md` | Approved architectural boundaries |
| `docs/AIM_DIS_Metadata_Tagging_Briefing.md` | Operational vs skills taxonomy boundary |
| `dis_backend/tests/test_metadata_framework_phase*.py` | Phase 0–3 characterization |
| `dis_backend/tests/test_metadata_schema_validation_phase4.py` | Phase 4 validation |
| `dis_backend/tests/test_upload_ui_config_phase5.py` | Phase 5 upload UI |
| `scripts/repair_source_index.py` | S3 index repair |
| `scripts/reindex_orphaned_units.py` | OpenSearch backfill |

---

## Appendix — Prioritization Matrix

| Capability | Business Need | Architectural Need | Current Gap | Risk | Priority |
|---|---|---|---|---|---|
| Registry-complete retrieval gating | MEDIUM | HIGH | HIGH | LOW | **HIGH** |
| OpenSearch metadata pre-filter | LOW | LOW | MEDIUM (perf) | HIGH | LOW |
| TaxonomyRegistry | NONE | NONE | NONE | MEDIUM | NONE |
| Provisioning metadata templates | MEDIUM (new tenants) | LOW | MEDIUM | LOW | LOW |
| Schema versioning | LOW | MEDIUM | MEDIUM | MEDIUM | LOW |
| Metadata provenance | LOW | MEDIUM | HIGH | MEDIUM | LOW |
| Cengage upload UI config | LOW | LOW | LOW | LOW | LOW |
| AIM topic upload/index promote | LOW | LOW | LOW | LOW | LOW |
| CAS dynamic Form passthrough | LOW | LOW | LOW (future fields) | LOW | LOW |
| `/sources` dynamic filters | LOW | LOW | LOW | LOW | LOW |

---

## Appendix — Fact / Inference / Decision Key

| Label | Meaning |
|---|---|
| **FACT** | Directly established by repository evidence (file content, tests, commits) |
| **INFERENCE** | Conclusion derived from multiple facts |
| **DECISION REQUIRED** | Cannot safely be determined without stakeholder approval |
