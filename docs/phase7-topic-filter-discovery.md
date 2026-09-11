# Phase 7 — AIM Topic Source Library Filter Discovery

**Status:** DISCOVERY ONLY. No production implementation.  
**Date:** 2026-08-31  
**Branch:** `feature/dis_metadata`  
**Baseline HEAD:** `b65e52a6d2322f02f4d06600426ebe6ab5c55270`

This document traces AIM `topic` end-to-end and recommends the smallest architecture that makes Topic a normal Source Library filter. Findings are marked **FACT**, **INFERENCE**, or **DECISION REQUIRED**.

Phase 6A **DECISION-001** deferred Field Registry promotion of `topic`. This phase re-opens that decision as a confirmed product requirement: AIM needs Topic as a Source Library filter.

---

## 1. Baseline

| Check | Result |
|---|---|
| Branch | `feature/dis_metadata` |
| HEAD | `b65e52a6d2322f02f4d06600426ebe6ab5c55270` (`b65e52a docs: add phase 6b metadata source of truth discovery`) |
| Expected baseline | `b65e52a` or descendant — **exact match** |
| `git status --short` | empty |

**FACT:** Working tree was CLEAN at discovery start. No stash/reset/clean.

Recent lineage (newest first):

- `b65e52a` docs: add phase 6b metadata source of truth discovery
- `c51baa6` fix: preserve legacy topic retrieval filtering
- `dc30412` feat: complete registry driven retrieval gating
- `519fd6c` docs: approve phase 6a retrieval gating
- `210723c` docs: add phase 6 metadata architecture discovery
- `91000cc` feat: add schema driven upload metadata
- `a6387bf` feat: generalize source library filter api
- `f550b0d` feat: complete registry-driven source filtering
- `2128e78` feat: add DIS metadata Field Registry (phase 1)

---

## 2. Current Topic Lifecycle

Three different `topic*` concepts exist. Only (A) is in scope for Source Library filtering.

| Concept | Storage | Used for | In scope? |
|---|---|---|---|
| **(A) Document metadata `topic`** | AIM `payload.metadata.topic` (string) | AIM schema field; profile inference; retrieval legacy exact-match | **YES** |
| **(B) Calendar-day `topic`** | `dis_calendar_days.topic`; day row `topic` | Lesson-day text in calendars / digests | **NO** |
| **(C) Unit `topics` (plural)** | OpenSearch `topics` keyword; `topics_json` | Extracted keywords, semantic search | **NO** |

### Stage-by-stage (document metadata `topic`)

| Stage | Exists? | Storage key | API key | Indexed? | Filterable? | Displayed? | Tenant-specific? |
|---|---|---|---|---|---|---|---|
| AIM source / schema | YES | `topic` (`metadata_schemas.aim` required, type `string`, no enum) | n/a | n/a | n/a | n/a | AIM (+ Academian schema copy). Cengage schema has no `topic`. |
| Extraction / inference | YES | `metadata.topic` | n/a | n/a | n/a | n/a | AIM profile only (`AIMClientProfile.enrich_metadata`) |
| Metadata payload | YES | `payload.metadata.topic` | n/a | n/a | n/a | n/a | Written for AIM ingests |
| S3 compact source index | **NO** | n/a on compact record | n/a | **NO** — not `PROMOTE_INDEX` | n/a | n/a | Compact shape follows tenant registry; AIM currently uses `DEFAULT_REGISTRY` |
| Field Registry | **NO** | `DEFAULT_REGISTRY.get("topic") is None` | n/a | NO | NO | n/a | No tenant YAML `metadata_framework` exists |
| `source_filter_options` | **NO** | not in map | n/a | n/a | **NO** | dropdown map omits `topic` | Tenant registry; default map has no `topic` |
| Source Library API (`/context/documents/library`) | query param not named | would be `topic` if authorized | `topic` | n/a | **NO** — unauthorized extras ignored | list whitelist has no `topic` unless `cas_list` | Authorization is tenant-registry |
| CAS proxy | forwards extras blindly | `topic` if UI sends it | `topic` | n/a | CAS does not authorize | n/a | CAS is tenant-agnostic |
| DIS `_source_matches` | would match if `filter_options`-promoted **and** compact record has the key | compact `src["topic"]` | `filters["topic"]` | n/a | **NO today** | n/a | Tenant registry |
| Frontend filter UI | configured but **deferred** | UI key `topic` | `topic` (no alias) | n/a | **NO** — `DEFERRED_TAXONOMY_KEYS` strips it | **NO** | AIM YAML already lists it; Cengage YAML does not |

