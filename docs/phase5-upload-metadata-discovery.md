# Phase 5 — Upload Metadata Experience Discovery

**Status:** Discovery / approval only — no implementation in this phase.  
**Branch baseline:** `feature/dis_metadata` @ `14fd727` (Phase 4 complete).  
**Investigation date:** 2026-08-31.

---

## 1. Baseline

| Check | Result |
|---|---|
| **branch** | `feature/dis_metadata` |
| **HEAD** | `14fd727b73ca7d24f4dec858e8b728360867d5bd` |
| **working_tree** | CLEAN (no staged/unstaged changes before this document was added) |
| **Phase_4_commit_present** | YES — `14fd727 feat: add metadata schema validation report` is HEAD |

---

## 2. Current Upload Architecture

### End-to-end flow (evidence-based)

The primary upload path for Source Library documents is:

```
Frontend (SourceLibraryPage)
  ↓  POST multipart FormData
CAS proxy (/api/v1/source-library/documents/upload)
  ↓  client resolution, block derivation, format gate
DISClient.upload_documents() → POST /v1/ingest/upload (one file per request)
  ↓  ValidationService (file admission), S3 storage, job creation
Immediate Source Library payload (_publish_immediate_payload / _build_source_library_payload)
  ↓  compact source index write (preview metadata)
Background pipeline (_process → run_pipeline → STEP_ORDER agents)
  ↓  metadata_extraction (LLM + metadata_schemas)
  ↓  metadata_tagging (enrich_metadata + apply_metadata_hints)
  ↓  validation_report (validate_metadata_against_schema — report-only)
  ↓  finalize → processed storage / vector / structure stores
```

### Step-by-step with files and symbols

#### Frontend

| Step | File | Symbol |
|---|---|---|
| Upload UI | `frontend/src/features/sourceLibrary/pages/SourceLibraryPage/SourceLibraryPage.jsx` | `SourceLibraryPage`, upload `<form onSubmit={handleUpload}>` |
| Upload handler | same | `handleUpload()` — builds `FormData`, appends `files`, `purpose`, `document_type`, `project_id`, `course_id`, folder paths |
| API client | `frontend/src/features/sourceLibrary/services/sourceLibraryApi.js` | `sourceLibraryApi.uploadDocument()` |
| Endpoint | `frontend/src/services/endpoints.js` | `SOURCE_LIBRARY.UPLOAD` → `/api/v1/source-library/documents/upload` |
| UI config load | `SourceLibraryPage.jsx` | `sourceLibraryApi.uiConfig()` → populates `uiConfig` state |
| Upload format policy | `frontend/src/features/sourceLibrary/utils/uploadPolicy.js` | `acceptAttribute()`, `rejectionReason()` — mirrors server `upload_formats.json` |
| Filter UI (not upload) | `frontend/src/features/sourceLibrary/utils/taxonomyFilters.js` | `taxonomyFiltersFromUiConfig()` |

**FACT:** Source Library does **not** use `frontend/src/components/common/FileUpload/FileUpload.jsx`.

#### CAS (Content AI Studio proxy)

| Step | File | Symbol |
|---|---|---|
| Upload route | `app/api/v1/routers/source_library.py` | `upload_source_document()` |
| UI config proxy | same | `get_source_ui_config()` → `dis_client.ui_config()` |
| Upload policy | same | `get_upload_policy()` → `load_upload_formats()` |
| DIS client | `app/core/dis_client.py` | `DISClient.upload_document()`, `upload_documents()` |
| Client resolution | `app/api/v1/routers/source_library.py` | `_resolved_client_async()` |
| Block derivation | same | `_block_from_course()` when form `block` is blank |
| Filter forwarding | `app/core/source_library_filter_forward.py` | `build_cas_documents_library_params()` |

**FACT:** CAS hardcodes `"visibility": "internal"` in `form_fields` when forwarding to DIS (`source_library.py` lines 345–360). The frontend upload form does not expose `visibility`.

#### DIS ingestion

| Step | File | Symbol |
|---|---|---|
| Upload endpoint | `dis_backend/api/routers/ingestion.py` | `upload_file()` — `POST /v1/ingest/upload` |
| Metadata hints | same | `metadata_hints` dict from form fields |
| Immediate payload | same | `_build_source_library_payload()`, `_publish_immediate_payload()` |
| Background job | same | `_process()` → pipeline |
| Upload intake agent | `dis_backend/services/agents/upload_intake_agent.py` | `UploadIntakeAgent.run()` — validates raw bytes only; **no metadata logic** |
| Pipeline order | `dis_backend/services/agents/registry.py` | `STEP_ORDER` |
| Metadata extraction | `dis_backend/services/agents/metadata_extraction_agent.py` | `MetadataExtractionAgent.run()` — uses `get_metadata_schema()` for LLM prompt |
| Metadata tagging | `dis_backend/services/agents/metadata_tagging_agent.py` | `MetadataTaggingAgent`, `apply_metadata_hints()` |
| Schema validation | `dis_backend/services/agents/validation_report_agent.py` | `ValidationReportAgent.run()` — report-only |
| Validator | `dis_backend/services/metadata_schema_validate.py` | `validate_metadata_against_schema()` |

