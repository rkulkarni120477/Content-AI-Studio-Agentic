# Phase 6B — Metadata Source-of-Truth & Configuration Drift Discovery

**Status:** Discovery / verification only — no implementation authorized.  
**Branch baseline:** `feature/dis_metadata` @ `c51baa6d6e57a10e80d3976f658d9d4439dfff51` (Phase 6A correction).  
**Investigation date:** 2026-08-31.

---

## 1. Baseline

| Check | Result |
|---|---|
| **branch** | `feature/dis_metadata` |
| **HEAD** | `c51baa6d6e57a10e80d3976f658d9d4439dfff51` |
| **working tree (start)** | CLEAN |
| **Phase 6A correction present** | YES — `c51baa6 fix: preserve legacy topic retrieval filtering` |
| **Phase 6A implementation present** | YES — `dc30412 feat: complete registry driven retrieval gating` |
| **Phase 6A approval present** | YES — `519fd6c docs: approve phase 6a retrieval gating` |
| **Phase 6 discovery present** | YES — `210723c docs: add phase 6 metadata architecture discovery` |

**FACT:** Phase 0–6A commit chain is intact on the branch:

```
c51baa6 fix: preserve legacy topic retrieval filtering     (Phase 6A correction)
dc30412 feat: complete registry driven retrieval gating    (Phase 6A)
519fd6c docs: approve phase 6a retrieval gating
210723c docs: add phase 6 metadata architecture discovery  (Phase 6)
91000cc feat: add schema driven upload metadata            (Phase 5)
7528dba docs: record phase 5 upload metadata approvals
3838b25 docs: add phase 5 upload metadata discovery
14fd727 feat: add metadata schema validation report        (Phase 4)
a6387bf feat: generalize source library filter api         (Phase 3)
f550b0d feat: complete registry-driven source filtering    (Phase 2)
7e10421 fix: wire tenant metadata registry to consumers
2128e78 feat: add DIS metadata Field Registry (phase 1)
255b4ff feat: implement metadata framework phase 0
```

---

## 2. Current Architecture

### Intended separation (FACT)

Phases 0–6A established and preserved a **four-layer** metadata architecture:

```
metadata_schemas          → domain definition + LLM extraction + report-only validation
retrieval.source_ui       → UI presentation (taxonomy_filters, upload)
Field Registry            → index / retrieval / filter_options / cas_list projection
client profiles           → tenant-specific inference transforms (AIM only today)
```

Phase 5 approval (`docs/phase5-upload-metadata-approval.md`) and Phase 6 discovery explicitly **forbid merging** these layers.

### End-to-end flow (FACT)

```
Upload (CAS → DIS metadata_hints)
  → Pipeline: metadata_extraction ← metadata_schemas
  → metadata_tagging ← client profile enrich_metadata + apply_metadata_hints
  → validation_report ← metadata_schemas (report-only)
  → compact_source_record ← Field Registry PROMOTE_INDEX
  → Source Library / retrieval ← Field Registry filter_options + _passes_filters
  → ui-config ← source_ui + upload_metadata (Phase 5)
```

### Phase 6A outcome (FACT)

`_passes_filters` exact-match metadata fields are now derived from `registry.for_promote(PROMOTE_RETRIEVAL)` with documented business-rule exceptions (`_RETRIEVAL_EXACT_SKIP`) and one legacy compat set (`_RETRIEVAL_LEGACY_EXACT` for `topic` per DECISION-001).

**INFERENCE:** The metadata framework Phases 0–6A targeted **operational metadata configurability** for DIS Source Library and retrieval — not a unified master registry or skills/competency taxonomy platform.

---

## 3. Metadata Authority Inventory

| Authority | Location | Defines | Consumed by |
|---|---|---|---|
| **metadata_schemas** | `dis_backend/config/clients/*.yaml` → `TenantConfig.get_metadata_schema()` | Field names, types, enum values, hints, required/optional | `MetadataExtractionAgent`, `ValidationReportAgent`, `build_upload_metadata_ui()` (enum options only) |
| **Field Registry** | `dis_backend/services/metadata_framework/registry.py` `DEFAULT_FIELDS`; optional tenant `metadata_framework.fields` | Canonical storage keys, promotion targets, aliases, filter_options keys, extract modes | `compact_source_record`, `_metadata_from_record`, `_source_matches`, `_passes_filters`, `build_documents_library_filters`, `source_filter_options` |
| **retrieval.source_ui.taxonomy_filters** | Tenant YAML | Filter UI key, label, control type | `ContextRetrievalService.ui_config()` → frontend `taxonomyFilters.js` |
| **retrieval.source_ui.upload** | Tenant YAML (AIM only in prod) | Upload field key, label, control, order | `build_upload_metadata_ui()` → frontend `uploadFields.js` |
| **Client profile (AIM)** | `dis_backend/services/client_profiles/aim.py` + `aim_content_rules` YAML | Inference, remapping (`document_type` operational vocabulary), block/day/topic | `MetadataTaggingAgent` |
| **document_processing** | Tenant YAML | `enabled_document_types`, `document_type_rules`, `unit_type_map`, structure patterns | Classification agents, structure extraction |
| **CAS upload Form** | `app/api/v1/routers/source_library.py` | Fixed allow-list of hint field names | DIS `/v1/ingest/upload` |
| **DIS upload Form** | `dis_backend/api/routers/ingestion.py` | Same hint fields → `metadata_hints` | Immediate payload, tagging |
| **Structural upload fields** | Frontend + code | `purpose`, `files`, `project_id`, `course_id` | CAS/DIS — not in metadata_schemas |
| **acs_codes pipeline** | Calendar parsers, content units | Unit-level structural ACS IDs | Digests, day-scoped retrieval — **excluded** from registry/schemas |
| **Frontend compat maps** | `taxonomyFilters.js` `TAXONOMY_API_KEY_MAP`, `TAXONOMY_OPTIONS_KEY_MAP` | `module`→`module_name`, `day_number`→`day` | API param mapping |
| **Registry adapters** | `dis_backend/services/metadata_framework/adapters.py` | Same alias contracts server-side | filter_query, compact record projection |

