# Phase 8 — Automated Metadata Configuration Provisioning Discovery

**Status:** DISCOVERY ONLY. No implementation. No tenant YAML, runtime, CAS, DIS, Field Registry, schema, UI, test, DB, S3, or infrastructure changes.

**Classification legend:** every important conclusion is tagged **FACT**, **INFERENCE**, or **DECISION REQUIRED**.

---

## 1. Baseline

| Item | Value |
|---|---|
| Repository | content-ai-studio (`C:\xampp\htdocs\Content-AI-Studio-`) |
| Branch | `feature/dis_metadata` |
| HEAD | `6120c46fef7afce7875c7a5211aa171aa9ec01d3` |
| HEAD subject | `feat: enable AIM topic source library filtering` |
| Working tree at start | CLEAN (`git status --short` empty) |

**FACT.** Expected branch `feature/dis_metadata` is checked out. Working tree was clean. No stash, reset, or clean was performed.

Recent history (abbreviated): Phases 0–7 metadata work (`255b4ff` … `6120c46`), then this discovery.

**FACT.** `docs/*` is gitignored (`.gitignore`). This file is the only permitted repository change and must be force-added.

---

## 2. Current Tenant Configuration Architecture

### 2.1 Two different “tenants”

**FACT.** The word “tenant” names two objects:

| Layer | Object | Storage | Created by |
|---|---|---|---|
| CAS organization | `Project` row (`slug`, `name`, `client_name`) | CAS database | `POST /api/v1/platform/tenants` → `tenant_service.create_tenant` |
| DIS client / workspace | One YAML file + in-process `TenantConfig` | `dis_backend/config/clients/<id>.yaml` | Hand-authored YAML, or `dis_provisioning.provision_dis_client` |

**FACT.** DIS has no database row for a client. `app/services/dis_provisioning.py` states: a client is a YAML file loaded into `TenantRegistry` plus an `available_clients` allowlist entry in `config/dis_access.json`.

**FACT.** CAS Source Library access is gated by `config/dis_access.json` (`available_clients`) with env override `DIS_AVAILABLE_CLIENTS`. Membership on a CAS `Project` can grant DIS access only when the normalized `client_name` is in that allowlist (`app/core/dis_access.py`).

### 2.2 How a client becomes a valid DIS tenant today

**FACT.** Load path (`dis_backend/config/settings.py` `TenantRegistry`):

1. Glob `dis_backend/config/clients/*.yaml` (sorted).
2. If any files exist, load them; ignore legacy `config/tenants/tenant_*.yaml`.
3. Convert each file via `_client_raw_to_tenant`.
4. Register by `tenant_id`; raise on namespace collision across different tenant ids.

**FACT.** Two YAML shapes are accepted:

| Shape | Detector | Path |
|---|---|---|
| **Client wrapper** | top-level `client:` | Converted to `TenantConfig` (`tenant_id` ← `client.client_id`, `display_name` ← `client.client_name`, `namespace` ← `client.namespace`). Applies env placeholder expansion, `DIS_MODEL_*` overrides, `DIS_S3_*` bucket/prefix/region overrides, forces `storage.provider = s3`, copies `aim_content_rules` / `content_ingestion_rules` / `blueprint_rules` / `course_generation_rules` into `client_rules`. |
| **Old TenantConfig** | no `client:` key | `TenantConfig(**raw)` only. **Does not** apply env expansion, model overrides, or S3 env overrides. |

**FACT.** Existing AIM / Cengage / Academian files use the **client wrapper**. Auto-provisioned files use the **old TenantConfig** shape (`tenant_id` / `display_name` / `namespace` only).

**FACT.** `TenantConfig.get_client()` synthesizes a virtual `ClientConfig` when `clients` is empty (“backward-safe one-client mode”). That is why the three-field provisioned YAML still loads.

**FACT.** After YAML exists, runtime availability also requires:

- CAS allowlist: slug in `config/dis_access.json` `available_clients` (unless `DIS_AVAILABLE_CLIENTS` replaces the file list).
- DIS process has loaded/reloaded the registry (`POST /admin/config/reload` or restart).
- Docker: `dis_backend` bind-mounts `./dis_backend/config:/app/config`; CAS `api` bind-mounts `.:/app`, so a YAML written on the host is visible to DIS without image rebuild **on that host**.

### 2.3 Configured clients found

**FACT.** Exactly three client YAML files exist:

| File | `client.client_id` | Display name | Namespace | In `available_clients` |
|---|---|---|---|---|
| `dis_backend/config/clients/aim.yaml` | `aim` | Aviation Institute of Maintenance | `aim_ns` | yes |
| `dis_backend/config/clients/cengage.yaml` | `cengage` | Cengage | `cengage_ns` | yes |
| `dis_backend/config/clients/academian.yaml` | `academian` | Academian Content Studio | `academian_ns` | **no** |

**FACT.** `config/dis_access.json` `available_clients` is `["aim", "cengage"]` only. Academian YAML is loaded by DIS `TenantRegistry` but is not a CAS Source Library allowlisted client.

**FACT.** Client-name aliases (`app/core/dis_access.py`): `cengage_learning` → `cengage`; identity aliases for `aim`, `cengage`, `academian`, `demo`. Unknown names become `lower().replace(" ", "_")`.

**FACT.** No `dis_backend/config/tenants/` directory. No Terraform. No tenant YAML template directory. No Jinja tenant-config templates.

### 2.4 Authority layers (do not merge)

**FACT.** Phases 0–7 established four separate authorities. Provisioning must configure them independently.

| Authority | Home | Responsibility |
|---|---|---|
| `metadata_schemas` | client YAML → `TenantConfig.metadata_schemas` | Domain field names, types, required/optional, enum `values`, hints. Used for ingest validation reports and upload option lists. |
| `retrieval.source_ui` | client YAML → `TenantConfig.retrieval.source_ui` | Presentation: `common_filters`, `taxonomy_filters`, `upload.fields`. |
| Field Registry | `DEFAULT_FIELDS` in `dis_backend/services/metadata_framework/registry.py`, overlay from YAML `metadata_framework.fields` | Index / filter_options / retrieval / cas_list projection. |
| Client profile | Python `services/client_profiles/` + YAML `client_rules` (e.g. `aim_content_rules`) | Tenant-specific inference / transformation. |

**FACT.** Empty/missing `metadata_framework` → `DEFAULT_REGISTRY` (merge-onto-defaults; YAML keys overlay or append). Empty `source_ui` → hardcoded generic UI in `ContextRetrievalService._source_ui_config`. Empty `metadata_schemas` → empty `MetadataSchema()` (no required fields). Unknown client id → `BaseClientProfile` (passthrough).

**FACT.** No master metadata registry exists. `registry.py` comments: do not put `acs_codes` in the Field Registry.

---

## 3. Existing Provisioning Mechanism

**File:** `app/services/dis_provisioning.py`  
**Tests:** `tests/unit/test_dis_provisioning.py`  
**Trigger:** `app/api/v1/routers/platform_tenants.py` `create_tenant` — after CAS DB commit, `background_tasks.add_task(provision_dis_client, project.client_name or project.name, project.name)`.

### 3.1 What it provisions today

**FACT.**