**FACT:** AIM YAML already declares `retrieval.source_ui.taxonomy_filters` entry `{ key: topic, label: Topic, type: text }`.

**FACT:** Frontend `DEFERRED_TAXONOMY_KEYS = {'topic'}` removes that entry from the sidebar and from API params (`taxonomyFilters.js`).

**FACT:** Compact source records do not contain `topic`. Golden compact metadata keys (`test_metadata_framework_phase1_characterization.py`) do not include `topic`.

**INFERENCE:** The live Source Library path (S3 compact index → `_source_matches`) cannot filter on topic even if the UI sent `topic=`, because the compact record has no value and the registry does not authorize the key.

### Retrieval path (related, not Source Library listing)

**FACT:** `_RETRIEVAL_LEGACY_EXACT = frozenset({"topic"})` still exact-matches `payload.metadata.topic` in `_passes_filters` (`context_retrieval.py`, commit `c51baa6`).

**FACT:** Production retrieval rebuilds doc metadata via `_metadata_from_record` → `project_retrieval_metadata` from the compact record. `topic` is not `PROMOTE_RETRIEVAL`, so reconstructed metadata has no `topic`.

**INFERENCE:** Legacy retrieval `topic=` gating is live in code and tests (synthetic payloads that inject `metadata.topic`), but the compact-index allow-set path (`_record_to_payload`) currently has nothing to match against. Clean content files also do not carry `topic` (`_PER_UNIT_METADATA_KEYS` omits it).

---

## 3. AIM Configuration Audit

Inspected: `dis_backend/config/clients/aim.yaml` (read-only).

### `metadata_schemas.aim`

**FACT:** `topic` is a **required** field:

```yaml
- name: topic
  type: string
```

- No `values` / enum.
- No `hint`.
- Sibling required fields: `course_name`, `document_type`, `unit_type`.

**FACT:** Phase 4 validation treats `"topic": "Landing gear"` as a valid AIM payload (`test_metadata_schema_validation_phase4.py`).

### `retrieval.source_ui.taxonomy_filters`

**FACT:** AIM already configures Topic as a Source Library taxonomy filter:

```yaml
- key: topic
  label: Topic
  type: text
```

Alongside `course_name` (select), `block` (select), `day` (select).

**FACT:** `type: text` means the existing frontend renderer draws a free-text input, not a `<select>` of `filter_options` values (`SourceLibraryPage.jsx`).

### `retrieval.source_ui.upload`

**FACT:** Upload fields are `document_type`, `chapter`, `module_name`, `learning_objective`. **`topic` is not an upload field.**

**INFERENCE:** Topic is inferred at ingest, not collected from the upload form. Making it a filter does not require upload-UI changes unless product later wants manual override.

### `metadata_framework`

**FACT:** AIM YAML has **no** `metadata_framework` block.

**FACT:** `test_real_aim_client_loads_without_metadata_framework` asserts `tenant.metadata_framework.model_dump() == {}`.

**FACT:** Empty/missing `metadata_framework` → `registry_for_tenant(aim) == DEFAULT_REGISTRY`.

### Profile-related configuration

**FACT:** `document_processing.profile: aviation_academic`. Topic inference is **not** YAML-driven; it lives in `AIMClientProfile.enrich_metadata` / `_topic_from_name`.

**FACT:** `structure_patterns.table_schedule_keywords` includes the word `topic` as a calendar-table header keyword — that is concept (B), not document metadata `topic`.

**FACT:** No closed vocabulary for document `topic` exists in AIM YAML.

---

## 4. Field Registry Audit

Inspected: `dis_backend/services/metadata_framework/registry.py`, `adapters.py`.

### Current: `topic → ?`

**FACT:** `DEFAULT_REGISTRY.get("topic")` is `None`.

| Promote target | Current topic |
|---|---|
| `PROMOTE_INDEX` | not promoted |
| `PROMOTE_RETRIEVAL` | not promoted (legacy `_RETRIEVAL_LEGACY_EXACT` instead) |
| `PROMOTE_FILTER_OPTIONS` | not promoted |
| `PROMOTE_CAS_LIST` | not promoted |
| aliases | none |
| `filter_options_key` | n/a |

**FACT:** No production client YAML defines `metadata_framework.fields` (AIM, Cengage, Academian). All tenants use `DEFAULT_REGISTRY`.

### Required: `topic → ?`

**INFERENCE:** Do **not** add `topic` to `DEFAULT_REGISTRY`. Adding it there would authorize `topic=` and emit `filter_options.topic` for Cengage/default tenants. Tenant isolation is already the `registry_from_config` overlay (`registry_for_tenant`).