**FACT:** No production client YAML defines `metadata_framework`; all tenants use `DEFAULT_REGISTRY` today.

**FACT:** Only AIM has a client profile transform module. Cengage has no `CengageClientProfile`.

---

## 4. Field Definition Matrix

Representative fields — classifications based on repository evidence only.

| Field | metadata_schema | Field Registry | UI filter | UI upload | Client profile | API | Index | Validation | Classification |
|---|---|---|---|---|---|---|---|---|---|
| **document_type** | AIM/Cengage enum lists | `PROMOTE_*` operational | common_filters | select (schema-derived options) | AIM remaps to operational types (`lesson_pdf`, etc.) | CAS/DIS Form | via registry | enum membership (report-only) | DOMAIN + PROJECTION + INFERENCE |
| **chapter** | absent (AIM) | index/retrieval/filter/cas_list | AIM select | AIM text | not inferred | CAS/DIS Form | yes | none | PROJECTION + PRESENTATION |
| **module** | — | alias of `module_name` | Cengage key `module` | — | — | maps to `module_name` | via `module_name` | — | COMPATIBILITY |
| **module_name** | AIM optional | full promote + alias `module` | — | AIM text | — | Form param | yes | optional string | PROJECTION |
| **day** | — | index/retrieval/filter + aliases | AIM select | not collected | calendar inference | Form `day` | yes | — | PROJECTION + SYSTEM-DERIVED |
| **day_number** | — | index/retrieval integer | frontend maps to `day` | not collected | filename/calendar | — | yes | — | PROJECTION + SYSTEM-DERIVED |
| **block** | AIM optional string | index/retrieval/filter; `same_block` in listing | AIM select | not collected | profile + CAS derive | Form | yes | optional | DOMAIN + INFERENCE + BUSINESS RULE |
| **learning_objective** | Cengage: `learning_objective_text` | key `learning_objective` + aliases | both tenants | AIM text | — | Form | yes | Cengage schema fields | PROJECTION + LEGACY (naming split) |
| **purpose** | absent | retrieval + filter_options | common_filters | structural hardcoded | `_purpose_flags()` | Form | operational | none | BUSINESS RULE |
| **topic** | AIM required string | **not promoted** | AIM yaml (deferred UI) | not collected | `_topic_from_name()` | legacy retrieval only | **no** | required presence | DOMAIN + INFERENCE + LEGACY |
| **program_id** | AIM uses `program` optional | registry `program_id` | — | — | profile default | — | yes | — | INFERENCE + PROJECTION |
| **course_id** | — | index/retrieval operational | — | Redux scope | profile default | Form | yes | — | SYSTEM-DERIVED |
| **visibility** | Cengage `access_level` enum | retrieval | common_filters | CAS hardcoded internal | AIM profile | CAS override | operational | Cengage enum | BUSINESS RULE |
| **status** | — | retrieval + filter_options | common_filters | system | profile sets | — | operational | — | SYSTEM-DERIVED |
| **quiz_number** | — | index/retrieval integer | — | — | calendar | — | yes | — | SYSTEM-DERIVED |
| **acs_codes** | absent | **explicitly excluded** | absent | absent | calendar units | unit metadata | unit-level only | none | SYSTEM-DERIVED (by design) |

---

## 5. document_type Audit

### Trace (FACT)

