# Decision log

Every load-bearing decision, with the reasoning and — where it exists — the
evidence. Entries marked **MEASURED** were verified against the live `bx-dev`
org, not taken from documentation. Three of them contradict documentation, which
is why they are recorded rather than assumed.

The governing principle behind almost everything below:

> A false `UNUSED` breaks production. A false `NEEDS_REVIEW` costs five minutes
> of human attention. Every ambiguity resolves toward `NEEDS_REVIEW`.

---

## Measured facts that overturned an assumption

### M1 — `composite/batch` does **not** cost one API call
**Claim in most sources:** a composite call counts as a single request against
the daily limit.
**Measured:** false. Cost scales with subrequest count at roughly **0.3–0.5 API
calls per subrequest**, so a full 25-subrequest batch compresses about **3×, not
25×**.

Method: held batch size constant and varied invocation count (15 singles vs 15
batches of 7 → a 53-call difference where "counts as 1" predicts 0), then held
invocation count constant and varied batch size (2 vs 20 → +0.31 calls per extra
subrequest). Repeated with 20 **distinct** sObject queries to rule out
server-side caching of repeated SOQL; per-subrequest cost did not rise.

**Consequence:** `SF_COMPOSITE_COMPRESSION_DEFAULT=3.0`, and the governor
re-measures per org at connect time rather than trusting the constant. Estimating
on 25× would understate a large org's cost by an order of magnitude.

### M2 — There is no 5-query sublimit on `composite/batch`
`/composite` permits only 5 queries among its 25 subrequests. `/composite/batch`
does not carry that restriction: batches of 7 and 20 distinct queries both
returned all-200. We can pack 25 queries per batch.

### M3 — `SymbolTable.references` is always empty
`ApexClass.SymbolTable` is available on the **Tooling API only** — the Data API
rejects the field outright with `INVALID_FIELD`. But across all 9 classes and 28
methods in this org, `methods[].references` and `externalReferences` are **empty**,
including for methods provably called from `ContactsResourceTest`. It is
compilation-unit-scoped, not org-wide.

**Consequence:** SymbolTable is a **method inventory** source and never a call
graph. Any design reading `references.length == 0` as "dead method" would mark
every method in the org dead. The call graph comes from offline ANTLR parsing.

### M4 — Three routing entries in our own registry were wrong
`CustomPermission`, `DuplicateRule` and `MatchingRule` read like setup metadata
but are served by the **Data API**, not Tooling. All three were wrong in the
first version of `routing.py` and were caught by `scripts/verify_routing.py`.

A further category error: `SharingRules`, `StandardValueSet`, `ReportType` and
`FlowVersionView` are not SOQL-queryable **at all** — they are Metadata API files.
The probe reported them "not present in this org", which is both wrong and wrong
in the dangerous direction.

### M5 — A Boolean field rejects `COUNT()`, and one bad field fails the whole query
`COUNT(Is_Priority__c)` returns `field ... does not support aggregate operator
COUNT`, and because aggregates are batched ~40 per SELECT, one unsupported field
discards the other 39 results.

**Consequence:** rather than maintain a list of non-aggregatable types (poorly
documented, varies, rots silently as Salesforce adds types), the client parses the
rejected field out of Salesforce's own error and retries without it, looping until
the remainder aggregates cleanly. Excluded fields fall back to a per-field
existence probe that returns `None` on failure, so an error can never be mistaken
for an empty field.

### M6 — The API budget is shared, and `/limits` cannot attribute cost
~3,250 calls were already consumed by unrelated activity before any run started.
Worse, two **consecutive** `/limits` reads differed by **−2**: the rolling 24-hour
window releases calls while other clients consume them.

**Consequence:** per-call cost cannot be derived by differencing two readings.
The governor counts its own calls for attribution and uses `Sforce-Limit-Info`
only to correct absolute position. Only large-N differential experiments are
trustworthy for measurement.