| Concern | Behavior |
|---|---|
| Input | `client_name`, `display_name`. Slug = `normalize_client_name(client_name)`. Empty slug → skip. `dis_enabled` false → skip. |
| Output | (1) `dis_backend/config/clients/{slug}.yaml` with three keys. (2) Append slug to `config/dis_access.json` `available_clients`. (3) Best-effort `POST {DIS}/admin/config/reload`. |
| Template | None. F-string of three YAML keys. Comment: “every other field defaults”. |
| Validation | None on YAML content. `available_clients` write refuses to overwrite a malformed JSON file (raises; outer wrapper swallows). CAS `create_tenant` rejects two active projects whose `client_name` normalizes to the same slug. |
| Tenant directory | `dis_backend/config/clients/` (repo-relative from `app/services/dis_provisioning.py`). |
| YAML generation | Always `path.write_text(...)`. **No exists check. No merge. Unconditional overwrite.** |
| Idempotency | `available_clients` append is idempotent (no duplicate slug). YAML write is **not** idempotent: re-run replaces the file. Reload is best-effort. |
| Error handling | Entire provision is try/except: logs and never raises. CAS tenant remains even if DIS provision fails. |
| Environment | No env-specific YAML. Warns if `DIS_AVAILABLE_CLIENTS` is set (file allowlist ignored at read time). |

**FACT.** Generated YAML (from tests):

```yaml
# Auto-created on tenant creation — see app/services/dis_provisioning.py.
tenant_id: "nova-publishing"
display_name: "Nova Publishing"
namespace: "nova-publishing"
```

**FACT.** That is identity only. It does **not** write `metadata_schemas`, `metadata_framework`, `retrieval.source_ui`, `document_processing`, stores, pipeline models, or `client:` wrapper.

### 3.2 Runtime defaults for a provisioned-only tenant

**FACT.** A three-field YAML loads as `TenantConfig` with Pydantic defaults:

- `metadata_schemas`: `{}` → empty schema
- `metadata_framework`: empty → `DEFAULT_REGISTRY`
- `retrieval.source_ui`: `{}` → generic taxonomy `course_name` / `module_name` / `lesson_name` plus common filters `purpose`, `document_type`, `visibility`, `status`, `search`
- `document_processing.profile`: `generic_academic` with default enabled types / rules empty (extractor then uses `DEFAULT_DOC_RULES` in `specialized_extractors.py`)
- `structure_store.enabled` / `vector_store.enabled` / `embedding.enabled`: `false`
- `pipeline.llm_provider`: `mock`; `bedrock_enabled`: `false`
- Client profile: `BaseClientProfile`

**INFERENCE.** Auto-provisioned tenants are CAS-visible Source Library clients (allowlisted + reload) but are **not** production-ready for metadata UX, ingest classification, or vector/structure stores until YAML is hand-edited.

### 3.3 Can existing provisioning be extended?

**FACT.** Yes, mechanically: `_write_client_yaml` is a single function; tests already isolate `_CLIENTS_DIR`. Extension does not require a new service.

**FACT.** Safety gaps that any extension must not inherit blindly:

1. Unconditional YAML overwrite (would destroy AIM/Cengage/Academian if slug collided and CAS collision check were bypassed, or if provision were re-run for an existing slug after a matching Project was deleted).
2. Old TenantConfig shape skips env expansion / S3 / model overrides used by hand-authored clients.
3. Best-effort errors (partial provision: YAML written, allowlist failed, or vice versa, then exception logged).
4. No metadata validation.
5. Cross-process race on `dis_access.json` (commented in source).

**DECISION REQUIRED.** Whether extension writes `client:` wrapper YAML (match AIM/Cengage/Academian) or keeps old shape.

---

## 4. Metadata Configuration Inventory

### 4.1 Matrix

Legend for cells: **Y** = present in that tenant’s YAML; **—** = absent (runtime default / empty); **D** = system-derived / code default, not tenant YAML.

| Configuration | AIM | Cengage | Academian | Other |
|---|---|---|---|---|
| Client YAML file | Y | Y | Y | none |
| `client` wrapper (id, name, namespace, file types) | Y | Y | Y | — |
| CAS `available_clients` | Y | Y | — | — |
| `metadata_schemas` | Y (`aim`) | Y (`cengage`) | Y (`academian`) | — |
| `metadata_framework.fields` overlay | Y (`topic` only) | — → D `DEFAULT_REGISTRY` | — → D `DEFAULT_REGISTRY` | — |
| `retrieval.source_ui.common_filters` | Y | Y | — → D generic | — |
| `retrieval.source_ui.taxonomy_filters` | Y | Y | — → D generic | — |
| `retrieval.source_ui.upload` | Y | — | — | — |
| `retrieval.source_type_mapping` | Y | Y | — → D generic | — |
| `retrieval.purpose_labels` | Y | Y | — → D generic | — |
| Python client profile | Y `AIMClientProfile` | — `BaseClientProfile` | — `BaseClientProfile` | — |
| YAML client rules (`aim_content_rules` etc.) | Y | — | — | — |
| `document_processing` | Y `aviation_academic` | Y `cengage_publishing` | Y `generic_academic` | — |
| `document_processing.profile` used as code switch | — (string only) | — | — | D unused |
| Curriculum profile (digest pipeline) | D AIM if `client_id==aim` | D base (raises on enumerate) | D base (raises) | — |
| `structure_store` / `vector_store` | Y enabled | Y enabled | Y disabled, empty URLs | — |
| Pipeline models / LLM | Y Bedrock Sonnet 4.5 | Y Bedrock Haiku/Sonnet 3 | Y `mock` | — |
| `embedding.enabled` | Y true | Y true | Y false | — |
| `deduplication` extra block | — | Y | — | — |
| `auth.client_admins` / `users` in YAML | Y | Y | Y | D unused for CAS login |
| Secrets in YAML (store DSN / OpenSearch basic auth) | Y plaintext | Y plaintext | — empty | — |

**No other configured tenants found.** **FACT.**

### 4.2 Classification of configuration

#### COMMON CONFIGURATION

**FACT / INFERENCE from the three files plus code defaults:**

| Item | Evidence |
|---|---|
| Identity keys | All three: `client_id`, display name, `namespace`, `namespace_prefix`, `enabled` |
| `document_type` as a schema field | All three `metadata_schemas` |
| `visual_summary` optional string | All three |
| Source Library common filters | AIM + Cengage explicit; Academian via code default: `purpose`, `document_type`, `visibility`, `status`, `search` |
| Field Registry default projection | All tenants without overlay; Cengage and Academian entirely. Includes `course_name`, `block`, `day`, `chapter`, `module_name`, `learning_objective`, `lesson_name`, plus operational `document_type`, `purpose`, `visibility`, `status`, … |
| S3 storage shape | `provider: s3`, shared bucket name pattern, `base_prefix: DIS` (AIM/Cengage/Academian) |
| Ingestion flags | `dedup_enabled`, `quarantine_on_fail`, `schema_validation: true` on all three |
| Generic extractor fallback | `specialized_extractors.DEFAULT_DOC_RULES` when YAML rules empty |

#### TENANT-SPECIFIC CONFIGURATION

**FACT.**