| Stage | Source of allowed values | Source of labels | Requiredness | Type | Defaults |
|---|---|---|---|---|---|
| YAML metadata_schemas | `values:` list per client | none (hint is LLM guidance) | required_fields | `enum` | none |
| Upload UI | **derived** from schema via `resolve_document_type_upload()` / `_resolve_upload_field()` | `retrieval.source_ui.upload` or fallback "Document Type" | not enforced at upload | select or text | free-text placeholder when no enum |
| Frontend | `uploadFields.js` renders ui-config | ui-config | HTML not required | from ui-config | `general_reference` purpose default only |
| CAS | accepts any string Form param | — | optional | string | empty → auto-detect |
| DIS ingestion | passes hint to `metadata_hints` | — | optional | string | pipeline may override |
| AIM profile | `_content_type()` operational vocabulary (`lesson_pdf`, `slide_deck`, `quiz`, …) | — | overwrites hint | string | inferred from path/name |
| metadata_schemas validation | schema enum | — | report-only missing/invalid | enum check only | — |
| Field Registry | no enum list — stores actual value | filter dropdown from index | — | string | — |
| Index | `compact_source_record` | — | — | string | from merged metadata |
| Retrieval filters | purpose gates + content_types list | — | — | normalized compare | — |
| Source Library filters | registry `document_type` filter_options | ui-config common_filters | — | case-insensitive eq | — |

### Duplication analysis

| Property | Definitions | Source of truth | Drift risk |
|---|---|---|---|
| Allowed values (AIM upload select) | metadata_schemas enum | **metadata_schemas** for upload UI | LOW — Phase 5 reads schema at ui-config time |
| Allowed values (post-ingestion AIM) | profile operational types ⊄ schema enum | **AIM profile + document_processing** for runtime type | **MEDIUM** — schema validation may report `invalid_value` for valid operational types |
| Labels | upload ui-config + purpose_labels | **source_ui** | LOW |
| Requiredness | schema required; upload optional | **schema for validation only** | NONE at upload (by design DECISION-003) |
| Type | schema enum; registry string | respective layers | LOW |

**FACT:** AIM `metadata_schemas.aim.document_type` values include `lesson_slide_deck`, `quiz_exam`; AIM profile commonly writes `slide_deck`, `quiz`, `lesson_pdf`. Validation is report-only, so this does not block ingestion.

**INFERENCE:** Dual vocabulary is **intentional separation** (domain schema vs operational tagging) with **acceptable validation noise**, not a runtime defect.

---

## 6. Taxonomy Field Audit

### module → module_name (FACT)

| Layer | Key used |
|---|---|
| Field Registry canonical | `module_name` |
| Registry aliases | `module`, `module_id`, `section_title` |
| Registry filter_options_key | `module` (response map) |
| Cengage taxonomy_filters | `module` |
| Frontend TAXONOMY_API_KEY_MAP | `module` → `module_name` |
| adapters.py `api_param_for_ui_key` | `module` → `module_name` |
| CAS/DIS upload Form | `module_name` |

**Classification:** COMPATIBILITY — aliases centralized in Field Registry; frontend map duplicates one mapping by design (documented in module docstrings).

### day_number → day (FACT)

| Layer | Behavior |
|---|---|
| Registry | canonical `day`; aliases include `day_number`, `day_id`, `mapped_day` |
| Frontend | `day_number` → `day` in TAXONOMY_API_KEY_MAP |
| `_passes_filters` | day fields in `_RETRIEVAL_EXACT_SKIP`; dedicated calendar logic |
| `_source_matches` | `same_block` for `block` only; day uses registry eq |

**Classification:** PROJECTION + BUSINESS RULE — day filtering uses special calendar logic, not plain registry equality in retrieval gating.

### block → same_block (FACT)

- `_source_matches` (Source Library listing): `block` filter uses `same_block()` normalization.
- `_passes_filters` (retrieval): `block` excluded from registry exact loop; `block_id`/`block_number` use exact match.

**Classification:** BUSINESS RULE — intentional asymmetry between listing and unit gating.

### chapter, learning_objective (FACT)

- Both in Field Registry with full promotion.
- AIM upload collects both; neither in AIM `metadata_schemas` lists.
- Cengage schema uses `chapter_number`/`chapter_title` and `learning_objective_id`/`learning_objective_text` — different field names from registry canonical keys.

**INFERENCE:** AIM upload-without-schema is **intentional** (Phase 5 DECISION-002: explicit upload list, not all schema fields). Harmless for validation (fields simply not validated). Cengage naming split is **LEGACY** client vocabulary preserved via registry aliases for `learning_objective_text`.

---

## 7. Field Registry Audit

### FieldSpec properties (FACT)

| Property | Purpose | Could come from metadata_schemas? |
|---|---|---|
| `key` | Canonical storage key on compact record | Partially — names overlap but registry includes operational fields not in schemas |
| `type` | Extraction/serialization hint | Partially — schema has types but validator doesn't enforce them |
| `tier` | operational vs structural | No — registry concern |
| `promote` | index/retrieval/filter_options/cas_list | No — pure projection concern |
| `filter_options_key` | API response key (e.g. `blocks` → `block`) | No — API contract |
| `aliases` | UI/API compat (`module` → `module_name`) | No — compat layer |
| `extract` | compact_source_record extraction mode | No — write-path logic |

### Could Field Registry depend on metadata_schemas?