**FACT:** Overlaying a YAML-only field is the proven Phase 1–3 pattern (`test_metadata_field` in `test_metadata_framework_phase1_tenant_wiring.py` and Phase 3 dynamic filters).

Recommended AIM overlay (config-only; not implemented here):

```yaml
metadata_framework:
  fields:
    topic:
      type: string
      tier: structural
      promote:
        - index
        - retrieval
        - filter_options
        # cas_list optional — see below
      # filter_options_key omitted → response key "topic"
      # aliases: none
      # extract: default EXTRACT_META_OR_EMPTY → payload.metadata.topic or ""
```

| Target | Required for Source Library filter? | Why |
|---|---|---|
| `index` | **YES** | Compact records must carry `topic` for `_source_matches` / `source_filter_options` |
| `filter_options` | **YES** | Authorizes `topic=` on `/documents/library`; includes key in `_source_matches` loop |
| `retrieval` | **Recommended** | Makes `_metadata_from_record` rebuild `topic` so retrieval `topic=` works on the compact-index path; matches other taxonomy fields |
| `cas_list` | **Optional** | Adds `topic` to the list-API whitelist. The Source Library **table** does not render taxonomy columns today. Other AIM taxonomy fields (`course_name`, `block`, `day`) **are** `cas_list`. Include for “normal filter” parity; not required for the filter control itself. |

**FACT:** `extract` default `EXTRACT_META_OR_EMPTY` reads `meta.get("topic") or ""` and does **not** fall back to unit metadata. That is the correct extract for document-level topic (calendar-day `topic` must not leak into the compact record).

**FACT:** No alias is required: UI key, API key, and storage key are all `topic`. Contrast `module` → `module_name`.

---

## 5. Index Representation

**FACT:** Compact records are flat. There is no nested `metadata.topic` on the source index. Golden keys are scaffold fields + `GOLDEN_COMPACT_METADATA_KEYS` (no `topic`).

**FACT:** `compact_source_record` writes only `project_index_metadata(...)` for registry `PROMOTE_INDEX` fields, then returns that dict. Extra payload keys are not copied.

**FACT:** `write_source_content_and_index` persists that compact record via `upsert_source_record`.

**FACT:** Retrieval reconstruction (`_metadata_from_record`) copies only `PROMOTE_RETRIEVAL` keys from the compact record.

**FACT:** Clean content documents strip unit metadata to `_PER_UNIT_METADATA_KEYS` (`block`, `block_id`, `block_number`, `day_number`, `acs_codes`, `missed_codes`, `document_type`, `content_type`, `visibility`, `sheet_name`). **`topic` is not among them.**

### Do existing indexed documents already have topic?

**FACT (code contract):** New and existing compact index records **do not** include `topic` under current `DEFAULT_REGISTRY`.

**INFERENCE:** Registry promotion alone is **not** sufficient for already-indexed AIM documents. Existing compact records will lack the key until rewritten.

**FACT:** Source of truth for a backfill is the studio payload (`payload_key` on the compact record) → `metadata.topic`, which AIM enrich always sets (`meta.get("topic") or _topic_from_name(...)`).

S3 was not queried (out of scope). Exact production occupancy of `metadata.topic` on payloads is therefore **not measured in this discovery**.

---

## 6. Source Library Backend Audit

Inspected: `source_library.py`, `context_retrieval.py` (`_source_matches`, `list_sources`, `documents_library`), `filter_query.py`.

### What prevents topic from being a normal filter today?

#### BACKEND BLOCKER

1. **FACT:** `topic` is not `PROMOTE_FILTER_OPTIONS`, so:
   - `resolve_filter_options_storage_key("topic", aim)` returns `None`
   - `build_documents_library_filters` ignores `topic=`
   - `_source_matches` does not loop `topic` (only `registry.for_promote(PROMOTE_FILTER_OPTIONS)` plus listing-compat `lesson_name` / `visibility`)
2. **FACT:** Compact records have no `topic` value, so even a forced match would see `src.get("topic")` as empty → any non-empty/non-wildcard filter would fail.

No new matching function is required. `_source_matches.eq()` already implements case-insensitive equality, empty-pass, and `all`/`*`/`any` wildcards for every `filter_options` field.

#### FRONTEND BLOCKER

**FACT:** `DEFERRED_TAXONOMY_KEYS` drops `topic` from `taxonomyFiltersFromUiConfig` and `toSourceLibraryApiFilters`. Tests lock this (`taxonomyFilters.test.js`: “defers AIM topic until a backend path exists”).

**FACT:** `SourceLibraryPage.jsx` already renders `type === 'text'` as an `<input>` and `select` from `filterOptions[optionsKey]`. No page-level topic special case.

#### CONFIGURATION BLOCKER