#### DIS context / UI config (filters, not upload form)

| Step | File | Symbol |
|---|---|---|
| UI config API | `dis_backend/api/routers/context.py` | `source_ui_config()` — `GET /v1/context/ui-config` |
| Config builder | `dis_backend/services/context_retrieval.py` | `ContextRetrievalService.ui_config()`, `_source_ui_config()` |
| Admin schema dump | `dis_backend/api/routers/admin.py` | `_client_config_summary()` — includes `metadata_schema` (super_admin only) |

#### Other upload surfaces (out of Source Library metadata path)

| Surface | Path | Notes |
|---|---|---|
| Style document upload | `frontend` Style pages → CAS `/styles` | Different API; `validation.js` `uploadDocumentSchema` |
| Folder scan | `SourceLibraryPage.handleFolderScan()` → CAS → DIS `/v1/ingest/folder-scan` | Server-side bulk; `block` derived like upload |
| Batch upload | `dis_backend/api/routers/ingestion.py` `batch_upload()` | Minimal hints: `course_id`, `course_name` only |

---

## 3. Current Metadata Sources

| Source | Location | Responsibility | Consumed by |
|---|---|---|---|
| **Upload form (frontend)** | `SourceLibraryPage.jsx` JSX + `handleUpload()` | User-editable `purpose`, `document_type`; scope fields `project_id`, `course_id`; folder paths | CAS upload proxy |
| **CAS upload Form model** | `app/api/v1/routers/source_library.py` `upload_source_document()` | Structural upload API: accepts taxonomy hint fields even when UI omits them | DIS `/ingest/upload` |
| **CAS derived metadata** | `_block_from_course()`, `_resolved_client_async()` | Tenant/client resolution; block hint when blank | DIS `metadata_hints` |
| **DIS upload Form model** | `dis_backend/api/routers/ingestion.py` `upload_file()` | Same hint field names as CAS | `metadata_hints`, immediate payload |
| **Immediate payload builder** | `_build_source_library_payload()` | Preview metadata + purpose flags | Source Library index |
| **metadata_schemas (tenant YAML)** | `dis_backend/config/clients/*.yaml` → `TenantConfig.metadata_schemas` | LLM extraction field list; report-only validation | `MetadataExtractionAgent`, `ValidationReportAgent` |
| **Field Registry** | `dis_backend/services/metadata_framework/registry.py` `DEFAULT_FIELDS` | Index/retrieval/filter_options/cas_list promotion | `compact_source_record`, filter APIs, listing |
| **retrieval.source_ui (tenant YAML)** | e.g. `aim.yaml` `retrieval.source_ui.taxonomy_filters` | Filter sidebar labels/types | `ContextRetrievalService.ui_config()` → frontend filters |
| **retrieval.purpose_labels** | tenant YAML | Display labels for purpose values | Frontend purpose dropdown/filter labels |
| **Client profile transforms** | e.g. `dis_backend/services/client_profiles/aim.py` `enrich_metadata()` | AIM-specific inference / block-scope rules | `MetadataTaggingAgent` |
| **Upload format policy** | `config/upload_formats.json` | Allowed/blocked extensions | CAS + frontend client gate |
| **System-derived fields** | `_build_source_library_payload()`, pipeline agents | `file_sha256`, `content_hash`, `title`, `status`, tags, etc. | Storage, validation report, index |

---

## 4. Hardcoded Upload Metadata

Only fields **actually found** in the repository are listed.

