# Phase 9 — Metadata Editor UI Discovery

**Status:** DISCOVERY ONLY — no implementation, no API, no migration, no application code changes.  
**Investigation date:** 2026-09-01.  
**Reference source of truth:** `cengage-publishing-platform.html` (supplied Cengage reference HTML).  
**Classification legend:** **FACT**, **INFERENCE**, **DECISION REQUIRED**.

---

## 1. Executive Summary

Phase 9 Stage A establishes that the Cengage reference HTML defines a **full-page Metadata Editor** with a mandatory **three-tab structure** (`AI Metadata`, `Taxonomy & Standards`, `Relationships`), a **300px left document panel**, provenance banners, chip/tag inputs, relationship linking, **unsaved-changes indicator**, and **Revert to AI values / Cancel / Save metadata** footer actions.

**FACT.** The existing Content AI Studio application has **no post-ingestion metadata editor**. Source Library metadata is **write-once at upload** and **read-only thereafter** (`SourceLibraryPage.jsx` detail panel shows content preview only).

**FACT.** The backend has **no PATCH/PUT metadata endpoint** for ingested source documents. Metadata lives primarily in **S3** (`studio_payload/payload.json`, `extracted/metadata.json`, `source_index/source_list.json`) with optional **RDS `metadata_json`** and **OpenSearch** mirrors.

**INFERENCE.** Implementing the reference UI faithfully requires:
1. A new **Metadata Editor page/route** in the React frontend reproducing the reference layout and all three tabs.
2. New **GET/PATCH metadata** and **POST revert-ai** APIs with multi-store cascade (S3 payload → compact index → optional RDS/OS).
3. **Immutable AI snapshot** storage (likely `extracted/metadata.json`) to support Revert to AI values.
4. New **relationship and taxonomy document fields** in `payload.metadata` (not presently modeled end-to-end).
5. **No Field Registry / metadata_schemas / source_ui merge** — Phase 6B authority separation preserved.

**Migrations required: NO** (formal RDS migration). User-edited metadata and relationships can be stored as **new keys in existing JSON artifacts**; index re-projection uses existing Field Registry adapters. A **JSON schema extension** (not a SQL migration) is required for relationship keys and AI snapshot immutability policy.

**Final verdict (Stage A):** Proceed to Stage B implementation with a **reference-faithful UI shell + backend metadata CRUD service** as the minimum viable scope; taxonomy/relationship fields may initially bind to **Cengage-oriented document metadata keys** with tenant-specific mapping, not a new TaxonomyRegistry.

---

## 2. Baseline

| Check | Result | Label |
|---|---|---|
| **branch** | `feature/dis_metadata` | **FACT** |
| **HEAD** | `8b07a384efbfd7d49ce827ca96abc561f25f2b02` | **FACT** |
| **HEAD subject** | `feat: fix topic backfill s3 payload key resolution` | **FACT** |
| **working_tree** | CLEAN (empty `git status --short` at discovery start) | **FACT** |
| **latest_phase_commit** | `8b07a38` (Phase 7B fix) | **FACT** |

**FACT.** Phase 0–8A commits present on branch (abbreviated):

```
8b07a38 feat: fix topic backfill s3 payload key resolution     (Phase 7B)
0a20196 docs: record phase 7b topic backfill approval
e24101f docs: add phase 7b topic backfill discovery
43c260e feat: add CAS tenant template selection                 (Phase 8A)
bb8f906 feat: add automated metadata config provisioning       (Phase 8)
6120c46 feat: enable AIM topic source library filtering        (Phase 7)
dc30412 feat: complete registry driven retrieval gating         (Phase 6A)
91000cc feat: add schema driven upload metadata                (Phase 5)
14fd727 feat: add metadata schema validation report            (Phase 4)
a6387bf feat: generalize source library filter api             (Phase 3)
f550b0d feat: complete registry-driven source filtering        (Phase 2)
2128e78 feat: add DIS metadata Field Registry (phase 1)        (Phase 1)
255b4ff feat: implement metadata framework phase 0             (Phase 0)
```

**FACT.** `docs/*` is gitignored (`.gitignore` line 125). This file must be force-added with `git add -f`.

---

## 3. Reference HTML Analysis

**Source file:** `cengage-publishing-platform.html` — a minified React SPA bundle (~317KB). Metadata Editor UI is embedded in component `xi()` (extracted verbatim from bundle).

**FACT.** Reference route:

```
/titles/:titleId/source-library/metadata/:docId
```

Query param `?tab=ai|taxonomy|relationships` initializes active tab (default `ai`).

**FACT.** Page title: **Edit metadata**  
Subtitle: **Review and refine the tags extracted from this document**

### 3.1 Overall Layout

| Region | Structure | Tailwind / design tokens |
|---|---|---|
| Page shell | `mi` layout: left sidebar nav + breadcrumb header + main | `bg-surface-bg`, full-height flex |
| Header row | Title + subtitle left; unsaved badge right | `mb-8 flex items-start justify-between` |
| Main grid | **300px left column + fluid right column** | `grid grid-cols-[300px_1fr] gap-6` |
| Right panel | White card with tabs + tab content + footer | `rounded-2xl bg-white p-7 shadow-card` |

### 3.2 Left Document Information Panel

White card (`rounded-2xl bg-white p-6 shadow-card`):

| Element | Details |
|---|---|
| Thumbnail placeholder | `h-36` centered icon area, `bg-surface-bg text-ink-300`, file icon `dr` size 34 |
| Document name | `text-[16px] font-bold text-ink-900` — bound to `a.name` |
| Definition list | `dl.flex.flex-col.gap-2.text-[14px]` |

**Left panel fields (exact labels):**

| Label | Source | Sample value |
|---|---|---|
| Type | `a.type` | (from sample doc) |
| Size | `a.size` | (from sample doc) |
| Pages | hardcoded in reference | `182` |
| Purpose | `a.purpose` | (from sample doc) |

**FACT.** Below the doc card, a clickable **Object Metadata** button navigates to `/titles/:titleId/source-library/object-metadata` with summary text: `12 chapters · 264 content objects · 12 reviewed`. This is a **separate page** (out of Phase 9 document-level editor scope but linked from left panel).

### 3.3 Unsaved Changes Indicator

**FACT.** Header badge (always shown in reference mock):

```
● Unsaved changes
```

Classes: `rounded-full bg-warn-light px-3.5 py-1.5 text-[13px] font-semibold text-warn-text`