**INFERENCE:** Technically possible for field **names** only. Would:

| Effect | Assessment |
|---|---|
| Simplify configuration | Marginal — still need promote/alias/extract |
| Create coupling | **High** — schemas include non-index fields (`file_sha256`) and miss operational fields |
| Break tenant overrides | Risk — `metadata_framework.fields` merge pattern would conflict |
| Complicate runtime | **Yes** — two systems loaded at different lifecycle points |
| Solve a real problem | **No** — no runtime failure from separation today |

**Verdict:** Registry should **not** depend on metadata_schemas. Overlap in field names is intentional.

---

## 8. metadata_schemas Audit

### Provides today (FACT)

- Field names, types (documented), enum `values`, LLM `hint`, required vs optional, empty `applies_to` (unused)

### Does NOT provide (FACT)

- UI labels, control types, ordering, upload/filter visibility
- Index/retrieval/filter promotion
- Alias maps, extraction modes
- Type enforcement at validation time

### Separation assessment

**INFERENCE:** Separation is **intentional and useful**. Schemas serve extraction/validation; they are explicitly **not** frontend form schemas (Phase 5 approval).

---

## 9. source_ui Audit

### taxonomy_filters (FACT)

| Information | Duplicated from | Genuinely UI-specific |
|---|---|---|
| Field keys | Overlap with registry/schema names | — |
| Labels | — | **Yes** |
| Control type (select/text) | — | **Yes** |
| Enum options | Not duplicated — dropdown values from `source_filter_options()` index scan | — |

### upload (FACT)

| Information | Duplicated from | Genuinely UI-specific |
|---|---|---|
| Field keys | References schema/domain names | — |
| Labels, order, control | — | **Yes** |
| Select options for `document_type` | **Derived** from metadata_schemas enum | — |
| Other select options | Only if schema has enum for that key | — |

**INFERENCE:** Apparent duplication (same field names in upload + filters + registry) is **mostly key reference**, not duplicate semantics. Maintenance burden is **manual YAML authoring** when adding fields — not silent runtime merge failure.

---

## 10. Client Profile Audit

### AIM (FACT)

| Concern | Profile role |
|---|---|
| Metadata inference | block/day/topic/document_type/content_type from path/filename |
| Transformation | Overwrites LLM extraction for operational fields |
| Field naming | Uses same keys as registry (`module_name`, `block`, `topic`) |
| Field values | Operational doc types differ from schema enum |
| Business rules | visibility, restricted, use_for_* flags, calendar_mapping_required |

**INFERENCE:** AIM profile is **inference logic**, not a competing metadata authority. It writes into the flat metadata dict that registry then projects.

### Cengage (FACT)

No client profile module. Behavior from YAML schemas + default registry + document_processing rules only.

---

## 11. Real Drift / Bug Evidence

### FACT — real defects occurred

| Incident | Evidence | Impact | Current status |
|---|---|---|---|
| **Phase 6A topic retrieval regression** | `dc30412` removed hardcoded `topic` from `_passes_filters`; fixed in `c51baa6` via `_RETRIEVAL_LEGACY_EXACT` | Retrieval API ignoring `topic=` filter | **FIXED** |
| **course_id sentinel mismatch** (historical) | `context_retrieval.py` comment lines 80–84: listing vs retrieval disagreed on `-1` sentinel | Wrong documents in scoped retrieval | **FIXED** (prior commit) |
| **Block filter string mismatch** (historical) | `_source_matches` comment: `Block 09` vs `Block 9` | Incomplete Source Library block filter | **FIXED** via `same_block()` |

### RISK — theoretical drift (no production bug evidenced)

| Risk | Evidence | Impact if drifted |
|---|---|---|
| metadata_schemas enum ≠ AIM operational document_type | Both vocabularies in repo | Report-only validation findings only |
| AIM upload fields ⊄ metadata_schemas | Phase 6 discovery noted | No validation coverage for user hints |
| Frontend `DEFERRED_TAXONOMY_KEYS` hides AIM `topic` filter | `taxonomyFilters.js`, test documents deferral | Topic filter UI not shown; backend retrieval supports topic |
| Cengage no upload YAML | Falls back to free-text document_type | UX only — schema enum available at ui-config |
| CAS fixed Form allow-list | New upload keys need CAS/DIS Form params | Silent drop of undeclared form fields |
| Upload extension regex vs server policy | Phase 5 discovery | Client-side pre-rejection drift |

### INFERENCE

Manual duplication across YAML layers creates **authoring burden** and **theoretical drift**, but **no evidence** of a systemic source-of-truth failure requiring architectural rewrite after Phase 6A correction.

---

## 12. AIM Analysis

| Layer | chapter | learning_objective | topic | module_name |
|---|---|---|---|---|
| metadata_schemas | absent | absent | required | optional |
| Field Registry | promoted | promoted | **not promoted** | promoted |
| taxonomy_filters | absent | absent | configured | absent |
| upload | collected | collected | not collected | collected |
| profile inference | no | no | yes | no |