| Field / Concept | Current Source | Backend/Frontend | Purpose | Hardcoded? | Tenant-specific? |
|---|---|---|---|---|---|
| `files` | Frontend file input | Frontend | File payload | Hardcoded control | No |
| `purpose` | `SourceLibraryPage.jsx` `<select name="purpose">` + `purposes()` | Both | Operational routing (`use_for_*` flags) | Options hardcoded in JS; labels from `uiConfig.purpose_labels` | Labels tenant-specific; values fixed set |
| `document_type` | `SourceLibraryPage.jsx` free-text `<input>` | Both | Type hint / auto-detect fallback | Hardcoded control (text, not enum) | No (user free text) |
| `project_id` | `handleUpload()` from Redux | Both | CAS client/tenant resolution | Programmatic | Per session |
| `course_id` | `handleUpload()` scope or `-1` global | Both | Course scoping / global upload sentinel | Programmatic | Per session |
| `source_relative_path` | Folder upload `webkitRelativePath` | Both | Preserve folder structure in S3 | Programmatic | Per file |
| `source_root` | First path segment | Both | Folder root hint for tagging | Programmatic | Per file |
| `client_id` | CAS `_resolved_client_async()` | Backend | Tenant routing | Derived, not user input | Yes |
| `visibility` | CAS `form_fields` | Backend only | Access level hint | **Hardcoded `"internal"`** in CAS | No |
| `block` | CAS `_block_from_course()` when blank | Backend (optional form) | Taxonomy / retrieval scoping | Derived from course DB; form field exists but UI does not send | Tenant-dependent behavior |
| `day`, `chapter`, `module_name`, `learning_objective`, `course_name` | CAS/DIS Form parameters | Backend API only | Taxonomy hints | Accepted by API; **not collected in upload UI** | Could be tenant-relevant |
| `file_sha256`, `content_hash` | DIS `_build_source_library_payload()` | Backend | Dedup / schema validation | System-derived | No |
| `title`, `status`, `doc_type` | DIS immediate payload + pipeline | Backend | Index preview | System-derived / inferred | Partially tenant via inference |
| `use_for_style`, `use_for_cdd`, etc. | `_purpose_flags()` | Backend | Purpose routing flags | Derived from `purpose` + `document_type` | No |
| Upload max 500 MB | `SourceLibraryPage.jsx` `MAX_UPLOAD_BYTES` | Frontend | Client-side batch cap | Hardcoded | No |
| Client-side extension filter | `SUPPORTED_EXT` regex in `handleUpload()` | Frontend | Pre-upload rejection | Hardcoded (may drift from server policy) | No |
| `metadata_schemas` enum values (e.g. AIM `document_type`) | Tenant YAML | Backend (LLM + report) | Post-upload validation / extraction | Tenant YAML | Yes — **not wired to upload form** |
| Filter taxonomy fields | `retrieval.source_ui.taxonomy_filters` | Frontend filters only | Listing/filter UX | Tenant YAML for labels/types | Yes — **not upload form** |

### Metadata category summary

| Category | Examples | Upload form today? |
|---|---|---|
| **A. Structural upload API** | `files`, `client_id`, `project_id`, `course_id`, folder paths | Partially (scope + files) |
| **B. Tenant/client metadata** | `block`, `day`, `document_type`, AIM `topic`, Cengage `product_title` | Minimal (`document_type` free text only) |
| **C. Field Registry metadata** | `course_name`, `block`, `module_name`, … promotion keys | Not on upload form; filters/listing only |
| **D. UI/form presentation** | `purpose` labels, filter `label`/`type`, placeholders | Hardcoded upload controls; filters dynamic |
| **E. Derived/system metadata** | `file_sha256`, `content_hash`, `title`, pipeline tags | Never user-collected |

---

## 5. metadata_schemas Capability Assessment

**Model (FACT):** `dis_backend/config/settings.py`

```python
class MetadataField(BaseModel):
    name: str
    type: str = "string"          # string | enum | list | number | date | boolean
    values: List[str] = []
    hint: str = ""
    applies_to: List[str] = []

class MetadataSchema(BaseModel):
    required_fields: List[MetadataField] = []
    optional_fields: List[MetadataField] = []
```

**Phase 4 validation behavior (FACT):** `validate_metadata_against_schema()` checks required presence and enum membership only; does **not** enforce `type` or `applies_to`; report-only via `validation_report.metadata_validation.findings`.

| Capability | Status | Evidence | Notes |
|---|---|---|---|
| Field name | **PRESENT** | `MetadataField.name`; YAML `name:` keys | Canonical key for validation/extraction |
| Display label | **ABSENT** | No property on `MetadataField` | `hint` is LLM guidance, not UI label |
| Description / help text | **PARTIALLY PRESENT** | `MetadataField.hint` | Used in LLM prompt (`metadata_extraction_agent.py`); not structured help text |
| Requiredness | **PRESENT** | `required_fields` vs `optional_fields` | Validation only; not upload-gating |
| Values / options | **PARTIALLY PRESENT** | `type: enum` + `values: [...]` | Only for enum validation; no select label map |
| Data type | **PARTIALLY PRESENT** | `MetadataField.type` | Documented types not enforced by validator |
| Control / input type | **ABSENT** | — | No select/text/checkbox semantics |
| Ordering | **ABSENT** | YAML list order implicit | Not exposed as API contract |
| Grouping / sections | **ABSENT** | — | — |
| Default value | **ABSENT** | — | Upload defaults live in CAS/frontend code |
| Placeholder | **ABSENT** | — | Frontend hardcodes e.g. "Optional, auto-detect if blank" |
| Visibility | **ABSENT** | — | — |
| Conditional visibility | **ABSENT** | — | — |
| `applies_to` | **UNDEFINED / SEMANTICALLY UNCLEAR** | Model field exists; `validate_metadata_against_schema` ignores it; no AIM/Cengage YAML usage | Phase 4 explicitly deferred |
| Validation rules | **PARTIALLY PRESENT** | Required + enum membership only | No min/max, regex, cross-field rules |
| Multi-value behavior | **PARTIALLY PRESENT** | `type: list` in schema; validator checks list members for enum | Upload collection semantics not defined |