**INFERENCE.** Reference is static mock data — badge is present regardless of edit state. Implementation must wire to real dirty-state tracking.

### 3.4 Tab Navigation

**FACT.** Exactly three tabs — **do not rename**:

| id | label |
|---|---|
| `ai` | **AI Metadata** |
| `taxonomy` | **Taxonomy & Standards** |
| `relationships` | **Relationships** |

Tab bar: `mb-6 flex gap-7 border-b border-surface-border`

Tab button classes:
- **Active:** `-mb-px border-b-2 pb-3 border-brand-600 text-ink-900 text-[15px] font-semibold`
- **Inactive:** `border-transparent text-ink-400 hover:text-ink-700`
- Transition: `transition-colors`

Tab state persisted in URL query param `tab`.

### 3.5 Shared UI Primitives (from reference bundle)

**Field row component `yi({label, value})`:**
```jsx
<div>
  <label className="mb-2 block text-[14px] font-semibold text-ink-900">{label}</label>
  <input defaultValue={value}
    className="w-full rounded-xl border border-surface-borderStrong px-4 py-3 text-[15px] outline-none focus:border-brand-500" />
</div>
```

**Chip component `bi({children, tone='blue'})`:**
- Default (blue): `border-brand-200 bg-brand-50 text-brand-700`
- Green tone: `border-success-bg/30 bg-success-light text-success-text`
- Shape: `inline-flex items-center gap-1.5 rounded-lg border px-3 py-1.5 text-[14px] font-medium`
- Trailing remove icon: `jr` size 13

**Chip container:** `flex flex-wrap gap-2 rounded-xl bg-surface-bg p-3`

**Add affordance:** `rounded-lg border border-dashed border-surface-borderStrong px-3 py-1.5 text-[14px] text-ink-400`

**Primary button `M`:** used for Save metadata  
**Secondary button `M` variant secondary:** Cancel

**Provenance banner:** `rounded-xl bg-brand-50 px-4 py-3 text-[14px] font-medium text-brand-700` with leading icon `Dr` size 16

### 3.6 Footer Actions (shared across all tabs)

Footer: `mt-10 flex items-center justify-between border-t border-surface-border pt-6`

| Control | Position | Style |
|---|---|---|
| **Revert to AI values** | Left | `text-[14px] font-semibold text-brand-600 hover:underline` (text button) |
| **Cancel** | Right group | Secondary button |
| **Save metadata** | Right group | Primary button |

**FACT.** Reference does not implement click handlers — controls are presentational only in the static bundle.

### 3.7 Validation / Error States

**FACT.** Reference HTML contains **no validation error UI**, no field-level error messages, no disabled Save state, and no toast notifications for save success/failure.

**INFERENCE.** Implementation should add minimal error handling (toast + inline) without redesigning the reference layout.

### 3.8 Responsive Behavior

**FACT.** Reference uses fixed `grid-cols-[300px_1fr]` — no responsive breakpoints in the metadata editor component. Desktop-first layout assumed.

---

## 4. Three-Tab UI Specification

### Tab 1 — AI Metadata

**Provenance banner (exact text):**
> Extracted by AI on 2024-01-15 · edits override the extracted values

**Fields:**

| Label | Control | Layout | Sample value |
|---|---|---|---|
| Title | `yi` text input | 2-col grid | Marketing Handbook 2024 |
| Author | `yi` text input | 2-col grid | Cengage Marketing Team |
| Subject | `yi` text input | 2-col grid | Digital Marketing |
| Language | `yi` text input | 2-col grid | en-US |
| Description | `<textarea rows={3}>` full width | below grid | Comprehensive internal reference covering brand guidelines… |
| Keywords | chip list + add | full width | marketing, brand, digital, campaigns, strategy, content |

**Keywords header:** label left, count right — `6 of 12 max` (`text-[13px] text-ink-400`)

**Add keyword affordance:** `+ Add keyword...` (dashed chip)

**Grid:** `grid grid-cols-2 gap-5` for the four single-line fields.

---

### Tab 2 — Taxonomy & Standards

**Provenance banner (exact text):**
> Derived from Cengage taxonomy v4 · aligned to AACSB and AMA frameworks

**Fields:**

| Label | Control | Layout | Sample value |
|---|---|---|---|
| Subject area | `yi` input | 2-col grid | Business & Management |
| Domain | `yi` input | 2-col grid | Marketing |
| Subdomain | `yi` input | 2-col grid | Digital Marketing |
| Bloom's level | `yi` input | 2-col grid | Apply |
| Skill level | `<input>` max-w 420px | full row | Intermediate |
| Learning standards | green chips + count | chip section | AACSB Marketing Competencies, AMA Professional Standards, CIM Digital Marketing Standards |
| Skills mapped | blue chips + count | chip section | Campaign Planning, Brand Strategy, Content Creation, Channel Analytics, Budget Management |

**Chip section headers:** label + count (`3 mapped`, `5 mapped`)

**Add affordance:** `+ Add...` (dashed chip) on chip sections

---

### Tab 3 — Relationships

**Provenance banner (exact text):**
> Links detected across the Source Library · edits affect retrieval, not file storage

**Fields:**

| Label | Control | Count badge | Sample content |
|---|---|---|---|
| Series / collection | `yi` input | — | Marketing Reference Library 2024 |
| Related documents | green chips | `2 linked` | Brand_Guidelines_V3.docx, Digital_Channels_Report.pdf |
| Prerequisites | empty state + inline add | `none set` | "No prerequisites — this document stands alone" + `+ Add...` |
| Cross-references | blue chips | `3 linked` | Social Media Policy, Brand Identity Guide, Agency Brief Template |

**FACT.** Related documents use **green-tone chips** (linked document filenames). Cross-references use default blue chips (policy/guide names, not necessarily filenames).

**FACT.** Prerequisites empty state is inline text in a `rounded-xl bg-surface-bg p-3` container with muted `text-ink-400`.

---

## 5. Existing Frontend Architecture

### 5.1 Source Library Today

| File | Role |
|---|---|
| `frontend/src/features/sourceLibrary/pages/SourceLibraryPage/SourceLibraryPage.jsx` | List, filter, upload, read-only detail |
| `frontend/src/features/sourceLibrary/services/sourceLibraryApi.js` | API client — **no metadata update methods** |
| `frontend/src/features/sourceLibrary/utils/taxonomyFilters.js` | Config-driven filter sidebar |
| `frontend/src/features/sourceLibrary/utils/uploadFields.js` | Config-driven upload form fields |
| `frontend/src/features/sourceLibrary/components/RetrievalStatus/RetrievalStatus.jsx` | Retrieval health pill |

