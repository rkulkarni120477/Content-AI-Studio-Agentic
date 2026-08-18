# Phoenix Production Setup (one-time, manual)

Self-hosted Phoenix (`phoenix` service in the root `docker-compose.yml`) is
part of the main CAS compose project — it comes up automatically with
`docker compose up -d`, no separate deploy step needed. It reads its
variables from the same root `.env` CAS itself uses.

## 1. Its database is created automatically — nothing to do here

Traces live in a dedicated database on the same Postgres instance CAS's own
app already uses (its RDS instance), not a new container. `contentai_api`
creates it itself on every startup if missing
(`promptops_app.database.ensure_phoenix_database`) — no manual `psql` step,
on any environment. It just needs the app's own `DATABASE_URL` user to have
`CREATEDB`, which is typical.

## 2. Add these keys to the root `.env` on the EC2 host

**`PHOENIX_SQL_DATABASE_URL` is mandatory, not optional** — leave it unset
and Phoenix silently falls back to a local SQLite file inside its own
container, which is wiped on every redeploy (a warning is logged on
`contentai_api` startup if this happens, but nothing fails loudly). The
others below have safe defaults in `docker-compose.yml` if omitted, but are
worth setting explicitly anyway.

```
PHOENIX_CONTAINER_NAME=contentai_phoenix
PHOENIX_COLLECTOR_ENDPOINT=http://phoenix:6006/v1/traces
PHOENIX_BASE_URL=http://phoenix:6006
PHOENIX_PROJECT_NAME=content-ai-studio
PHOENIX_API_KEY=
# Same host/user/password as the app's own DATABASE_URL, different db name.
PHOENIX_SQL_DATABASE_URL=postgresql://<user>:<password>@<rds-host>:5432/phoenix
```

## 3. Reaching the Phoenix UI

Its port (6006) is bound to `127.0.0.1` only on the host — reach it via an
SSH tunnel:

```bash
ssh -L 6006:localhost:6006 <user>@<ec2-host>
```

then open `http://localhost:6006` in your own browser. CAS's own users never
talk to Phoenix directly; the trace-detail endpoint
(`GET /generations/{id}/trace`) proxies through the CAS API.

## Verifying it worked

```bash
docker compose up -d --build --remove-orphans
docker ps --filter name=phoenix   # one container, "Up"
curl -s http://localhost:6006/healthz
```

Then trigger any real generation/regeneration in the app and confirm a trace
shows up in the Phoenix UI, and that `GET /api/v1/generations/{id}/trace`
returns real span data instead of a 404.