**INFERENCE:** `metadata_schemas` are sufficient for **post-ingestion domain validation and LLM extraction prompting**, but **insufficient alone** for upload form generation without inventing UI semantics.

---

## 6. Field Registry Boundary

### What Field Registry represents (FACT)

From `dis_backend/services/metadata_framework/registry.py` module docstring and `FieldSpec`:

- Defines which metadata fields are **promoted** to `index`, `retrieval`, `filter_options`, `cas_list`
- Provides extraction strategies (`extract: meta_or_empty`, etc.)
- Falls back to `DEFAULT_FIELDS` when client YAML has no `metadata_framework` block
- **FACT:** No production client YAML (`dis_backend/config/clients/*.yaml`) defines `metadata_framework`; all tenants use `DEFAULT_REGISTRY`

### What metadata_schemas represent (FACT)

- Tenant/client-scoped **domain field definitions** for LLM metadata extraction and report-only validation
- Loaded via `TenantConfig.get_metadata_schema(client_id)`
- Explicitly **not** merged into Field Registry (`metadata_schema_validate.py` docstring)

### Overlap and differences

| Concern | Field Registry | metadata_schemas |
|---|---|---|
| Field names | `FieldSpec.key` | `MetadataField.name` |
| Overlapping keys | `document_type`, `course_name`, `block`, `module_name`, … | Same names appear in AIM/Cengage schemas |
| Purpose | Storage projection + filters + listing columns | Extraction + validation reporting |
| UI driving | Indirect — `filter_options` keys authorize filter query params | None today |
| Tenant override | Optional `metadata_framework.fields` (unused in prod YAML) | Required `metadata_schemas.<client_id>` in prod YAML |

**FACT:** Overlap in field **names** is intentional but responsibilities differ. Phase 4 preserved `metadata_schemas ≠ Field Registry`.

### Is upload-form definition a third concern?

**INFERENCE (supported by evidence):** Yes.

1. **Upload API structural fields** (`files`, `course_id`, `purpose`) are not in metadata_schemas.
2. **Field Registry** governs post-storage projection, not user data entry.
3. **metadata_schemas** include system-populated required fields (Cengage `file_sha256`, `content_hash`) unsuitable for upload forms.
4. **retrieval.source_ui.taxonomy_filters** already demonstrates a **separate UI configuration** pattern for filters (key, label, type) without merging into Field Registry or metadata_schemas.

**Recommendation for boundary preservation:** Upload form configuration should remain a **presentation-layer concern** that **references** domain field names where appropriate, without merging metadata_schemas into Field Registry or altering registry promotion rules.

---

## 7. CAS Impact

### What CAS currently owns (evidence)

| Concern | CAS ownership | Evidence |
|---|---|---|
| Upload metadata collection | **Partial** — proxy only; UI collects minimal fields | `upload_source_document()` Form params |
| Metadata schema definitions | **None** | Schemas live in DIS tenant YAML |
| Metadata validation | **None** (file format gate only) | `_reject_unsupported()`, not schema validation |
| Upload form configuration | **None** — serves DIS ui-config unchanged (+ client_aliases) | `get_source_ui_config()` |
| Metadata persistence | **None** | DIS stores to S3/index |
| Metadata transformation | **Limited** — client resolution, block derivation, hardcoded visibility | `_resolved_client_async()`, `_block_from_course()` |

### Impact classification (future schema-driven upload)

| Change area | Classification | Rationale |
|---|---|---|
| Expose upload form config via ui-config | **READ-ONLY INTEGRATION** or **API CHANGE REQUIRED** | Depends whether DIS extends `/context/ui-config` or new endpoint; CAS proxy would forward |
| Accept new dynamic metadata form fields | **API CHANGE REQUIRED** | Today Form params are fixed in `upload_source_document()` |
| Schema validation at upload | **API CHANGE REQUIRED** | Not present today; Phase 4 validation is DIS report-only |
| Block/course derivation | **NONE** (should remain) | Independent of form definition |
| Hardcoded `visibility: internal` | **API CHANGE REQUIRED** if upload form exposes visibility | Currently CAS-overridden |

