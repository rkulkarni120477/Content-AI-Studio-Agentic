# Phase 7B — AIM Topic Backfill: `payload_key` Format Correction (Discovery)

**Status:** Discovery only — no implementation in this phase.
**Branch at discovery start:** `feature/dis_metadata`
**Starting commit:** `43c260ea9c9ebc4ba676639822d3ac4a57efd2e0` (`43c260e feat: add CAS tenant template selection`)
**Related commits:** `6120c46` (Phase 7 topic implementation), `43c260e` (Phase 8A CAS template selection)
**Related operational doc:** `docs/phase7-topic-backfill.md`
**Target script (future):** `scripts/backfill_source_index_topic.py`

---

## 1. Baseline

| Check | Result | Label |
|---|---|---|
| Branch | `feature/dis_metadata` | **FACT** |
| HEAD | `43c260ea9c9ebc4ba676639822d3ac4a57efd2e0` | **FACT** |
| Working tree | Clean (no staged/unstaged changes before discovery doc) | **FACT** |
| Phase 7 commit present | `6120c46 feat: enable AIM topic source library filtering` | **FACT** |
| Phase 8A commit present | `43c260e feat: add CAS tenant template selection` | **FACT** |

---

## 2. Objective

Phase 7B corrects **read-path** resolution in the existing AIM topic backfill so compact source-index records whose `payload_key` was stored as an `s3://` URI (instead of a relative studio payload key) can still be read via `ArtifactWriter.read_json()`.

**Approved source of truth (unchanged):**

```
compact record payload_key → studio payload.json → payload["metadata"]["topic"]
```

**Explicit non-sources for Topic:** filename, `source_file_name`, chapter, module, title, extracted text, or other metadata fields.

---

## 3. Existing 459-Record Finding

| Finding | Label |
|---|---|
| 459 AIM compact records store `payload_key` as `s3://content-ai-studio/...` | **PREVIOUSLY ESTABLISHED** (operator investigation; not revalidated live in this run) |
| All 459 have recoverable studio payloads with `payload["metadata"]["topic"]` | **PREVIOUSLY ESTABLISHED** |
| Current backfill skips them because `read_json(s3://...)` double-prefixes the key and S3 GET fails | **INFERENCE** (proven by code path analysis below) |
| 24 additional records are genuinely missing artifacts and are **out of scope** for Phase 7B | **PREVIOUSLY ESTABLISHED** |

This discovery run did **not** execute live S3 reads (dry-run backfill was not run; no production credentials assumed). Counts in §11 are labeled accordingly.

---

## 4. Repository Evidence

### 4.1 `ArtifactWriter` key semantics

**`read_json()`** (`dis_backend/services/artifacts.py`):

```python
def read_json(self, key: str) -> Any:
    storage_key = _join_prefix(self.base_prefix, key)
    return json.loads(s3.get_object(Bucket=self.processed_bucket, Key=storage_key)["Body"].read())
```

- **FACT:** `read_json()` expects a **logical relative key** (e.g. `processed/aim_ns/aim/{env}/{job_id}/studio_payload/payload.json`).
- **FACT:** `_join_prefix(self.base_prefix, key)` prepends tenant `storage.base_prefix` (AIM: `DIS`) unless the key already starts with that prefix.
- **FACT:** Passing `s3://content-ai-studio/DIS/processed/...` produces storage key `DIS/s3://content-ai-studio/DIS/processed/...` — an invalid S3 object key.

**`write_json()`** (`dis_backend/services/artifacts.py`):

```python
storage_key = _join_prefix(self.base_prefix, key)
s3.put_object(Bucket=self.processed_bucket, Key=storage_key, Body=data, **extra)
return f"s3://{self.processed_bucket}/{storage_key}"
```

- **FACT:** `write_json()` writes using the joined storage key and **returns a full `s3://{bucket}/{storage_key}` URI**.
- **FACT:** The return value is a URL for callers (job store, artifact_urls); it is **not** a logical key suitable for `read_json()`.

**Canonical relative studio payload key** (`dis_backend/services/source_library.py`):

