# Phase 7B — AIM Topic Backfill: `s3://` `payload_key` Read-Path Correction Approval Record

**Status:** Approval recording — implementation authorized; production execution is **not** authorized by this document.
**Approval-recording date:** 2026-08-31
**Discovery document:** `docs/phase7b-topic-backfill-discovery.md`
**Related operational doc:** `docs/phase7-topic-backfill.md`

---

## 1. Baseline

| Item | Value |
|---|---|
| **branch** | `feature/dis_metadata` |
| **HEAD at approval recording** | `e24101fb590fb1386ffa0348cdae0cb1e9e9acc8` |
| **Phase 7B discovery commit** | `e24101fb590fb1386ffa0348cdae0cb1e9e9acc8` |
| **Discovery document** | `docs/phase7b-topic-backfill-discovery.md` |
| **working_tree at start** | CLEAN |

---

## 2. Discovery Reference

This approval record is based on `docs/phase7b-topic-backfill-discovery.md`.

### Established facts (from discovery)

| Finding | Label |
|---|---|
| `ProcessedStorageAgent` stores `write_json()`'s `s3://` URL as `payload_key` | **FACT** |
| The immediate upload path stores a relative logical `payload_key` | **FACT** |
| `ArtifactWriter.read_json()` expects a relative logical key and cannot directly consume a stored `s3://` URI | **FACT** |
| The 459-record population has recoverable studio payloads | **PREVIOUSLY ESTABLISHED** |
| Source of truth remains `payload["metadata"]["topic"]` | **FACT** |
| The 24 genuinely missing artifacts are a separate remediation problem | **PREVIOUSLY ESTABLISHED** |

### Discovery recommendations adopted by this approval

| Recommendation | Disposition |
|---|---|
| Phase 7B should normalize `s3://` `payload_key` values in the existing backfill read path | **APPROVE** (DECISION-004) |
| Phase 7C should separately fix the future `ProcessedStorageAgent` write-path inconsistency | **APPROVE** (DECISION-016) |

---

## 3. Decision Table

| ID | Question (short) | Decision |
|---|---|---|
| **DECISION-001** | Phase 7B scope | **APPROVE** |
| **DECISION-002** | Source of truth | **APPROVE** |
| **DECISION-003** | Relative `payload_key` behavior | **APPROVE** |
| **DECISION-004** | `s3://` normalization | **APPROVE** |
| **DECISION-005** | Bucket validation | **APPROVE** |
| **DECISION-006** | Tenant/environment validation | **APPROVE** |
| **DECISION-007** | 24 missing artifacts | **APPROVE** |
| **DECISION-008** | Production scope (AIM only) | **APPROVE** |
| **DECISION-009** | Data mutation (`topic` only) | **APPROVE** |
| **DECISION-010** | Index identity | **APPROVE** |
| **DECISION-011** | Dry-run default | **APPROVE** |
| **DECISION-012** | `--apply` behavior | **APPROVE** |
| **DECISION-013** | Idempotency | **APPROVE** |
| **DECISION-014** | Manifest / rollback | **APPROVE** |
| **DECISION-015** | Concurrency / locking | **APPROVE** |
| **DECISION-016** | `ProcessedStorageAgent` | **APPROVE** (defer to Phase 7C) |
| **DECISION-017** | OpenSearch | **APPROVE** (no changes) |
| **DECISION-018** | Database / infrastructure | **APPROVE** (no changes) |
| **DECISION-019** | Field Registry | **APPROVE** (no changes) |
| **DECISION-020** | Source Library / retrieval | **APPROVE** (no architecture changes) |

---

### DECISION-001 — Phase 7B scope

**DECISION-001: APPROVE**

Correct the historical AIM Topic backfill read path so existing `s3://` `payload_key` values can be resolved to their referenced studio payload.

No new backfill architecture.

---

### DECISION-002 — Source of truth

**DECISION-002: APPROVE**

The only Topic source of truth remains:

```
payload["metadata"]["topic"]
```

No filename inference.
No `source_file_name` inference.
No chapter/module inference.
No extracted-text inference.

---

### DECISION-003 — Relative payload keys

**DECISION-003: APPROVE**

Existing relative `payload_key` values must continue to work exactly as before.

No behavior regression for existing relative keys.

---

### DECISION-004 — `s3://` normalization

**DECISION-004: APPROVE**

For historical records containing an `s3://` `payload_key`, parse and normalize the URI to the logical object key expected by `ArtifactWriter.read_json()`.

Use existing repository parsing/dedup/finalization conventions where applicable.

Do not invent a new storage abstraction.

---

### DECISION-005 — Bucket validation

**DECISION-005: APPROVE**

