#!/usr/bin/env bash
# =============================================================================
# local_env.sh — LOCAL DEV ONLY. Opens an SSM tunnel to the private dev RDS and
# prints the env overrides a host-run app needs.
#
# Nothing here touches .env, docker-compose.yml, the images, or any code path a
# deployed container takes: the script only writes export lines to stdout for
# you to eval. It is never invoked by a Dockerfile CMD or an entrypoint, and it
# refuses to run inside a container or a deployed tree (see guards below).
#
# Usage:
#   eval "$(scripts/local_env.sh)"   # tunnel up (if needed) + export the profile
#   scripts/local_env.sh url         # print just the tunnelled DATABASE_URL
#   scripts/local_env.sh up          # start the tunnel, print nothing
#   scripts/local_env.sh status      # is the tunnel listening?
#   scripts/local_env.sh doctor      # does .env's DB match the export + running app?
#   scripts/local_env.sh down        # stop the tunnel
#
# Why eval: it keeps the DB password out of your shell history (only the
# command is recorded, not the expanded URL).
#
# Overridable: LOCAL_DB_PORT (5433), CAS_BASTION_ID, RDS_REGION (us-east-1).
# =============================================================================
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="$REPO_ROOT/.env"
LOCAL_DB_PORT="${LOCAL_DB_PORT:-5433}"
CAS_BASTION_ID="${CAS_BASTION_ID:-i-0992ec49038075453}"
RDS_REGION="${RDS_REGION:-us-east-1}"          # where the RDS lives — NOT exported
LOCAL_DIS_PORT="${LOCAL_DIS_PORT:-8010}"
PIDFILE="${TMPDIR:-/tmp}/cas-db-tunnel.$LOCAL_DB_PORT.pid"

# All diagnostics go to stderr so `eval "$(...)"` only ever consumes exports.
say() { printf '%s\n' "$*" >&2; }
die() { printf 'local_env.sh: %s\n' "$*" >&2; exit 1; }

# ── Guards: never run anywhere that isn't a developer's own checkout ─────────
[ -f /.dockerenv ] && die "refusing to run inside a container (local dev only)"
case "${ENVIRONMENT:-}" in
    ""|local|dev|development) ;;
    *) die "ENVIRONMENT=${ENVIRONMENT} looks deployed — refusing to run" ;;
esac
case "$REPO_ROOT" in
    /opt/*) die "repo root $REPO_ROOT looks like a deployed tree — refusing to run" ;;
esac
[ -f "$ENV_FILE" ] || die "no .env at $ENV_FILE"

env_val() { grep -E "^$1=" "$ENV_FILE" | head -1 | cut -d= -f2-; }

tunnel_listening() { ss -ltn 2>/dev/null | grep -q "127.0.0.1:${LOCAL_DB_PORT}"; }

cmd_up() {
    if tunnel_listening; then
        say "tunnel already listening on 127.0.0.1:${LOCAL_DB_PORT}"
        return 0
    fi
    local key secret host
    key=$(env_val AWS_ACCESS_KEY_ID_RDS)
    secret=$(env_val AWS_SECRET_ACCESS_KEY_RDS)
    [ -n "$key" ] && [ -n "$secret" ] || die "AWS_ACCESS_KEY_ID_RDS / AWS_SECRET_ACCESS_KEY_RDS missing from .env"
    host=$(python3 -c "
import sys
from urllib.parse import urlparse
print(urlparse(sys.argv[1]).hostname or '')" "$(env_val DATABASE_URL)")
    [ -n "$host" ] || die "could not parse a host out of DATABASE_URL"

    command -v aws >/dev/null              || die "aws CLI not found"
    command -v session-manager-plugin >/dev/null \
        || die "session-manager-plugin not found (required by start-session)"

    say "opening SSM tunnel: localhost:${LOCAL_DB_PORT} -> ${host}:5432 via ${CAS_BASTION_ID}"
    # Credentials and region are scoped to THIS command only. Exporting them
    # would override the app's Bedrock identity (different account) and region.
    AWS_ACCESS_KEY_ID="$key" \
    AWS_SECRET_ACCESS_KEY="$secret" \
    AWS_DEFAULT_REGION="$RDS_REGION" \
    nohup aws ssm start-session \
        --target "$CAS_BASTION_ID" \
        --document-name AWS-StartPortForwardingSessionToRemoteHost \
        --parameters "{\"host\":[\"${host}\"],\"portNumber\":[\"5432\"],\"localPortNumber\":[\"${LOCAL_DB_PORT}\"]}" \
        >"${TMPDIR:-/tmp}/cas-db-tunnel.${LOCAL_DB_PORT}.log" 2>&1 &
    echo $! > "$PIDFILE"

    local i
    for i in $(seq 1 30); do
        tunnel_listening && { say "tunnel up (pid $(cat "$PIDFILE"))"; return 0; }
        sleep 1
    done
    die "tunnel did not come up in 30s — see ${TMPDIR:-/tmp}/cas-db-tunnel.${LOCAL_DB_PORT}.log"
}

cmd_down() {
    if [ -f "$PIDFILE" ] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null; then
        kill "$(cat "$PIDFILE")" && say "tunnel stopped"
    else
        say "no tunnel started by this script is running"
    fi
    rm -f "$PIDFILE"
}

cmd_status() {
    if tunnel_listening; then
        say "listening on 127.0.0.1:${LOCAL_DB_PORT}"
    else
        say "not listening on ${LOCAL_DB_PORT} (SSM drops idle sessions — rerun 'up')"
        return 1
    fi
}

# Rewrite DATABASE_URL's host:port to the tunnel, keeping user/password/db intact.
tunnel_url() {
    python3 -c "
import sys
from urllib.parse import urlparse, urlunparse
p = urlparse(sys.argv[1])
netloc = f'{p.username}:{p.password}@localhost:{sys.argv[2]}'
print(urlunparse(p._replace(netloc=netloc, query='sslmode=require')))" \
        "$(env_val DATABASE_URL)" "$LOCAL_DB_PORT"
}

# Foreground tunnel for a supervisor (systemd). Does not background, does not
# write a pidfile: the supervisor owns the lifecycle and restarts on exit.
cmd_serve() {
    local key secret host
    key=$(env_val AWS_ACCESS_KEY_ID_RDS)
    secret=$(env_val AWS_SECRET_ACCESS_KEY_RDS)
    [ -n "$key" ] && [ -n "$secret" ] || die "AWS_ACCESS_KEY_ID_RDS / AWS_SECRET_ACCESS_KEY_RDS missing from .env"
    host=$(python3 -c "
import sys
from urllib.parse import urlparse
print(urlparse(sys.argv[1]).hostname or '')" "$(env_val DATABASE_URL)")
    [ -n "$host" ] || die "could not parse a host out of DATABASE_URL"
    say "serving tunnel localhost:${LOCAL_DB_PORT} -> ${host}:5432 via ${CAS_BASTION_ID}"
    AWS_ACCESS_KEY_ID="$key" \
    AWS_SECRET_ACCESS_KEY="$secret" \
    AWS_DEFAULT_REGION="$RDS_REGION" \
    exec aws ssm start-session \
        --target "$CAS_BASTION_ID" \
        --document-name AWS-StartPortForwardingSessionToRemoteHost \
        --parameters "{\"host\":[\"${host}\"],\"portNumber\":[\"5432\"],\"localPortNumber\":[\"${LOCAL_DB_PORT}\"]}"
}

# Database name from a postgres URL (empty if unparseable).
db_of_url() { printf '%s' "${1:-}" | sed -nE 's#^[^/]*//[^/]*/([^/?]+).*$#\1#p'; }