**FACT:** AIM **already** lists Topic in `taxonomy_filters`. Configuration of the **UI slot** is not the gap.

**FACT:** The missing configuration is AIM `metadata_framework.fields.topic` (registry promotion). Without it, DIS will not authorize `topic=`.

**INFERENCE:** Enabling the frontend deferral **without** registry promotion would show a Topic box that sends `topic=` and appears to do nothing (CAS forwards; DIS ignores). Frontend and backend must ship together.

#### DATA / INDEX BLOCKER

**FACT:** Existing compact source-index documents omit `topic`. After promotion, **new** ingests would write it; **old** AIM rows need a compact-index rewrite (see §13).

---

## 7. API Audit

**Question:** Does Phase 3 dynamic filter forwarding already support `topic=<value>` without API code changes once topic is registry-authorized?

### YES

**FACT (CAS):** `merge_cas_forwarded_query_params` forwards any non-control extra query key. `topic` is not in `CAS_DOCUMENTS_CONTROL_PARAMS`. `test_dynamic_synth_field_forwarded_to_dis_params` proves extras (e.g. `test_metadata_field`) reach DIS params. `topic` would be forwarded the same way.

**FACT (DIS):** `documents_library` named `Query` params do not include `topic`. Extra keys come from `request.query_params` into `merge_registry_authorized_query_filters`. Once `registry_for_tenant(aim)` promotes `topic` to `filter_options`, `resolve_filter_options_storage_key("topic", aim)` returns `"topic"` and the value is placed on `filters["topic"]`.

**FACT:** Unknown/unauthorized keys are ignored (`test_unknown_query_key_ignored`). Today `topic=` is ignored for every tenant.

**FACT:** No FastAPI signature change, no CAS allowlist edit, no new DIS route is required.

Named DIS params (`block`, `day`, `chapter`, `module_name`, `learning_objective`, …) are a Phase 0–2 compatibility set (`DOCUMENTS_LIBRARY_CONTROL_PARAMS`). Topic should **not** join that named set; Phase 3 extras are the intended path.

**INFERENCE:** Adding `topic` as a named FastAPI `Query` would be redundant architecture. Do not.

---

## 8. Frontend Audit

Inspected: `taxonomyFilters.js`, `taxonomyFilters.test.js`, `SourceLibraryPage.jsx`, `sourceLibraryApi.js`.

### `DEFERRED_TAXONOMY_KEYS`

```js
export const DEFERRED_TAXONOMY_KEYS = new Set(['topic']);
```

**FACT:** This is the only frontend topic-specific logic in Source Library. Comments state AIM `topic` remains deferred.

**FACT:** `TAXONOMY_API_KEY_MAP` has no `topic` entry → `apiParamForTaxonomyKey('topic') === 'topic'`.

**FACT:** `TAXONOMY_OPTIONS_KEY_MAP` has no `topic` entry → `optionsKeyForTaxonomyKey('topic') === 'topic'` (fallthrough). Correct if `filter_options_key` is omitted / `"topic"`.

**FACT:** `toSourceLibraryApiFilters` already forwards any non-deferred, non-empty key (Phase 3 synth-field test). After removing the deferral, `topic` is forwarded with no allowlist edit.

### Minimum frontend change

1. Remove `'topic'` from `DEFERRED_TAXONOMY_KEYS` (and the comment that it is deferred).
2. Update `taxonomyFilters.test.js` (deferral tests become “renders / sends topic”).
3. No `SourceLibraryPage.jsx` change if AIM YAML stays `type: text`.

### Can topic simply be enabled through existing `taxonomy_filters` after backend promotion?

**INFERENCE:** **Almost.** YAML already has the filter. The page already renders config-driven taxonomy filters. The **only** frontend code change is removing the deferral. Backend promotion + compact-record data are still required for the control to work.

Do **not** add a hardcoded Topic widget. Do **not** add `topic` to `TAXONOMY_API_KEY_MAP` unless a rename is introduced (none needed).

---

## 9. Retrieval Audit

**FACT:** Phase 6A preserved top-level `topic` via `_RETRIEVAL_LEGACY_EXACT` (DECISION-001). Tests in `TestTopicLegacyCompat` lock:

| Filter | Payload `topic` | Result |
|---|---|---|
| missing / `{}` | Engines | pass |
| `""` | Engines | pass |
| `Engines` | Engines | pass |
| `engines` | Engines | pass (case-insensitive) |
| `Hydraulics` | Engines | fail |
| `Engines` | missing | fail |
| `all` | Engines | **fail** (`all` is literal, not wildcard) |
| `metadata_filters.topic` | Engines / Hydraulics | match / fail |

