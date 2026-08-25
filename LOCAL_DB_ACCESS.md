# Local DB access — connecting to the dev database from your machine

The dev database is **not reachable directly**. `content-ai-studio-dev-rds` is
`PubliclyAccessible=false`, so its endpoint always resolves to a VPC-private
address (`10.0.10.180`) no matter where you resolve it from. A raw connection
just times out:

```
psycopg2.OperationalError: connection to server at "content-ai-studio-dev-rds…"
(10.0.10.180), port 5432 failed: timeout expired
```

**That is not a credentials problem.** If you see a timeout, no password change,
`.env` edit, or quoting fix will help — you have no network route. Editing the
connection string is the most common wasted hour here.

The supported route is an **SSM port-forward** through the `CAS-dev` EC2
instance, which sits in the same VPC as the database. `scripts/local_env.sh`
wraps it. No AWS infrastructure changes are needed — the RDS security group
already admits the bastion.

---

## 1. Install the two CLI tools

```bash
# aws CLI v2
curl -s "https://awscli.amazonaws.com/awscli-exe-linux-x86_64.zip" -o /tmp/awscli.zip
unzip -q /tmp/awscli.zip -d /tmp && sudo /tmp/aws/install
# session-manager-plugin — start-session does not work without it
curl -s "https://s3.amazonaws.com/session-manager-downloads/plugin/latest/ubuntu_64bit/session-manager-plugin.deb" -o /tmp/smp.deb
sudo dpkg -i /tmp/smp.deb
```

No sudo? Both install into `~/.local` without root — `./aws/install -i
~/.local/aws-cli -b ~/.local/bin`, and `dpkg-deb -x smp.deb ~/.local/smp` then
copy the binary onto your `PATH`.

Verify: `aws --version` and `session-manager-plugin --version` both print.

**On Windows:** run all of this inside WSL, not PowerShell. The script is bash,
and — importantly — the tunnel must be started *inside WSL* so that `localhost`
means the same thing to the tunnel and to the app.

---

## 2. Get AWS credentials

Ask whoever administers AWS for an IAM identity in account **410453487786**
(the account holding the RDS and EC2 — *not* the Bedrock account). It needs:

| purpose | actions |
|---|---|
| open the tunnel | `ssm:StartSession` on instance `i-0992ec49038075453` **and** on document `AWS-StartPortForwardingSessionToRemoteHost` |
| let the script find things | `ec2:DescribeInstances`, `ssm:DescribeInstanceInformation` |

`rds:DescribeDBInstances` is *not* required — it is denied for these identities
and the script never calls it.

Put them in `.env` as:

```
AWS_ACCESS_KEY_ID_RDS=…
AWS_SECRET_ACCESS_KEY_RDS=…
```

These names are deliberate. **Do not** name them `AWS_ACCESS_KEY_ID` /
`AWS_SECRET_ACCESS_KEY` — those are the *Bedrock* identity, in a **different AWS
account**, and overwriting them repoints LLM inference and loses model access.
`config.py` uses `extra="ignore"`, so the `_RDS` pair is inert to the app; only
the tunnel reads it.

---

## 3. Get `.env`

Copy `.env.example` and have a teammate send you the real values **through your
secret manager, not chat**. The one that matters here:

```
DATABASE_URL=postgresql+psycopg2://postgres:<password>@content-ai-studio-dev-rds.co7gbazco3kw.us-east-1.rds.amazonaws.com:5432/cas_dev_db
```

Two things that have bitten people:

- **The database name is `cas_dev_db`.** A stale `cas_dev` also exists on the
  same instance, four alembic revisions behind, with roughly a quarter of the
  data and a `tenants` table that no longer exists at head. Connecting to it
  gives you plausible-looking but wrong data.
- **Do not wrap the password in quotes.** Quoting only strips when it wraps the
  *entire* line, so `postgres:'p(w)'@…` sends the quotes as password characters.
  In a URL, `( ) ! $ & * + , ; =` are all legal unencoded; only
  `@ / : ? # %` need percent-encoding (`@`→`%40`, and so on).