### M7 — The Dependency API is structurally incomplete
270 total rows in this org, sourced only from Layout (131), ApexClass (44), LWC
(26), Trigger (20), Flow (20), FlexiPage (17), App (9). **Zero rows** for Report,
Dashboard, ValidationRule, WorkflowRule, EmailTemplate, ApprovalProcess or
CustomReportType — despite 30 reports and 3 dashboards existing.

`COUNT()` is also rejected (`DEPENDENCY_API_UNSUPPORTED_EXCEPTION`), so truncation
against the 2,000-row cap cannot be pre-detected; it must be inferred from a shard
returning exactly 2,000.

**Consequence:** local XML parsing is primary. The Dependency API is a
cross-check that **can save a component but never condemn one** — its absence is
worth nothing.

### M8 — `EntityDefinition` silently drops unsupported WHERE predicates
`WHERE QualifiedApiName LIKE '%__c'` returned non-matching standard entities
(`Topic`, `PushTopic`, …) with no error. Never filter server-side on it: pull wide
and filter client-side.

### M9 — Event Monitoring and async telemetry are empty here
`EventLogFile` and `AsyncApexJob` both return **0 rows**. Event Monitoring is
unlicensed, and no batch or scheduled Apex has ever run.

**Consequence:** those collectors report `NOT_APPLICABLE` with a reason, which
lowers coverage. They must never report `NO_EVIDENCE_FOUND` — absence of
observability is not absence of use.

### M10 — No CLI route to an access token exists
`sf org display --json` returns `[REDACTED]`; `sf org auth show-access-token`
requires an interactive confirmation prompt that cannot be scripted. Also
`sf api request rest --body` mangles JSON quoting on Windows.

**Consequence:** Python owns authentication. The CLI is reserved for
`project retrieve` and `project deploy --dry-run`, and is *handed* a token rather
than scraped for one. We deliberately did not read the credential file off disk
to route around the confirmation prompt — it is a security control placed there
on purpose.

---

## Architecture decisions

### A1 — Multi-signal classification with per-analyzer attribution
No component is classified on one analysis. 27 collectors across 9 analyzer
families each emit their own result — **positive or negative, every time** — as
its own persisted row. Collectors are blind to each other, so agreement carries
information.

Collectors return `EVIDENCE_OF_USE` / `NO_EVIDENCE_FOUND` / `INCONCLUSIVE` /
`NOT_APPLICABLE` / `FAILED` — deliberately *not* used/unused, because one
collector is not entitled to a verdict.

### A2 — The completeness gate
`UNUSED` requires that **every required collector completed successfully**. A
collector that errored, was unavailable, or is simply missing fails the gate and
forces `NEEDS_REVIEW`. This is what makes "no usage signal missed" enforceable
rather than aspirational, and it is why `collector_run` and `evidence_gaps` must
be built *before* the first scoring rule — retrofitted negative evidence is always
incomplete.

### A3 — Verdicts are rule-driven, never score-driven
Confidence is a 0–100 number for sorting the review queue. No weighted sum may
cross the threshold into `UNUSED`; rules R0–R9 decide that alone. A single
`EVIDENCE_OF_USE` from any collector overrides every `NO_EVIDENCE_FOUND` —
finding usage is proof, not finding it is only absence.

### A4 — Dynamic Apex: detect and quarantine, don't try to resolve
No extractor can resolve a name assembled at runtime, so we don't try. Any class
using `Schema.getGlobalDescribe()`, `Database.query(`, `.get(`/`.put(`,
`Type.forName(` etc. is marked `DYNAMIC_CAPABLE`, and **every field on every
object it touches is capped at `NEEDS_REVIEW`**, with the offending file and line
reported.

Deliberately blunt. A generic `SObjectUtils.copyFields(source, target)` can touch
anything, and pretending otherwise is how tools delete production fields.

### A5 — The LLM never classifies
The rule engine decides the verdict *before* the model is called; the verdict is
passed in as read-only context. The LLM writes the business-purpose summary,
narrates the evidence chain, and may **widen** a quarantine. Invariant: it can
move a component from `UNUSED` to `NEEDS_REVIEW` and never in the other direction.
Temperature 0, structured output, prompt hash persisted.