| Item | AIM | Cengage | Academian |
|---|---|---|---|
| Schema field set | Academic: `course_name`, `topic`, `unit_type`, `block`, `module_name`, … | Publishing: ISBN, product, chapter_number, LO id/text, art, … | Academic, almost AIM minus knowledge-test types |
| `document_type` enums | Aviation/training types + `knowledge_test_report` | Textbook/manuscript/layout types | AIM-like without `knowledge_test_report` |
| Taxonomy filter keys | `course_name`, `block`, `day`, `topic` | `course_name`, `chapter`, `module`, `learning_objective` | (default) `course_name`, `module_name`, `lesson_name` |
| Upload fields | `document_type`, `chapter`, `module_name`, `learning_objective` | none | none |
| `metadata_framework` | `topic` → index, filter_options, retrieval (not cas_list) | none | none |
| Processing rules / regex / unit_type_map | Aviation calendar/block/day | Publishing part/chapter | Generic academic |
| Index name | `dis-content-dev-aim` | `dis-content-dev-cengage` | `dis-content-dev` (disabled) |
| JWT audience | `aim-dis-api` | `cengage-dis-api` | `aim-dis-api` (copy of AIM) |
| File size / types | 250 MB; no ppt/xls/json | 750 MB; ppt, xls, json | 250 MB; like AIM |
| Digest fan-out | enabled | absent (default off) | absent |
| Restricted content flag | false | true | false |

#### OPTIONAL CONFIGURATION

**FACT.** `metadata_framework` overlay, `source_ui` (including upload), `source_type_mapping`, `purpose_labels`, Python client profile, `aim_content_rules`, `deduplication` block, structure/vector stores, embedding, digest fan-out, curriculum_profile.

A tenant can omit all of these and still **load**. Product completeness is another question.

#### SYSTEM-DERIVED CONFIGURATION

**FACT.** `upload_ui_config.SYSTEM_DERIVED_FIELD_KEYS`: `file_sha256`, `content_hash`, `client_id`, `source_file_name`, `tenant_id`, `job_id`, `namespace`, `raw_storage_url`, `s3_key`, `status` — never offered as upload inputs.

**FACT.** Field Registry operational fields (`title`, `purpose`, `visibility`, `restricted`, `access_level`, `document_type`, `status`, `source_file_type`, calendar-join keys, AIM ingest flags) are defined in code `DEFAULT_FIELDS`, not copied into tenant YAML.

**FACT.** Compact source records, filter_options maps, and cas_list columns are computed from the registry, not from `metadata_schemas`.

### 4.3 Field-name overlap (actual YAML)

**FACT.** Schema **required/optional names**:

| Field | AIM | Cengage | Academian |
|---|---|---|---|
| `course_name` | required | — | required |
| `document_type` | required | required | required |
| `topic` | required | — | required |
| `unit_type` | required | — | required |
| `program`, `block`, `module_name`, `lesson_name`, `difficulty_level`, `keywords` | optional | — | optional |
| `visual_summary` | optional | optional | optional |
| `client_id`, `source_file_name`, `file_sha256`, `content_hash`, `access_level` | — | required | — |
| Publishing product/chapter/LO/art fields | — | optional (large set) | — |

**FACT.** AIM `source_ui.upload` includes `chapter` and `learning_objective`, which are **not** AIM `metadata_schemas` fields. They exist on `DEFAULT_REGISTRY` (and Cengage schema uses `chapter_number` / `learning_objective_id` with registry aliases).

**FACT.** AIM taxonomy includes `day`, which is **not** in AIM `metadata_schemas`. It is a Field Registry default field.

**FACT.** Cengage taxonomy keys `course_name`, `chapter`, `module`, `learning_objective` are presentation keys; schema uses `product_title`, `chapter_number`/`chapter_title`, `section_*`, `learning_objective_id`/`text`. Registry aliases bridge some of these.

**INFERENCE.** “Common metadata fields” exist at the **Field Registry / UI key** layer (`course_name`, `chapter`, `module`/`module_name`, `learning_objective`, `day`, `block`) more than as identical `metadata_schemas` documents. AIM and Academian share an academic schema family. Cengage is a different family. A single universal schema template would be wrong.

---

## 5. Manual Onboarding Workflow

**FACT** unless noted. Trace from a new client request to runtime.

| Step | What happens today | Class | Automate in Phase 8 scope? |
|---|---|---|---|
| 1. New client request | Business intake (not in repo) | OPERATIONAL | No |
| 2. CAS tenant identity | Platform admin: Organization Code (`slug`) + Client Name. Frontend sets `client_name` = `name`. API: `TenantCreateRequest`. | CONFIG + DATA (CAS DB) | Already automated |
| 3. CAS admin user | Username/password hashed, membership `admin`, license `max_users` | DATA + SECRET | Already automated |
| 4. DIS client id | `normalize_client_name(client_name)`; collision rejected vs other active Projects | CONFIG | Already automated |
| 5. Minimal DIS YAML | Background `provision_dis_client` writes 3-field YAML | CONFIG | Already automated (insufficient for metadata) |
| 6. Allowlist | Append `available_clients` | CONFIG | Already automated |
| 7. DIS reload | HTTP `/admin/config/reload` | OPERATIONAL | Already attempted |
| 8. Hand-author full client YAML | Copy AIM or Cengage; edit identity, schema, source_ui, processing, stores, models | CONFIG | **This is the 20-client problem** |
| 9. `metadata_schemas` | Per-client required/optional/enums | CONFIG | Candidate for template |
| 10. `metadata_framework` | Only if extra promote keys needed (AIM `topic`) | CONFIG | Candidate for optional overlay |
| 11. `source_ui` | Taxonomy labels, upload fields, purpose mapping | CONFIG | Candidate for template |
| 12. `document_processing` | Rules, keywords, unit_type_map — **processing logic**, not presentation | CONFIG | **Out of metadata-provisioning v1 unless decided otherwise** |
| 13. Client profile Python | Only if inference cannot live in YAML (`cid == "aim"`) | CODE | Do not auto-generate modules |
| 14. Curriculum profile Python | Only if digest pipeline enabled | CODE | Out of scope (not metadata config) |
| 15. Stores / index names | Shared RDS + OpenSearch; per-tenant index; credentials | INFRASTRUCTURE + SECRET + CONFIG | Must already exist; do not Terraform from Phase 8 |
| 16. Secrets / AWS / Bedrock | Env `AWS_*`, `DIS_BEDROCK_*`, optional `${VAR}` in YAML | SECRET | Do not put new secrets in generated YAML |
| 17. CAS OAuth (optional) | `TenantUpdateRequest` azure_* fields | SECRET + CONFIG | Unrelated to DIS metadata |
| 18. Commit YAML | Required for new hosts / CI image `COPY . .`; bind-mount makes it live on current host without commit | OPERATIONAL | Process, not code |
| 19. Deploy | GitHub Actions SSH: `git reset --hard` + `docker compose up -d --build` | INFRASTRUCTURE | Untracked YAML on host **survives** `reset --hard` but is **not** on a fresh clone |
| 20. Runtime | `TenantRegistry` load; Source Library `ui-config` | OPERATIONAL | Reload exists; no hot-reload of CAS processes except mtime on `dis_access.json` |