### Upload fields not in AIM metadata_schemas (FACT)

`chapter`, `learning_objective` appear in upload UI but not schema lists.

**Classification:** **Intentional** per Phase 5 DECISION-002 (explicit upload list). **Harmless** — hints merge into metadata; validation simply doesn't cover them. **Not accidental.**

### topic (FACT)

- Schema-required; profile-inferred; retrieval-gated via legacy exact match.
- Not index-promoted → not in Source Library filter_options dropdown values path.
- Frontend **defers** topic filter despite AIM YAML config.

**DECISION REQUIRED:** Whether AIM topic Source Library filter UX should be enabled (requires frontend undeferral + registry filter_options or metadata_filters path).

---

## 13. Cengage Analysis

| Layer | Status |
|---|---|
| metadata_schemas | Rich publishing schema; system-required hash fields |
| Field Registry | DEFAULT_REGISTRY (no tenant override) |
| source_ui taxonomy_filters | course_name, chapter, module, learning_objective |
| source_ui upload | **Absent** — legacy free-text document_type fallback |
| Client profile | None |
| Filters vs registry | Consistent via alias maps (`module`→`module_name`) |

**INFERENCE:** Cengage is **consistent** with architecture. Missing upload YAML is **optional tenant authoring**, not drift.

### learning_objective naming (FACT)

Schema: `learning_objective_id`, `learning_objective_text`. Registry/filter UI: `learning_objective` with alias `learning_objective_text`. **LEGACY compat**, not conflict.

---

## 14. topic Analysis

### Lifecycle (FACT)

```
source → AIM profile _topic_from_name() + LLM extraction (schema-guided)
  → validation: required field check (report-only)
  → index: NOT in compact_source_record (not PROMOTE_INDEX)
  → retrieval: _RETRIEVAL_LEGACY_EXACT gating in _passes_filters
  → Source Library filter: NOT in filter_options; frontend DEFERRED
  → UI: configured in AIM yaml but hidden by frontend
```

**INFERENCE:** topic is a **deliberate legacy exception** (Phase 6A DECISION-001 rejected registry promotion). Partial functionality works for retrieval API; Source Library topic filter is incomplete by design + frontend deferral.

**Not a source-of-truth architectural problem** requiring broad implementation — **product decision** on promotion/filter UX.

---

## 15. acs_codes Analysis

| Layer | Present? |
|---|---|
| metadata_schemas | **No** |
| Field Registry | **Explicitly excluded** (`registry.py` lines 238–240) |
| UI filters/upload | **No** |
| Retrieval (document catalogue) | **No** |
| Content unit metadata | **Yes** — calendar parsing, `_PER_UNIT_METADATA_KEYS` |
| Digests / day-scoped | **Yes** — primary consumer |

**FACT:** acs_codes belongs to the **calendar/content-unit structural pipeline**, not document catalogue metadata.

**Verdict:** **NOT A REAL PROBLEM** — intentional design. Do not move to registry/schemas.

---

## 16. Source-of-Truth Matrix

| Property | Current authority | Should remain? | Duplicated? | Drift risk |
|---|---|---|---|---|
| FIELD NAME (canonical storage) | Field Registry `key` | YES | Aliases elsewhere | LOW |
| FIELD NAME (domain) | metadata_schemas `name` | YES | Overlapping names | LOW (convention) |
| TYPE | metadata_schemas (domain); FieldSpec.type (projection) | YES | Yes | LOW — validator ignores type |
| ALLOWED VALUES (domain enum) | metadata_schemas `values` | YES | Derived copy in upload select | LOW (Phase 5 derivation) |
| ALLOWED VALUES (operational doc types) | AIM profile + document_processing | YES | Overlaps schema enum | MEDIUM (validation noise only) |
| REQUIREDNESS | metadata_schemas required_fields | YES | Not enforced at upload | NONE (by design) |
| ALIASES | Field Registry + adapters + frontend map | YES | 3 locations | MEDIUM — documented compat |
| INDEX PROMOTION | Field Registry `promote: index` | YES | OpenSearch top-level separate | LOW for framework scope |
| RETRIEVAL PROMOTION | Field Registry `promote: retrieval` | YES | topic legacy exception | LOW |
| FILTER PROMOTION | Field Registry `promote: filter_options` | YES | taxonomy_filters keys reference | LOW |
| UI LABEL | source_ui | YES | — | LOW |
| UI CONTROL | source_ui | YES | — | LOW |
| UI ORDER | source_ui.upload | YES | — | LOW |
| INFERENCE RULE | Client profile + document_processing | YES | — | LOW |
| SYSTEM DERIVATION | Pipeline, CAS, profile | YES | SYSTEM_DERIVED_FIELD_KEYS denylist | LOW |

---

## 17. Duplication Classification

### Configuration drift examples (evidence-based)

#### document_type enum (AIM)