### A6 — Delete rehearsal is the authoritative oracle
Generating `destructiveChanges.xml` and running `sf project deploy --dry-run`
makes Salesforce's own dependency checker enumerate what breaks — including
references our parsers missed. A refusal is conclusive proof of use. It carries
the highest evidence weight and can promote a component back to `USED`, never the
reverse. It is also the primary accuracy metric: any `UNUSED` that fails the
dry-run is a **confirmed false positive**.

### A7 — Routing errors are fatal, not silent
Querying `Report` via Tooling (or `ValidationRule` via Data) returns
`INVALID_TYPE`. Swallowed anywhere in the collector chain that becomes "zero
references found". `assert_routable()` raises instead, and the registry is
verified empirically by `scripts/verify_routing.py` rather than trusted.

### A8 — Hand-rolled asyncio DAG runner, not Celery/Temporal
The requirement is *"the pipeline is the UI"*: every state transition must be
visible, streamable and replayable, which means writing a durable event log and a
per-task state table regardless of engine. Once those exist the scheduling is
~500 lines of `TaskGroup` + `Semaphore`, and every alternative becomes a layer to
fight through to reach state we already own. Kept behind an `Orchestrator`
interface so a later port is mechanical.

### A9 — Postgres, not SQLite
Concurrent writers (API + worker + N SSE readers) would serialize behind SQLite's
single-writer lock, and multiple concurrent viewers is this application's entire
premise. Postgres also gives `LISTEN/NOTIFY`, JSONB + GIN, and partial indexes.

### A10 — A single event-writer coroutine per run
`BIGSERIAL` under concurrency produces gaps and out-of-order commits: writer A
takes seq 100, writer B takes 101 and commits first, a reader polling
`seq > last_seen` sees 101, advances, and **permanently misses 100**. Late-joiner
replay would be silently lossy. One writer allocates `runs.last_seq` in the same
transaction as the insert, so seq is gapless by construction.

### A11 — Snapshot + delta, not replay-from-zero
A late joiner fetches a materialised snapshot with `as_of_seq` and tails from
there. Durable events are capped at ~2,000 per run (lifecycle transitions, shard
milestones, errors, budget reconciliations); per-component progress is derived
from domain tables on demand rather than stored as events.

### A12 — Retrieve once, analyse offline
Querying per component is `O(components)` API calls and exhausts any budget.
Instead one bulk metadata retrieve, then ~90% of analysis runs on the filesystem.
Multi-aggregate SOQL makes field population `O(objects)` rather than `O(fields)`.
A full run on this org plans out at **~63 calls**.

---

## Constraint-driven decisions

### C1 — 15-second client timeout on every ordinary call
The 5-concurrent ceiling applies only to requests exceeding **20 seconds**. A call
that can never exceed 15s can never enter that class, so short calls draw from a
generous semaphore and only declared-long operations (retrieve, deploy, bulk)
contend for the scarce slots. One of the five is deliberately left free so a human
using the org during a run is not locked out.

### C2 — No retry on `REQUEST_LIMIT_EXCEEDED`
Retrying is what converts a soft limit into a hard block. The governor opens a
shared circuit breaker so all in-flight calls see it at once — rather than N
workers each burning a doomed request discovering exhaustion independently — and
the run parks in `AWAITING_BUDGET` with checkpoints intact.

### C3 — Bulk API is the wrong tool for the main analysis
It supports neither `COUNT()` nor `GROUP BY` (nor `LIMIT`/`ORDER BY`/`TYPEOF`),
and the field-population probe is entirely aggregate SOQL. Using it would force
one existence query per field and reintroduce the `O(fields)` cost explosion.
Bulk belongs in exactly one place: exporting raw records for the rollback
snapshot, where query jobs are `O(1)` calls and do not consume the 15,000-batch
allocation.

### C4 — Postgres LISTEN/NOTIFY instead of Redis Streams
Originally forced: Docker Desktop was blocked by a Bounteous org sign-in policy
(enforced via `registry.json`) — even `docker images` failed. **Docker has since
been unblocked and Redis 7.4 is now running**, but LISTEN/NOTIFY was kept.