**FACT.** Updating a CAS tenant (`PUT` platform tenant) can change `client_name` and does **not** re-run DIS provisioning.

**FACT.** Frontend New Tenant form does not collect metadata schema, filters, or upload fields.

---

## 6. 20-Client Scale Analysis

**FACT.** Repository evidence is three YAML clients. No operational ticket volumes in-repo. Estimates below are **duplication measured across those three**, not staffing forecasts.

### 6.1 What repeats

| Repeated artifact | Evidence | Scale problem |
|---|---|---|
| Full client YAML boilerplate | Same top-level sections on all three (~15 sections) | 20 files × hundreds of lines |
| Academic `metadata_schemas` | AIM vs Academian: same required names; Academian omits knowledge-test enum values only | Copy-paste family |
| `source_ui.common_filters` | AIM and Cengage identical lists | Safe to template |
| Field Registry YAML | Cengage/Academian omit it and still work via `DEFAULT_REGISTRY` | **Do not** duplicate DEFAULT_FIELDS into 20 YAMLs |
| Taxonomy/upload | AIM vs Cengage different keys; Academian omitted | Needs families, not one blob |
| `document_processing` rules | Entirely tenant keyword lists | High duplication risk if copied from AIM into non-aviation clients |
| Python profiles | 1 of 3 tenants | 20 modules **not** required |
| Deploy/reload | Same for every client | Operational, already have reload |
| Store connection blocks | AIM and Cengage copy the same host pattern with different index names | Infra shared; index name per tenant |

### 6.2 Duplication (measurable)

**FACT.**

- AIM `metadata_schemas` vs Academian: required field **names** identical (`course_name`, `document_type`, `topic`, `unit_type`); optional names identical. Document-type enum: Academian is AIM minus `knowledge_test_report`. Unit-type enum: Academian minus `knowledge_test_item`.
- AIM vs Cengage schema: shared names = `document_type`, `visual_summary` only (plus conceptually “chapter” / “LO” under different names).
- `source_ui.common_filters`: 5/5 match AIM↔Cengage.
- Taxonomy keys: 1/4 match (`course_name` only) AIM↔Cengage.
- `metadata_framework`: 0/3 Cengage/Academian files; AIM adds one key.

**INFERENCE.** For ~20 **academic** clients, schema + source_ui + identity is the repetitive core. For a **publishing** client, Cengage is the outlier template. Mixing both into one generated schema would create false commonality.

**INFERENCE.** Repeating `DEFAULT_FIELDS` in YAML would be the worst scale mistake: the registry is already shared in code.

---

## 7. Common Configuration

**FACT.** Already shared without a YAML template:

1. `FieldRegistry.DEFAULT_FIELDS` / `DEFAULT_REGISTRY`
2. Generic `source_ui` fallback in `context_retrieval.py`
3. Generic `source_type_mapping` / `purpose_labels` fallbacks
4. `DocumentProcessingConfig` Pydantic defaults + `DEFAULT_DOC_RULES`
5. `BaseClientProfile`
6. `upload_ui_config` document_type derivation from schema enums
7. CAS tenant create + 3-field DIS YAML + allowlist + reload

**FACT.** There is **no** reusable tenant YAML template file.

**INFERENCE.** Smallest reusable abstraction (not implemented):

```
TEMPLATE FAMILY (academic | publishing | minimal)
  + TENANT OVERRIDES (id, name, namespace, enums, labels, extra schema fields, extra source_ui keys, optional metadata_framework overlay)
  → generated client YAML (client: wrapper)
  → existing TenantRegistry load
```

Do **not** introduce a master registry. Templates copy **sections**, they do not become runtime authorities.

---

## 8. Tenant-Specific Configuration

**FACT.** Must remain per client (cannot be identical for all 20):

- `tenant_id` / `client_id`, `display_name`, `namespace` / `namespace_prefix`
- `metadata_schemas` field lists and enum `values`
- Taxonomy **labels** and which keys are shown
- Upload field list and `control` / `order`
- `source_type_mapping` document types per purpose
- `document_processing` keywords/rules if ingest classification matters
- Client profile / `aim_content_rules` if path/visibility/calendar inference is custom
- Index names, JWT audience, file-type policy, restricted_content
- Store enablement and credentials (if any)

**INFERENCE.** Template + override is feasible **because** the loader already merges `metadata_framework` onto defaults and already falls back for empty `source_ui`. Overrides are naturally “only what differs”.

**DECISION REQUIRED.** How many families (academic vs publishing vs minimal-only). Evidence supports at least two content families plus identity-only.

---

## 9. metadata_schemas Provisioning

**FACT.** Configured as `metadata_schemas: { <client_id>: { required_fields, optional_fields } }`. Lookup: `TenantConfig.get_metadata_schema(client_id)` — if the map key is not the effective client id, schema is empty. Loader remaps `default` → `client_id` if needed.

**FACT.** `MetadataField`: `name`, `type`, `values`, `hint`, `applies_to`. Types are documented (`string | enum | list | number | date | boolean`) but **not** enum-validated at YAML load beyond Pydantic types.

**FACT.** Runtime validator `validate_metadata_against_schema` checks **document metadata instances**, not tenant YAML quality. Empty schema → no findings.

**FACT.** Upload UI reads schema `values` for select options. Presentation is not the schema.

Can schemas be copied / parameterized / overridden / generated / validated?

| Mechanism | Repository evidence |
|---|---|
| Copy from template | **INFERENCE:** AIM↔Academian already look like a copy. No template file. |
| Parameterize | No template engine for tenant YAML. |
| Override | No overlay for schemas (unlike Field Registry merge). Whole schema document per client. |
| Generate | Provisioner does not write schemas. |
| Validate YAML | Pydantic `MetadataSchema` only. No uniqueness check on field names. |

**Tenant-specific vs common:** academic required set vs publishing required set. `document_type` is common **as a field**, not as enum values.

**FACT.** Do not convert `metadata_schemas` into a frontend form schema (Phase 5 already keeps upload as `source_ui`). Do not merge with `source_ui`.

---

## 10. Field Registry Provisioning

**FACT.** A new tenant receives `metadata_framework.fields` today by:

1. Omitting `metadata_framework` → `DEFAULT_REGISTRY`, or
2. Supplying `metadata_framework.fields` in YAML → merge overlay (add/replace by key; default order first; `acs_codes` stripped; invalid `promote` values dropped).

**FACT.** AIM is the only overlay: `topic` type string, tier structural, promote `index`, `filter_options`, `retrieval`. Comment: AIM-only; do not copy into `DEFAULT_REGISTRY`; `cas_list` omitted (Phase 7 DECISION-002).

**FACT.** `metadata_framework` **can** be supplied as tenant configuration. `MetadataFrameworkConfig` is `extra="allow"` passthrough. `registry_from_config` is the consumer. This is already the intended overlay architecture — **not** “derive registry from metadata_schemas”.

**INFERENCE.** A reusable **overlay fragment** (e.g. academic `topic`) can be copied into generated YAML. Generating a full registry dump of `DEFAULT_FIELDS` into every tenant would freeze defaults into 20 files and fight later DEFAULT_FIELDS changes.