- **Definition A:** `metadata_schemas.aim.document_type.values` — `lesson_slide_deck`, `quiz_exam`, …
- **Definition B:** AIM profile `_content_type()` output — `lesson_pdf`, `slide_deck`, `quiz`, …
- **Difference:** Disjoint operational vs schema vocabularies
- **Actual impact:** POTENTIAL validation findings (report-only); no ingestion block
- **Recommended authority:** Schema for extraction/validation vocabulary; profile for operational runtime type
- **Classification:** D — ACCEPTABLE DUPLICATION

#### document_type upload select

- **Definition A:** metadata_schemas enum
- **Definition B:** `upload_metadata.document_type.options` from `build_upload_metadata_ui()`
- **Difference:** B derived from A at ui-config build time
- **Actual impact:** NONE if schema updated
- **Recommended authority:** metadata_schemas for values; source_ui for presentation
- **Classification:** D — ACCEPTABLE DUPLICATION (controlled derivation)

#### module / module_name

- **Definition A:** Registry key `module_name`, alias `module`
- **Definition B:** Frontend `TAXONOMY_API_KEY_MAP.module = 'module_name'`
- **Difference:** Same mapping in two places
- **Actual impact:** NONE today; tests lock behavior
- **Recommended authority:** Field Registry + adapters; frontend map as thin client compat
- **Classification:** D — ACCEPTABLE DUPLICATION

#### topic filter

- **Definition A:** AIM `taxonomy_filters` includes `topic`
- **Definition B:** Frontend `DEFERRED_TAXONOMY_KEYS` excludes `topic`
- **Definition C:** `_RETRIEVAL_LEGACY_EXACT` gates topic in retrieval only
- **Difference:** UI disabled; listing filter_options absent; retrieval works
- **Actual impact:** POTENTIAL BUG for users expecting Source Library topic filter
- **Recommended authority:** DECISION REQUIRED on topic promotion scope
- **Classification:** F — REQUIRES DECISION

#### Phase 6A topic regression (historical)

- **Definition A:** Pre-6A hardcoded exact_fields included `topic`
- **Definition B:** dc30412 registry migration omitted topic
- **Difference:** Retrieval ignored topic filter
- **Actual impact:** BUG (fixed c51baa6)
- **Recommended authority:** Legacy exact until DECISION-001 resolved
- **Classification:** A — was MUST FIX (completed)

### Issue classification summary

| ID | Issue | Class |
|---|---|---|
| I-01 | Layer separation (schemas/registry/source_ui/profile) | E — NOT A REAL PROBLEM |
| I-02 | AIM dual document_type vocabulary | D — ACCEPTABLE |
| I-03 | AIM upload fields outside schema | D — ACCEPTABLE |
| I-04 | topic partial filter support | F — REQUIRES DECISION |
| I-05 | Cengage missing upload YAML | C — FUTURE (optional) |
| I-06 | CAS fixed Form allow-list | C — FUTURE (when new upload keys) |
| I-07 | Phase 6A topic regression | A — FIXED |
| I-08 | Master registry proposal | E — NOT JUSTIFIED |

---

## 18. Candidate Solutions

Evaluated for **actual problems only** (I-04, I-05, I-06). No option implemented.

### Option A — Keep current separation (RECOMMENDED baseline)

| | |
|---|---|
| **Benefits** | Preserves Phase 0–6A boundaries; lowest risk |
| **Risks** | Manual YAML authoring continues |
| **Files affected** | None |
| **Migration complexity** | None |
| **Backward compatibility** | Full |
| **Recommendation** | **Default for architecture** |

### Option B — Reference metadata_schemas from UI config only (already partial)

| | |
|---|---|
| **Benefits** | document_type enum already derived; extend pattern |
| **Risks** | Schemas lack UI semantics; system fields unsuitable |
| **Files affected** | `upload_ui_config.py` only for extensions |
| **Migration complexity** | Low |
| **Backward compatibility** | Strong |
| **Recommendation** | **Already implemented for document_type**; sufficient |

### Option C — Generate Field Registry from metadata_schemas

| | |
|---|---|
| **Benefits** | Fewer manual field lists |
| **Risks** | High coupling; breaks promote/alias/extract model; forbidden by approvals |
| **Files affected** | registry.py, all consumers |
| **Migration complexity** | High |
| **Backward compatibility** | Risky |
| **Recommendation** | **Reject** |

### Option D — Shared canonical field-name constants

| | |
|---|---|
| **Benefits** | Reduces alias drift between frontend/backend |
| **Risks** | New shared package; marginal gain |
| **Files affected** | frontend + adapters + registry |
| **Migration complexity** | Medium |
| **Backward compatibility** | Good |
| **Recommendation** | **C — FUTURE** only if alias bugs recur |

### Option E — Controlled vocabulary source for document_type