`value_matches` (retrieval) does **not** treat `all`/`*`/`any` as wildcards. Listing `_source_matches.eq()` **does**.

### If topic becomes `PROMOTE_RETRIEVAL`

**FACT:** The registry exact-match loop would include `topic` (it is not in `_RETRIEVAL_EXACT_SKIP`). `_RETRIEVAL_LEGACY_EXACT` would then apply the **same** `value_matches` check again — redundant, same result.

**FACT:** Reconstruction would `rec.get("topic")` only if `PROMOTE_INDEX` actually wrote the key (and backfill for old rows).

### Cleanest transition

| Option | Verdict |
|---|---|
| **A. registry promotion + remove legacy exception** | Cleanest end state. Tests in `TestTopicLegacyCompat` should be rewritten to assert AIM-registry `PROMOTE_RETRIEVAL` behavior, plus a default-tenant test that `topic=` is **not** globally active. |
| **B. registry promotion + retain compatibility temporarily** | Safe during rollout; double-gates AIM. Harmless but leaves DECISION-001 scaffolding. |
| **C. registry promotion only for filter_options/index** | Sufficient for **Source Library listing**. Retrieval compact-index `topic=` stays broken/no-op. Not “a normal taxonomy field”. |
| **D. other** | Not needed. |

**INFERENCE:** Recommend **C for the listing MVP is incomplete**; treat **A** as the target and **B** as an acceptable single-PR stepping stone if reviewers want the legacy tests green before deleting `_RETRIEVAL_LEGACY_EXACT`.

Do not invent a second retrieval matching path.

---

## 10. Tenant Isolation

### Desired

| Tenant | Topic filter |
|---|---|
| AIM | enabled |
| Cengage | unchanged (no Topic UI, `topic=` unauthorized) |
| Default / empty `metadata_framework` | unchanged |

### Does `registry_for_tenant()` already give this?

**FACT: YES**, if `topic` is added **only** as an AIM YAML overlay, not to `DEFAULT_REGISTRY`.

Proven by Phase 3:

- Tenant A with `metadata_framework.fields.test_metadata_field` → key authorized.
- Tenant B / `None` / empty framework → key ignored.
- `source_filter_options` includes the key only for Tenant A.

**FACT:** Cengage YAML `taxonomy_filters` are `course_name`, `chapter`, `module`, `learning_objective`. **No `topic`.** After frontend deferral removal, Cengage still will not **render** Topic.

**FACT:** Cengage YAML has no `topic` string at all.

**INFERENCE:** Isolation is configuration, not new code: AIM `metadata_framework` + existing AIM `taxonomy_filters` vs Cengage omitting both.

**DECISION REQUIRED:** Academian YAML copies AIM’s `metadata_schemas` required `topic` but has **no** `source_ui.taxonomy_filters` and no `metadata_framework`. Default = do not enable Academian Topic unless product asks.

---

## 11. Filter Semantics

### Source Library (`_source_matches.eq`)

| Query | Behavior **if** `topic` were `filter_options`-promoted |
|---|---|
| `topic=Engines` | case-insensitive exact vs `src["topic"]` |
| `topic=engines` | match Engines |
| `topic=Hydraulics` | fail if stored value is Engines |
| `topic=""` | no-op (pass) |
| `topic=all` / `*` / `any` | **wildcard pass** (listing) |
| missing `topic` | no-op (pass) |
| compact record missing `topic` | `src.get("topic")` empty → non-wildcard filter **fails** |

**FACT:** This is the same `eq()` used for `course_name`, `chapter`, `module_name`, `learning_objective`, etc. Block is the exception (`same_block`).

**FACT:** Listing does **not** do substring / multi-value split. `metadata_filters` is a separate dict path (also case-insensitive exact).

### Legacy retrieval (`value_matches`)

Same case-insensitive exact; empty filter passes; **`topic=all` is literal** (test-locked); multi-value via `as_list` intersection if a list is passed.

### What Source Library should use

**INFERENCE:** Do **not** invent new semantics. Once promoted to `filter_options`, Topic automatically gets listing `eq()` behavior. That is what “a normal Source Library filter” means in this codebase.

**DECISION REQUIRED:**

1. **Listing vs retrieval `all`:** listing wildcards `all`; retrieval treats `all` as a literal topic string. Aligning them would be a **new** change to one path. Product must say whether Source Library Topic should keep listing wildcards (recommended: yes, consistent with Block/Day) or copy retrieval’s literal `all`.
2. **Text exact-match UX:** AIM `type: text` plus `_topic_from_name` (cleaned filename title, max 120 chars) implies **high cardinality**, not a closed set like Engines/Hydraulics. Exact match on a free-text box may be awkward if stored values are near-unique titles. Schema has **no** enum. Do not silently switch to substring or select-dropdown without a product call.
3. **Multi-value:** not established. Current listing is single-value exact.