The normalized `s3://` URI must be validated against the resolved configured bucket before it is used.

A URI targeting another bucket must **NOT** be read.

---

### DECISION-006 — Tenant/environment validation

**DECISION-006: APPROVE**

The normalized object key must remain within the resolved AIM tenant/environment prefix.

A payload URI outside the resolved AIM prefix must **NOT** be read.

Do not allow the stored URI to bypass tenant/environment isolation.

---

### DECISION-007 — 24 missing artifacts

**DECISION-007: APPROVE**

The 24 genuinely missing-artifact records remain untouched.

Phase 7B must not reconstruct, re-ingest, infer, or otherwise repair them.

They remain a separate future remediation decision.

---

### DECISION-008 — Production scope

**DECISION-008: APPROVE**

Phase 7B is limited to AIM:

```
--client aim
```

No Cengage or Academian backfill.

---

### DECISION-009 — Data mutation

**DECISION-009: APPROVE**

Phase 7B may modify only:

```
rec["topic"]
```

on the existing AIM compact source-index record.

No other compact-index fields may be changed.

No studio payload may be modified.

---

### DECISION-010 — Index identity

**DECISION-010: APPROVE**

The existing source-index record identity remains unchanged.

No new `sources[]` records.
No duplicate records.
No new index file.

---

### DECISION-011 — Dry-run

**DECISION-011: APPROVE**

Dry-run remains the default.

Without `--apply`:

- S3 reads are permitted.
- No source index writes.
- No payload writes.
- No rollback manifest write.

---

### DECISION-012 — Apply

**DECISION-012: APPROVE**

Existing `--apply` behavior remains.

Apply must:

1. read the current index,
2. resolve payloads,
3. determine Topic from `payload.metadata.topic`,
4. re-read the fresh source index before mutation,
5. modify only `topic`,
6. write the same AIM `source_list.json`.

Do not redesign the apply mechanism.

---

### DECISION-013 — Idempotency

**DECISION-013: APPROVE**

Records whose compact `topic` already equals `payload.metadata.topic` remain skipped.

A clean second run should produce zero updates for successfully backfilled records.

---

### DECISION-014 — Manifest / rollback

**DECISION-014: APPROVE**

Existing manifest and rollback behavior remains unchanged.

Phase 7B must not introduce a new rollback architecture.

---

### DECISION-015 — Concurrency

**DECISION-015: APPROVE**

Do not introduce `source_index_lock` as part of Phase 7B.

The existing quiet-window operational recommendation remains.

Any locking improvement is deferred unless separately approved.

---

### DECISION-016 — ProcessedStorageAgent

**DECISION-016: APPROVE**

Do **NOT** fix `ProcessedStorageAgent` in Phase 7B.

Track the write-path inconsistency as **Phase 7C**.

---

### DECISION-017 — OpenSearch

**DECISION-017: APPROVE**

No OpenSearch changes.

---

### DECISION-018 — Database / infrastructure

**DECISION-018: APPROVE**

No DB, S3 architecture, AWS, Terraform, infrastructure, or storage redesign.

S3 is used only through the existing backfill read/write path.

---

### DECISION-019 — Field Registry

**DECISION-019: APPROVE**

No Field Registry changes.

Topic remains AIM tenant-overlay configuration only.

---

### DECISION-020 — Source Library / retrieval

**DECISION-020: APPROVE**

No Source Library filtering architecture changes.

No retrieval matching changes.

Phase 7B only enables historical compact records to receive the already-approved Topic value.

---

## 4. Implementation Boundary

**APPROVE**

### Production file (only)

| File | Change |
|---|---|
| `scripts/backfill_source_index_topic.py` | Add `s3://` `payload_key` read-path normalization and validation |

### Tests

| Location | Scope |
|---|---|
| Existing backfill test location **or** a narrowly scoped Phase 7B test file (e.g. `dis_backend/tests/test_backfill_source_index_topic_phase7b.py`) | Unit tests for normalization, validation, and backfill contract |

### Preferred implementation behavior

1. **Detect relative `payload_key`:** existing behavior unchanged.
2. **Detect `s3://` `payload_key`:**
   - parse bucket + key,
   - validate bucket,
   - validate tenant/environment prefix,
   - pass the validated logical key to `ArtifactWriter.read_json()`.
3. **If validation fails:**
   - skip safely,
   - do not read another tenant/bucket,
   - do not write Topic.
4. Read studio payload.
5. Read `payload["metadata"]["topic"]`.
6. Apply only the approved Topic mutation.

### Boundary rule

No other production files should be changed.

If implementation requires another production file, that must be treated as a **NEW decision** and must **NOT** be implemented under this approval.

---