**FACT.** `sourceLibraryApi.js` exposes: `listDocuments`, `uploadDocument`, `getOverview`, `getUnits`, `deleteDocument`, `uiConfig` — **no `getMetadata` / `updateMetadata` / `revertMetadata`**.

**FACT.** Document detail "View" opens inline read-only content preview (overview/units text) — **not metadata editing**.

### 5.2 Closest Reusable Patterns

| Pattern | File | Reuse for Phase 9 |
|---|---|---|
| Dirty-state + gated Save | `TenantLabelsPanel.jsx` | `isDirty` via JSON compare; disabled save when clean |
| Dynamic config fields | `uploadFields.js` + `SourceLibraryPage` upload form | Field rendering pattern (not field set) |
| Modal CRUD | `EditEntityModal.jsx` | Save/cancel/error pattern |
| Toast feedback | `react-hot-toast` via `WorkspaceLayout` | Save success/failure |
| Tabs (custom CSS) | `StylePage.jsx`, `AnalyticsPage.jsx` | Tab state pattern (no shared Tab component) |
| Chips (display only) | `StylePage.module.scss`, `PromptDetailPage` | Styling reference — **no chip input component exists** |
| MultiSelect | `MultiSelect.jsx` | Document picker for relationship "Add..." |

### 5.3 Components NOT Present (must build)

- Metadata Editor page/route
- Three-tab metadata panel matching reference
- Chip/tag input with remove icon and dashed add affordance
- Field row matching `yi` styling
- Provenance banners
- Unsaved-changes badge wired to real state
- Relationship document picker modal
- Drawer component (not used in reference — full page instead)

### 5.4 Routing Gap

**FACT.** Current CAS frontend route: `/workspace/:courseId/sources` (`SourceLibraryPage`).

**FACT.** Reference route: `/titles/:titleId/source-library/metadata/:docId`.

**DECISION REQUIRED.** Map reference route pattern to existing workspace routing (e.g. `/workspace/:courseId/sources/:jobId/metadata?tab=ai`) while preserving three-tab UX.

---

## 6. Existing Backend Architecture

### 6.1 Four Metadata Authorities (unchanged)

| Authority | Location | Role |
|---|---|---|
| `metadata_schemas` | `dis_backend/config/clients/*.yaml` | Domain definition, LLM extraction hints, report-only validation |
| `retrieval.source_ui` | Tenant YAML `retrieval.source_ui` | Filter/upload UI presentation |
| Field Registry | `dis_backend/services/metadata_framework/registry.py` | Index / retrieval / filter_options / cas_list projection |
| Client profiles | `dis_backend/services/client_profiles/` | Tenant inference transforms |

**FACT.** No `TaxonomyRegistry` exists. Phase 6B explicitly rejected merging authorities.

### 6.2 Key Backend Files

| Concern | Path |
|---|---|
| Compact index write | `dis_backend/services/source_library.py` — `compact_source_record`, `write_source_content_and_index` |
| Registry projection | `dis_backend/services/metadata_framework/adapters.py` — `project_index_metadata` |
| UI config | `dis_backend/services/upload_ui_config.py`, `context_retrieval.py` |
| Hint precedence | `dis_backend/services/agents/metadata_tagging_agent.py` — `apply_metadata_hints` |
| AI extraction | `dis_backend/services/agents/metadata_extraction_agent.py` |
| Schema validation | `dis_backend/services/metadata_schema_validate.py` (report-only) |
| RDS + OpenSearch | `dis_backend/services/indexing.py` |
| CAS proxy | `app/api/v1/routers/source_library.py` |
| DIS context API | `dis_backend/api/routers/context.py` |
| DIS ingestion API | `dis_backend/api/routers/ingestion.py` |

### 6.3 Existing API Endpoints (metadata-relevant)

**Read paths:**

| Method | Path | Role |
|---|---|---|
| GET | `/v1/context/documents/library` | List compact index records |
| GET | `/v1/context/sources/{job_id}/overview` | Document overview |
| GET | `/v1/ingest/jobs/{job_id}/payload` | Full studio payload (includes `metadata`) |
| GET | `/v1/context/ui-config` | Filter/upload UI config |
| GET | `/api/v1/source-library/documents` | CAS proxy list |
| GET | `/api/v1/source-library/documents/{job_id}/overview` | CAS proxy overview |

**Write paths (upload only):**

| Method | Path | Role |
|---|---|---|
| POST | `/v1/ingest/upload` | Creates job; `metadata_hints` from form |
| POST | `/api/v1/source-library/documents/upload` | CAS proxy upload |

**FACT.** **No PATCH/PUT/POST metadata update endpoint** exists for existing documents.

**FACT.** `DELETE /v1/context/sources/{job_id}` deletes document (S3 + index + OS).

**FACT.** `POST /v1/ingest/jobs/{job_id}/index` reindexes RDS/OS from existing S3 artifacts — useful post-save hook, not metadata edit.

---

## 7. Existing Metadata Flow

```
Upload (CAS FormData → DIS /v1/ingest/upload)
  metadata_hints (user form fields)
       ↓
Background pipeline (graph.py STEP_ORDER):
  metadata_extraction   ← metadata_schemas guide LLM → doc_metadata
  metadata_tagging      ← enrich_metadata (client profile) + apply_metadata_hints
  validation_report     ← report-only schema findings
  studio_payload_preparation → payload.metadata = merged doc_metadata
  processed_storage     → S3: extracted/metadata.json, payload.json, source_index refresh
  structure_store_upsert  → RDS metadata_json (if enabled)
  vector_store_upsert     → OpenSearch metadata.* + top-level fields
       ↓
Source Library UI reads compact index (list) + overview/content (detail)
```

**Where AI metadata is generated:** `metadata_extraction_agent.py` (LLM) + `metadata_tagging_agent.py` (enrichment + hints).

**Where taxonomy metadata is generated:** Client profile inference + LLM extraction into flat `doc_metadata`. Cengage schema has publishing fields (`product_title`, `authors`, `discipline`, `chapter_*`, etc.) — **not** the reference taxonomy labels (`Subject area`, `Domain`, `Bloom's level`, etc.).

