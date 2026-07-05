#!/usr/bin/env bash
# =============================================================================
# Rehearsal database — disposable local Postgres seeded from a prod dump.
#
# Every schema migration in the prompt-consolidation initiative is rehearsed
# here on prod-shaped data before it is allowed anywhere near RDS
# (see PROMPT_CONSOLIDATION_PLAN.md, Phase 0S).
#
# Usage:
#   scripts/rehearsal_db.sh dump      # pg_dump prod RDS -> backups/ (full + pl_* SQL)
#   scripts/rehearsal_db.sh up        # start the local postgres:17 container
#   scripts/rehearsal_db.sh restore   # restore the newest full dump into it
#   scripts/rehearsal_db.sh reset     # drop + recreate the DB, then restore (fresh rehearsal)
#   scripts/rehearsal_db.sh verify    # row-parity spot check against known counts
#   scripts/rehearsal_db.sh url       # print the rehearsal DATABASE_URL
#   scripts/rehearsal_db.sh down      # stop + remove the container (data discarded)
#
# The rehearsal DB is DISPOSABLE by design: `reset` gives a clean prod clone
# in seconds. Point the app / alembic at it with:
#   DATABASE_URL=$(scripts/rehearsal_db.sh url)
# =============================================================================
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKUP_DIR="$REPO_ROOT/backups"
CONTAINER=cas-rehearsal-db
PORT=55432
REHEARSAL_URL="postgresql://rehearsal:rehearsal@localhost:${PORT}/promptops_db"

prod_url() {
    # Read DATABASE_URL from .env and strip the +psycopg2 driver suffix for psql/pg_dump.
    local url
    url=$(grep -E '^DATABASE_URL=' "$REPO_ROOT/.env" | cut -d= -f2-)
    echo "${url/postgresql+psycopg2/postgresql}"
}

cmd_dump() {
    mkdir -p "$BACKUP_DIR"
    local stamp url
    stamp=$(date -u +%Y%m%dT%H%M%SZ)
    url=$(prod_url)
    echo ">> Full custom-format dump (all tables)..."
    pg_dump "$url" -Fc -f "$BACKUP_DIR/promptops_db_full_${stamp}.dump"
    echo ">> Plain-SQL dump of the 10 pl_* tables (Phase 2/6 safety artifact)..."
    pg_dump "$url" --format=plain --inserts -t 'pl_*' -f "$BACKUP_DIR/pl_tables_pre_migration_${stamp}.sql"
    ls -la "$BACKUP_DIR"
}

cmd_up() {
    if docker ps -a --format '{{.Names}}' | grep -q "^${CONTAINER}$"; then
        docker start "$CONTAINER" >/dev/null
    else
        docker run -d --name "$CONTAINER" \
            -e POSTGRES_USER=rehearsal -e POSTGRES_PASSWORD=rehearsal \
            -e POSTGRES_DB=promptops_db -p "${PORT}:5432" postgres:17 >/dev/null
    fi
    until docker exec "$CONTAINER" pg_isready -U rehearsal -q; do sleep 1; done
    echo "rehearsal DB up: $REHEARSAL_URL"
}

newest_dump() {
    ls -t "$BACKUP_DIR"/promptops_db_full_*.dump 2>/dev/null | head -1
}

cmd_restore() {
    local dump
    dump=$(newest_dump)
    [ -n "$dump" ] || { echo "No dump found in $BACKUP_DIR — run 'dump' first." >&2; exit 1; }
    echo ">> Restoring $(basename "$dump")..."
    pg_restore --no-owner --no-privileges -d "$REHEARSAL_URL" "$dump"
    cmd_verify
}

cmd_reset() {
    cmd_up
    docker exec "$CONTAINER" psql -U rehearsal -d postgres -qc "DROP DATABASE IF EXISTS promptops_db WITH (FORCE);"
    docker exec "$CONTAINER" psql -U rehearsal -d postgres -qc "CREATE DATABASE promptops_db;"
    cmd_restore
}

cmd_verify() {
    psql "$REHEARSAL_URL" -Atc "
        SELECT 'tables='   || (SELECT count(*) FROM information_schema.tables WHERE table_schema='public')
            || ' prompts=' || (SELECT count(*) FROM prompts)
            || ' prompt_versions=' || (SELECT count(*) FROM prompt_versions)
            || ' pl_prompts=' || (SELECT count(*) FROM pl_prompts)
            || ' pl_total=' || (
                 (SELECT count(*) FROM pl_prompts) + (SELECT count(*) FROM pl_prompt_versions)
               + (SELECT count(*) FROM pl_prompt_tags) + (SELECT count(*) FROM pl_prompt_variables)
               + (SELECT count(*) FROM pl_teams) + (SELECT count(*) FROM pl_prompt_requests)
               + (SELECT count(*) FROM pl_attachments) + (SELECT count(*) FROM pl_prompt_teams)
               + (SELECT count(*) FROM pl_reviews) + (SELECT count(*) FROM pl_audit_events));"
    echo "(expected at 2026-07-05 baseline: tables=47 prompts=8 prompt_versions=7 pl_prompts=20 pl_total=126)"
}

case "${1:-}" in
    dump)    cmd_dump ;;
    up)      cmd_up ;;
    restore) cmd_restore ;;
    reset)   cmd_reset ;;
    verify)  cmd_verify ;;
    url)     echo "$REHEARSAL_URL" ;;
    down)    docker rm -f "$CONTAINER" ;;
    *)       grep '^#   scripts/' "$0" | sed 's/^# *//'; exit 1 ;;
esac