---

## 8. DIS Impact

### Current DIS upload metadata handling (FACT)

- Fixed Form parameters on `upload_file()` → `metadata_hints`
- No call to `get_metadata_schema()` or `validate_metadata_against_schema()` at upload admission
- Schema used later in pipeline (`metadata_extraction`, `validation_report`)

### Potential future changes — classification

| Change area | Classification | Rationale |
|---|---|---|
| Serve upload form definition to CAS/frontend | **API CHANGE REQUIRED** | Not in `ui_config()` today; admin exposes schema to super_admin only |
| Load tenant upload UI config from YAML | **DATA MODEL CHANGE REQUIRED** (config only, not DB) | New config section; no migration unless persisted forms added |
| Upload-time schema validation (hard-fail) | **API CHANGE REQUIRED** | Explicitly out of Phase 4/5 scope |
| Report-only schema validation | **NONE** | Already implemented Phase 4 |
| Extend `metadata_hints` dynamically | **API CHANGE REQUIRED** | Fixed dict keys in `ingestion.py` |
| Field Registry changes for upload | **NONE** | Registry should remain projection-focused |
| `metadata_extraction` prompt building | **READ-ONLY INTEGRATION** | Already reads metadata_schemas; unchanged if upload adds hints |

---

## 9. Frontend Impact

### Current state (FACT)

| Aspect | Finding |
|---|---|
| Form definition location | Hardcoded JSX in `SourceLibraryPage.jsx` (lines ~659–681) |
| Dynamic vs static | **Static** upload form; **dynamic** filter sidebar via `taxonomyFiltersFromUiConfig(uiConfig)` |
| Required fields | No HTML `required`; `purpose` defaults to `general_reference`; `document_type` optional |
| Select options | `purpose` — hardcoded value list; `document_type` — free text (not enum from schema) |
| Error display | Toast + inline upload queue status; server errors via `errorMessage()` |
| Submission shape | Multipart FormData with named fields (not generic JSON metadata object) |
| Schema-driven form mechanism | **None for upload.** Closest precedent: taxonomy filter rendering (select/text from config) |

### Minimum future frontend work (INFERENCE — not implemented)

1. Fetch upload form definition from ui-config (or dedicated endpoint proxied by CAS).
2. Render fields dynamically (reuse taxonomy filter rendering patterns: `key`, `label`, `type`, options).
3. Map submitted values onto existing CAS/DIS Form field names (or new generic envelope if approved).
4. Preserve backward-compatible defaults when config absent (current static form behavior).
5. Keep upload format policy server-driven (`getUploadPolicy()`).
6. Do **not** conflate filter taxonomy config with upload metadata config without explicit field-role decisions.

---

## 10. Architectural Options

### OPTION A — metadata_schemas directly become the upload-form definition

| Criterion | Assessment |
|---|---|
| Architectural fit | **Poor** — schemas mix system-required fields (`file_sha256`) with user-facing fields (`topic`) |
| Separation of concerns | **Weak** — couples LLM/validation domain to UI presentation |
| Tenant configurability | **Partial** — enum values present; labels/controls absent |
| Backward compatibility | **Risky** — exposing all required_fields would show non-uploadable fields |
| CAS / DIS / Frontend impact | Medium — could skip new config, but requires UI inference rules not in repo |
| Field Registry impact | **NONE** if boundary preserved |
| Migration complexity | Low config churn, high semantic risk |
| Validation impact | Could accidentally imply upload-time hard-fail |
| Coupling risk | **High** |
| Duplication risk | Low field-name duplication, high semantic duplication |

### OPTION B — metadata_schemas remain validation/domain schema + separate UI config (e.g. uiConfig)

| Criterion | Assessment |
|---|---|
| Architectural fit | **Strong** — mirrors existing `retrieval.source_ui.taxonomy_filters` pattern |
| Separation of concerns | **Strong** — domain schema vs presentation |
| Tenant configurability | **High** — uiConfig adds label, control, order, visibility |
| Backward compatibility | **Strong** — absent uiConfig ⇒ keep static form |
| CAS / DIS / Frontend impact | Medium — extend ui-config payload; CAS continues proxy |
| Field Registry impact | **NONE** |
| Migration complexity | Medium — add YAML + API surface |
| Validation impact | Unchanged if Phase 4 report-only preserved |
| Coupling risk | **Low–medium** — uiConfig references field names |
| Duplication risk | **Controlled** — enum options may reference schema values |

### OPTION C — metadata_schemas remain validation/domain + fully separate form-definition schema