---

## 12. Filter Options

**FACT:** `source_filter_options` scans compact records in memory and collects distinct non-empty string values per `filter_options` map key. No OpenSearch aggregation. No SQL distinct query.

**FACT:** For `type: text`, `SourceLibraryPage` does **not** read `filterOptions` for that control.

### Does Topic need distinct-values?

| Need | Required? |
|---|---|
| Distinct-values query (OS/SQL) | **NO** |
| Existing `source_filter_options` | **YES** for authorization side-effect and for a future `type: select`; unused by current `type: text` UI |
| Configured static options | **NO** — schema has no `values` |
| Index-derived options | Automatic once `PROMOTE_FILTER_OPTIONS` + compact `topic` exist |
| New options API | **NO** |

**INFERENCE:** Minimum change is promote `filter_options` (and `index`). Do not add a Topic vocabulary table or TaxonomyRegistry.

**DECISION REQUIRED:** Keep YAML `type: text` (current) vs change to `select` (dropdown of index-derived topics). Discovery recommends **keep text** unless product confirms a small closed list — inferred titles are a poor dropdown.

---

## 13. Existing Data / Backfill Assessment

S3 was not read. Assessment is from code contracts only.

| Operation | Required? |
|---|---|
| OpenSearch reindex | **NO** (Source Library does not filter topic in OS) |
| Full pipeline reingest | **NO** if payloads already have `metadata.topic` |
| Compact source-index rewrite (“backfill”) | **YES** for pre-promotion AIM documents |
| Schema/DB migration | **NO** |
| No data operation | **NO** — old compact rows would never match a Topic filter |

### If backfill is required

| Item | Finding |
|---|---|
| Source of truth | Studio payload JSON at compact `payload_key` → `metadata.topic` |
| Fallback if payload missing `topic` | AIM `_topic_from_name` from `source_file_name` — **INFERENCE** only; prefer stored payload value |
| Approximate scope | All AIM compact `sources[]` entries. A historical comment in `source_library.py` mentions **605 AIM records** as of that measurement; **not re-verified** here. |
| Safe mechanism | Read index → for each record read payload → copy `metadata.topic` onto the compact row → `write_source_index`. Equivalent: re-run `compact_source_record(payload, ..., tenant_cfg=aim)` after YAML promotion and upsert. |
| Independent of deploy? | **YES** for the rewrite job, but **new writes** only include `topic` after AIM registry overlay is live. Sequence: deploy YAML overlay → backfill compact index (can be a one-shot script). Independent of OpenSearch. |
| Cengage | Do not backfill Cengage. |

**DECISION REQUIRED:** Whether backfill ships in the same release as the filter, or filter launches knowing older docs appear as “no topic” until rewritten.

---

## 14. Cengage Impact

**FACT:** Cengage has no `topic` in YAML (`taxonomy_filters`, schemas, upload).

**FACT:** Enabling AIM via tenant `metadata_framework` does **not** add `topic` to `DEFAULT_REGISTRY`.

**FACT:** `registry_for_tenant(cengage)` stays `DEFAULT_REGISTRY` → `topic=` ignored; `source_filter_options` omits `topic`.

**FACT:** Frontend after deferral removal still only shows filters listed in that tenant’s `uiConfig.source_library.taxonomy_filters`. Cengage list unchanged.

**INFERENCE:** Natural isolation holds **if and only if** topic is not added to `DEFAULT_REGISTRY`. Adding it to the default registry would authorize Cengage `topic=` and emit an empty/partial `topic` options list — avoid.

---

## 15. Test Impact

Do not modify tests in this discovery. Future implementation should add/update:

| Area | Existing lock | Future |
|---|---|---|
| Registry topic promotion | `DEFAULT_REGISTRY` golden keys exclude `topic` | AIM overlay: `registry_for_tenant(aim).get("topic")` promotes index + filter_options (+ retrieval). Default still `None`. |
| Tenant isolation | Phase 3 synth-field tests | AIM vs Cengage/default: `resolve_filter_options_storage_key("topic", …)` |
| `source_filter_options` | golden map | AIM includes `topic`; Cengage does not |
| `_source_matches` | Phase 2 characterization | AIM compact `{topic: "Engines"}` vs `topic=engines` / mismatch / empty / missing key |
| API forwarding | CAS synth extras; DIS `build_documents_library_filters` | `topic=Engines` merges for AIM; ignored for default |
| Frontend | deferral tests | Remove deferral; assert Topic renders from AIM uiConfig and `toSourceLibraryApiFilters` sends `topic` |
| E2E filter flow | Phase 3 HTTP e2e for synth | AIM: YAML → filters dict → `_source_matches` |
| Legacy retrieval | `TestTopicLegacyCompat` | Keep until `_RETRIEVAL_LEGACY_EXACT` removed; then assert via `PROMOTE_RETRIEVAL` on AIM registry |
| Empty / default | `test_real_aim_client_loads_without_metadata_framework` | **Must update** once AIM YAML gains `metadata_framework` — assertion `model_dump() == {}` will fail |
| Compact key contract | `test_compact_source_record_key_contract` uses **no** tenant_cfg | Stays green if DEFAULT unchanged |