**Where relationships are generated:** **Nowhere** for Source Library documents.

**Where user metadata can be modified today:** **Upload time only** via `metadata_hints`. No post-ingestion edit.

**Where metadata is persisted:** S3 primary (`payload.json`, `metadata.json`, `source_list.json`); optional RDS `metadata_json`; OpenSearch per content unit.

---

## 8. Persistence Architecture

| Store | Path / Table | Contents | Write on edit? |
|---|---|---|---|
| **Studio payload** | `processed/.../{job_id}/studio_payload/payload.json` | Full `metadata` dict + content_units | **YES — primary edit target** |
| **AI extraction snapshot** | `processed/.../{job_id}/extracted/metadata.json` | Flat `doc_metadata` at extraction time | **NO — keep immutable for revert** |
| **Compact source index** | `processed/.../source_index/source_list.json` | Registry-projected fields per document | **YES — re-project via `compact_source_record`** |
| **Clean content** | `processed/.../{job_id}/source_content/content.json` | Reader-facing; trimmed unit metadata | **CONDITIONAL** — only if edited fields appear in `_PER_UNIT_METADATA_KEYS` |
| **RDS structure store** | `{schema}.dis_documents.metadata_json` | Full metadata JSONB | **YES — via existing upsert path** |
| **OpenSearch** | per content-unit docs | `metadata.*` blob + hardcoded top-level | **YES — via reindex/upsert** |
| **CAS Postgres** | — | No source metadata tables | N/A |

**FACT.** Field Registry `PROMOTE_INDEX` fields written to compact index via `project_index_metadata()`. Fields not in registry remain in `payload.metadata` but may not appear in list/filter UI.

**FACT.** `title` is in registry with `PROMOTE_RETRIEVAL` only (not index) — compact record uses separate scaffolding for display title.

### Recommended Persistence Path (DECISION REQUIRED → recommended)

**INFERENCE.** Manual edits should update:

1. **`studio_payload/payload.json` → `metadata`** (authoritative document metadata)
2. **`source_index/source_list.json`** compact record (re-project registry-promoted fields)
3. **Optional cascade:** RDS `metadata_json` + OpenSearch via existing `write_source_content_and_index` / `POST /jobs/{job_id}/index` pattern

**Do NOT** overwrite `extracted/metadata.json` on user save — preserve as AI baseline for Revert.

---

## 9. Source Library Document Identity

| Layer | Identifier | Notes |
|---|---|---|
| UI list row | `doc.job_id \|\| doc.document_id` | Treated as equivalent in frontend |
| Metadata Editor route (reference) | `docId` URL param | Resolves via `Qr.find(e => e.id === t)` |
| CAS API | `{job_id}` path param | `/api/v1/source-library/documents/{job_id}/...` |
| DIS API | `{job_id}` | `/v1/context/sources/{job_id}/...` |
| Compact index | `document_id = job_id` | Set in `compact_source_record()` |
| S3 artifacts | `{job_id}` directory | All processed artifacts keyed by job |
| Tenant scope | `client_id` + `course_id` | Upload/list scoping; `-1` = global |

**Complete chain:**

```
SourceLibraryPage row (job_id)
  → GET /api/v1/source-library/documents/{job_id}/overview (read)
  → [PROPOSED] GET /api/v1/source-library/documents/{job_id}/metadata
  → DIS GET /v1/context/sources/{job_id}/metadata (or payload)
  → S3 studio_payload/payload.json
  → compact index record in source_list.json
```

**FACT.** `job_id` is the **sole document primary key** across all layers.

---

## 10. Reference-to-Code Mapping Matrix

| Reference UI | Frontend component | Existing API | Existing model/storage | Status |
|---|---|---|---|---|
| Title | — (new MetadataEditor) | — | `title` (registry retrieval); `product_title` (cengage schema) | **PARTIAL** — data may exist; no edit API |
| Author | — | — | `authors` (cengage schema, list) | **PARTIAL** — schema only; label mismatch (`Author` vs `authors`) |
| Subject | — | — | `discipline` (cengage schema) | **PARTIAL** — semantic overlap, different key |
| Language | — | — | extraction may set `language` in doc_metadata | **PARTIAL** — inconsistent |
| Description | — | — | no dedicated doc-level field; `visual_summary` exists | **MISSING** as document description |
| Keywords | — | — | unit-level `keywords`; optional schema field (AIM) | **PARTIAL** — unit vs document level |
| Subject Area | — | — | — | **MISSING** |
| Domain | — | — | — | **MISSING** |
| Subdomain | — | — | — | **MISSING** |
| Bloom's Level | — | — | — (bloom in text_keywords only) | **MISSING** |
| Skill Level | — | — | — | **MISSING** |
| Learning Standards | — | — | — | **MISSING** |
| Skills Mapped | — | — | — (explicitly out of AIM skills scope) | **MISSING** |
| Series / Collection | — | — | — | **MISSING** |
| Related Documents | — | — | — | **MISSING** |
| Prerequisites | — | — | — | **MISSING** |
| Cross References | — | — | — | **MISSING** |
| Left panel Type | SourceLibraryPage list cols | GET documents | `document_type` / `source_file_type` | **FOUND** (read) |
| Left panel Size | — | GET documents | `file_size` / similar | **FOUND** (read) |
| Left panel Pages | — | — | not standard index field | **MISSING** |
| Left panel Purpose | — | GET documents | `purpose` (registry) | **FOUND** (read) |
| Unsaved indicator | TenantLabelsPanel pattern | — | — | **UI pattern FOUND** |
| Save metadata | TenantLabelsPanel / EditEntityModal | — | — | **UI pattern FOUND; API MISSING** |
| Revert to AI values | — | — | `extracted/metadata.json` (read-only today) | **DATA PARTIAL; API MISSING** |
| Tab: AI Metadata | — | — | — | **MISSING** (UI) |
| Tab: Taxonomy & Standards | — | — | — | **MISSING** (UI) |
| Tab: Relationships | — | — | — | **MISSING** (UI) |

---

## 11. AI Metadata Mapping

### Reference → Backend field mapping (proposed, not implemented)