Retained deliberately rather than by inertia: the path is built and tested, and
the `events` table is the durable source of truth either way, so notifications
need only be a wake-up carrying `run_id` and `seq`. Postgres caps a notification
at 8,000 bytes and an oversized one raises at **commit** time — which would abort
a run for the sake of a UI update — so listeners select the rows they are missing
and a dropped notification costs nothing.

The tradeoff being accepted: LISTEN/NOTIFY needs one listening connection per SSE
viewer, whereas Redis Streams lets each connection hold an independent cursor with
no per-viewer server state. That matters at many concurrent viewers, not at POC
scale. Redis is running and configured, so switching is contained if it becomes
the bottleneck.

### C8 — Docker Postgres 16, with local PG15 retained as fallback
Once Docker was unblocked, the app moved to containerised **Postgres 16.14**:
disposable, rebuildable in seconds, schema auto-applied on first boot, and it
matches how this would run on a server. All six schema invariants were re-verified
against PG16, not assumed to carry over from PG15.

Host ports are shifted to **5433/6380** so the containers do not collide with the
local PostgreSQL 15 still listening on 5432. That local instance is kept as a
fallback and its bootstrap script retained.

One wrinkle worth recording: the container initialised as role `postgres` rather
than `app`, because compose substitutes `POSTGRES_USER` from `.env` and the
local-Postgres switch had removed the `app` defaults. Harmless, but it is why the
connection string names `postgres`.

### C9 — Delete rehearsal is enabled against the live org
Authorised explicitly. It runs `sf project deploy --dry-run` with a generated
`destructiveChanges.xml`, which is **validate-only and cannot delete anything** —
Salesforce simply reports whether the delete would succeed and, if not, what
still references the component.

Enabled because it is the only signal that is *server-validated rather than
inferred*, and it is also the system's own accuracy metric: any component we call
`UNUSED` that fails the dry-run is a confirmed false positive. Costs ~10 API
calls, batched ~50 components at a time so one blocker does not invalidate the
whole set.

### C10 — LLM summaries enabled
Authorised. ~100 components in scope makes the cost negligible, and the
plain-English summary is what makes the report usable by someone who did not write
the original code. This does not weaken A5: the LLM still never decides
used-versus-unused, it only explains a verdict the rules already reached.

### C5 — Budget telemetry throttled by time, not call count
Count-based sampling (every 25th call) is wrong for a live meter: a short run
emits nothing and the gauge appears frozen, while a burst emits faster than anyone
can read. Now throttled to ~2 Hz, with the first tick and every breaker state
change always emitted — those are exactly the moments a user is watching for.

### C6 — Client credentials now, JWT later
Client credentials needs only a key and secret, but requires *"Enable Client
Credentials Flow"* plus an assigned *Run As* user, and issues tokens only from the
My Domain host. It leaves a long-lived secret at rest, which is why JWT bearer is
the intended path for deployment. Both sit behind a `TokenProvider` protocol, so
switching is a config change.

### C7 — The Run As identity is recorded on every run
The analysis can only see what its user sees, and a restricted user narrows
coverage in a way **indistinguishable from components genuinely being unused**.
Verified for this org: `reactapi.integration@poc.com` is a System Administrator
whose counts match ground truth exactly, so coverage is not narrowed here — but
the check must run every time, not once.

---

---

## Retrieval and indexing — findings from building Stage S11/S20

### M11 — Folder-scoped types need TWO levels of enumeration
Listing `Report` directly returns **nothing**: reports live inside folders, so
you must list `ReportFolder` first and then list reports *within each folder*.
The first implementation found 0 reports and reported success. Correct behaviour
finds **30 reports across 8 folders**, 3 dashboards, and 28 email templates.

Dangerous precisely because it is silent: retrieving zero reports makes every
field referenced only by a report look unused. Same applies to `Dashboard`,
`EmailTemplate` and `Document` (`DashboardFolder`, `EmailFolder`, `DocumentFolder`).

### M12 — `ReportingSnapshot` does not exist; it is `AnalyticSnapshot`
And an unknown type name fails the **entire manifest chunk**, not just that type
— one wrong name silently cost 11 other types including Flow, Workflow and
ApprovalProcess. Chunks are now self-healing: the rejected type is parsed out of
the error and dropped, then the chunk retries (same approach as M5).