**FACT:** `test_real_aim_client_loads_without_metadata_framework` is a hard regression against adding AIM `metadata_framework` unless the test is updated as part of implementation.

---

## 16. Production Consumer Impact

Desired shape: **topic configuration + registry promotion + existing dynamic filter infrastructure**, no new filtering architecture.

### CONFIG-ONLY (AIM YAML)

| File | Change |
|---|---|
| `dis_backend/config/clients/aim.yaml` | Add `metadata_framework.fields.topic` with promote list. **Do not** modify `metadata_schemas`, AIM transforms, or `taxonomy_filters` (already present). |

### CODE CHANGES (production)

| File | Change | Required? |
|---|---|---|
| `frontend/src/features/sourceLibrary/utils/taxonomyFilters.js` | Remove `topic` from `DEFERRED_TAXONOMY_KEYS` | **YES** |
| `dis_backend/services/context_retrieval.py` | Remove `_RETRIEVAL_LEGACY_EXACT` after `PROMOTE_RETRIEVAL` | Optional (transition A vs B) |
| `registry.py` `DEFAULT_FIELDS` | Add topic | **NO — do not** |
| `filter_query.py` | named `topic` param | **NO** |
| CAS `source_library.py` / `source_library_filter_forward.py` | allowlist | **NO** |
| `source_library.py` matching | special `topic` matcher | **NO** |
| AIM `client_profiles/aim.py` | transform | **NO** |
| OpenSearch / indexing | mappings, `topics` field | **NO** |
| `SourceLibraryPage.jsx` | Topic widget | **NO** if YAML stays `type: text` |

**Count:** **1 config file + 1 frontend file** (plus tests). Retrieval cleanup is a 1-file optional follow-on.

That is as close to the desired minimum as the repository allows.

---

## 17. OpenSearch Assessment

**FACT:** Source Library listing reads the S3 compact source index and filters in Python (`list_sources` → `_source_matches`). OpenSearch is used only to attach `indexed_units` counts (`_attach_indexed_units`).

**FACT:** Vector search filters by `client_id` + allow-listed `job_id`s, not by topic (`indexing.py` / retrieve allow-set).

**FACT:** OpenSearch field `topics` (plural, keyword extraction) is **not** AIM document `topic`.

**Verdict:** **OpenSearch is OUT OF SCOPE** for making Topic a Source Library filter. Do not redesign OpenSearch. Do not reindex OS for this feature.

---

## 18. Recommended Implementation

Repository evidence confirms the smallest architecture:

```
AIM metadata.topic
    → AIM metadata_framework Field Registry overlay
        → PROMOTE_INDEX          (compact S3 source record)
        → PROMOTE_FILTER_OPTIONS (authorize topic=; _source_matches; filter_options map)
        → PROMOTE_RETRIEVAL      (rebuild topic on retrieval payloads)
    → existing Source Library filter infrastructure
    → frontend: undeffer taxonomy key `topic`
```

Tenant isolation: AIM overlay only; `DEFAULT_REGISTRY` unchanged; Cengage YAML unchanged.

### Implementation sequence (future phase — not this commit)

1. AIM YAML `metadata_framework.fields.topic` (`index`, `filter_options`, `retrieval`; optional `cas_list`).
2. Compact-index backfill from `payload.metadata.topic`.
3. Remove frontend `DEFERRED_TAXONOMY_KEYS` `topic`.
4. Update tests listed in §15.
5. Remove `_RETRIEVAL_LEGACY_EXACT` once AIM retrieval tests pass via the registry (transition A), or keep it one release (B).

### Explicitly not required

- TaxonomyRegistry
- `metadata_schemas` edits
- AIM `_topic_from_name` changes
- CAS filter architecture
- New `_source_matches` branch
- OpenSearch
- Upload field for topic (unless product later wants it)
- Changing AIM `taxonomy_filters` (already `key: topic`, `type: text`)