```python
def studio_payload_key(tenant_cfg, client_id, job_id) -> str:
    return f"{source_base_prefix(tenant_cfg, client_id)}/{job_id}/studio_payload/payload.json"

def source_base_prefix(tenant_cfg, client_id) -> str:
    namespace = tenant_cfg.get_namespace(client_id)
    return f"processed/{namespace}/{get_settings().environment}"
```

For AIM: namespace resolves to `aim_ns/aim` →
`processed/aim_ns/aim/{ENVIRONMENT}/{job_id}/studio_payload/payload.json` (**FACT** from `aim.yaml` + `get_namespace()`).

Stored `s3://` form (when written via `write_json` return):
`s3://content-ai-studio/DIS/processed/aim_ns/aim/{ENVIRONMENT}/{job_id}/studio_payload/payload.json` (**INFERENCE** from `write_json` return format + AIM storage config).

### 4.2 Where compact `payload_key` is set

**FACT:** `compact_source_record()` stores the caller-supplied `payload_key` verbatim:

```python
"payload_key": payload_key,
```

(`dis_backend/services/source_library.py` line 281)

**FACT:** `write_source_content_and_index()` passes `payload_key` through to `compact_source_record()`:

```python
p_key = payload_key or studio_payload_key(tenant_cfg, client_id, job_id)
record = compact_source_record(payload, p_key, c_key, tenant_cfg=tenant_cfg)
```

### 4.3 Production write paths that populate `payload_key`

| Path | File | `payload_key` passed | Label |
|---|---|---|---|
| **A. Immediate upload** | `dis_backend/api/routers/ingestion.py` `_write_source_library_payload()` | Relative: `processed/{namespace}/{env}/{job_id}/studio_payload/payload.json` | **FACT** |
| **B. Pipeline refresh** | `dis_backend/services/agents/processed_storage_agent.py` | `urls['studio_payload']` = return value of `write_json()` → `s3://...` | **FACT** |

**FACT:** Only these two production call sites pass `payload_key=` to `write_source_content_and_index()` (repository grep).

**FACT:** `repair_source_index.py` uses `studio_payload_key()` (relative) — repair path, not routine ingestion.

**FACT:** `content_key` is always the relative `source_content_key(...)` regardless of path; only `payload_key` diverges.

### 4.4 Retrieval does not use compact `payload_key`

**FACT:** `ContextRetrievalService._load_payload_by_job()` reconstructs the studio path from `job_id`:

```python
prefix = f"{self._base_prefix(client_id)}/{job_id}/studio_payload/payload.json"
return self.writer.read_json(prefix)
```

Retrieval is unaffected by stored `payload_key` format (**FACT**). Phase 7B backfill read-path correction does not change retrieval semantics.

---

## 5. `payload_key` Write-Path Comparison

| Aspect | Immediate upload (A) | ProcessedStorageAgent refresh (B) |
|---|---|---|
| When | Upload-time placeholder before/during pipeline | After pipeline classification completes |
| Studio write | `ArtifactWriter.write_json(relative_key, payload)` | `ctx.writer.write_json(f'{prefix}/studio_payload/payload.json', ...)` |
| Value stored in compact index | Relative logical key | `s3://{processed_bucket}/{storage_key}` |
| Compatible with `read_json()` | Yes | No (without normalization) |
| Records affected | Uploads whose compact row was **not** overwritten by B | Records refreshed when pipeline completes processed_storage |

**INFERENCE:** Records that receive pipeline refresh (path B) overwrite the immediate-upload placeholder (path A). Large files deferred to background extraction always go through B. Files whose pipeline refresh runs will have `s3://` `payload_key`; files that never get B may retain relative keys.

---

## 6. Root Cause

**FACT + INFERENCE (high confidence):**

1. `ArtifactWriter.write_json()` returns an `s3://` URI.
2. `ProcessedStorageAgent` passes that URI as `payload_key` into `write_source_content_and_index()`.
3. `compact_source_record()` persists it unchanged.
4. `backfill_source_index_topic.py` calls `writer.read_json(payload_key)` without normalization.
5. `read_json()` treats the URI as a logical key and prepends `base_prefix`, producing an invalid S3 key → exception → record listed as `unreadable payload`.

**FACT:** The immediate upload path (`ingestion.py`) passes the relative key explicitly and does not exhibit this bug on write.

