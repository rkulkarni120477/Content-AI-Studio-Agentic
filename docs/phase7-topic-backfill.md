# Phase 7 — Compact Source-Index Topic Backfill (Operational)

**Status:** Documentation only. Do **not** run against production from CI.  
**Related:** `scripts/backfill_source_index_topic.py`  
**Approval:** DECISION-009 (`docs/phase7-topic-filter-approval.md`)

---

## Command

```bash
# Plan only (default)
python scripts/backfill_source_index_topic.py --client aim

# Apply
python scripts/backfill_source_index_topic.py --client aim --apply

# Re-copy even when compact topic already matches
python scripts/backfill_source_index_topic.py --client aim --apply --recount

# Rollback
python scripts/backfill_source_index_topic.py --client aim --apply --from-manifest <manifest.json>
```

## Environment

- Run with the same `ENVIRONMENT` the target DIS deployment uses (the source-index S3 path includes the environment name).
- Requires DIS S3 credentials / config as used by `ArtifactWriter` / `get_tenant_config`.
- Working directory: repository root (script adds `dis_backend/` to `sys.path`).

## Tenant scope

- `--client` defaults to `aim`.
- Only that client's compact source index is read/written.
- Do **not** run against Cengage unless a future decision authorizes it.

## Source of truth

| Item | Value |
|---|---|
| Reads | Compact record `payload_key` → studio `payload.json` → `metadata.topic` |
| Writes | Compact source-index record field `topic` on `source_index/source_list.json` |
| Does not modify | Studio payloads, content files, raw uploads, OpenSearch, DB schema |

## Idempotency

- Records whose compact `topic` already equals payload `metadata.topic` are skipped (unless `--recount`).
- Re-running after a clean apply reports `0 to update`.

## Dry-run

- Default is plan-only. `--apply` is required to write.
- Empty source index → refuse (likely wrong `ENVIRONMENT`).

## Expected impact

- Existing AIM compact records gain a top-level `topic` field for Source Library `filter_options` / `_source_matches` / retrieval reconstruction.
- New AIM ingests already write `topic` via registry-driven `compact_source_record` after AIM `metadata_framework` is deployed — backfill is for **existing** rows.

## Rollback / recovery

- Before apply, the script writes a manifest containing `index_before`.
- Restore with `--apply --from-manifest <path>` (client id must match).

## Verification steps

1. Plan: `python scripts/backfill_source_index_topic.py --client aim` — review update count.
2. Apply: add `--apply`.
3. Re-plan: expect `0 to update`.
4. Spot-check Source Library Topic filter for a known `metadata.topic` value.
5. Confirm Cengage Source Library UI still has no Topic filter.
