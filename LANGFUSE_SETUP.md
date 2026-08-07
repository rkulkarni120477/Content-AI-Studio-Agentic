# Langfuse Production Setup (one-time, manual)

Self-hosted Langfuse (`langfuse/docker-compose.yml`) is a separate compose
project from the main CAS stack — see the header comment in that file for why.
The backend deploy workflow (`.github/workflows/backend-deploy.yml`) will
start it automatically on every deploy **once** the steps below have been
done once on the EC2 host. Until then, it silently skips Langfuse and deploys
CAS/DIS as normal — nothing here can break the main deploy.

## 1. Create `langfuse/.env` on the EC2 host

This file holds real secrets and is gitignored — `git reset --hard` in the
deploy script will never create or touch it. SSH into the host, `cd
/opt/content-ai/langfuse`, and create `.env` with these keys:

```
# Generate each of these three with: openssl rand -hex 32
SALT=
ENCRYPTION_KEY=
NEXTAUTH_SECRET=

NEXTAUTH_URL=http://localhost:3001

POSTGRES_USER=langfuse
POSTGRES_PASSWORD=<pick a strong password>
POSTGRES_DB=langfuse
DATABASE_URL=postgresql://langfuse:<same password as POSTGRES_PASSWORD>@postgres:5432/langfuse

CLICKHOUSE_USER=clickhouse
CLICKHOUSE_PASSWORD=<pick a strong password>

MINIO_ROOT_USER=minio
MINIO_ROOT_PASSWORD=<pick a strong password, 8+ chars>
LANGFUSE_S3_EVENT_UPLOAD_SECRET_ACCESS_KEY=<same as MINIO_ROOT_PASSWORD>
LANGFUSE_S3_MEDIA_UPLOAD_SECRET_ACCESS_KEY=<same as MINIO_ROOT_PASSWORD>
LANGFUSE_S3_BATCH_EXPORT_SECRET_ACCESS_KEY=<same as MINIO_ROOT_PASSWORD>

REDIS_AUTH=<pick a strong password>

# Auto-provisions one org/project/API-keypair on first boot — without
# LANGFUSE_INIT_ORG_ID and LANGFUSE_INIT_PROJECT_ID set, Langfuse silently
# skips ALL auto-provisioning (a real gotcha found during local setup).
LANGFUSE_INIT_ORG_ID=cas-org
LANGFUSE_INIT_ORG_NAME=Content AI Studio
LANGFUSE_INIT_PROJECT_ID=cas-prod
LANGFUSE_INIT_PROJECT_NAME=CAS Production
# Generate these two yourself (any random strings) — they become the API
# keypair CAS's own .env authenticates with (step 2 below).
LANGFUSE_INIT_PROJECT_PUBLIC_KEY=pk-lf-<random>
LANGFUSE_INIT_PROJECT_SECRET_KEY=sk-lf-<random>
# First login user for the Langfuse web UI itself (separate from the API keypair).
LANGFUSE_INIT_USER_EMAIL=<your email>
LANGFUSE_INIT_USER_NAME=<your name>
LANGFUSE_INIT_USER_PASSWORD=<pick a strong password>
```

Every `<...>` placeholder should be a real generated/chosen value — none of
these have safe defaults. Auto-provisioning (the `LANGFUSE_INIT_*` vars) only
fires on that project's very first boot; changing them later does nothing
until the underlying `langfuse_postgres_data` volume is wiped.

## 2. Add 3 values to the root `.env` on the EC2 host

So the CAS API can actually send traces. These must match what you put above:

```
LANGFUSE_HOST=http://host.docker.internal:3001
LANGFUSE_PUBLIC_KEY=<same as LANGFUSE_INIT_PROJECT_PUBLIC_KEY above>
LANGFUSE_SECRET_KEY=<same as LANGFUSE_INIT_PROJECT_SECRET_KEY above>
```

## 3. Decide who can reach the Langfuse dashboard, then lock it down

`langfuse-web` publishes port 3001 on **all interfaces** (`3001:3000`), unlike
its other services (`clickhouse`/`redis`/`postgres`/`minio`), which are
already bound to `127.0.0.1` only. Decide one of:

- **Keep it private (recommended)** — leave EC2's security group closed on
  3001, and reach the dashboard only via an SSH tunnel:
  `ssh -L 3001:localhost:3001 <user>@<ec2-host>`, then open
  `http://localhost:3001` in your own browser.
- **Expose it directly** — open port 3001 in the EC2 security group, ideally
  restricted to your office/VPN IP range rather than `0.0.0.0/0`, and put it
  behind the same TLS/reverse-proxy setup `cas-api.academian.com` already
  uses rather than serving plain HTTP on a public port.

Nothing in the app depends on which you pick — this only affects who can log
into the Langfuse UI itself. CAS's own users never talk to Langfuse directly;
the trace-detail endpoint (`GET /generations/{id}/trace`) proxies through the
CAS API, which already runs on `cas-api.academian.com`.

## Verifying it worked

After the next deploy (or after running the two steps above and re-running
`docker compose up -d --build --remove-orphans` from `/opt/content-ai/langfuse`
by hand once):

```bash
docker ps --filter name=langfuse   # 6 containers, all "healthy" or "Up"
curl -s http://localhost:3001/api/public/health   # {"status":"OK", ...}
```

Then trigger any real generation/regeneration in the app and confirm a trace
shows up in the Langfuse UI, and that `GET /api/v1/generations/{id}/trace`
returns real observation data instead of a 404.