| | |
|---|---|
| **Benefits** | Unify AIM schema enum and operational types |
| **Risks** | Large semantic change; affects validation, profile, source_type_mapping |
| **Files affected** | aim.yaml, aim.py, document_processing |
| **Migration complexity** | High |
| **Backward compatibility** | Risky |
| **Recommendation** | **Reject unless product mandates single vocabulary** |

### Option F — Enable AIM topic filter (targeted, if I-04 approved)

| | |
|---|---|
| **Benefits** | Aligns UI with AIM yaml; retrieval already supports topic |
| **Risks** | Source Library listing still needs filter_options or metadata_filters path |
| **Files affected** | `taxonomyFilters.js`; possibly registry or filter forwarding |
| **Migration complexity** | Low–medium |
| **Backward compatibility** | Additive |
| **Recommendation** | **Only if DECISION-001 revisited** |

---

## 19. Future-Proofing Assessment

| Scenario | Without production code changes? | Explanation |
|---|---|---|
| **New tenant** | PARTIAL | Provisioning writes minimal YAML; metadata config hand-authored |
| **New metadata field (domain only)** | YES | Add to tenant `metadata_schemas` YAML |
| **New filterable field** | PARTIAL | Tenant `metadata_framework.fields` with filter_options promote works; DEFAULT_REGISTRY changes need `registry.py`; taxonomy_filters YAML for UI label |
| **New upload field** | PARTIAL | YAML `source_ui.upload` + must add CAS/DIS Form params if new key |
| **New controlled vocabulary** | YES | metadata_schemas enum values; upload select auto-derives for document_type |
| **New client profile** | NO | New Python profile module if inference rules needed (AIM pattern) |

---

## 20. Production Consumer Impact

For a **new metadata field** (representative counts):

| Goal | Config-only changes | Production code files (if defaults insufficient) |
|---|---|---|
| 1. metadata_schemas only | 1 YAML file | **0** — extraction/validation auto-consume |
| 2. Field Registry only | tenant `metadata_framework.fields` YAML | **0** with YAML merge; **1** (`registry.py`) for DEFAULT_REGISTRY |
| 3. Make filterable | registry promote + taxonomy_filters YAML | **0–1** (`filter_query.py` if new named API param needed) |
| 4. Make uploadable | source_ui.upload YAML | **2** (`source_library.py` CAS Form + `ingestion.py` DIS Form) for new keys |
| 5. Make retrievable | registry `promote: retrieval` | **0** with tenant YAML; **1** (`context_retrieval.py`) only if business-rule skip list update |
| 6. Make index-promoted | registry `promote: index` | **0** code; S3 backfill operational |

**FACT:** Phase 3 dynamic filter forwarding reduced frontend allowlist edits for new filter_options fields. Upload remains the **highest-friction** path due to fixed Form signatures.

---

## 21. Remaining Problems

| Problem | Severity | Blocks operations? |
|---|---|---|
| Manual multi-YAML authoring for new fields | Low | No |
| AIM topic Source Library filter incomplete | Low | No — retrieval API works |
| Cengage upload UI not configured | Low | No — fallback works |
| AIM schema vs operational document_type vocabulary | Low | No — report-only |
| No metadata_framework tenant overrides in prod | None | No — defaults sufficient |
| OpenSearch dual metadata path vs registry | Low | No for current retrieval architecture |

**INFERENCE:** No **MUST FIX** source-of-truth problem remains after Phase 6A correction.

---

## 22. Recommendation

### **NO IMPLEMENTATION REQUIRED**

**Rationale (evidence-based):**

1. **FACT:** Phase 6A completed registry-driven retrieval gating; the one regression (topic) was corrected in `c51baa6`.
2. **FACT:** Layer separation is **explicitly approved** (Phase 5/6/6A docs) and **enforced in code** (`upload_ui_config.py`, `metadata_schema_validate.py` docstrings).
3. **INFERENCE:** Remaining duplications are **intentional** (projection vs domain vs presentation vs inference) or **acceptable** (controlled derivation, compat aliases).
4. **INFERENCE:** No master registry would reduce complexity net — it would increase coupling without solving a demonstrated runtime failure mode.
5. **INFERENCE:** Optional enhancements (Cengage upload YAML, topic filter UX, CAS dynamic Form) are **tenant/product scope**, not architectural debt.

**Do not authorize a broad metadata rewrite or Phase 6C master registry.**

Optional **targeted** work (only if stakeholders approve separately):

- I-04: AIM topic Source Library filter (F — requires DECISION-001 revisit)
- I-05: Cengage upload YAML authoring (C — future)
- I-06: CAS generic upload field passthrough (C — future)

---

## 23. Open Decisions

### DECISION-001 (carried from Phase 6A)

**Question:** Should `topic` be promoted to Field Registry index/filter_options?

**Evidence:** Works for retrieval via legacy exact; not in Source Library filter path; frontend deferred.

**Options:** A. Promote to registry B. Keep legacy exception C. Remove topic filter from AIM yaml

**Recommendation:** B unless product requires Source Library topic filtering.

**Label:** DECISION REQUIRED

### DECISION-002