**FACT.** No repository evidence that schema→registry automatic merge is intended. Characterization tests pin overlay-on-defaults.

Do not modify `registry.py`. Do not create a master registry. **FACT / constraint.**

---

## 11. source_ui Provisioning

**FACT.** `RetrievalConfig.source_ui` is `Dict[str, Any]` (untyped). Keys observed: `common_filters`, `taxonomy_filters` (`key`, `label`, `type`), `upload.fields` (`key`, `label`, `control`, `order`).

**FACT.** Resolution order: `retrieval.source_ui` → `client_rules.source_ui` → generic defaults.

**FACT.** `build_upload_metadata_ui` resolves upload fields against `metadata_schemas` for enum options; skips system-derived keys; does not fail the request if a key is missing from schema (still emits the field). `document_type` can be derived from schema even without upload config.

**Common:** filter names `purpose`, `document_type`, `visibility`, `status`, `search`; taxonomy **types** `select`/`text`; upload **controls** `text`/`select`/`textarea`.

**Tenant-specific:** which taxonomy keys, labels, order, upload keys.

**INFERENCE.** Templates can reference field **names** (strings) without creating a second metadata authority, matching today’s YAML. Consistency with schema/registry would be a **validator**, not a merge.

**INFERENCE.** Safe to template-generate `source_ui` as YAML. Not safe to infer it from `metadata_schemas` (AIM upload keys are not a subset of AIM schema).

---

## 12. Client Profile Analysis

**FACT.**

| Tenant | Profile class | Selection |
|---|---|---|
| `aim` | `AIMClientProfile` | Hardcoded `if cid == "aim"` |
| any other | `BaseClientProfile` | Default |

**FACT.** Selection is **not** a YAML switch. `document_processing.profile` (`aviation_academic` / `cengage_publishing` / `generic_academic`) is **not** read by profile loader or extractors as a branch (no code references). Extractors use `document_type_rules` lists.

**FACT.** `AIMClientProfile` enriches metadata from `aim_content_rules` (defaults in Python merged with YAML): visibility, version priority, calendar mapping, restricted filenames, source types. Comment: content-team decisions belong in YAML, not new Python.

**FACT.** Cengage operates without a custom profile.

**FACT.** A new client can operate without a custom Python module.

**INFERENCE.** 20 new clients do **not** require 20 Python modules for **metadata configuration**. They require a module only if they need AIM-like path/calendar inference that cannot be expressed in existing YAML rule blocks.

**FACT.** Curriculum profiles (`services/digests/profiles`) are a **separate** code seam for digest enumeration. Base profile **raises** if digest pipeline runs. That is processing/digest enablement, not Source Library metadata UI. Out of Phase 8 implementation scope; noted as a later onboarding burden if digests are required.

Does this block automated metadata configuration? **INFERENCE: no.** Profile absence is already the default.

Do not redesign client profiles in Phase 8. **Constraint.**

---

## 13. Document Processing Dependencies

**FACT.** `document_processing` in YAML drives classification keywords, restricted types, structure regexes, `unit_type_map`. `specialized_extractors.infer_doc_type` uses those rules, then `DEFAULT_DOC_RULES` if the list is empty.

**FACT.** This is **client-specific processing logic**, adjacent to but not the same as metadata **presentation** (`source_ui`) or **domain schema** (`metadata_schemas`) or **index projection** (Field Registry).

**INFERENCE.** Onboarding a tenant that must classify aviation calendars vs publishing manuscripts requires `document_processing` edits. Onboarding a tenant that only needs Source Library filters/upload can use generic defaults initially.

**DECISION REQUIRED.** Whether Phase 8 implementation includes `document_processing` in generated YAML or only metadata_schemas + source_ui + optional metadata_framework.

**Recommendation (discovery):** keep document_processing **out** of the first metadata provisioning template except `profile: generic_academic` implicit default — to avoid copying AIM keyword lists onto unrelated clients.

---

## 14. Configuration Validation

**What exists (FACT):**

| Check | Where | What it catches |
|---|---|---|
| Pydantic `TenantConfig` | YAML load | Missing `tenant_id`/`display_name`/`namespace` (old shape); type errors on known fields; extra top-level keys **ignored** (Pydantic default) |
| Namespace collision | `TenantRegistry._register` | Two tenants, same `namespace`, different ids → `ValueError` (can fail **entire** registry load) |
| CAS client_name collision | `tenant_service.create_tenant` | Two active Projects, same normalized DIS id |
| Malformed `dis_access.json` | `_add_to_available_clients` | JSON parse error (no clobber) |
| Document metadata vs schema | `validate_metadata_against_schema` | Missing required / invalid enum **on ingested documents** |
| Promote target names | `registry._normalize_promote` | Unknown promote values **silently dropped** |
| Upload keys | `build_upload_metadata_ui` | System keys skipped; missing schema → still shown |
| Unknown client profile | `get_client_profile` | Silent `BaseClientProfile` |

**What is missing (FACT — no such validator in repo):**

- Tenant YAML lint dedicated to metadata
- Duplicate field names inside `metadata_schemas`
- `source_ui` key must exist in schema **or** registry
- Invalid `control` / taxonomy `type` at provision time (`SUPPORTED_CONTROLS` only applied at upload resolve)
- Invalid enum configuration (empty values with `type: enum`)
- Unknown profile / curriculum_profile at provision time
- Promotion target validity as a hard error
- Cross-file tenant_id vs filename vs schema map key vs `available_clients` consistency
- Overwrite protection on existing YAML

**INFERENCE.** Automated provisioning should validate **relationships** without merging authorities: same string keys, three documents. That is a checker, not a registry.

---

## 15. Configuration Consistency / Drift

**FACT.** Observed drift:

| Drift | Detail |
|---|---|
| AIM upload vs schema | `chapter`, `learning_objective` in upload; not in AIM schema |
| AIM taxonomy vs schema | `day` in taxonomy; not in AIM schema; present in DEFAULT_REGISTRY |
| AIM schema vs registry overlay | `topic` in schema **and** taxonomy **and** metadata_framework (aligned for that field) |
| Cengage UI vs schema names | UI `chapter` / `module` / `learning_objective` / `course_name` vs schema `chapter_number`, `section_*`, `learning_objective_*`, `product_title` |
| Academian vs AIM copy | `jwt_audience: aim-dis-api`; Azure container names `dis-raw-aim`; no `source_ui`; not in `available_clients` |
| AIM `storage.vector_index` vs `vector_store.index_name` | `dis-content-dev` vs `dis-content-dev-aim` |
| Provisioned YAML vs hand YAML | Old shape vs `client:` wrapper |

**INFERENCE.** Provisioning can run a **referential** check (`source_ui.key` ∈ schema names ∪ registry keys ∪ aliases) without merging. Today’s AIM upload would **fail** a naive “upload key must be in metadata_schemas” rule — so that rule would be a **behavior change**, not a restatement of current production AIM.

**DECISION REQUIRED.** Strict vs lenient reference rules (schema-only vs schema∪registry).

---

## 16. Idempotency

**FACT. Current:** same slug twice → YAML replaced with 3-field stub; allowlist unchanged if already present; reload attempted again.