| Reference label | Proposed storage key | Current source | Notes |
|---|---|---|---|
| Title | `title` or `product_title` | extraction / filename | Cengage uses `product_title` in schema; registry has `title` |
| Author | `authors` (list → display string) | LLM extraction | Reference single text input; backend list |
| Subject | `discipline` or `subject` | LLM extraction | Key alignment needed |
| Language | `language` | LLM extraction | |
| Description | `description` (new) or reuse `visual_summary` | — | **DECISION REQUIRED** — avoid overloading `visual_summary` |
| Keywords | `keywords` | unit-level + optional doc-level | Reference is doc-level chip list |

**FACT.** Cengage `metadata_schemas.cengage.optional_fields` includes: `product_title`, `authors`, `discipline`, `key_terms` — partial overlap with AI Metadata tab.

**FACT.** None of these are editable post-ingestion today.

---

## 12. Taxonomy & Standards Mapping

**FACT.** Reference taxonomy fields (`Subject area`, `Domain`, `Subdomain`, `Bloom's level`, `Skill level`, `Learning standards`, `Skills mapped`) **do not exist** as named fields in:
- Cengage YAML `metadata_schemas`
- Field Registry `DEFAULT_FIELDS`
- `retrieval.source_ui`

**FACT.** Cengage operational taxonomy in production uses: `course_name`, `chapter`, `module`, `learning_objective` (filter/upload UI) — **different semantic model** from reference.

**INFERENCE.** Taxonomy tab fields should be stored as **document-level metadata keys** in `payload.metadata` (e.g. `taxonomy_subject_area`, `taxonomy_domain`, … or nested `taxonomy: {}`). Promotion to index/filter requires **Field Registry YAML overlay** per tenant — separate from UI implementation.

**FACT.** Phase 6 AIM briefing: skills/competency ontology is **explicitly not present** — `Skills mapped` and `Learning standards` are reference-UI concepts requiring **new storage**, not existing skills platform.

---

## 13. Relationships Mapping

**FACT.** No relationship model exists for Source Library documents.

**Proposed storage structure (PROPOSED — not implemented):**

```json
{
  "relationships": {
    "series_collection": "Marketing Reference Library 2024",
    "related_documents": [
      {"job_id": "...", "label": "Brand_Guidelines_V3.docx"}
    ],
    "prerequisites": [],
    "cross_references": [
      {"job_id": "...", "label": "Social Media Policy"}
    ]
  }
}
```

**FACT.** Reference uses display names/filenames in chips — implementation needs job_id resolution for persistence and retrieval filter integration.

**FACT.** Provenance banner states: "edits affect retrieval, not file storage" — relationships are **logical links**, not file duplication.

---

## 14. AI vs User Override Analysis

**Current behavior (FACT):**

```
metadata_hints (upload) → metadata_extraction (LLM) → enrich_metadata (profile)
  → apply_metadata_hints (user wins) → flat payload.metadata
```

**FACT.** Single flat dict — **no per-field provenance** (`{value, source: ai|user}`).

**FACT.** After pipeline completes, AI-original and user-override values are **indistinguishable** in `payload.metadata`.

**FACT.** `extracted/metadata.json` is written at extraction time and **not updated** by current pipeline steps after tagging — suitable as **immutable AI snapshot** if write path preserves it.

**Minimum architecture for reference UI (PROPOSED):**

| Layer | Purpose |
|---|---|
| `extracted/metadata.json` | Immutable AI baseline (never overwrite on user save) |
| `payload.metadata` | Current effective values (AI + user overrides merged) |
| Optional `_metadata_overrides` key | Track which keys user edited (for banner/UX only) |

**INFERENCE.** Full field-level provenance is **not required by reference UI** (banner text is document-level, not per-field). Revert resets `payload.metadata` AI-editable keys from `extracted/metadata.json`.

---

## 15. Revert-to-AI Analysis

**Reference control:** "Revert to AI values" — left footer text button.

**Required behavior (INFERENCE from reference + banner text):**
1. Reset all AI Metadata tab fields to values from AI extraction snapshot
2. Optionally reset taxonomy fields if they were AI-derived (reference banner implies taxonomy is taxonomy-v4-derived, not purely AI — **DECISION REQUIRED** on revert scope per tab)
3. Mark form dirty; user must still click Save metadata to persist (reference shows unsaved state)

**Technical path (PROPOSED):**
```
POST /v1/context/sources/{job_id}/metadata/revert-ai
  → read extracted/metadata.json
  → merge AI values over current payload.metadata (for revert-eligible keys)
  → clear override markers
  → return updated metadata (client refreshes form)
  → persist only on subsequent PATCH
```

**Alternative:** Revert is client-side only until Save — matches reference static mock.

**FACT.** No backend revert endpoint exists today.

---

## 16. Taxonomy/Excel Import Future Architecture

**Requirement (future, not Phase 9):** Users may import taxonomy lists from Excel, controlled vocabularies, standards, classification lists.

**FACT.** Phase 6B rejected `TaxonomyRegistry` as unnecessary authority layer.

**Recommended future architecture (INFERENCE):**

| Data type | Correct home |
|---|---|
| Controlled vocabulary values (enum lists) | `metadata_schemas` field `values` arrays **or** tenant YAML `retrieval.source_ui` select options |
| Hierarchical taxonomy (subject → domain → subdomain) | Tenant YAML config block (new **`retrieval.taxonomy_config`** or extend `source_ui`) — **not** Field Registry |
| Document-level assigned taxonomy | `payload.metadata` keys |
| Filter/index promotion | Field Registry overlay when taxonomy fields should filter Source Library |
| Excel import | Admin script → tenant YAML or metadata_schemas values — **not** Phase 9 |

**Do NOT** introduce TaxonomyRegistry. **Do NOT** implement Excel import in Phase 9.

---

## 17. API Gap Analysis

### Existing — sufficient for read foundation

- `GET /v1/ingest/jobs/{job_id}/payload` — full metadata (DIS internal)
- `GET /v1/context/sources/{job_id}/overview` — partial overview
- Compact index list — projected subset

### Missing — required for Phase 9

| Endpoint (proposed) | Method | Purpose | Priority |
|---|---|---|---|
| `/v1/context/sources/{job_id}/metadata` | GET | Return effective metadata + AI snapshot metadata + relationships | **P0** |
| `/v1/context/sources/{job_id}/metadata` | PATCH | Apply user edits; cascade to payload + index + RDS/OS | **P0** |
| `/v1/context/sources/{job_id}/metadata/revert-ai` | POST | Reset to AI baseline | **P0** |
| CAS proxy `/api/v1/source-library/documents/{job_id}/metadata` | GET/PATCH/POST | Frontend-facing with auth + tenant scoping | **P0** |

