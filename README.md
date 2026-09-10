# Salesforce Org Cleanup Analyzer

Finds unused metadata in any Salesforce org — Custom Fields, Custom Objects, Apex,
and the automation, layout, reporting and access components around them — and
explains *why* it thinks each one is unused, showing every source it searched,
**including the ones that came back empty**.

The governing rule: **a false "unused" breaks production, a false "needs review"
costs five minutes.** Every ambiguity resolves toward needs-review.

Nothing about a particular org is baked into the code. Edition, API allowance,
Event Monitoring availability, Dependency API coverage and endpoint routing are
all discovered per org and cached. See [DECISIONS.md](DECISIONS.md) for why
things are built the way they are.

---

## Contents

- [Prerequisites](#prerequisites)
- [Setup on a new machine](#setup-on-a-new-machine) — the ordered path, start here
- [How the database gets populated](#how-the-database-gets-populated) — there is no seed fixture
- [Running it day to day](#running-it-day-to-day)
- [Script reference](#script-reference)
- [Verification](#verification)
- [Configuration reference](#configuration-reference)
- [API](#api)
- [Troubleshooting](#troubleshooting)
- [Project layout](#project-layout)

---

## Prerequisites

| Tool | Version used | Why |
|---|---|---|
| Docker Desktop | any current | Postgres 16 + Redis 7.4 |
| Python | 3.11+ (3.14.5 in use) | Backend |
| [`uv`](https://docs.astral.sh/uv/) | 0.11+ | Fast venv + resolver. `pip` works too — see step 3 |
| Node.js | 20+ (22.22 in use) | Frontend, and the `sf` CLI |
| Salesforce CLI (`sf`) | 2.146+ | Metadata retrieve and delete rehearsal |
| Java 21 | — | **Optional.** Only for the Graph Engine stage, off by default |

Two host ports must be free: **5173** (frontend) and **8000** (backend). Docker
publishes Postgres on **5433** and Redis on **6380** — deliberately shifted off
5432/6379 so they cannot collide with a local database service.

> **Windows paths.** Every command below shows the Windows venv path
> (`.venv/Scripts/python.exe`). On macOS and Linux that is `.venv/bin/python`.
> Nothing else differs.

---

## Setup on a new machine

Ten steps, in order. Steps 1–5 are local plumbing; 6–8 connect a Salesforce org;
9–10 produce the first results.

### 1. Start the infrastructure

```bash
docker compose up -d postgres redis
```

`backend/db/schema.sql` is mounted into the container's init directory, so the
**19 base tables apply automatically on first boot only** — that is, only when
the `pgdata` volume is empty. Confirm both containers are healthy:

```bash
docker compose ps
```

### 2. Apply the migrations

**Do not skip this.** Compose only auto-applies `schema.sql`; the three
migrations under `backend/db/migrations/` are *not* included, and the backend
will fail at the connect stage without them (it needs the `org_capabilities`
table to cache the org probe).

```bash
for f in backend/db/migrations/*.sql; do docker exec -i sfc-postgres psql -U postgres -d sfcleanup < "$f"; done
```

PowerShell:

```powershell
Get-ChildItem backend\db\migrations\*.sql | Sort-Object Name | ForEach-Object { Get-Content $_.FullName | docker exec -i sfc-postgres psql -U postgres -d sfcleanup }
```

They are idempotent (`IF NOT EXISTS` / `ADD VALUE IF NOT EXISTS`), so re-running
is safe. You should end with **25 tables**:

```bash
docker exec sfc-postgres psql -U postgres -d sfcleanup -c "\dt"
```

| Migration | Adds |
|---|---|
| `002_org_capabilities.sql` | `org_capabilities` table — the per-org probe cache |
| `003_removal_prerequisites.sql` | `classifications` columns for removal readiness |
| `004_standard_object_type.sql` | `StandardObject` to the `component_type` enum |
| `005_agent_chat.sql` | Chat threads, messages, tool trace, and agent findings |
| `006_auth.sql` | `app_user` — sign-in accounts and roles |
| `007_ui_component_types.sql` | `LightningComponentBundle` and `AuraDefinitionBundle` on `component_type` |

### 3. Install backend dependencies

```bash
cd backend
uv venv
uv pip install -e ".[anthropic,dev]"
```

Swap the extra for your LLM provider: `openai`, `google`, or omit it entirely if
you set `LLM_ENABLED=false`. Without `uv`:

```bash
python -m venv .venv && .venv/Scripts/python.exe -m pip install -e ".[anthropic,dev]"
```

### 4. Install frontend dependencies

```bash
npm --prefix frontend install
```

### 5. Configure

```bash
cp .env.example .env
```

`.env` is gitignored. Fill in the values marked `<<<` in the file. The minimum to
get running:

| Variable | Notes |
|---|---|
| `AUTH_SUPERUSER_EMAIL` | Your sign-in address. The account is created at startup |
| `AUTH_SUPERUSER_PASSWORD` | At least 12 characters. Without it nobody can sign in |
| `AUTH_SECRET_KEY` | `openssl rand -hex 32`. Optional locally — leave it empty and every backend restart signs you out |
| `SF_CLIENT_ID` | Consumer Key from the Connected App (step 6) |
| `SF_CLIENT_SECRET` | Consumer Secret |
| `SF_INSTANCE_URL` | Your **My Domain** host, e.g. `https://acme.my.salesforce.com`. Client-credentials tokens are never issued from `login.salesforce.com` |
| `SF_CLI_PATH` | Absolute path to `sf`. It is frequently not on PATH for child processes — on Windows this is typically `C:\Users\<you>\AppData\Roaming\npm\sf.cmd` |
| `ANTHROPIC_API_KEY` | Or the key matching your `LLM_PROVIDER` |
| `WORKSPACE_DIR` | **Use a relative path**, e.g. `./.cache/workspace`. See the warning below |

> **`WORKSPACE_DIR` must not be an absolute POSIX path on Windows.** A value of
> `/workspace` resolves to the drive root, and the retrieve stage then writes
> hundreds of files to `C:\`. Relative values are resolved against the repo root
> by `Settings.workspace`, which is what you want. The committed
> `.env.example` uses `./.cache/workspace`.

Everything else in `.env.example` has a working default, and each block explains
what it does and why the value is what it is.

### 6. Create a Connected App in the target org

Setup → App Manager → New Connected App:

1. **Enable OAuth Settings**, scopes: `api`, `refresh_token`, `offline_access`
2. **Enable Client Credentials Flow** — without this you get `unsupported_grant_type`
3. Save, then **Manage → Edit Policies → Client Credentials Flow → assign a Run
   As user** — without this you get `invalid_grant`
4. Copy the **Consumer Key** and **Consumer Secret** into `.env`

Allow ~10 minutes for a new app to propagate before the first token request.

> **Choose the Run As user carefully.** The analysis sees exactly what that user
> sees, and anything invisible to it is indistinguishable from something
> genuinely unused. A System Administrator is strongly recommended; step 8 warns
> you if it is not one.

### 7. Register the org

**One org** — the `SF_*` values in `.env` from step 5 are enough. Skip to step 8.

**Several orgs** — copy `orgs.example.json` to `orgs.json` (gitignored) and add
an entry per org, then leave the `SF_*` values in `.env` blank. When more than one
org is configured every command requires an alias; the tool refuses to guess,
because analysing the wrong org would propose deletions against the wrong system.

```bash
cp orgs.example.json orgs.json
```

### 8. Run org setup

One idempotent command does the rest — re-run it any time something looks wrong
or the org's configuration changes.

```bash
cd backend && .venv/Scripts/python.exe scripts/setup_org.py
```

```bash
.venv/Scripts/python.exe scripts/setup_org.py acme-prod          # a named org
.venv/Scripts/python.exe scripts/setup_org.py --all              # every org in orgs.json
.venv/Scripts/python.exe scripts/setup_org.py acme-prod --full   # also retrieve metadata
```

It checks the database, authenticates, discovers the org's capabilities, runs the
inventory, and prints either `READY` or a list of blocking problems with the fix
for each. Roughly **60 API calls** without `--full`.

**Read the coverage limitations it prints at the end.** They are not errors —
they are the honest bounds of what can be proven in *that* org, and they appear
verbatim in the final report. An org without Event Monitoring cannot observe
external API traffic, so a component called only by an integration can never be
shown to be unused there.

Two warnings worth acting on rather than ignoring:

- **"API user is NOT an administrator"** — coverage is bounded by that user.
- **"Report/Dashboard: none found"** — if the org genuinely has them, retrieval is
  broken, and every field referenced only by a report will look unused.

### 9. Retrieve the metadata

```bash
cd backend && .venv/Scripts/python.exe scripts/run_retrieve.py
```

Pulls the org's metadata to `WORKSPACE_DIR` (~700 files for a small org) so
reference searching afterwards costs zero API calls. Already done if you passed
`--full` in step 8.

### 10. Produce the first results

Start both processes (see [Running it day to day](#running-it-day-to-day)), open
**http://localhost:5173**, and start a run from the Pipeline view. All ten stages
execute in-process with live progress over SSE.

Or from the CLI, if you only want collectors and verdicts against metadata you
have already retrieved:

```bash
cd backend && .venv/Scripts/python.exe scripts/run_analysis.py
```

---

## How the database gets populated

**There is no seed fixture, and no demo dataset.** Every row is derived from a
real org, because seeded verdicts would be indistinguishable from measured ones
in the UI — which is the one thing this tool must never blur.

So "populating the database" means four distinct things, in order:

| # | What | Produced by | When |
|---|---|---|---|
| 1 | **Schema** — 19 base tables | `backend/db/schema.sql`, auto-applied by compose | First boot of an empty `pgdata` volume |
| 2 | **Migrations** — 1 more table, columns, enum value | `backend/db/migrations/*.sql`, applied **by hand** | Setup step 2 |
| 3 | **Org capabilities** — one cached row per org | `scripts/probe_org.py`, or step 8 | Once per org, ~46 API calls |
| 4 | **Runs, components, evidence, verdicts** | The pipeline, from the UI or `run_analysis.py` | Every analysis run |

To start completely clean, destroy the volume and repeat steps 1–2:

```bash
docker compose down -v && docker compose up -d postgres redis
```

`docker compose down` **without** `-v` keeps the data. Note that because
`schema.sql` only runs on an empty volume, editing it has no effect on an
existing database — write a migration instead.

---

## Running it day to day

Two processes. Backend first:

```bash
cd backend && .venv/Scripts/python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

On Windows you can instead double-click `backend/run-backend.cmd`, which runs
the same command in its own console so it survives the shell that started it.

Then the frontend:

```bash
npm --prefix frontend run dev
```

Open **http://localhost:5173**. Vite proxies `/api` and `/health` to
`127.0.0.1:8000`, so the browser talks to a single origin and there is no CORS to
configure and no API base URL in the client.

> **Do not add `--reload`.** uvicorn sets `use_subprocess=True` whenever reload
> (or `--workers > 1`) is on, and that makes it select a `SelectorEventLoop`.
> On Windows that loop cannot spawn subprocesses *at all* — so the retrieve
> stage, which shells out to the `sf` CLI, dies with a bare
> `NotImplementedError` and the whole pipeline stops at `source.retrieve`.
> Without reload, uvicorn uses a `ProactorEventLoop` and subprocesses work.
> The cost is that a backend edit needs a manual restart.

### The views

| View | What it answers |
|---|---|
| **Overview** | Verdict counts, coverage state, where to start |
| **Pipeline** | Live stage-by-stage progress. Start a run here. Survives a reload — it reconnects to the active run and replays what it missed |
| **Components** | Filter by verdict, click any component for its full evidence trail: every collector, including the ones that found nothing, with the literal query each used |
| **Dependencies** | Interactive graph. Shape per component type, colour per verdict, five layouts. Click a node to isolate its neighbourhood; hover for type, verdict and why |
| **Report** | Export XLSX, Markdown, JSON, and a `destructiveChanges.xml` |
| **How it works** | Method, architecture and limits, with diagrams |
| **Access** | Admins only. Add users, set roles, disable or delete accounts |

`GET /health` reports database connectivity **and** whether the schema is
applied — a probe that only checks the socket returns green against an empty
database.

---

## Script reference

All from `backend/`, all with the venv Python. An optional trailing `[alias]`
selects the org when several are configured.

| Script | Does | Cost |
|---|---|---|
| `scripts/setup_org.py [alias] [--all] [--full]` | Full onboarding: DB check, auth, capability probe, inventory, readiness report | ~60 calls |
| `scripts/probe_org.py [alias] [--deep]` | Capability discovery only: edition, limits, features, routing | ~46 calls |
| `scripts/run_inventory.py [alias]` | What exists and is in scope, plus the alias table | ~20 calls |
| `scripts/run_retrieve.py [alias]` | Pull metadata to `WORKSPACE_DIR` | ~10 calls |
| `scripts/run_index.py [alias]` | Split code from comments, harvest literals, resolve mentions | free |
| `scripts/run_analysis.py [alias]` | All collectors, then classify. Produces verdicts | varies |
| `scripts/run_graph.py [alias]` | Rebuild the reachability graph | free |
| `scripts/verify_client.py` | Auth, governor, batching, field-population probe | ~13 calls |
| `scripts/verify_routing.py` | Every routing-registry entry against this org | ~40 calls |

`run_*` scripts and the UI execute **the same** pipeline modules, so CLI and UI
results cannot drift apart.

---

## Verification

Run these after connecting a new org, or whenever something looks wrong. Each is
read-only and safe.

```bash
cd backend
.venv/Scripts/python.exe scripts/verify_client.py
.venv/Scripts/python.exe scripts/verify_routing.py
```

Schema invariants — gapless event sequencing, cascade behaviour, the
one-active-run-per-org constraint. Everything rolls back at the end, so it is
safe against a live database:

```bash
docker exec -i sfc-postgres psql -U postgres -d sfcleanup < backend/db/smoke_test.sql
```

`verify_routing.py` deserves special attention on a new org. The registry in
`app/salesforce/routing.py` is a *claim* about which endpoint serves which
object. A wrong entry is not cosmetic: a mis-routed query returns `INVALID_TYPE`,
which reads downstream as "no references found" and can turn a live component
into a deletion candidate. Three entries were wrong when first probed.

---

## Configuration reference

Full documentation is inline in `.env.example`. The values most likely to need
changing on a new machine:

| Variable | Default | Note |
|---|---|---|
| `SF_CLI_PATH` | `sf` | Set the absolute path — `sf` is often not on PATH for child processes |
| `WORKSPACE_DIR` | `./.cache/workspace` | Keep it relative; see the warning in step 5 |
| `LLM_MODEL` | `claude-opus-5` | Must be a model your gateway actually allows. A corporate gateway may permit only a subset |
| `LLM_BASE_URL` | unset | Takes precedence over `ANTHROPIC_BASE_URL`. Prefer it — an `ANTHROPIC_BASE_URL` exported in your shell overrides the one in `.env`, which is confusing to debug |
| `LLM_ENABLED` | `true` | `false` skips narration entirely. Verdicts are unaffected — narration is enrichment, and never classifies |
| `SF_API_SAFETY_FLOOR` | `1500` | Calls never consumed, so the org stays usable by humans mid-run |
| `ANALYSIS_ENABLE_DELETE_REHEARSAL` | `true` | Asks Salesforce whether a delete is permitted. Validate-only; it cannot delete anything |
| `ANALYSIS_RECENT_CHANGE_DAYS` | `90` | Recently changed components are capped at needs-review — half-built features look exactly like dead ones |
| `ANALYSIS_ENABLE_SFGE` | `false` | Graph Engine is Developer Preview and OOM-prone. Corroboration only, never primary |

### Safety properties worth knowing before you point this at production

- The tool **generates** `destructiveChanges.xml` but never deploys it.
- The delete rehearsal is **validate-only** (`--dry-run`, `checkOnly: true`) and
  cannot delete anything.
- Credential-shaped strings are redacted from source before any of it is sent to
  an LLM.
- The HTTP API is read-only apart from starting and cancelling a run, so a stray
  browser request cannot spend an org's API budget.
- `.env`, `orgs.json` and any private key files are gitignored. Keys are
  referenced by path, never pasted into variable values.

---

## API

```
GET  /health                                   DB connectivity + schema applied

POST /api/auth/login                           sets the session cookie
POST /api/auth/logout
GET  /api/auth/me                              200 with authenticated:false when signed out
POST /api/auth/password                        change your own
GET  /api/auth/users                           admin only -- list, create, patch, delete
GET  /api/orgs                                 configured orgs
POST /api/runs                                 start a run
GET  /api/runs                                 run history
GET  /api/runs/active                          the currently running run, if any
POST /api/runs/{id}/cancel
GET  /api/runs/{id}/snapshot                   full current state, for a reconnect
GET  /api/runs/{id}/events                     SSE stream, honours Last-Event-ID
GET  /api/runs/{id}/summary                    verdict counts, collectors, caveats
GET  /api/runs/{id}/components?verdict=UNUSED  filterable results
GET  /api/runs/{id}/components/{cid}           full evidence trail
GET  /api/runs/{id}/graph?max_nodes=400        cytoscape nodes + edges
GET  /api/runs/{id}/graph/path/{cid}           why this is reachable, as a chain
POST /api/runs/{id}/report                     build the artifacts
GET  /api/runs/{id}/report/{kind}              download xlsx | md | json | xml
```

Everything except `/health` and the login endpoints requires a session cookie,
so `curl` needs `-b`/`-c`:

```bash
curl -c /tmp/j -X POST localhost:8000/api/auth/login -H 'Content-Type: application/json'   -d '{"email":"you@example.com","password":"..."}'
curl -b /tmp/j localhost:8000/api/runs
```

Interactive docs at **http://127.0.0.1:8000/docs**.

---

## Troubleshooting

| Symptom | Cause and fix |
|---|---|
| `relation "org_capabilities" does not exist` | Migrations not applied — setup step 2 |
| `unsupported_grant_type` | "Enable Client Credentials Flow" is not checked on the Connected App |
| `invalid_grant` | No Run As user assigned under Manage → Edit Policies |
| `invalid_client` | Wrong key/secret, or the app has not propagated yet (~10 min) |
| `[Errno 10048] error while attempting to bind` | Port 8000 already has a backend. Find it with `netstat -ano \| grep :8000` and stop it, or use `--port 8001` |
| `NotImplementedError` at `source.retrieve` | Backend started with `--reload`. That forces a Windows `SelectorEventLoop`, which cannot spawn the `sf` CLI. Restart without it |
| Backend edits have no effect | Expected — reload is deliberately off, restart by hand |
| Hundreds of files appear at `C:\` | `WORKSPACE_DIR` is an absolute POSIX path; make it relative |
| `duplicate key ... one_active_run_per_org` | A previous run crashed while RUNNING. Stale runs are reaped automatically, so this means one is genuinely active — cancel it from the Pipeline view |
| Error mentioning `INVALID_TYPE` | A query aimed at the wrong endpoint. Deliberately fatal: an unnoticed one reads as "zero references found". Run `verify_routing.py` |
| `REQUEST_LIMIT_EXCEEDED` | Org API budget spent. The run parks with checkpoints intact rather than failing; resume once the rolling 24h window frees calls |
| Narration stage reports DEGRADED | Provider unreachable, or `LLM_MODEL` not allowed by your gateway. Verdicts are unaffected |
| `llm_base_url_unsupported` in the log | The provider client rejected `base_url` and retried **without** it — traffic went to the public endpoint. Fix the provider/gateway combination rather than ignoring it |
| Reports show 0 reports/dashboards for an org that has them | Retrieval is enumerating folders incorrectly; fields referenced only by a report will look unused |

---

## Project layout

```
backend/
  app/
    config.py               typed settings from .env; resolves relative paths
    api/                    routes, SSE endpoint
    db/session.py           async engine + a separate un-pooled LISTEN connection
    orchestration/
      runner.py             the 10 stages and their order
      stages.py             thin wrappers over the pipeline modules
      events.py             single-writer event bus, gapless sequencing
    salesforce/
      connections.py        multi-org registry
      auth.py               token providers (Python owns auth; the CLI gets a token)
      governor.py           rate-limit metering, concurrency, circuit breaker
      client.py             REST/Tooling client, composite batching
      routing.py            which endpoint serves which object — fatal on mismatch
      capabilities.py       per-org discovery and caching
    pipeline/
      aliases.py            the many textual forms one component appears under
      inventory.py          what exists and is in scope
      retrieve.py           sf CLI driver: retrieve, and validate-only deploy
      source_text.py        splits Apex into code / comments / literals
      indexer.py            resolves mentions against the alias table
      collectors.py         C10-C60 evidence collectors
      collectors_data.py    C80 config data, C90 delete rehearsal
      graph.py              entry points and reachability
      classify.py           rules R0-R9; first match wins
      narrate.py            LLM summaries, with secrets redacted
    report/builder.py       XLSX, Markdown, JSON, destructiveChanges.xml
    llm/client.py           provider-agnostic via init_chat_model
  db/
    schema.sql              19 tables; negative evidence and gaps are first-class
    migrations/             incremental changes — apply by hand, see step 2
    smoke_test.sql          invariant assertions
    bootstrap.sql           optional: local Postgres instead of Docker
  scripts/                  probe / verify / run entrypoints
frontend/
  src/
    lib/stream.ts           framework-free SSE client, ring buffer, rAF batching
    lib/theme.ts            five themes; unused is never red or green
    components/             Dashboard, Pipeline, Graph, DetailPanel, Docs, Nav
deck/
  build.js                  the product presentation generator
docker-compose.yml          Postgres 16 + Redis 7.4
```

---

## Current state

Working end to end: connect and discover, inventory, retrieve, index, nine
evidence collectors, reachability graph, rule-based classification, delete
rehearsal against Salesforce, LLM narration, report export, and a React UI that
runs the whole pipeline with live progress and survives a reload.

Known limits, stated plainly because they bound what the output means:

- `MetadataComponentDependency` is Beta, caps at 2000 rows, and is blind to
  Reports, Dashboards, Validation Rules, Workflows, Email Templates, Approval
  Processes and Custom Report Types. Local parsing is the primary signal; the
  Dependency API corroborates.
- `ApexClass.SymbolTable.references[]` is always empty — it is scoped to one
  compilation unit and is not a call graph.
- Dynamic Apex that builds a field or object name at runtime cannot be resolved
  statically. Every object such code touches is held at needs-review.
- Event Monitoring is licensed separately. Without it, external API traffic is
  unobservable, so a component called only by an integration can never be proven
  unused.
- Remediation — the removal agent, refactoring and PR raising described in the
  deck — is designed but **not implemented**. The tool reports; a human deletes.
