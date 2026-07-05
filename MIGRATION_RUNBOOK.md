# Database Migration Runbook

Operational procedure for every schema phase of the prompt-consolidation
initiative (see `PROMPT_CONSOLIDATION_PLAN.md`). Follow it in order; do not
skip the backup steps even for "additive-only" migrations.

## Environment map

| Environment | Where | How to reach |
|---|---|---|
| Production | AWS RDS `atlas-db…ap-south-1` / `promptops_db` | `DATABASE_URL` in `.env` |
| Rehearsal | Local Docker `cas-rehearsal-db` (postgres:17, port 55432) | `scripts/rehearsal_db.sh url` |

Alembic owns the schema as of the `000100000001` baseline revision.
`init_db()` no longer executes DDL unless `DB_AUTO_DDL=true` (legacy escape
hatch — do not enable in production).

## Before ANY schema migration (prod)

1. **Manual RDS snapshot** (no automation exists in-repo; the deploy pipeline
   backs up only app files + `.env`):
   AWS console → RDS → Databases → *atlas-db* → Actions → **Take snapshot**,
   name it `pre-<phase>-YYYYMMDD`. Wait for `available`.
   *(No AWS CLI on the dev box — this is a console step. Point-in-time
   recovery is also enabled on RDS as a second net.)*
2. **Logical dump** (fast — DB is ~16 MB):
   ```bash
   scripts/rehearsal_db.sh dump     # full -Fc dump + plain-SQL pl_* dump → backups/
   ```
   Copy the newest files in `backups/` somewhere durable (they are
   git-ignored — never commit; they contain prod data).
3. **Rehearse first**:
   ```bash
   scripts/rehearsal_db.sh reset                      # fresh prod clone
   DATABASE_URL=$(scripts/rehearsal_db.sh url) .venv/bin/alembic upgrade head
   # then: upgrade → downgrade → upgrade round-trip for the new revision
   DATABASE_URL=$(scripts/rehearsal_db.sh url) .venv/bin/pytest tests/ -q
   ```
   Only proceed to prod when the round-trip is clean and the full test suite
   (incl. `tests/characterization/`) passes against the rehearsal DB schema.
4. **Quiet window**: unique-index / constraint migrations take
   `ACCESS EXCLUSIVE` locks that queue behind the app's connection pool —
   run during low traffic or quiesce the app.

## Applying to prod

The deploy pipeline (`dev-fastapi-deploy.yml`) runs
`docker-compose run --rm api alembic upgrade head` before starting
containers — merging a migration to the deployed branch applies it on the
next deploy. For a manual apply:
```bash
DATABASE_URL=<prod url> alembic upgrade head
```

## Restore procedures (rehearsed 2026-07-05)

**Logical restore** (drill result: restore of full prod dump ≈ 2 s):
```bash
pg_restore --no-owner --no-privileges -d "<target-db-url>" backups/promptops_db_full_<stamp>.dump
```
For a partial recovery (e.g. only `pl_*` rows) use the plain-SQL
`backups/pl_tables_pre_migration_<stamp>.sql`.

**RDS snapshot restore** (the guaranteed path for a corrupted prod DB):
console → RDS → Snapshots → select → **Restore snapshot** (creates a new
instance; repoint `DATABASE_URL` in the EC2 `.env`, redeploy). PITR:
"Restore to point in time" on the instance.
*Status: not yet executed end-to-end (console access required) — the logical
dump/restore path IS drilled and timed. Treat the first real snapshot
restore as a scheduled exercise before Phase 6 (the destructive phase).*

## Verification after any migration

```bash
scripts/rehearsal_db.sh verify        # row-parity spot check (rehearsal)
alembic current                       # revision matches head
pytest tests/ -q                      # 147+ tests green, incl. characterization
curl -s localhost:8000/api/v1/health  # {"status":"ok","database":"connected"}
```

## Phase-specific gates

- **Phase 2/6 (pl_* migration + drop):** both dumps in `backups/` MUST exist
  and be copied off-box before `DROP TABLE`. Row-parity query (126 rows)
  must pass after the carry-over, per the plan.
- **Phase 2 prod apply** (same quiet window as the Phase 3 `alembic upgrade
  head`, immediately after it): take a **fresh** `pl_*` dump
  (`scripts/rehearsal_db.sh dump`), then
  `python scripts/migrate_pl_to_native_prompts.py --database-url <prod url>
  --dry-run --allow-non-local`, review the row-for-row output, then re-run
  with `--apply`. The script self-verifies 126-row parity in the same
  transaction and aborts (rolls back) on any mismatch; it is idempotent, so
  an interrupted or repeated run is safe.
- **Phase 6:** after deleting `pl_models.py`, verify startup:
  `python -c "from promptops_app.database import init_db; init_db()"`
  against the rehearsal DB (catches the hidden import at `database.py`
  `init_db()`).