# Compare .env's database against the shell export and any running uvicorn.
# Exists because an exported DATABASE_URL OUTRANKS .env, so a stale export
# silently keeps a running app on the wrong database after a .env edit.
cmd_doctor() {
    local env_db shell_db rc=0
    env_db=$(db_of_url "$(env_val DATABASE_URL)")
    say ".env database            : ${env_db}"
    if [ -n "${DATABASE_URL:-}" ]; then
        shell_db=$(db_of_url "$DATABASE_URL")
        if [ "$shell_db" = "$env_db" ]; then
            say "this shell's export      : ${shell_db} (matches)"
        else
            say "this shell's export      : ${shell_db}  <-- MISMATCH, re-run: eval \"\$(scripts/local_env.sh)\""
            rc=1
        fi
    else
        say "this shell's export      : (none — .env would be used)"
    fi
    local pid pdb found=0
    for pid in $(pgrep -f 'uvicorn app.main:app' 2>/dev/null); do
        [ -r "/proc/$pid/environ" ] || continue
        found=1
        pdb=$(db_of_url "$(tr '\0' '\n' < "/proc/$pid/environ" | sed -nE 's/^DATABASE_URL=//p' | head -1)")
        # No export in that process means it read .env directly — that always matches.
        if [ -z "$pdb" ]; then
            say "running uvicorn pid $pid : ${env_db} (no export; reads .env — matches)"
            continue
        fi
        if [ "$pdb" = "$env_db" ]; then
            say "running uvicorn pid $pid : ${pdb} (matches)"
        else
            say "running uvicorn pid $pid : ${pdb}  <-- MISMATCH, restart it (reload will NOT fix this)"
            rc=1
        fi
    done
    [ "$found" = 1 ] || say "running uvicorn          : (none)"
    return $rc
}

case "${1:-env}" in
    up)     cmd_up ;;
    serve)  cmd_serve ;;   # foreground, for systemd
    down)   cmd_down ;;
    status) cmd_status ;;
    doctor) cmd_doctor ;;   # is anything on the wrong database?
    url)    cmd_up; tunnel_url ;;
    env)
        if [ -n "${DATABASE_URL:-}" ] \
           && [ "$(db_of_url "$DATABASE_URL")" != "$(db_of_url "$(env_val DATABASE_URL)")" ]; then
            say "WARNING: your shell had DATABASE_URL on '$(db_of_url "$DATABASE_URL")' but .env says"
            say "         '$(db_of_url "$(env_val DATABASE_URL)")'. This eval fixes THIS shell; any already-running"
            say "         app keeps the old database until it is restarted (reload is not enough)."
        fi
        cmd_up
        # stdout from here is meant to be eval'd — exports only.
        printf 'export DATABASE_URL=%q\n' "$(tunnel_url)"
        printf 'export REDIS_URL=%q\n' "redis://localhost:6379/0"
        printf 'export DIS_API_BASE_URL=%q\n' "http://localhost:${LOCAL_DIS_PORT}/v1"
        say "exported DATABASE_URL, REDIS_URL, DIS_API_BASE_URL for this shell"
        say "note: AWS creds/region deliberately NOT exported — Bedrock keeps using .env"
        ;;
    *) die "unknown command '${1}' (env|up|down|status|url|serve|doctor)" ;;
esac