| Criterion | Assessment |
|---|---|
| Architectural fit | **Good** — clearest separation |
| Separation of concerns | **Strongest** |
| Tenant configurability | **High** |
| Backward compatibility | **Strong** |
| CAS / DIS / Frontend impact | Medium–high — third config artifact to load/version |
| Field Registry impact | **NONE** |
| Migration complexity | **Higher** — new schema type, linking rules, tooling |
| Validation impact | Unchanged if boundaries held |
| Coupling risk | **Low** |
| Duplication risk | **Higher** — field names/types/enums may repeat across metadata_schemas and form schema |

### Additional option surfaced by repository evidence

**OPTION B′ — UI config under `retrieval.source_ui` (parallel to `taxonomy_filters`)**

- **FACT:** Filter UI already uses `retrieval.source_ui.taxonomy_filters` with `{key, label, type}`.
- Upload UI config could be `retrieval.source_ui.upload_metadata_fields` referencing domain field names validated by metadata_schemas post-ingestion.
- Avoids nesting presentation inside `metadata_schemas` while staying tenant-scoped in existing YAML.

---

## 11. Recommended Architecture

**Recommendation: OPTION B (domain schema + attached UI configuration), implemented preferentially as OPTION B′ under `retrieval.source_ui` unless stakeholders explicitly prefer nesting uiConfig inside `metadata_schemas`.**

### Why (repository evidence)

1. **metadata_schemas lack UI primitives** (labels, control types, ordering, visibility) — Section 5.
2. **metadata_schemas include non-uploadable required fields** (Cengage `file_sha256`, `content_hash`) — `cengage.yaml`.
3. **Existing dynamic UI precedent** is `retrieval.source_ui.taxonomy_filters`, not metadata_schemas — `context_retrieval.py`, `taxonomyFilters.js`.
4. **Phase 4 boundaries must hold:** report-only validation, no Field Registry merge, no upload hard-fail — `metadata_schema_validate.py`, `validation_report_agent.py`.
5. **Field Registry remains projection/filter authority** — separate from upload collection.

### What remains unchanged

- Field Registry design and DEFAULT_REGISTRY behavior
- metadata_schemas validation semantics (report-only; no `applies_to` enforcement)
- DIS pipeline step order and agent responsibilities
- AIM/Cengage transform profiles (`enrich_metadata`)
- OpenSearch / reindex behavior
- Upload admission validation (`ValidationService` file checks)
- CAS block derivation and client resolution

### What a future implementation phase would add

1. Tenant YAML upload UI config (field list with `key`, `label`, `type`, `required`, `order`, optional `options_ref` / `values`)
2. DIS `ui_config()` (or dedicated endpoint) exposing upload form definition to CAS/frontend
3. Frontend dynamic upload form renderer (pattern reuse from taxonomy filters)
4. Optional CAS Form param generalization for configured fields already accepted by DIS (`block`, `day`, …)
5. Documentation linking ui field keys → `metadata_hints` → metadata_schemas field names

### Boundaries preserved

```
metadata_schemas     → extraction + report-only validation
Field Registry       → index / filter / listing projection
upload UI config     → presentation + collectable hints only
client profiles      → inference transforms (AIM/Cengage)
```

---

## 12. Open Decisions

DECISION-001  
**Question:** Where should upload UI configuration live in tenant YAML?  
**Options:**  
A. Nested `uiConfig` under `metadata_schemas.<client_id>`  
B. `retrieval.source_ui.upload_metadata_fields` (parallel to `taxonomy_filters`)  
C. Separate top-level `upload_form` section  
**Recommendation:** B — matches existing `source_ui` consumer (`ContextRetrievalService.ui_config()`) and keeps metadata_schemas domain-pure.  
**Reason:** Repository already serves filter UI from `retrieval.source_ui`; frontend already consumes `uiConfig.source_library.*`.

DECISION-002  
**Question:** Which metadata_schemas fields should be upload-collectable vs system-derived only?  
**Options:**  
A. All `required_fields` + `optional_fields`  
B. Explicit upload UI config list only (recommended)  
C. Required fields minus a denylist of system keys  
**Recommendation:** B.  
**Reason:** Cengage schema requires `file_sha256`, `content_hash`, `client_id` — populated by pipeline, not users.

DECISION-003  
**Question:** Should upload enforce metadata_schemas required fields (hard-fail)?  
**Options:**  
A. Yes — reject upload at API  
B. No — keep Phase 4 report-only validation  
C. Soft warning in UI only  
**Recommendation:** B (preserve Phase 4).  
**Reason:** Phase 4 explicitly established non-blocking findings; user query forbids ingestion hard-fail.

DECISION-004  
**Question:** Should `document_type` on upload become a schema-driven enum select per tenant?  
**Options:**  
A. Yes — bind to metadata_schemas enum values  
B. Optional enum when uiConfig specifies; else free text  
C. Keep free text forever  
**Recommendation:** B.  
**Reason:** AIM/Cengage define enum values in metadata_schemas but upload currently uses free text; backward compatibility requires fallback.