**FACT.** Desired future properties in the brief (same input → same config; no duplicate fields/tenants; no accidental overwrite of overrides; explicit update; deterministic output) are **not** implemented.

**INFERENCE.** Safe default for implementation: **create-only** — if `{slug}.yaml` exists, refuse (or no-op without overwrite) and still ensure allowlist contains slug.

**DECISION REQUIRED.** Exact idempotency contract (see §17).

---

## 17. Create vs Update

**FACT.** Re-running provision for an existing file **overwrites** custom YAML. Tests do not cover “file already exists”.

**FACT.** CAS create is create-only for `Project.slug`. DIS YAML is not create-only.

Risks of CREATE+UPDATE without a spec:

- Destroy AIM/Cengage/Academian
- Wipe operator overrides
- Partial merge producing duplicate keys / invalid YAML

Risks of CREATE ONLY:

- Cannot fix a botched generate without a separate reconcile tool
- Operators hand-edit forever (status quo for metadata)

Risks of CREATE + EXPLICIT RECONCILE:

- Need a merge algorithm and a flag; more code; still no evidence of required algorithm

**DECISION REQUIRED.** Choose CREATE ONLY vs CREATE+UPDATE vs CREATE+EXPLICIT RECONCILE. Repository evidence does **not** support silently choosing UPDATE. Discovery recommendation: **CREATE ONLY** for implementation v1; updates remain hand edits or a later explicit command.

---

## 18. Versioning

**FACT.** Tenant YAML has no `schema_version`, `config_version`, `migration_version`, `generated-at`, or `template_version`. `metadata_framework` **may** carry extra keys (`version` appears in a Phase 0 unit-test fixture only, not in AIM YAML).

**FACT.** Other version strings exist for **documents/indexes/prompts**, not tenant config.

**INFERENCE.** For 20 generated files, a comment or `x-provisioning: { template, version }` extra key (ignored by `TenantConfig`) would aid rollback/debug. Not required for load. Do not implement in discovery.

**DECISION REQUIRED.** Whether generated YAML must stamp template version.

---

## 19. Environment Model

**FACT.** One YAML per client is shared across local / dev / prod. `test_config_env.py`: store **locations** must equal YAML literals; `DIS_STRUCTURE_STORE_URL` / `DIS_VECTOR_STORE_*` overrides were removed. DIS is “one shared corpus, not one per environment.”

**FACT.** Environment still controls: `DIS_MODEL_*` (client-wrapper path only), AWS/Bedrock credentials, `DIS_S3_BUCKET` / prefix / region (client-wrapper path only), `GlobalSettings.environment` used in processed prefix `processed/{namespace}/{environment}`.

**FACT.** `platform.yaml` `environment: development` is platform metadata, not per-tenant forks.

**INFERENCE.** Metadata provisioning should generate **one** tenant config, not env-specific copies. Store URLs should not be invented per env. Model pins stay env vars.

**DECISION REQUIRED.** Whether generated YAML uses `${VAR}` placeholders for any store fields if stores are enabled.

---

## 20. Secrets

**FACT.** AIM and Cengage client YAML currently contain plaintext structure-store DSNs and OpenSearch basic-auth passwords. Academian store fields are empty. Settings commentary says credentials must not be committed and `${VAR}` / `${VAR:-fallback}` expansion exists — **not** used for those AIM/Cengage secret fields.

**FACT.** Azure/GCP blocks reference secret **names** (`AZURE_STORAGE_KEY`, `GCP_SERVICE_ACCOUNT_JSON`), unused while `storage.provider` is forced to `s3`.

**FACT.** Provisioning does **not** write secrets. CAS tenant create writes a password **hash** in the CAS DB. Optional Microsoft OAuth secret is a CAS tenant update field, not DIS YAML.

**INFERENCE.** Generated metadata YAML must not add secrets. Leave stores disabled / URLs empty unless a future decision supplies placeholders. Do not copy AIM’s plaintext credential pattern into 20 new files.

This discovery document does not reproduce secret values.

---

## 21. Infrastructure Boundary

**FACT.** Phase 8 as specified is **metadata configuration provisioning**, not AWS account/S3/OpenSearch/Cognito/IAM/Terraform/DB/network provisioning.

**FACT.** Dependencies that must **already exist** if a tenant enables search/structure:

- S3 bucket (shared `content-ai-studio` in current YAML; overridable via `DIS_S3_BUCKET`)
- OpenSearch domain (shared endpoint in AIM/Cengage YAML); `auto_create_index: true` can create a **new index name**
- Postgres (shared RDS URL in AIM/Cengage YAML); `auto_create_schema: true` is schema-in-existing-DB, not a new instance

**FACT.** Metadata-only YAML (schemas, source_ui, registry overlay) loads with stores disabled. Source Library listing uses S3 source-index JSON paths, not a new OpenSearch domain.

**INFERENCE.** Generating metadata config does **not** require creating infrastructure. Enabling `vector_store` / `structure_store` on a new tenant **does** require existing shared stores plus a unique `index_name` (and operational approval). Keep that **out** of metadata template v1.

No Terraform files in this repository.

---

## 22. Deployment Model

**FACT.** Path today:

```
CAS create_tenant (DB commit)
  → background provision_dis_client
      → write YAML on host (compose bind mount)
      → patch dis_access.json
      → POST DIS /admin/config/reload
  → DIS TenantRegistry rebuild
  → tenant YAML visible to DIS on that host
```

**FACT.** `POST /admin/config/reload` exists; comment: “Restart is still safer after major changes.” No general hot-reload of CAS Python. `dis_access.json` is re-read on mtime.

**FACT.** Dev deploy (`.github/workflows/backend-dev-deploy.yml`): `git reset --hard origin/develop` then `docker compose up -d --build`. `dis_backend/Dockerfile` `COPY . .` bakes config into the image; compose **overrides** `/app/config` with the host directory.

**INFERENCE.** Untracked `{slug}.yaml` on an EC2 working tree remains after `git reset --hard` (untracked files are not removed) and remains visible via bind mount. It is **absent** on a new host/clone until committed. Image rebuild without bind mount would only include files present at `COPY` time.

**FACT.** Do not introduce hot reload. Reload endpoint already exists.

**DECISION REQUIRED.** Operational rule: must generated YAML be committed before the tenant is considered production-ready? Evidence says versioned YAML is the source of truth for hand-authored clients.

---

## 23. Solution Options

Evaluation against **this** repository only. Effort is relative (S/M/L/XL), not hours.

### OPTION A — Reusable YAML template + tenant overrides (files only)

| Criterion | Assessment |
|---|---|
| Effort | S–M |
| Operational complexity | Operators still copy/merge by hand |
| Safety | High if templates are new files and existing tenants untouched |
| Compatibility | Fits static YAML model |
| Version control | Excellent |
| Tenant isolation | One file per client |
| Rollback | Git revert |
| 20 clients | Reduces boilerplate; still manual generate |
| Architecture fit | Good |

**Gap:** no generation, no overwrite guard, no validation CLI. Incomplete alone.

### OPTION B — CLI that generates validated tenant YAML