**PATCH body design (PROPOSED):**
```json
{
  "ai_metadata": { "title": "...", "authors": "...", ... },
  "taxonomy": { "subject_area": "...", "domain": "...", ... },
  "relationships": { "related_documents": [...], ... }
}
```

**Validation (PROPOSED):**
- Allow-list keys per tenant (from metadata_schemas + editor config)
- Reject unknown keys (security)
- Schema validation report-only (Phase 4 policy) unless DECISION to block save

**FACT.** Follow repository conventions: CAS proxy → DIS internal, JWT + `client_id` resolution via `source_library.py`.

---

## 18. Database/Migration Analysis

**Migrations required: NO**

**Rationale (FACT + INFERENCE):**
- Document metadata already stored as JSON in S3 and optional RDS `metadata_json`
- Relationships and taxonomy fields are **new JSON keys** in existing structures
- Compact index update uses existing `write_source_content_and_index` / `compact_source_record`
- No new CAS Postgres tables needed

**Operational backfill (optional, not migration):**
- Existing documents lack `relationships` keys → default empty
- Existing documents have `extracted/metadata.json` → revert available where artifact exists
- Documents ingested before extraction agent may have thin AI snapshots

**DECISION REQUIRED:** Whether to add `metadata_editor` config block to tenant YAML (field definitions for editor) vs hardcoding reference field set for Cengage first.

---

## 19. Security/Authorization

| Concern | Current state | Phase 9 requirement |
|---|---|---|
| Authentication | JWT required (`security.require_jwt` in tenant YAML) | Same |
| Tenant isolation | `client_id` routing; CAS `dis_access.json` allowlist | Metadata edit must verify document belongs to resolved client |
| Document access | Source Library gated by project membership + DIS allowlist | Same checks on GET/PATCH metadata |
| Role-based edit | Upload allowed for authenticated users; admin-only for some list scopes | **DECISION REQUIRED:** all Source Library users vs admin/reviewer only |
| Arbitrary key injection | N/A today (upload form fixed fields) | PATCH must enforce **field allow-list** from tenant config |
| Controlled vocabulary | Schema enum validation (report-only) | Editor selects should use schema `values` when defined |
| Audit history | Prompt library has audit; source metadata does not | **DECISION REQUIRED:** whether metadata edits need audit trail |
| PII / restricted content | `restricted_content_filter`, `access_level` | Edits must not downgrade access controls via metadata PATCH |

**FACT.** CAS `source_library.py` checks `role == admin` for some cross-course listing — metadata edit authorization is not yet defined.

---

## 20. Tenant Isolation

**FACT.** Two "tenant" concepts persist (Phase 8 discovery):
- CAS `Project` (organization)
- DIS `client_id` (YAML workspace: aim, cengage, academian)

**FACT.** Metadata Editor must resolve DIS `client_id` from CAS project/course context using existing `dis_access.py` / `source_library.py` patterns.

**FACT.** S3 paths namespaced: `processed/{namespace}/{environment}/...`

**INFERENCE.** Relationship links must validate target documents belong to **same client_id** (and optionally same course scope).

---

## 21. Source Library Filter Compatibility

Phase 9 metadata edits **must not break** Phases 1–7B mechanisms:

| Phase | Mechanism | Compatibility requirement |
|---|---|---|
| 1 | Field Registry | Re-project index fields via `compact_source_record` after save |
| 2 | Index projection | Edited registry-promoted fields must appear in `source_list.json` |
| 3 | Dynamic filtering | Filter params unchanged; values update when metadata changes |
| 4 | Schema validation | Report-only policy preserved unless explicitly changed |
| 5 | Upload UI | Upload form unchanged; editor is post-ingestion |
| 6A | Retrieval gating | `_passes_filters` uses compact index — must reflect edits |
| 7 | Topic filter | `topic` field promotion unchanged |
| 7B | payload_key backfill | Unrelated to editor write path |

**CRITICAL (FACT):** If a user edits a field that participates in `filter_options` or retrieval gating (e.g. `course_name`, `chapter`, `topic`), **compact source index must be updated** on save — not payload alone.

**INFERENCE.** Relationship edits affect retrieval only if retrieval rules are extended to use relationship fields — default Phase 9 should store relationships without breaking existing filters.

---

## 22. Phase 0–8 Compatibility Assessment

| Phase | Status | Phase 9 impact |
|---|---|---|
| 0 — metadata framework config | Complete | No change |
| 1 — Field Registry | Complete | May add YAML overlays for new taxonomy/relationship index promotion (optional) |
| 2 — Registry-driven filtering | Complete | Must re-project index on save |
| 3 — Dynamic filter API | Complete | No API contract change |
| 4 — Schema validation | Complete | Editor save may trigger validation report (optional) |
| 5 — Upload metadata UI | Complete | Editor is additive |
| 6A — Retrieval gating | Complete | Index sync required |
| 6B — Source of truth | Complete | **Must not merge authorities** |
| 7 — Topic filter | Complete | Unaffected unless topic edited in editor |
| 7B — Topic backfill | Complete | Unaffected |
| 8 — Metadata provisioning | Complete | Editor config could extend provisioning templates (future) |

**FACT.** No Phase 0–8 code changes required for discovery. Stage B must not regress existing tests.

---

## 23. Required Implementation Scope (Stage B Proposal)

### P0 — Must implement (reference fidelity)

1. **MetadataEditorPage** — full-page layout matching reference (`grid-cols-[300px_1fr]`, header, breadcrumbs)
2. **Left document panel** — thumbnail, name, Type/Size/Pages/Purpose, Object Metadata link
3. **Three tabs** with exact labels: AI Metadata, Taxonomy & Standards, Relationships
4. **All fields** documented in §4 — no removals, no tab merges
5. **Provenance banners** per tab (exact reference copy adaptable for live extraction date)
6. **Chip/tag inputs** with counts, dashed add affordances, remove icons
7. **Footer:** Revert to AI values, Cancel, Save metadata
8. **Unsaved changes** indicator (functional dirty state)
9. **Backend GET/PATCH metadata** + **POST revert-ai** with S3 cascade
10. **AI snapshot preservation** (`extracted/metadata.json` immutable)
11. **Route + entry point** from Source Library document row (e.g. "Edit metadata" action)

### P1 — Should implement