DECISION-005  
**Question:** Should `purpose` remain a fixed structural upload field outside schema-driven config?  
**Options:**  
A. Yes — operational routing field, not tenant metadata  
B. Move into tenant upload UI config  
C. Hybrid — fixed values, tenant labels only (current partial state)  
**Recommendation:** C short-term; revisit if tenants need custom purposes.  
**Reason:** `purpose` drives `_purpose_flags()` and is not in metadata_schemas; labels already tenant-configurable via `purpose_labels`.

DECISION-006  
**Question:** When upload UI collects taxonomy fields (`block`, `day`, …), how do they relate to filter taxonomy config?  
**Options:**  
A. Duplicate config in upload UI section  
B. Shared field dictionary referenced by both filters and upload  
C. Upload inherits subset of taxonomy_filters  
**Recommendation:** Explicit upload config referencing same keys (A or B) — **NOT ESTABLISHED FROM CURRENT REPOSITORY** which is preferable long-term.  
**Reason:** Filters and upload serve different lifecycle moments; implicit inheritance risks showing filter-only fields on upload.

DECISION-007  
**Question:** Should CAS continue hardcoding `visibility: internal`?  
**Options:**  
A. Yes  
B. Expose via upload UI config  
C. Defer to DIS/client profile defaults only  
**Recommendation:** Defer change until upload UI config approved; document current behavior.  
**Reason:** Empty DIS default was changed to avoid clobbering inference; CAS override is load-bearing today.

DECISION-008  
**Question:** Should `applies_to` eventually filter upload-form fields by document type?  
**Options:**  
A. Yes, when semantics defined  
B. No  
C. Defer (Phase 4 stance)  
**Recommendation:** C — do not define semantics in this phase.  
**Reason:** No runtime consumer; test explicitly documents deferral.

---

## 13. Proposed Future Implementation Scope

### IN SCOPE (likely Phase 6+)

- Define tenant upload UI config schema (YAML + Pydantic passthrough)
- Expose upload form definition via DIS ui-config (additive JSON)
- CAS proxy forwarding (no business logic change beyond optional Form param passthrough)
- Frontend dynamic upload form rendering with static fallback
- Link upload field keys to existing DIS `metadata_hints` / CAS Form params
- Tests for config loading, ui-config shape, frontend rendering contract
- Documentation of field-role matrix (structural vs collectable vs derived)

### OUT OF SCOPE

- Field Registry redesign or merge with metadata_schemas
- Upload-time schema hard-fail / pipeline behavior change
- OpenSearch mapping or reindex changes
- AIM/Cengage `enrich_metadata` transform changes
- DB migrations
- S3 layout changes
- Implementing full conditional visibility engine
- Defining `applies_to` enforcement semantics
- Merging metadata_schemas into Field Registry

### DEPENDENCIES

- Stakeholder approval on DECISION-001 through DECISION-008
- Agreement on ui-config JSON contract (field shape, versioning)
- Tenant YAML authoring for initial clients (aim, cengage, academian)

### RISKS

- Duplication between upload UI config and metadata_schemas enum lists if not linked by convention
- Frontend/client extension regex (`SUPPORTED_EXT`) drifting from server upload policy
- Exposing taxonomy fields on upload without block-derivation rules may regress AIM retrieval scoping
- Expanding CAS Form surface without DIS parity could drop fields silently

---

## 14. Explicit Non-Goals

This discovery phase and the recommended future phase **do not** pursue:

- [x] No Field Registry redesign
- [x] No metadata_schemas merge into Field Registry
- [x] No upload form implementation (this phase)
- [x] No frontend implementation (this phase)
- [x] No OpenSearch changes
- [x] No reindexing
- [x] No ingestion hard-fail
- [x] No AIM transform changes
- [x] No Cengage transform changes
- [x] No DB migration unless explicitly approved later
- [x] No S3 changes
- [x] No unrelated refactoring

---

## 15. Evidence Index