This is the definitive source of the relative-key vs `s3://` URI split (**INFERENCE**, supported by the only two production write paths).

---

## 7. Normalization Options

### Candidate A — Strip known `s3://{bucket}/` prefix

| Criterion | Assessment |
|---|---|
| Correctness | Works when bucket matches `processed_bucket` |
| Tenant isolation | Must validate path segment after strip |
| Environment isolation | Must validate `{ENVIRONMENT}` in path |
| Future compatibility | Same pattern as historical records |
| Wrong-object risk | Medium if bucket name is wrong but path coincidentally exists |
| Complexity | Low |

### Candidate B — Parse `s3://` URI; extract object key after bucket

| Criterion | Assessment |
|---|---|
| Correctness | Extracts full storage key (`DIS/processed/...`); `read_json` accepts via `_join_prefix` idempotence |
| Tenant isolation | Requires post-parse path validation |
| Environment isolation | Requires post-parse path validation |
| Compatibility | Matches existing dedup/finalize pattern |
| Wrong-object risk | Lower if bucket is validated |
| Complexity | Low |

**Existing repository precedent** (`deduplication_agent.py`, `finalize_agent.py`):

```python
if key.startswith("s3://"):
    key = "/".join(key.split("/", 3)[3:])
```

Optional strip of `base_prefix` to logical form (same files).

### Candidate C — Ignore stored key; reconstruct via `studio_payload_key(job_id)`

| Criterion | Assessment |
|---|---|
| Correctness | High when path conventions are stable |
| Tenant isolation | Strong (derived from tenant config + `--client`) |
| Environment isolation | Strong (uses `get_settings().environment`) |
| Compatibility | Works for standard layout; ignores corrupt stored URI |
| Wrong-object risk | Low for standard records; fails if non-standard path was ever used |
| Complexity | Low |

### Candidate D — Normalize stored key first; fallback to `studio_payload_key()`

| Criterion | Assessment |
|---|---|
| Correctness | Highest resilience |
| Risk | Fallback could mask key mismatches (should log) |
| Complexity | Medium |

---

## 8. Recommended Normalization (Phase 7B Implementation)

**RECOMMENDATION:** **Candidate B + scope validation**, with optional **Candidate D fallback** only when normalized read fails and stored key was `s3://` (log mismatch).

### Proposed algorithm (inside `scripts/backfill_source_index_topic.py` only)

1. Let `raw = rec.get("payload_key")`.
2. If `raw.startswith("s3://")`:
   - Parse bucket and object key (`"/".join(raw.split("/", 3)[3:])`) — **reuse dedup/finalize convention**.
   - **Validate** bucket equals `tenant_cfg.storage.processed_bucket` (reject/skip on mismatch).
   - If object key starts with `{base_prefix}/`, strip to logical key (optional but aligns with immediate-upload records).
   - **Validate** logical key starts with `source_base_prefix(tenant_cfg, client_id) + "/"` (tenant + environment scope).
3. Else: use `raw` as logical key (relative path — existing behavior).
4. `payload = writer.read_json(normalized_key)`.
5. Optional: if step 4 fails and original was `s3://`, try `studio_payload_key(tenant_cfg, client_id, job_id)` once; if keys differ, count as `key_mismatch` diagnostic.

**Why not Candidate A alone:** Stripping a hard-coded bucket string is brittle across env overrides (`DIS_PROCESSED_BUCKET`). Parsing URI + bucket validation is safer.

**Why not Candidate C alone:** Stored URI carries ground truth for the object that was written; normalization preserves that while fixing format. `studio_payload_key` is the right fallback/validation reference, not the primary resolver.

**AIM-specific vs generic:** The normalization logic is **generic** (any tenant with `s3://` stored keys). Phase 7B **execution** remains AIM-scoped via `--client aim` (**RECOMMENDATION**).

**Does not change:** stored `payload_key` in the compact index during backfill (only `topic` is written on apply).

---

## 9. Tenant / Environment Isolation

### Scope controls (existing)