1. Relationship document picker (search Source Library for `+ Add...` on related documents)
2. Keyword max count enforcement (`6 of 12 max` from reference)
3. Toast notifications on save/error
4. Tab deep-linking via `?tab=` query param
5. CAS proxy endpoints mirroring DIS
6. Post-save reindex hook for RDS/OpenSearch

### P2 — Deferred

1. Object Metadata sub-page (linked from left panel but separate reference page)
2. Excel taxonomy import
3. Per-field provenance indicators
4. Metadata edit audit history
5. Field Registry promotion for all taxonomy fields

---

## 24. Non-Goals

- Excel taxonomy importer
- TaxonomyRegistry or master metadata registry
- Schema → registry merge
- OpenSearch redesign
- S3 architecture redesign
- Field Registry redesign
- Upload UI redesign
- Tenant provisioning redesign
- PipelineState changes
- Existing tenant migration
- Infrastructure changes
- CAS redesign
- Cengage/AIM transform redesign
- Object Metadata page (separate reference page — link only)

---

## 25. Risks

| Risk | Severity | Mitigation |
|---|---|---|
| Reference taxonomy fields don't map to Cengage production schema | High | Store as document metadata keys; tenant-specific label→key mapping config |
| Multi-store sync failure on save (S3 index vs payload) | High | Transactional update service with index lock; retry pattern from `source_index_lock` |
| Revert without `extracted/metadata.json` | Medium | Disable revert with clear message; fallback to re-run extraction (out of scope) |
| Relationship links to deleted documents | Medium | Validate on save; show stale chip state |
| Field Registry not promoting new taxonomy fields | Medium | Phase 9 UI works without filter promotion; registry overlay in follow-up |
| UI scope creep (Object Metadata page) | Medium | Strict Phase 9 boundary — link only |
| CAS/DIS field name mismatch (Author vs authors) | Medium | Normalization layer in metadata API response |
| Breaking Phase 3 filters by partial index update | High | Always call `compact_source_record` + `write_source_index` on save |

---

## 26. Open Decisions

| ID | Question | Options | Recommendation |
|---|---|---|---|
| OD-1 | Route pattern in CAS app | Reference `/titles/...` vs `/workspace/:courseId/sources/:jobId/metadata` | **Workspace pattern** — match existing app |
| OD-2 | Description field key | New `description` vs reuse `visual_summary` | **New `description`** — avoid semantic collision |
| OD-3 | Revert scope | AI tab only vs all tabs | **AI tab fields + AI-derived taxonomy**; relationships manual |
| OD-4 | Who can edit metadata | All users vs admin/reviewer | **Same as upload permission** initially |
| OD-5 | Save validation policy | Report-only vs blocking | **Report-only** (Phase 4 continuity) with UI warnings |
| OD-6 | Editor field config source | Hardcoded reference fields vs tenant YAML | **Tenant YAML `retrieval.metadata_editor`** (PROPOSED) mirroring `source_ui` pattern |
| OD-7 | Relationship retrieval integration | Store only vs index for retrieval | **Store only in P0**; retrieval integration P2 |
| OD-8 | Pages field in left panel | Omit vs compute from extraction | **Compute from structure** when available; else "—" |
| OD-9 | Audit trail | Required vs optional | **Optional P2** — `updated_at` + `updated_by` in index record minimum |

---

## 27. Recommended Architecture

### 27.1 Component Architecture

```
SourceLibraryPage
  └─ "Edit metadata" action on row
       └─ MetadataEditorPage (new)
            ├─ DocumentInfoPanel (left)
            ├─ MetadataEditorTabs (right)
            │    ├─ AIMetadataTab
            │    ├─ TaxonomyStandardsTab
            │    └─ RelationshipsTab
            └─ MetadataEditorFooter (revert/cancel/save)

MetadataChipInput (new) — implements bi chip + dashed add
MetadataFieldRow (new) — implements yi field row
```

### 27.2 Backend Service (PROPOSED)

```
MetadataEditorService (new in dis_backend/services/)
  ├─ get_metadata(job_id) → { effective, ai_baseline, relationships, provenance }
  ├─ patch_metadata(job_id, changes, user_id) → cascade write
  └─ revert_to_ai(job_id) → merge from extracted/metadata.json

Uses existing:
  ├─ ArtifactWriter (read/write S3)
  ├─ compact_source_record + write_source_index
  └─ indexing.rds_upsert / opensearch_upsert (post-save)
```

### 27.3 Persistence on Save (PROPOSED)

```
PATCH metadata
  1. Load payload.json + extracted/metadata.json + index record
  2. Validate allowed keys (tenant editor config + schema)
  3. Merge changes into payload.metadata
  4. Set override markers (optional)
  5. Write payload.json
  6. compact_source_record → update source_list.json
  7. Update content.json if needed
  8. Async/sync RDS + OS reindex
  9. Return updated metadata
```

### 27.4 Three-Layer Authority (preserved)

Editor reads/writes **document instance metadata** (`payload.metadata`).  
Editor field definitions come from **`retrieval.metadata_editor`** (PROPOSED presentation config).  
Domain validation uses **`metadata_schemas`**.  
Index/filter promotion uses **Field Registry** (when configured).

---

## 28. Acceptance Criteria (Stage B)

1. Metadata Editor renders with **exact three tabs**: AI Metadata, Taxonomy & Standards, Relationships
2. All fields listed in §4 present with correct labels and control types
3. Left panel shows document info (Type, Size, Pages, Purpose)
4. Provenance banners displayed per tab
5. Chip inputs support add/remove with count badges
6. Unsaved changes indicator appears when form is dirty
7. Cancel navigates away without saving (with confirm if dirty)
8. Save metadata persists to S3 payload and updates compact index
9. Revert to AI values restores AI baseline fields
10. GET metadata returns effective + AI baseline values
11. No regression in Source Library list/filter/retrieval (Phase 1–7B tests pass)
12. UI matches reference spacing, typography, colors, and layout within Tailwind token equivalence

---

## 29. Implementation Plan (Stage B — ordered)