---

## 19. Open Decisions

| ID | Question | Notes |
|---|---|---|
| **P7-D1** | Promote `cas_list` as well as index / filter_options / retrieval? | Needed only for list-API field parity. Filter control does not depend on it. |
| **P7-D2** | Retrieval transition A vs B (delete vs temporarily keep `_RETRIEVAL_LEGACY_EXACT`)? | A is cleanest; B is safer for one release. |
| **P7-D3** | `topic=all` listing wildcard vs retrieval literal? | Do not invent; default = keep each path’s current helper. Confirm product is fine with listing wildcards. |
| **P7-D4** | Keep `type: text` vs change to `select` of index-derived values? | YAML is already `text`. High-cardinality inferred titles argue against select. |
| **P7-D5** | Exact match on filename-derived strings acceptable? | Established listing semantics are exact. Changing to substring would be new architecture. |
| **P7-D6** | Backfill in the same release as the UI? | Old compact rows will not match until rewritten. |
| **P7-D7** | Enable Academian Topic? | Schema has `topic`; no taxonomy_filters. Default: AIM only. |
| **P7-D8** | Collect topic on upload? | Not required for filtering inferred values. Out of minimum scope. |

---

## 20. Explicit Non-Goals

- Do not add topic everywhere
- Do not redesign metadata architecture, OpenSearch, or filtering
- Do not create TaxonomyRegistry
- Do not modify `metadata_schemas` (already has `topic`)
- Do not modify AIM transforms / CAS (except CAS already forwards extras)
- Do not promote `acs_codes`
- Do not confuse document `topic` with calendar-day `topic` or OS `topics`
- Do not put `topic` on `DEFAULT_REGISTRY`
- Do not implement in this discovery commit

---

## 21. Evidence Index

| Path | Role |
|---|---|
| `dis_backend/config/clients/aim.yaml` | schema `topic`; taxonomy_filters Topic text; no `metadata_framework`; no upload topic |
| `dis_backend/config/clients/cengage.yaml` | no topic; taxonomy_filters without Topic |
| `dis_backend/config/clients/academian.yaml` | schema `topic`; no source_ui taxonomy_filters |
| `dis_backend/services/metadata_framework/registry.py` | `DEFAULT_FIELDS`; `registry_for_tenant`; YAML overlay |
| `dis_backend/services/metadata_framework/adapters.py` | index / retrieval / filter_options / cas_list projection |
| `dis_backend/services/metadata_framework/filter_query.py` | Phase 3 authorization; `DOCUMENTS_LIBRARY_CONTROL_PARAMS` |
| `dis_backend/services/source_library.py` | `compact_source_record`; `source_filter_options`; `_PER_UNIT_METADATA_KEYS` |
| `dis_backend/services/context_retrieval.py` | `_source_matches`; `_RETRIEVAL_LEGACY_EXACT`; `_metadata_from_record`; `list_sources`; `ui_config` |
| `dis_backend/api/routers/context.py` | `/context/documents/library` extra query keys |
| `app/core/source_library_filter_forward.py` | CAS extras forwarding |
| `app/api/v1/routers/source_library.py` | CAS documents list |
| `dis_backend/services/client_profiles/aim.py` | `metadata.topic` + `_topic_from_name` |
| `dis_backend/services/agents/metadata_extraction_agent.py` | LLM extracts schema fields including `topic` |
| `dis_backend/services/agents/metadata_tagging_agent.py` | `enrich_metadata` then hints |
| `frontend/src/features/sourceLibrary/utils/taxonomyFilters.js` | `DEFERRED_TAXONOMY_KEYS` |
| `frontend/src/features/sourceLibrary/pages/SourceLibraryPage/SourceLibraryPage.jsx` | config-driven text/select taxonomy UI |
| `dis_backend/tests/test_metadata_framework_phase1_characterization.py` | golden compact / filter_options / cas_list keys |
| `dis_backend/tests/test_metadata_framework_phase3_dynamic_filters.py` | tenant isolation of extras |
| `dis_backend/tests/test_metadata_framework_phase6a_retrieval_gating.py` | `TestTopicLegacyCompat`; AIM empty framework |
| `tests/unit/test_source_library_filter_forward.py` | CAS forwards unknown extras |
| `docs/phase6a-retrieval-gating-approval.md` (git `519fd6c`) | DECISION-001: reject topic promotion in 6A |
| `docs/phase6-metadata-architecture-discovery.md` (git `210723c`) | prior topic gap analysis |

---

## Document Control

| Version | Date | Notes |
|---|---|---|
| 1.0 | 2026-08-31 | Phase 7 discovery only; no implementation |