## 5. Explicit Non-Goals

Do **NOT** implement or execute:

- `ProcessedStorageAgent` fix (Phase 7C)
- 24-record remediation
- filename Topic inference
- Topic vocabulary changes
- Field Registry redesign
- `DEFAULT_REGISTRY` Topic
- Cengage Topic
- Academian Topic
- OpenSearch changes
- DB migration
- frontend changes
- CAS changes
- DIS API changes
- upload UI changes
- metadata schema changes
- TaxonomyRegistry
- provisioning changes
- PipelineState changes
- locking redesign
- rollback redesign
- production backfill execution (operator `--apply` against production)

---

## 6. Approval Acceptance Criteria

| ID | Criterion | Status |
|---|---|---|
| **AC-1** | `s3://` `payload_key` records can be safely resolved | **APPROVED** |
| **AC-2** | Relative `payload_key` records continue to work | **APPROVED** |
| **AC-3** | Bucket is validated | **APPROVED** |
| **AC-4** | Tenant/environment prefix is validated | **APPROVED** |
| **AC-5** | No cross-tenant object can be read through the normalized URI | **APPROVED** |
| **AC-6** | Topic source remains `payload.metadata.topic` | **APPROVED** |
| **AC-7** | No Topic inference | **APPROVED** |
| **AC-8** | Only `rec["topic"]` is modified | **APPROVED** |
| **AC-9** | 24 missing-artifact records remain untouched | **APPROVED** |
| **AC-10** | No duplicate source records | **APPROVED** |
| **AC-11** | Dry-run remains non-mutating | **APPROVED** |
| **AC-12** | `--apply` remains explicit | **APPROVED** |
| **AC-13** | Existing manifest/rollback behavior remains | **APPROVED** |
| **AC-14** | Idempotency remains | **APPROVED** |
| **AC-15** | No Source Library/retrieval semantics change | **APPROVED** |
| **AC-16** | No Field Registry changes | **APPROVED** |
| **AC-17** | No OpenSearch/DB/infrastructure changes | **APPROVED** |
| **AC-18** | `ProcessedStorageAgent` fix is deferred to Phase 7C | **APPROVED** |
| **AC-19** | Production execution is **NOT** authorized by this approval | **APPROVED** |

---

## 7. Implementation Gate

**Phase 7B implementation is authorized** subject to this approval record.

### Authorized implementation scope

1. Read-path `s3://` `payload_key` normalization in `scripts/backfill_source_index_topic.py`
2. Bucket and tenant/environment prefix validation before `read_json()`
3. Narrowly scoped Phase 7B unit tests
4. Dry-run verification against AIM (`--client aim`, no `--apply`)

### Not authorized by this approval

- Production `--apply` execution
- Changes outside `scripts/backfill_source_index_topic.py` and Phase 7B tests
- Phase 7C `ProcessedStorageAgent` write-path fix
- 24-record remediation

---

## Decision Summary

| ID | Decision |
|---|---|
| DECISION-001 | **APPROVE** — historical AIM backfill read-path correction; no new architecture |
| DECISION-002 | **APPROVE** — `payload["metadata"]["topic"]` only; no inference |
| DECISION-003 | **APPROVE** — relative keys unchanged |
| DECISION-004 | **APPROVE** — normalize `s3://` URI to logical key; use existing conventions |
| DECISION-005 | **APPROVE** — validate bucket; reject foreign buckets |
| DECISION-006 | **APPROVE** — validate AIM tenant/environment prefix |
| DECISION-007 | **APPROVE** — 24 missing artifacts untouched |
| DECISION-008 | **APPROVE** — AIM only (`--client aim`) |
| DECISION-009 | **APPROVE** — mutate `rec["topic"]` only |
| DECISION-010 | **APPROVE** — no new/duplicate index records |
| DECISION-011 | **APPROVE** — dry-run default; reads OK, no writes |
| DECISION-012 | **APPROVE** — existing `--apply` flow unchanged |
| DECISION-013 | **APPROVE** — idempotency unchanged |
| DECISION-014 | **APPROVE** — manifest/rollback unchanged |
| DECISION-015 | **APPROVE** — no `source_index_lock` in Phase 7B |
| DECISION-016 | **APPROVE** — defer `ProcessedStorageAgent` to Phase 7C |
| DECISION-017 | **APPROVE** — no OpenSearch changes |
| DECISION-018 | **APPROVE** — no DB/infrastructure changes |
| DECISION-019 | **APPROVE** — no Field Registry changes |
| DECISION-020 | **APPROVE** — no Source Library/retrieval architecture changes |

---

**PHASE 7B APPROVAL COMPLETE — IMPLEMENTATION AUTHORIZED**