| Step | Task | Est. dependency |
|---|---|---|
| 1 | Define `retrieval.metadata_editor` tenant YAML schema (PROPOSED) | OD-6 approval |
| 2 | Implement `MetadataEditorService` + DIS API routes | — |
| 3 | Implement CAS proxy routes | Step 2 |
| 4 | Add `sourceLibraryApi.getMetadata / patchMetadata / revertMetadata` | Step 3 |
| 5 | Build shared UI primitives (MetadataFieldRow, MetadataChipInput) | — |
| 6 | Build MetadataEditorPage with three tabs | Steps 4–5 |
| 7 | Wire dirty state, save, cancel, revert | Step 6 |
| 8 | Add "Edit metadata" entry from SourceLibraryPage | Step 6 |
| 9 | Post-save index cascade + optional reindex | Step 2 |
| 10 | Characterization tests for save/revert/index sync | Step 9 |
| 11 | Visual QA against reference HTML | Step 6 |

---

## 30. Final Verdict

**Stage A complete.** The reference HTML provides a **complete, implementable specification** for a three-tab Metadata Editor. The existing codebase provides **strong foundations** (S3 payload storage, Field Registry projection, upload UI patterns, dirty-save UX) but has **critical gaps**: no post-ingestion metadata API, no relationship storage, no AI snapshot revert path, and no Metadata Editor UI.

**Proceed to Stage B** with:
- Reference-faithful frontend (mandatory three tabs, all fields)
- New metadata CRUD + revert API layer
- S3-first persistence with compact index cascade
- Immutable `extracted/metadata.json` as AI baseline
- **No SQL migration**
- **No authority layer merge**

**Do not simplify** the reference UI by removing Relationships, merging tabs, or substituting generic metadata forms.

---

## Appendix A — Eraser Diagram Prompts

### DIAGRAM 1 — Metadata Editor Architecture

```
Title: Phase 9 Metadata Editor Architecture

Nodes:
- User (Content Author)
- Source Library Page (React)
- Metadata Editor Page (React) — 3 tabs: AI Metadata | Taxonomy & Standards | Relationships
- CAS Source Library API (/api/v1/source-library/documents/{job_id}/metadata)
- DIS Context API (/v1/context/sources/{job_id}/metadata)
- MetadataEditorService (PROPOSED)
- S3 studio_payload/payload.json
- S3 extracted/metadata.json (immutable AI baseline)
- S3 source_index/source_list.json
- RDS metadata_json (optional)
- OpenSearch content units (optional)

Edges:
User → Source Library Page → Metadata Editor Page
Metadata Editor Page → CAS API → DIS API → MetadataEditorService
MetadataEditorService → payload.json (read/write effective metadata)
MetadataEditorService → extracted/metadata.json (read-only AI baseline)
MetadataEditorService → source_list.json (re-project via Field Registry)
MetadataEditorService → RDS/OS (cascade reindex)

Style: solid lines = existing (Phase 0-8), dashed lines = PROPOSED Phase 9
```

### DIAGRAM 2 — Metadata Lifecycle

```
Title: Metadata Lifecycle (Upload → Edit → Retrieval)

Flow:
1. Upload (CAS FormData) → metadata_hints
2. DIS Pipeline → metadata_extraction (LLM) → metadata_tagging → doc_metadata
3. Write extracted/metadata.json (AI snapshot — immutable)
4. Write studio_payload/payload.json (effective metadata)
5. compact_source_record → source_list.json (Field Registry projection)
6. Optional: RDS metadata_json + OpenSearch upsert
7. Source Library list/filter/retrieval (Phases 1-7)
8. [PROPOSED] Human edit in Metadata Editor
9. [PROPOSED] PATCH metadata → update payload + re-project index
10. [PROPOSED] Revert → restore from extracted/metadata.json

Annotate: steps 8-9 are Phase 9 additions; steps 1-7 exist today
```

### DIAGRAM 3 — Three-Layer Metadata Architecture

```
Title: Metadata Authority Separation (Phase 6B — DO NOT MERGE)

Layer 1: metadata_schemas (tenant YAML)
  → domain fields, types, enums, LLM hints, validation

Layer 2: retrieval.source_ui (+ PROPOSED metadata_editor)
  → presentation: filters, upload fields, editor tabs/labels

Layer 3: Field Registry (DEFAULT_FIELDS + YAML overlay)
  → index / filter_options / retrieval / cas_list projection

Layer 4: Client Profile (Python + client_rules)
  → tenant inference transforms

Document instance: payload.metadata (S3)
  → actual values per document

Rules (callout boxes):
- metadata_schemas ≠ source_ui ≠ Field Registry
- Editor writes payload.metadata
- Registry projects to source_list.json
- Schema validates (report-only)
```

### DIAGRAM 4 — Save/Revert Flow

```
Title: Save and Revert Flow (PROPOSED)

AI extraction → extracted/metadata.json (frozen snapshot)
              → payload.metadata (initial effective copy)

User edit in Metadata Editor (in-memory form state)
  → dirty flag = true
  → "Unsaved changes" badge

Save metadata:
  → PATCH { changes }
  → validate allow-list keys
  → merge into payload.metadata
  → write payload.json
  → compact_source_record → source_list.json
  → [optional] RDS + OpenSearch reindex
  → dirty flag = false

Revert to AI values:
  → POST revert-ai (or client-side pre-save)
  → read extracted/metadata.json
  → replace AI-eligible keys in form state
  → dirty flag = true
  → user must Save to persist

Cancel:
  → navigate away (confirm if dirty)
  → discard form state
```

---

## Appendix B — Reference Component Source (Extracted)

Full metadata editor page component `xi()` extracted from reference bundle — see investigation artifacts. Key structural confirmation:

- Function: `xi()` — Metadata Editor page
- Field row: `yi({label, value})` — labeled text input
- Chip: `bi({children, tone})` — removable tag chip
- Sample doc resolver: `Qr.find(e => e.id === t)`
- Tab state: `useState(searchParams.get('tab') || 'ai')`

---

## Appendix C — Backend Gap Classification Summary

| Class | Description | Count (approx.) |
|---|---|---|
| **A — Existing end-to-end** | purpose, document_type, course_name, chapter, module, topic (AIM) — read via index; write at upload only | ~8 fields |
| **B — Existing data, no edit API** | product_title, authors, discipline, keywords, title | ~5 fields |
| **C — Existing API, insufficient UI** | Overview/content read APIs without editor | — |
| **D — UI concept, backend storage missing** | Taxonomy tab fields, all Relationships tab fields | ~11 field groups |
| **E — Completely missing** | Metadata Editor UI, PATCH/revert APIs, relationship model, AI revert | Core Phase 9 scope |

---

*End of Phase 9 Stage A discovery document.*