**Question:** Should AIM metadata_schemas document_type enum align with operational profile types?

**Recommendation:** Defer — report-only validation; alignment is large semantic change.

**Label:** DECISION REQUIRED (product)

### DECISION-003

**Question:** Should frontend remove `topic` from DEFERRED_TAXONOMY_KEYS?

**Depends on:** DECISION-001 and filter_options strategy.

**Label:** DECISION REQUIRED

### DECISION-004

**Question:** Should Cengage receive `retrieval.source_ui.upload` configuration?

**Recommendation:** Optional tenant authoring — not architectural.

**Label:** DECISION REQUIRED (product)

---

## 24. Explicit Non-Goals

This discovery **does not** authorize:

- [x] Master metadata registry merging schemas + registry + source_ui + profiles
- [x] metadata_schemas → Field Registry merge
- [x] metadata_schemas as automatic upload form schema
- [x] Field Registry dependency on metadata_schemas
- [x] OpenSearch registry alignment
- [x] Provisioning template inheritance
- [x] AIM/Cengage transform changes
- [x] acs_codes promotion to registry
- [x] Upload-time schema hard-fail
- [x] `applies_to` enforcement
- [x] TaxonomyRegistry implementation
- [x] Schema versioning tooling
- [x] Production code changes of any kind

---

## 25. Evidence Index

| File | Relevance |
|---|---|
| `dis_backend/services/metadata_framework/registry.py` | DEFAULT_FIELDS, FieldSpec, acs_codes exclusion |
| `dis_backend/services/metadata_framework/adapters.py` | Alias adapters, api_param_for_ui_key |
| `dis_backend/services/metadata_framework/filter_query.py` | Phase 3 dynamic filters, control params |
| `dis_backend/services/context_retrieval.py` | _passes_filters Phase 6A, _source_matches, ui_config, legacy topic |
| `dis_backend/services/upload_ui_config.py` | Phase 5 upload resolution, SYSTEM_DERIVED |
| `dis_backend/services/metadata_schema_validate.py` | Phase 4 report-only validation |
| `dis_backend/services/source_library.py` | compact_source_record, _PER_UNIT_METADATA_KEYS |
| `dis_backend/services/client_profiles/aim.py` | AIM enrich_metadata, operational document_type |
| `dis_backend/config/clients/aim.yaml` | schemas, source_ui, upload, content rules |
| `dis_backend/config/clients/cengage.yaml` | Cengage schemas, taxonomy_filters |
| `dis_backend/config/settings.py` | MetadataField, MetadataSchema, TenantConfig |
| `app/api/v1/routers/source_library.py` | CAS upload Form params |
| `dis_backend/api/routers/ingestion.py` | DIS metadata_hints |
| `frontend/src/features/sourceLibrary/utils/taxonomyFilters.js` | DEFERRED_TAXONOMY_KEYS, alias maps |
| `frontend/src/features/sourceLibrary/utils/uploadFields.js` | Upload UI consumption |
| `dis_backend/tests/test_metadata_framework_phase6a_retrieval_gating.py` | Phase 6A characterization, topic legacy tests |
| `dis_backend/tests/test_upload_ui_config_phase5.py` | Upload/schema derivation tests |
| `frontend/src/features/sourceLibrary/utils/__tests__/taxonomyFilters.test.js` | Topic deferral test |
| `docs/phase5-upload-metadata-discovery.md` | Phase 5 boundaries |
| `docs/phase5-upload-metadata-approval.md` | Approved decisions |
| `docs/phase6-metadata-architecture-discovery.md` | Phase 6 gap analysis |
| `docs/phase6a-retrieval-gating-approval.md` | Phase 6A scope, topic deferral |
| Git commits `dc30412`, `c51baa6` | Phase 6A implementation + topic fix |

---

## Appendix — Master Registry Evaluation (§19 mandate)

**Question:** Should metadata_schemas + Field Registry + source_ui + profiles become one master registry?

**Answer: REJECT**

| Criterion | Separate layers | Master registry |
|---|---|---|
| Coupling | Low | High |
| Maintainability | Manual YAML, clear roles | Single file complexity |
| Tenant overrides | metadata_framework merge works | Conflicts with schema-driven UI |
| Runtime complexity | Each layer loaded where needed | Cross-cutting dependency graph |
| Backwards compatibility | Proven Phases 0–6A | Breaking migration |
| Testing burden | Layer-specific tests exist | Full re-characterization |
| Configuration simplicity | Multiple files, clear purpose | Apparent simplicity, hidden coupling |

**INFERENCE:** Master registry **does not solve a demonstrated problem** and **violates approved Phase 5/6 boundaries**.

---

## Appendix — Fact / Inference / Decision Key

| Label | Meaning |
|---|---|
| **FACT** | Established directly from repository files, tests, or commits |
| **INFERENCE** | Conclusion derived from multiple facts |
| **DECISION REQUIRED** | Stakeholder choice; not safely derivable from code alone |