---

## 4. Open the tunnel and run the app

```bash
eval "$(scripts/local_env.sh)"    # starts the tunnel if needed, exports the profile
scripts/local_env.sh doctor       # sanity check before you debug anything
.venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

`eval` exports three things for that shell: `DATABASE_URL` pointed at
`localhost:5433`, plus `REDIS_URL` and `DIS_API_BASE_URL` pointed at the ports
the Redis and DIS containers publish. AWS credentials and region are
deliberately **not** exported (see above).

Other subcommands: `up`, `down`, `status`, `url`, `serve` (foreground, for a
supervisor), `doctor`.

**Startup takes ~25 seconds** — alembic runs over the tunnel and every
round-trip crosses to us-east-1. An early `ECONNRESET` from the Vite proxy just
means the backend has not finished booting. Wait, then retry.

---

## 5. The two traps

### The tunnel dies when idle

SSM terminates idle sessions, so the first request after a break fails. The
signature is distinctive: **endpoints that don't touch the DB keep working**
(`/api/v1/auth/config` → 200) while anything that queries fails. Fix:

```bash
scripts/local_env.sh up      # no re-export, no app restart needed
```

The app recovers on its own because the engine sets `pool_pre_ping=True`, and
the tunnel address never changes. To stop babysitting it, install a systemd
user service that restarts it automatically (Linux/WSL with systemd):

```ini
# ~/.config/systemd/user/cas-db-tunnel.service
[Unit]
Description=SSM port-forward to CAS dev RDS (localhost:5433)
StartLimitIntervalSec=0

[Service]
Type=simple
WorkingDirectory=/path/to/Content-AI-Studio-
ExecStart=/path/to/Content-AI-Studio-/scripts/local_env.sh serve
Restart=always
RestartSec=5

[Install]
WantedBy=default.target
```

```bash
systemctl --user daemon-reload && systemctl --user enable --now cas-db-tunnel
```

`Restart=always` (not `on-failure`) is required: SSM exits **0** on idle
timeout, so an `on-failure` policy would never restart it.

### An exported `DATABASE_URL` outranks `.env`

pydantic-settings ranks real environment variables **above** the `.env` file,
and a running process never re-reads either. `eval "$(scripts/local_env.sh)"`
snapshots `.env`'s user, password, **and database name** at eval time.

So after **any** `.env` change: re-`eval` **and fully restart** the app.
`--reload` is not enough — it re-imports your modules and re-reads `.env`, but
the inherited export still wins, and every reload child inherits the reloader
parent's environment.

`scripts/local_env.sh doctor` catches exactly this, comparing `.env` against
your shell export and every running uvicorn:

```
.env database             : cas_dev_db
this shell's export       : (none — .env would be used)
running uvicorn pid 27778 : cas_dev_db (matches)
```

Run it first whenever the data looks wrong. It exits non-zero on a mismatch.

---

## Please don't

- **Don't make the RDS publicly accessible** to avoid the tunnel. Its security
  group already has a `0.0.0.0/0` rule on 5432 that is inert only because the
  instance is private — flipping that flag exposes the database to the internet
  behind one password.
- **Don't commit `docker-compose.override.yml`.** It is gitignored for a reason:
  it is absent from `.dockerignore`, so a committed one can reach an image and
  silently override deployed config.
- **Don't run migrations or experiments inside the long-running dev containers** —
  they mount the repo live. Use the host venv against the tunnel.

## If you'd rather run everything in Docker

Harder, and not recommended. The tunnel binds `127.0.0.1:5433`, which a
container cannot reach even via `host.docker.internal` — a loopback-bound
listener refuses connections arriving on other interfaces. You would need a
relay listening on `0.0.0.0` in front of the tunnel, plus a
`docker-compose.override.yml` repointing `DATABASE_URL` for `api` and
`celery_worker`. Running just the API on the host avoids all of it, and Redis
and DIS already publish their ports to the host.