| Criterion | Assessment |
|---|---|
| Effort | M |
| Operational complexity | Low if documented |
| Safety | High with create-only |
| Compatibility | Good |
| Version control | Generated files committed |
| Isolation / rollback | Same as A |
| 20 clients | Strong |
| Architecture fit | Good |

**Gap:** CAS tenant create would still write 3-field YAML unless CLI is wired to provisioner. Two entry points to keep in sync.

### OPTION C — DB-backed metadata configuration

| Criterion | Assessment |
|---|---|
| Effort | XL |
| Safety / fit | **Poor** — contradicts YAML-as-source-of-truth, env-cannot-repoint-stores, no DIS client DB row |
| Version control / rollback | Weaker unless dual-written |
| 20 clients | Solves scale the wrong way |

**Not recommended.** Unauthorized redesign.

### OPTION D — Admin UI for tenant configuration

| Criterion | Assessment |
|---|---|
| Effort | XL |
| Safety | Overwrite/UX risk |
| Fit | Frontend currently only sends name/slug/admin; no metadata editor |
| 20 clients | Nice later, not smallest |

**Not recommended** for Phase 8.

### OPTION E — Extend `dis_provisioning.py` only (no templates)

| Criterion | Assessment |
|---|---|
| Effort | S |
| Safety | Low if it keeps overwrite; Medium if create-only but still dumps one hardcoded schema |
| 20 clients | Fails if clients are not identical |
| Fit | Reuses trigger (CAS create) |

**Gap:** one baked-in schema cannot serve AIM-like and Cengage-like tenants.

### OPTION F — Extend existing provisioning **+** reusable metadata templates

| Criterion | Assessment |
|---|---|
| Effort | M |
| Operational complexity | One path: create tenant → generate from template name + overrides |
| Safety | High **if** create-only and templates do not rewrite AIM/Cengage/Academian |
| Compatibility | Reuses `provision_dis_client`, `TenantRegistry`, DEFAULT_REGISTRY overlay, source_ui dict |
| Version control | Generated YAML in `config/clients/` |
| Isolation | One file per slug |
| Rollback | Delete file + git; allowlist entry leftover is low risk |
| 20 clients | Fits |
| Architecture fit | Best of A+B+E; no new DB/UI/service |

**INFERENCE.** Option F is the smallest safe mechanism that matches “reuse existing provisioning” and “do not merge authorities.”

---

## 24. Recommended Architecture

**INFERENCE (recommendation, not an approved implementation decision).**

**Option F**, constrained:

1. **Reuse** `provision_dis_client` as the only runtime trigger (CAS tenant create). Optional later: same generator callable from a CLI for dry-run.
2. **Templates** as data (YAML fragments or Python dicts stored as **new** files under something like `dis_backend/config/provisioning_templates/` — path is illustrative, not created here):
   - `minimal` — identity + empty metadata (today’s behavior, but `client:` wrapper + create-only)
   - `academic` — AIM/Academian-like `metadata_schemas` + academic `source_ui` + optional `topic` registry overlay
   - `publishing` — Cengage-like schema/UI **structure** without Cengage secrets or store URLs
3. **Overrides** from a small input: slug, display_name, namespace, template id, optional extra fields/enums/labels.
4. **Emit** `client:` wrapper YAML so env expansion and S3 overrides match existing tenants.
5. **Do not** write `DEFAULT_FIELDS` into YAML. Omit `metadata_framework` unless the template adds keys beyond defaults (e.g. `topic`).
6. **Do not** generate Python profiles, curriculum profiles, Terraform, OpenSearch, or DB rows.
7. **Do not** enable structure/vector stores or copy credentials.
8. **Do not** merge schema ↔ registry ↔ source_ui.
9. **CREATE ONLY** if `{slug}.yaml` exists.
10. **Validate** with `TenantConfig` load + namespace uniqueness vs already-loaded tenants + optional referential checks (DECISION REQUIRED strictness).
11. **Apply to new tenants only.** Do not migrate AIM/Cengage/Academian.
12. Keep `available_clients` append + reload as they are (idempotent allowlist).

**FACT.** This stays config-generation, not a new metadata platform.

---

## 25. Example Tenant Provisioning Flow

Conceptual only. **Do not create this client.**

**Client A** (placeholder), academic family.

```
INPUT
  cas_slug: "client-a-org"
  display_name: "Client A"
  client_name: "Client A"          → normalize → "client_a"
  template: academic
  overrides:
    document_type.values: [syllabus, lesson_slide_deck, other]
    taxonomy_filters.labels.course_name: "Programme"
    namespace: "client_a_ns"

        ↓
TEMPLATE academic
  metadata_schemas.client_a (from academic fragment; key = client_id)
  retrieval.source_ui (common_filters + course_name/block/module taxonomy + upload document_type)
  metadata_framework.fields.topic (optional overlay; only if template says so)
  client: { client_id, client_name, namespace, enabled, default file types }
  document_processing: omitted (Pydantic generic_academic)
  stores: omitted / disabled defaults

        +
OVERRIDES applied by key (enums, labels, extra optional field names)

        ↓
VALIDATION
  TenantConfig parse
  file does not already exist
  namespace not owned by another tenant_id
  [DECISION] source_ui keys ⊆ schema ∪ registry ∪ aliases

        ↓
GENERATED FILE
  dis_backend/config/clients/client_a.yaml
  available_clients += "client_a"

        ↓
RELOAD DIS registry (best-effort)

        ↓
RUNTIME
  TenantRegistry["client_a"]
  DEFAULT_REGISTRY ∪ topic overlay
  Source Library ui-config from source_ui
  BaseClientProfile
  Stores disabled until a later, separate ops change
```

CAS `Project` is already committed before this runs (current code). Failure here does not roll back CAS. **FACT.**

---

## 26. Production Code Impact

| Question | Answer | Class |
|---|---|---|
| Config-only possible? | **No** for a generator; **yes** for the **output** (YAML). Existing hand-authored YAML can stay. | FACT / INFERENCE |
| Code changes required? | Yes, to `dis_provisioning` + tests + new template files | INFERENCE |
| New service? | No | INFERENCE |
| New database? | No | FACT (not needed) |
| New UI? | No (CAS create already triggers provision; template choice may later need a form field — not required if default template is `minimal` or `academic`) | INFERENCE |

**Exact files that would change for the recommended implementation (do not modify now):**

| File | Role |
|---|---|
| `app/services/dis_provisioning.py` | Generator, create-only, wrapper YAML, template selection |
| `tests/unit/test_dis_provisioning.py` | Generation, idempotency, no-overwrite, validation |
| **New** `dis_backend/config/provisioning_templates/*.yaml` (or equivalent under `app/`) | Academic / publishing / minimal fragments |
| **New** validator module (optional, e.g. `app/services/dis_tenant_config_validate.py` or `dis_backend/services/tenant_config_validate.py`) | Referential checks |
| `app/api/v1/routers/platform_tenants.py` | Only if template id is passed from API |
| `app/schemas/tenant.py` / frontend TenantsPage | Only if operators must choose a template |

**Must not change for this recommendation:** `registry.py` DEFAULT_FIELDS, existing `aim.yaml` / `cengage.yaml` / `academian.yaml`, CAS/DIS runtime consumers, OpenSearch, Terraform, PipelineState, client_profiles (unless a future tenant truly needs code).