| File | Relevant symbols / configuration |
|---|---|
| `frontend/src/features/sourceLibrary/pages/SourceLibraryPage/SourceLibraryPage.jsx` | `SourceLibraryPage`, `handleUpload`, `purposes()`, upload form JSX, filter rendering |
| `frontend/src/features/sourceLibrary/services/sourceLibraryApi.js` | `uploadDocument`, `uiConfig`, `getUploadPolicy` |
| `frontend/src/features/sourceLibrary/utils/taxonomyFilters.js` | `taxonomyFiltersFromUiConfig`, `toSourceLibraryApiFilters` |
| `frontend/src/features/sourceLibrary/utils/uploadPolicy.js` | `acceptAttribute`, `rejectionReason` |
| `frontend/src/services/endpoints.js` | `SOURCE_LIBRARY.*` routes |
| `app/api/v1/routers/source_library.py` | `upload_source_document`, `get_source_ui_config`, `get_upload_policy`, `_block_from_course` |
| `app/core/dis_client.py` | `upload_document`, `upload_documents`, `ui_config` |
| `app/core/dis_access.py` | `CLIENT_NAME_ALIASES` |
| `app/core/upload_formats.py` | `load_upload_formats` |
| `config/upload_formats.json` | Supported/blocked extensions |
| `dis_backend/api/routers/ingestion.py` | `upload_file`, `_build_source_library_payload`, `metadata_hints`, `_process` |
| `dis_backend/api/routers/context.py` | `source_ui_config` |
| `dis_backend/api/routers/admin.py` | `_client_config_summary` (`metadata_schema`) |
| `dis_backend/config/settings.py` | `MetadataField`, `MetadataSchema`, `get_metadata_schema`, tenant YAML loading |
| `dis_backend/config/clients/aim.yaml` | `metadata_schemas.aim`, `retrieval.source_ui`, `purpose_labels` |
| `dis_backend/config/clients/cengage.yaml` | `metadata_schemas.cengage` |
| `dis_backend/config/clients/academian.yaml` | `metadata_schemas.academian` |
| `dis_backend/services/context_retrieval.py` | `ContextRetrievalService.ui_config`, `_source_ui_config` |
| `dis_backend/services/metadata_schema_validate.py` | `validate_metadata_against_schema`, `findings_to_report_section` |
| `dis_backend/services/metadata_framework/registry.py` | `FieldSpec`, `DEFAULT_FIELDS`, `registry_for_tenant` |
| `dis_backend/services/metadata_framework/filter_query.py` | `build_documents_library_filters` |
| `dis_backend/services/agents/registry.py` | `STEP_ORDER`, `AGENT_REGISTRY` |
| `dis_backend/services/agents/upload_intake_agent.py` | `UploadIntakeAgent` |
| `dis_backend/services/agents/metadata_extraction_agent.py` | `MetadataExtractionAgent` (schema → LLM prompt) |
| `dis_backend/services/agents/metadata_tagging_agent.py` | `apply_metadata_hints`, `MetadataTaggingAgent` |
| `dis_backend/services/agents/validation_report_agent.py` | `ValidationReportAgent` (Phase 4 findings) |
| `dis_backend/services/client_profiles/aim.py` | `enrich_metadata` |
| `dis_backend/tests/test_metadata_schema_validation_phase4.py` | Schema validation behavior, `applies_to` deferred test |
| `dis_backend/tests/test_metadata_framework_phase1_registry.py` | Field Registry characterization |

---

## Appendix — Duplication Analysis

| Concept | Locations | Classification |
|---|---|---|
| Purpose values | Frontend `purposes()`, DIS normalization, YAML `purpose_labels` | **DUPLICATED BUT ACCEPTABLE** — values structural; labels tenant-specific |
| Purpose flags | `_purpose_flags()` in ingestion | **SAFE / INTENTIONAL** — derived from purpose |
| Taxonomy field names | CAS Form params, DIS hints, Field Registry keys, metadata_schemas names, filter uiConfig | **ARCHITECTURAL DUPLICATION TO REMOVE** (future) — needs explicit field-role map, not silent merge |
| Document type enums | metadata_schemas YAML vs free-text upload input | **ARCHITECTURAL DUPLICATION TO REMOVE** (future) |
| Upload extension rules | `upload_formats.json`, frontend `uploadPolicy.js`, `handleUpload` regex | **DUPLICATED BUT ACCEPTABLE** with drift risk — server is authoritative |
| Filter UI config vs upload form | Both could describe `block`, `day`, … | **UNKNOWN — NEEDS DECISION** (DECISION-006) |
| metadata_schemas vs Field Registry field lists | Overlapping keys, different jobs | **SAFE / INTENTIONAL** if boundaries preserved |

---

## Appendix — Backward Compatibility Requirements

| Scenario | Expected future behavior |
|---|---|
| `metadata_schemas` empty | No validation findings (already); upload form falls back to static fields |
| `metadata_schemas` absent for client | `get_metadata_schema()` returns empty `MetadataSchema()` |
| Upload UI config absent | Frontend renders current static form (`purpose`, `document_type`, files) |
| Existing API clients send current Form fields | CAS/DIS continue accepting fixed Form parameters |
| No ui-config upload section | No change to upload UX |
| Report-only validation | Metadata findings remain non-blocking |

**FACT:** DIS upload API already accepts taxonomy hint fields (`block`, `day`, …) that the current frontend does not send; adding UI fields is additive for typical users.