| Control | Mechanism | Label |
|---|---|---|
| Client | `--client` → `get_tenant_config(client_id)` | **FACT** |
| Index | `read_source_index(tenant_cfg, client_id)` — one client index | **FACT** |
| Environment | `source_base_prefix` includes `get_settings().environment` | **FACT** |
| S3 bucket | `ArtifactWriter.processed_bucket` from tenant config | **FACT** |

### Bypass risk from `s3://` parsing

**INFERENCE:** Without path validation, a compact record containing
`s3://content-ai-studio/DIS/processed/cengage_ns/cengage/production/...`
could be read while running `--client aim`, because parsing alone does not enforce tenant prefix.

**RECOMMENDATION:** After normalization, require:

```text
logical_key.startswith(source_base_prefix(tenant_cfg, client_id) + "/")
```

and require `.../studio_payload/payload.json` suffix. This prevents cross-tenant/cross-environment reads even if a corrupt URI appears in the AIM index.

**FACT:** `ContextRetrievalService` already reconstructs paths from `job_id` + tenant prefix (no `payload_key` trust).

| Tenant | Phase 7B impact |
|---|---|
| AIM | In scope (`--client aim`) |
| Cengage | Out of scope; script default is `aim` |
| Academian | Out of scope |

---

## 10. Discovery Question Answers (C.1–C.20)

| # | Question | Answer | Label |
|---|---|---|---|
| 1 | How does `read_json()` resolve keys? | `_join_prefix(base_prefix, key)` then GET `processed_bucket` | **FACT** |
| 2 | How does `write_json()` return S3 value? | `s3://{processed_bucket}/{storage_key}` after `_join_prefix` | **FACT** |
| 3 | Where does compact `payload_key` come from? | Caller of `write_source_content_and_index` → `compact_source_record` | **FACT** |
| 4 | Production write paths? | `ingestion._write_source_library_payload` (relative); `ProcessedStorageAgent` (`s3://`) | **FACT** |
| 5 | Path A vs B? | See §5 | **FACT** |
| 6 | Where does divergence originate? | `ProcessedStorageAgent` passes `write_json()` return as `payload_key` | **FACT** |
| 7 | Safe normalization? | Parse URI → object key; validate bucket + tenant prefix; `studio_payload_key` for cross-check | **RECOMMENDATION** |
| 8 | Safest strategy? | Candidate B + scope validation (§8) | **RECOMMENDATION** |
| 9 | AIM-specific or generic? | Logic generic; run scoped to AIM | **RECOMMENDATION** |
| 10 | Entirely inside backfill script? | Yes | **RECOMMENDATION** |
| 11 | Changes source of truth? | No — still `payload.metadata.topic` | **FACT** |
| 12 | Changes compact index schema? | No — only populates existing `topic` field | **FACT** |
| 13 | Changes Topic matching semantics? | No | **FACT** |
| 14 | Changes Source Library filtering? | No architecture change; enables backfill to complete | **FACT** |
| 15 | Changes retrieval gating? | No (`_load_payload_by_job` ignores `payload_key`) | **FACT** |
| 16 | OpenSearch changes? | No | **FACT** |
| 17 | DB migration? | No | **FACT** |
| 18 | `metadata_schema` changes? | No | **FACT** |
| 19 | Field Registry changes? | No | **FACT** |
| 20 | Frontend/API changes? | No | **FACT** |

---

## 11. 459-Record Data Verification

**NOT VERIFIED in this discovery run** (no live S3 access executed).

### Expected formats (from code + prior investigation)

| Field | Expected value |
|---|---|
| Stored `payload_key` | `s3://content-ai-studio/DIS/processed/aim_ns/aim/{ENVIRONMENT}/{job_id}/studio_payload/payload.json` |
| Canonical relative key | `processed/aim_ns/aim/{ENVIRONMENT}/{job_id}/studio_payload/payload.json` |
| Same S3 object? | Yes, when normalization strips bucket and optional `DIS/` prefix correctly (**INFERENCE**) |

### Count table