---

## 27. Test Impact

Do **not** create these now. Future tests should cover:

- Tenant generation from each template
- Template rendering determinism
- Overrides replace/add fields without duplicating names
- Generated YAML loads as `TenantConfig` via `_client_raw_to_tenant`
- `metadata_schemas` map key equals `client_id`
- Field Registry overlay validity (`promote` ⊆ valid set; no `acs_codes`)
- `source_ui` references (per chosen strictness)
- Tenant isolation (namespace collision)
- Duplicate prevention (file exists → no overwrite)
- Idempotent allowlist
- Update behavior (explicit refuse)
- Reload failure does not raise
- `DIS_AVAILABLE_CLIENTS` warning path
- Environment: generated wrapper YAML still accepts `DIS_MODEL_*` / `DIS_S3_*`
- Rollback: deleting the file un-registers on next reload (load glob)
- No mutation of AIM/Cengage/Academian fixtures

---

## 28. Security / Safety

| Risk | Evidence | Notes |
|---|---|---|
| Tenant cross-contamination | Namespace in S3 prefix; OpenSearch `index_name` per tenant; CAS allowlist | New tenants must get unique namespace + index **if** stores enabled |
| Namespace collision | `_register` raises | Can take down DIS boot if a bad generate ships |
| Accidental overwrite | `_write_client_yaml` today | Highest risk for Phase 8 |
| Secrets | Existing YAML has plaintext store creds | Do not proliferate |
| Invalid configuration | Weak tenant-config validation | Partial UI/schema mismatch already exists in prod tenants |
| Partial provisioning | Best-effort try/except | CAS org without working DIS metadata |
| Deployment failure | Uncommitted YAML host-only | Fresh clone missing client |
| Rollback | Git for tracked YAML; allowlist append not auto-removed | Orphan allowlist entries are low severity |
| `DIS_AVAILABLE_CLIENTS` | Env hides file-based new clients | Operational footgun, already logged |
| Filename vs tenant_id mismatch | Glob loads all `*.yaml`; id comes from YAML body | Generator should use `{slug}.yaml` == `client_id` |

---

## 29. Existing Tenant Migration

**FACT.** AIM, Cengage, and Academian already load. Academian is incomplete vs AIM (no source_ui, not allowlisted) but that is an existing ops gap, not a Phase 8 prerequisite.

**INFERENCE.** Automation should apply **only to new tenants**. Migration of AIM/Cengage/Academian is **not necessary** for 20-client onboarding and would risk production metadata.

Do not migrate existing tenants. **Constraint.**

---

## 30. Backward Compatibility

**FACT.** `TenantRegistry` already loads mixed files: wrapper YAML and old-shape YAML. Empty `metadata_framework` / `source_ui` already have fallbacks. Unknown clients already get `BaseClientProfile`.

**INFERENCE.** A future generator can be introduced without changing AIM/Cengage/Academian files **if** it only writes **new** paths and create-only skips existing files.

**FACT.** Changing `DEFAULT_FIELDS` would still affect Cengage/Academian (they rely on defaults). Provisioning templates must not require a DEFAULT_FIELDS change.

---

## 31. Open Decisions

| ID | Decision | Why it is open |
|---|---|---|
| D1 | CREATE ONLY vs CREATE+UPDATE vs EXPLICIT RECONCILE | Current overwrite is unsafe; no merge algorithm exists |
| D2 | Default template for CAS create (`minimal` vs `academic`) | Frontend has no template field; 20 clients may not all be academic |
| D3 | Include `document_processing` in generated YAML? | Processing ≠ metadata; AIM rules are dangerous to copy |
| D4 | Include `topic` registry overlay in academic template? | AIM-specific Phase 7 decision; not in DEFAULT_REGISTRY |
| D5 | Referential validation strictness (schema vs schema∪registry) | AIM production would fail schema-only |
| D6 | Generated YAML shape: `client:` wrapper vs old three-field | Wrapper matches prod tenants and env overrides |
| D7 | Must generated YAML be committed for production? | Host bind-mount vs fresh clone |
| D8 | `${VAR}` placeholders for future store enablement | Secrets policy vs current AIM practice |
| D9 | Pass template id on `TenantCreateRequest` / UI | Code vs default-only |
| D10 | Stamp `x-provisioning` metadata on generated files | Versioning; TenantConfig ignores extras |
| D11 | Namespace default `{slug}` vs `{slug}_ns` | Provisioner uses slug; hand YAML uses `*_ns` |
| D12 | Academian allowlist / copy-paste JWT — out of Phase 8 but related ops debt | Not required to automate new tenants |

---

## 32. Explicit Non-Goals

**FACT (this discovery does not authorize):**

- Master metadata registry
- Automatic `metadata_schemas` → Field Registry merge (or reverse)
- DB-backed metadata redesign
- OpenSearch redesign
- TaxonomyRegistry
- Upload hard-fail
- PipelineState redesign
- New admin UI
- New runtime metadata service
- New public API (beyond possibly a field on existing tenant create)
- CAS redesign
- DIS pipeline/runtime redesign
- Client profile redesign
- Curriculum profile / digest enablement
- Terraform / AWS account / Cognito / IAM / networking
- Migrating AIM, Cengage, or Academian YAML
- Hot reload
- Changing `registry.py` DEFAULT_FIELDS
- Copying production secrets into templates

Objective: **automated configuration provisioning**, not metadata architecture redesign.

---

## 33. Evidence Index

| Topic | Path |
|---|---|
| Provisioning | `app/services/dis_provisioning.py`, `tests/unit/test_dis_provisioning.py` |
| CAS create | `app/api/v1/routers/platform_tenants.py`, `app/services/tenant_service.py`, `app/schemas/tenant.py` |
| Frontend create | `frontend/src/features/platform/pages/TenantsPage/TenantsPage.jsx` |
| Allowlist | `config/dis_access.json`, `app/core/dis_access.py`, `DIS_CLIENT_ACCESS_README.md` |
| Tenant load | `dis_backend/config/settings.py` (`TenantConfig`, `TenantRegistry`, `_client_raw_to_tenant`) |
| Client YAML | `dis_backend/config/clients/aim.yaml`, `cengage.yaml`, `academian.yaml` |
| Field Registry | `dis_backend/services/metadata_framework/registry.py` |
| source_ui / ui-config | `dis_backend/services/context_retrieval.py`, `dis_backend/services/upload_ui_config.py` |
| Schema validation (documents) | `dis_backend/services/metadata_schema_validate.py` |
| Client profiles | `dis_backend/services/client_profiles/__init__.py`, `aim.py`, `base.py` |
| Extractors | `dis_backend/services/specialized_extractors.py` |
| Curriculum profiles | `dis_backend/services/digests/profiles/` |
| Reload | `dis_backend/api/routers/admin.py` |
| Env/store rules | `dis_backend/tests/test_config_env.py` |
| Compose mounts | `docker-compose.yml` |
| DIS image | `dis_backend/Dockerfile` |
| Deploy | `.github/workflows/backend-dev-deploy.yml` |
| gitignore docs | `.gitignore` `docs/*` |

---

*End of Phase 8 discovery. No implementation. Wait for review and explicit approval.*
