# Deploying to a Linux VM

Everything runs in Docker: Postgres, Redis, the API, and nginx serving the UI.
The VM needs Docker and nothing else — no Python, no Node, no Salesforce CLI,
because the backend image carries its own.

A fresh install is three commands once Docker is present.

```bash
git clone <your-repo> sfc && cd sfc
cp .env.production.example .env && chmod 600 .env   # then fill it in
./deploy/deploy.sh
```

---

## Contents

- [What gets deployed](#what-gets-deployed)
- [Requirements](#requirements)
- [1. Prepare the VM](#1-prepare-the-vm)
- [2. Get the code](#2-get-the-code)
- [3. Configure](#3-configure)
- [4. Deploy](#4-deploy)
- [5. Connect a Salesforce org](#5-connect-a-salesforce-org)
- [6. First run](#6-first-run)
- [Accounts and access](#accounts-and-access)
- [HTTPS](#https)
- [Day-two operations](#day-two-operations)
- [Upgrading](#upgrading)
- [Backups](#backups)
- [Troubleshooting](#troubleshooting)
- [Security notes](#security-notes)

---

## What gets deployed

| Container | Image | Published | Purpose |
|---|---|---|---|
| `web` | nginx 1.27 | **yes**, `HTTP_PORT` | Serves the built SPA, proxies `/api` and `/health` |
| `backend` | built here, ~185 MB | no | FastAPI, the analysis pipeline, the agent, and the `sf` CLI |
| `postgres` | postgres 16 | no | Runs, evidence, verdicts, chat threads |
| `redis` | redis 7.4 | no | Cache |

Only nginx is reachable from outside. Postgres and Redis are on the compose
network only — unlike the development compose file, which publishes them on
5433/6380 so they can coexist with a local install. Exposing a database to the
internet is the mistake worth designing out.

Three named volumes hold state: `pgdata`, `redisdata`, and `workspace`
(retrieved org metadata plus generated reports, mounted at `/app/.cache`).

**The schema applies itself.** On boot the backend waits for Postgres, applies
`schema.sql` if the database is empty, then replays every migration in
`backend/db/migrations/`. Development requires that as a manual step and
forgetting it is the most common cause of a broken first start; here a fresh VM
and an upgraded one converge on the same schema with no intervention.

---

## Requirements

| | |
|---|---|
| OS | Any Linux with Docker. Verified against Ubuntu 22.04/24.04 and Debian 12 |
| CPU / RAM | 2 vCPU, 4 GB. The retrieve stage unpacks a few hundred MB and the graph build is memory-hungry on large orgs |
| Disk | 20 GB. Images ~1 GB, the rest is retrieved metadata and Postgres |
| Network | Outbound HTTPS to your Salesforce My Domain, your LLM gateway, and Docker Hub. **No inbound port is required except the one you publish** |

---

## 1. Prepare the VM

Install Docker Engine and the compose plugin:

```bash
curl -fsSL https://get.docker.com | sudo sh
```

Let your user run Docker without `sudo`, then reload the group:

```bash
sudo usermod -aG docker $USER && newgrp docker
```

Confirm both are present — `deploy.sh` checks this too, but failing here is clearer:

```bash
docker --version && docker compose version
```

Open the port you intend to publish (skip if a load balancer or security group
already handles it):

```bash
sudo ufw allow 80/tcp && sudo ufw --force enable
```

---

## 2. Get the code

```bash
git clone <your-repo> sfc && cd sfc
```

No git on the VM? Copy the tree up instead — but exclude the local build
artefacts, or you will ship a Windows virtualenv into a Linux image:

```bash
rsync -av --exclude node_modules --exclude .venv --exclude .cache \
      --exclude .git ./ user@vm:~/sfc/
```

Either way, make sure the scripts are executable. Copying from Windows, or
through an archive that does not preserve permissions, silently drops the bit
and every command below fails with `Permission denied`:

```bash
chmod +x deploy/*.sh
```

---

## 3. Configure

```bash
cp .env.production.example .env
chmod 600 .env
```

Fill in the values marked `[REQUIRED]`. The minimum to boot:

| Variable | Notes |
|---|---|
| `POSTGRES_PASSWORD` | Generate one: `openssl rand -base64 24` |
| `AUTH_SECRET_KEY` | Signs session cookies: `openssl rand -hex 32`. Leaving it empty makes every restart sign everyone out |
| `AUTH_SUPERUSER_EMAIL` | The account that always exists. Defaults to `hritik.khatri@bounteous.com` |
| `AUTH_SUPERUSER_PASSWORD` | At least 12 characters. This is your way in on a fresh deploy |
| `SF_CLIENT_ID` / `SF_CLIENT_SECRET` | Consumer Key and Secret from the Connected App |
| `SF_INSTANCE_URL` | Your **My Domain** host. Client-credentials tokens are never issued from `login.salesforce.com` |
| `ANTHROPIC_API_KEY` | Or set `LLM_ENABLED=false` — verdicts are unaffected, since no model decides one |
| `HTTP_PORT` | Defaults to 80. Use 8080 if something already owns it |

**Do not set** `DATABASE_URL`, `REDIS_URL`, `WORKSPACE_DIR`, `SF_CLI_PATH`,
`API_HOST` or `API_PORT`. The compose file overrides all of them with
container-internal values, so the same `.env` works on a laptop and on the VM.

### The Connected App

Setup → App Manager → New Connected App:

1. **Enable OAuth Settings**, scopes `api`, `refresh_token`, `offline_access`
2. **Enable Client Credentials Flow** — without it you get `unsupported_grant_type`
3. Save, then **Manage → Edit Policies → Client Credentials Flow → assign a Run
   As user** — without it you get `invalid_grant`

Allow ~10 minutes for a new app to propagate before the first token request.

> **Choose the Run As user carefully.** The analysis sees exactly what that user
> sees, and anything invisible to it is indistinguishable from something
> genuinely unused. A System Administrator is strongly recommended; step 5 warns
> you if it is not one.

---

## 4. Deploy

```bash
./deploy/deploy.sh
```

It validates `.env`, builds both images, starts the stack, waits for the
backend to report healthy, and checks `/health` through nginx.

The first build pulls Node and installs the Salesforce CLI, so allow a few
minutes. Later runs reuse the layers.

```bash
./deploy/deploy.sh --pull       # also refresh the postgres/redis/nginx bases
./deploy/deploy.sh --no-build   # fastest restart, reuse existing images
```

Expected first-boot log:

```
[entrypoint] postgres is up
[entrypoint] empty database -- applying schema.sql
[entrypoint] migration 002_org_capabilities.sql
[entrypoint] migration 003_removal_prerequisites.sql
[entrypoint] migration 004_standard_object_type.sql
[entrypoint] migration 005_agent_chat.sql
[entrypoint] migration 006_auth.sql
[entrypoint] schema ready: 25 tables
[entrypoint] sf CLI @salesforce/cli/2.x linux-x64 node-v20.x
[entrypoint] starting uvicorn on 0.0.0.0:8000
```

**25 tables** is the number to look for. On a restart the second line becomes
`NN relations present -- skipping schema.sql` and the migrations replay
harmlessly. Right after it you should see the auth bootstrap:

```
auth_superuser_ready  email=... password='applied from environment'
```

If that line says `auth_superuser_unset` or `auth_superuser_no_password`
instead, nobody can sign in — fix the `AUTH_SUPERUSER_*` values and run
`./deploy/manage.sh reload-env`.

Open `http://<vm-ip>/`.

---

## 5. Connect a Salesforce org

Run once per org. Idempotent, so re-run it whenever something looks wrong or the
org's configuration changes.

```bash
./deploy/manage.sh setup-org
```

It checks the database, authenticates, discovers the org's capabilities, runs
the inventory, and prints either `READY` or the blocking problems with a fix for
each. Roughly **60 API calls**.

**Read the coverage limitations it prints.** They are not errors — they are the
honest bounds of what can be proven in *that* org, and they appear verbatim in
the report. An org without Event Monitoring cannot observe external API traffic,
so a component called only by an integration can never be shown to be unused
there.

Two warnings worth acting on:

- **"API user is NOT an administrator"** — coverage is bounded by that user.
- **"Report/Dashboard: none found"** — if the org has them, retrieval is broken
  and every field referenced only by a report will look unused.

Then pull the metadata to the workspace volume:

```bash
./deploy/manage.sh retrieve
```

---

## 6. First run

Sign in first — see [Accounts and access](#accounts-and-access).
Then open the UI, go to **Pipeline**, and press **Run analysis**. All ten stages run
in the backend with live progress over SSE; closing the tab does not stop it,
and reopening rejoins in progress.

Sanity-check the connection first if you prefer:

```bash
./deploy/manage.sh verify
```

---

## Accounts and access

The UI and every API route sit behind a sign-in. The only exceptions are
`/health` and the three endpoints the login page itself needs.

### First sign-in

`deploy.sh` refuses to start without `AUTH_SECRET_KEY`, `AUTH_SUPERUSER_EMAIL`
and a `AUTH_SUPERUSER_PASSWORD` of at least 12 characters, so by the time the
stack is up the superuser exists. Open the UI and sign in with those values.

### Adding people

As an admin, open **Access** in the sidebar. Enter an email, press **Generate**
for a random password, pick a role, and **Add user**.

| Role | Can do |
|---|---|
| `user` | Everything the product does: run an analysis, read the evidence, use the assistant, export |
| `admin` | The same, plus managing accounts |

Passwords are stored as salted scrypt hashes and are **not recoverable** — copy
the generated one before saving and send it over something other than email. To
give someone a new one, use the role/status controls on their row or delete and
re-add them.

Disabling or deleting an account ends the sessions it already has, on the next
request. There is no "log everyone out" button; rotating `AUTH_SECRET_KEY` and
restarting does that.

### The superuser

The account named by `AUTH_SUPERUSER_EMAIL` cannot be demoted, disabled or
deleted from the admin panel. It exists so a deploy always has one login that
works, no matter what an admin does to the others.

It is reconciled from the environment at every boot, but **only when the
password there has changed** — so a password rotated in the UI survives
restarts, while editing `.env` still works as a reset:

```bash
# forgot the password
nano .env                          # set a new AUTH_SUPERUSER_PASSWORD
./deploy/manage.sh reload-env      # recreates the backend so it re-reads .env
```

Use `reload-env`, not `restart`: `docker compose restart` reboots a container
with the environment it was *created* with, so an edited `.env` would have no
effect and nothing would say so.

The backend logs which happened, so you can tell the two apart:

```
auth_superuser_ready  email=... password='applied from environment'
auth_superuser_ready  email=... password=unchanged
```

### Sessions

A session is an httpOnly, SameSite=Lax cookie signed with `AUTH_SECRET_KEY`,
valid for `AUTH_SESSION_HOURS` (default 12). It is marked `Secure` only when the
request arrived over HTTPS — set unconditionally, the cookie would be silently
dropped on a plain-HTTP VM and login would appear to succeed and then 401.

Nothing is stored server-side, so `AUTH_SECRET_KEY` must be **identical across
restarts and across every backend replica**. If it is empty the backend invents
one per process and logs a warning; sessions then die at every restart.

---

## HTTPS

The stack serves plain HTTP so it can sit behind whatever you already use. Three
options, easiest first.

**A load balancer or existing proxy** — point it at `HTTP_PORT` and terminate
TLS there. Nothing to change; the backend already trusts `X-Forwarded-Proto`
because uvicorn runs with `--proxy-headers`.

**Caddy on the same VM**, which obtains and renews certificates automatically.
Set `HTTP_PORT=8080` in `.env`, redeploy, then:

```bash
sudo apt install -y caddy
echo 'cleanup.example.com {
    reverse_proxy 127.0.0.1:8080 {
        flush_interval -1
    }
}' | sudo tee /etc/caddy/Caddyfile
sudo systemctl restart caddy
```

`flush_interval -1` is not optional. It disables response buffering, which the
Server-Sent Events streams require — see the warning below.

**nginx + certbot** on the host — same idea, and the proxy block needs
`proxy_buffering off;` for the same reason.

> ### Whatever you put in front, disable response buffering
>
> Pipeline progress and the assistant's replies are Server-Sent Events. A proxy
> with default buffering holds the whole stream and releases it at the end: the
> UI looks frozen for the length of a run and then blinks to "done". Nothing
> errors, nothing logs, and it is very easy to ship. The bundled nginx already
> sets `proxy_buffering off` on those two routes; anything you add in front
> needs the equivalent.

---

## Day-two operations

```bash
./deploy/manage.sh status            # health, table count, run count, disk
./deploy/manage.sh logs              # all services
./deploy/manage.sh logs backend      # one
./deploy/manage.sh psql              # database shell
./deploy/manage.sh shell             # shell in the backend container
./deploy/manage.sh restart backend
./deploy/manage.sh stop | start
```

---

## Upgrading

```bash
./deploy/manage.sh update
```

Pulls, rebuilds, restarts, and lets the entrypoint reconcile the schema. New
migrations apply on boot, so there is no separate step.

To apply a migration without a restart:

```bash
./deploy/manage.sh migrate
```

---

## Backups

What is worth backing up is the **database**. The workspace volume is a cache —
re-run `retrieve` and it comes back.

```bash
./deploy/manage.sh backup            # -> ./backups/sfcleanup-<timestamp>.sql.gz
./deploy/manage.sh restore backups/sfcleanup-20260101-120000.sql.gz
```

Nightly, via the deploying user's crontab:

```
0 2 * * * cd /home/USER/sfc && ./deploy/manage.sh backup && find backups -name '*.sql.gz' -mtime +14 -delete
```

Restore is destructive and asks you to type the database name to confirm.

---

## Troubleshooting

| Symptom | Cause and fix |
|---|---|
| `deploy.sh: these are empty in .env` | Fill the named keys. It fails by name rather than letting the stack come up half-configured |
| Backend never becomes healthy | `./deploy/manage.sh logs backend`. Almost always a bad `SF_*` value or an unreachable LLM gateway |
| `relation "org_capabilities" does not exist` | Migrations did not run. Check the entrypoint output for `schema ready: 24 tables`; force with `./deploy/manage.sh migrate` |
| `unsupported_grant_type` | "Enable Client Credentials Flow" is unchecked on the Connected App |
| `invalid_grant` | No Run As user under Manage → Edit Policies |
| `invalid_client` | Wrong key/secret, or the app has not propagated yet (~10 min) |
| Pipeline appears frozen, then jumps to done | A proxy in front is buffering SSE. See [HTTPS](#https) |
| `NotImplementedError` at `source.retrieve` | Only happens if uvicorn is started with `--reload`. The entrypoint never does; do not add it |
| Retrieve fails, `sf: not found` | The image build skipped the CLI. Rebuild with `./deploy/deploy.sh` and check for `sf CLI` in the boot log |
| 429 from the LLM gateway | Token quota for the window. The run stops cleanly and keeps partial results; the UI shows when the quota resets |
| Port 80 already in use | Set `HTTP_PORT=8080` in `.env` and redeploy |
| Disk filling up | `./deploy/manage.sh status` shows workspace size. Old run reports live under `/app/.cache/reports` |

---

## Security notes

- **`.env` holds live credentials.** `chmod 600`, and never commit it. It is in
  `.gitignore` and `.dockerignore`, so it is not baked into any image — the
  backend reads it at runtime through compose's `env_file`.
- **Postgres and Redis are not published.** They are reachable only from the
  compose network.
- **The container runs as a non-root user** (uid 10001).
- **The API is read-only apart from starting and cancelling a run**, so a stray
  request cannot spend the org's API budget.
- **The tool generates `destructiveChanges.xml` but never deploys it.** The
  delete rehearsal is validate-only (`--dry-run`, `checkOnly`) and cannot delete
  anything.
- **Credential-shaped strings are redacted** from source before any of it
  reaches an LLM.
- **The UI and API require a sign-in.** Sessions are signed cookies; passwords
  are salted scrypt hashes. A wrong password and an unknown address return the
  same message, so the login page cannot be used to enumerate accounts.
- **Roles are read from the database on every request, not from the cookie.**
  Demoting, disabling or deleting an account takes effect on that account's next
  request rather than whenever its token expires.
- **The login is a front door, not a perimeter.** It runs over plain HTTP unless
  you put TLS in front of it — see [HTTPS](#https) — and until you do, passwords
  and session cookies cross the network in the clear. Keep the stack on a VPN or
  behind a TLS-terminating proxy before exposing it beyond a trusted network.