| Metric | Count | Label |
|---|---|---|
| 1. Total `s3://` URI records | 459 | **PREVIOUSLY ESTABLISHED** |
| 2. Readable after normalization | 459 | **PREVIOUSLY ESTABLISHED** |
| 3. Unreadable after normalization | 0 (among the 459) | **PREVIOUSLY ESTABLISHED** |
| 4. Payloads with non-empty `metadata.topic` | 459 | **PREVIOUSLY ESTABLISHED** |
| 5. Payloads with empty/null topic | 0 (among the 459) | **PREVIOUSLY ESTABLISHED** |
| 6. Non-string topic | Unknown | **NOT VERIFIED** |
| 7. Key mismatches (stored vs `studio_payload_key`) | 0 expected | **INFERENCE** |
| 8. Unexpected cases | Unknown | **NOT VERIFIED** |

### Operator revalidation command (read-only plan)

```bash
python scripts/backfill_source_index_topic.py --client aim
```

After Phase 7B implementation, expect `unreadable payload` to drop by ~459 and `to update` to rise accordingly. The 24 missing-artifact records should remain in `unreadable` / `missing payload_key`.

---

## 12. 24-Record Boundary

**PREVIOUSLY ESTABLISHED:** 24 records lack studio payloads (deleted/missing artifacts).

Phase 7B correction **must not**:

- Attempt repair or reconstruction
- Use `dis.dis_content_units` or RDS
- Use raw files or re-ingest
- Infer Topic from any non-payload source
- Modify those 24 records

They remain a **separate future remediation** item (out of Phase 7B scope).

---

## 13. Backfill Safety Analysis

Reviewed: `scripts/backfill_source_index_topic.py`

| Mechanism | Behavior | Changed by 7B? |
|---|---|---|
| Default mode | Dry-run (plan only) | No |
| `--apply` | Writes manifest, re-reads index, sets `topic` only, writes index | No |
| Fresh index re-read | `fresh = read_source_index()` before write | No |
| Manifest / rollback | `index_before` snapshot; `--from-manifest` restore | No |
| Idempotency | Skips when compact `topic` matches payload | No |
| Write scope | Only `rec["topic"]` for planned `job_id`s | No |
| `source_index_lock` | **Not used** by backfill script (unlike `upsert_source_record`) | No change proposed |
| Concurrency | Single-process operator script | No change proposed |

**RECOMMENDATION:** Phase 7B does **not** require new locking, CLI flags, or rollback architecture. Normalization is read-path only inside the existing loop.

**FACT:** Backfill does not modify `payload_key`, studio payloads, `content_key`, or other compact fields.

---

## 14. Test Plan (Future Implementation — Do Not Create Now)

### Preserve existing tests

| File | Relevance |
|---|---|
| `dis_backend/tests/test_metadata_framework_phase7_topic_filter.py` | Phase 7 registry, filtering, retrieval, `_topic_from_payload` contract |
| `dis_backend/tests/test_metadata_framework_phase1_characterization.py` | Compact record scaffold includes `payload_key` |
| `dis_backend/tests/test_metadata_framework_phase1_tenant_wiring.py` | Tenant-specific compact promotion |

### Minimum new tests (suggested)

**Location:** `dis_backend/tests/test_backfill_source_index_topic_phase7b.py`

| Test name | Covers |
|---|---|
| `test_resolve_payload_key_relative_unchanged` | Relative key passed through |
| `test_resolve_payload_key_s3_uri_normalizes` | `s3://bucket/DIS/processed/...` → logical key |
| `test_resolve_payload_key_s3_reads_same_object` | Mock `read_json` receives key that matches `studio_payload_key` target |
| `test_resolve_payload_key_missing_still_skipped` | Normalization failure → skip, no write |
| `test_resolve_payload_key_rejects_foreign_tenant_path` | `cengage_ns` path rejected for `aim` |
| `test_resolve_payload_key_rejects_wrong_bucket` | Foreign bucket URI rejected |
| `test_backfill_topic_from_payload_metadata_only` | No inference from filename/title |
| `test_backfill_idempotent_with_s3_payload_key` | Second plan pass: 0 updates |
| `test_backfill_apply_only_touches_topic_field` | Mock index write: only `topic` changes |
| `test_backfill_no_duplicate_source_records` | Record count unchanged after apply |

Use mocked `ArtifactWriter` / `read_source_index` (no S3) following existing Phase 7 test patterns.

---

## 15. Phase 7B Implementation Boundary

### In scope (smallest change)