### M13 — `--target-metadata-dir` writes zips, not a source tree
Each chunk produces `unpackaged.zip`. The retrieve reports success while the
workspace looks nearly empty. Extraction is now automatic.

### M14 — A POSIX `WORKSPACE_DIR` resolves to the drive root on Windows
`/workspace` is correct inside a container and silently scatters a retrieved org
across `C:\` on a Windows host. Relative paths now resolve against the repo root.

### A13 — Permission metadata can never exceed Tier C
Every field receives FLS entries when it is created, so a Profile or Permission
Set naming a field says only that someone *could* see it — never that anything
uses it. Counting FLS as proof of use would mark essentially every field USED and
make the analysis worthless.

Measured effect on this org: capping `Profile`, `PermissionSet`,
`PermissionSetGroup`, `CustomApplication`, `CustomTab` and `SharingRules` at
Tier C moved **206 edges** out of "proof of use" (Tier A fell 739 → 533).

Layouts are deliberately **not** in that list: layout presence is genuinely
binding, since deleting the field breaks the deploy. That it is nonetheless weak
evidence of *business* use is handled separately by the `LAYOUT_ONLY` tag.

### A14 — Three extraction layers, unioned
`token_sweep` (word-boundary tokens), `string_literal` (every literal, plus
tokens *inside* dynamic SOQL strings), and `merge_field` (`{!Obj.Field__c}`,
split into segments). Results are unioned so a failure in one layer cannot delete
a reference another found. The literal layer is what catches dynamic access such
as `sObj.get('Legacy_Code__c')` and Aura's `component.get("c.method")` with no
semantic understanding at all — 33 Tier-A edges on this org came from it, and 42
from merge fields.

---

## The definition of "used"

Agreed with the user and now encoded in the code itself:

> **Used** means the component participates in an actual business process —
> something reads it, writes it, decides on it, or shows it to someone as part of
> doing work. **Used does not mean merely present.**

Counts as business use: Apex, triggers, Flows, Workflow rules, validation rules,
formulas, reports, dashboards, list views, LWC/Aura/Visualforce, email templates,
and real data in real records.

Does **not** count, because it is placement rather than use:

| Signal | Why it is not use |
|---|---|
| Page layout / FlexiPage / compact layout | Presentation placement. Every custom field gets one the moment it is created. |
| Field-level security grants | Every field gets FLS on creation. Means someone *could* see it, not that anyone does. |
| Its own object definition file | A declaration, not a use. |
| Tab or app membership | Navigation structure. |

This distinction *is* the product. Counting presence as use marks essentially
everything USED and finds nothing — worse than useless, because it looks
authoritative. Measured on this org: treating layout presence as use produced
**0 findings**; separating the two produced **6**.

### A15 — Verdict and removal prerequisites are separate fields
Two different questions were being conflated:

1. *Is anything using this?* — the business question. Answered by `label`.
2. *What must happen before it can be deleted?* — mechanical. Answered by
   `removal_prerequisites`.

Layout presence answers only the second. Holding such fields at `NEEDS_REVIEW`
(the earlier behaviour) buried the finding and made whoever read the report
re-derive the same reasoning for every field.

Now a layout-only field with no data is `UNUSED`, carrying an explicit
**blocking** prerequisite that names the exact layouts to edit, plus an advised
FLS-hide-and-observe step. The safety property is unchanged — nothing is
presented as one-click safe, and the delete rehearsal verifies it independently
against the org — but the report can now say *"delete these six, and here is the
preparation each needs"* instead of *"nine things need review, go work out why."*

The transitive guard needed a matching fix: it would otherwise have flipped every
one of these straight back to `NEEDS_REVIEW`, since a layout *is* a reference. It
now ignores presentation metadata and self-definition, because neither is a live
consumer keeping a component alive.

---

## Portability — the solution must work against any org

Requirement: point the application at a different Salesforce org tomorrow and it
behaves correctly, with no code change.

The risk was not hardcoded strings — an audit found exactly one (`"bx-dev"` as a
run alias). The real risk was **constants measured against one Developer Edition
org and quietly treated as universal truths.** Pointed at an Enterprise org, that
code would have mis-sized the API budget by ~7×; pointed at an org with Event
Monitoring licensed, it would have ignored the strongest usage signal available.

### P1 — Capabilities are discovered per org, never assumed
`org_capabilities` caches what each org actually does: edition, live API
allowance, Event Monitoring availability, Dependency API coverage (and which
source types it really returns), field-history availability, code-coverage
availability, and endpoint-routing corrections. Populated by
`scripts/probe_org.py`, ~46 API calls, once per org.

Crucially it distinguishes **three** states, not two: *available*, *unavailable*
(collector reports `NOT_APPLICABLE`, lowering coverage), and *not yet probed*
(unknown). Collapsing the third into the second is exactly how "we never checked"
becomes "there is nothing here" becomes a deletion candidate.

### P2 — Multi-org connection registry
`orgs.json` (gitignored) holds any number of orgs; a single org can still be
configured via `.env`. `registry.get()` **refuses to guess** when several are
configured and no alias is given — silently picking the wrong org would analyse,
and propose deletions against, the wrong system.

The single-org path derives its alias from the host rather than any
developer-specific name, and the authoritative identifier is always the org id
the org itself reports.

### P3 — Routing is verified per org, and corrections are recorded
The registry in `routing.py` is a sensible default, not a universal truth: three
of its original entries were wrong (M4), and editions differ in which objects
they expose. The capability probe sweeps every registry entry against both
endpoints on the target org and stores per-org overrides, so a legitimately
different org produces a recorded correction rather than a hard failure.

### P4 — Composite compression is deliberately NOT measured per org
Attempting it produced **9.0×** on one run and **0.36×** on the next against the
same org — the latter implying composite is worse than individual calls, which is
impossible. The API counter moves under other clients, the rolling window
releases calls mid-measurement, and the probe's own traffic pollutes the sample.
A trustworthy figure cost ~800 calls of differential sampling during development.

So the default is the conservative **1:1** (one call per subrequest), with an
opt-in `--deep` measurement that is accepted only if samples are both plausible
and stable within 2×. This matters less than it first appears: the ratio only
feeds the pre-flight *estimate*, while live accounting reconciles against the
`Sforce-Limit-Info` header on every response. Erring pessimistic makes a run look
costlier than it is; erring optimistic exhausts the org's budget mid-analysis.

### P5 — Coverage limitations are generated, not written
`coverage_caveats()` derives the report's Limitations section from what was
actually observed on that org. On an org without Event Monitoring it emits the
caveat automatically; on one with it licensed, that caveat simply does not
appear. The report therefore states *its own* blind spots per org rather than a
generic disclaimer.

### P6 — The API user's permissions are checked every run
Coverage is bounded by the Run As user. A non-admin narrows what the analysis can
see in a way indistinguishable from components genuinely being unused, so the
probe records whether that user is an administrator and raises a coverage caveat
when it is not. Verified on the development org: `run_as_is_admin = true`.

---

## Open items

| Item | Status |
|---|---|
| Project directory contains a stray space (`salesforce -project-POC`) | User will rename between sessions; every path is quoted meanwhile |
| Two `sf` installs drifted (2.146.3 npm-global vs 2.140.6 Program Files) | Pinned to the npm one via `SF_CLI_PATH`; consolidate when convenient |
| `code-analyzer` JIT plugin not installed | Not needed until the optional Graph Engine stage |
| JWT bearer flow | Not implemented; `build_token_provider` raises a clear error |
| ~~Docker Desktop sign-in~~ | **Resolved** — unblocked; Postgres 16 + Redis 7.4 running |
| Redis running but unused for events | Deliberate (see C4); switch if viewer count grows |
| Python 3.14.5 rather than 3.11 | `uv` chose it; all dependencies verified working |
| `api`/`worker`/`apexd` services in compose | Defined but not yet built; only `postgres` and `redis` are up |