| File | Change |
|---|---|
| `scripts/backfill_source_index_topic.py` | Add `_normalize_payload_key(...)` (or inline equivalent); use before `read_json` |
| `dis_backend/tests/test_backfill_source_index_topic_phase7b.py` | New tests (above) |

### Explicitly out of scope

| Area | Reason |
|---|---|
| `dis_backend/services/context_retrieval.py` | Retrieval already reconstructs path |
| `registry.py`, `adapters.py`, `aim.yaml` | No Topic semantics change |
| `metadata_schemas`, frontend, CAS, DIS API | No contract change |
| OpenSearch, DB, infrastructure | Not required |
| `ProcessedStorageAgent` | Phase 7C (write-path) |
| Compact index `payload_key` rewrite | Not required for topic backfill |

**RECOMMENDATION:** If implementation cannot be confined to the backfill script + tests, **stop and escalate** — no production file changes are proven necessary for the 459-record correction.

---

## 16. Phase 7C — Future Write-Path Issue

| Question | Answer | Label |
|---|---|---|
| Is ProcessedStorageAgent definitively the split source? | Yes — only path storing `write_json()` return as `payload_key` | **FACT** |
| Which records affected? | Records whose compact row was last written by pipeline `processed_storage` refresh | **INFERENCE** |
| Safe to fix? | Yes — pass relative key: `f"{prefix}/studio_payload/payload.json"` (mirror `ingestion.py`) | **RECOMMENDATION** |
| Required for Phase 7B? | No — historical backfill can normalize on read | **RECOMMENDATION** |
| Separate phase? | Yes — **Phase 7C** = write-path normalization in `ProcessedStorageAgent` | **RECOMMENDATION** |

**FACT:** `urls['studio_payload']` must remain the `s3://` URL for `artifact_urls` / job store; only the argument to `write_source_content_and_index(payload_key=...)` should be the relative key.

---

## 17. Non-Goals

- Implement Phase 7B fix
- Run `--apply` or any S3 write
- Modify `source_list.json`, studio payloads, DB, OpenSearch
- Repair 24 missing-artifact records
- Change Phase 7 Topic architecture or Field Registry design
- Promote Topic globally or alter Cengage/Academian behavior
- Fix `ProcessedStorageAgent` in this phase
- Add locking / new rollback / unrelated safety refactors

---

## 18. Acceptance Criteria

| ID | Criterion | Status |
|---|---|---|
| AC-1 | 459 records recoverable without changing source of truth | **PASS** (with read-path normalization) |
| AC-2 | Source of truth remains `payload["metadata"]["topic"]` | **PASS** |
| AC-3 | Correction only fixes payload-key resolution | **PASS** |
| AC-4 | Relative payload keys continue to work | **PASS** |
| AC-5 | `s3://` keys safely resolve to same object | **PASS** (with B + validation) |
| AC-6 | 24 missing records untouched | **PASS** |
| AC-7 | No Topic inference introduced | **PASS** |
| AC-8 | No Field Registry changes | **PASS** |
| AC-9 | No Source Library filter architecture changes | **PASS** |
| AC-10 | No retrieval semantics change | **PASS** |
| AC-11 | No OpenSearch/DB changes | **PASS** |
| AC-12 | AIM tenant isolation intact | **PASS** (with path validation) |
| AC-13 | Environment isolation intact | **PASS** (with `source_base_prefix` validation) |
| AC-14 | Backfill dry-run by default | **PASS** |
| AC-15 | Existing `--apply`/manifest/rollback intact | **PASS** |
| AC-16 | ProcessedStorageAgent issue = Phase 7C | **PASS** |
| AC-17 | Smallest implementation boundary defined | **PASS** |

---

## 19. Final Verdict

**PHASE 7B DISCOVERY COMPLETE — AWAITING APPROVAL**

Phase 7B should add **read-path `payload_key` normalization** inside `scripts/backfill_source_index_topic.py` using the existing dedup/finalize `s3://` parsing convention, plus **bucket and tenant/environment path validation**. The approved Topic source of truth, compact schema, retrieval, and registry remain unchanged. The production write-path bug in `ProcessedStorageAgent` is documented as **Phase 7C** and is not required to unblock the 459-record historical backfill.
