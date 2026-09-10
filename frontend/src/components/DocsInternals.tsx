/**
 * Technical reference — the bottom half of "How it works".
 *
 * Separate from Docs.tsx purely for size. The split is by audience, not by
 * topic: everything above answers "should I trust this verdict?", everything
 * here answers "what exactly did it do to get there?" — which API was called,
 * with which query, and what the result was allowed to mean.
 *
 * Every claim here is drawn from the code that implements it. If a query, flag
 * or file name below drifts from the backend, the documentation is the thing
 * that is wrong.
 */

import { DocSection } from './DocSection'

const C = {
  used: 'var(--used)', unused: 'var(--unused)', review: 'var(--review)',
  scope: 'var(--scope)', accent: 'var(--accent)', ai: 'var(--ai)',
  line: 'var(--border-strong)', text: 'var(--text-dim)', bright: 'var(--text)',
  surface: 'var(--surface)', sunken: 'var(--sunken)', soft: 'var(--accent-soft)',
}

/* ---------------------------------------------------------------- helpers */

const box = (x: number, y: number, w: number, h: number,
             fill = C.sunken, stroke = C.line, dash?: string) => (
  <rect x={x} y={y} width={w} height={h} rx={6} fill={fill} stroke={stroke}
        strokeDasharray={dash} />
)

const label = (x: number, y: number, t: string, size = 10,
               fill = C.bright, anchor: 'start' | 'middle' | 'end' = 'middle',
               weight: number | string = 400) => (
  <text x={x} y={y} fontSize={size} fill={fill} textAnchor={anchor}
        fontFamily="var(--ui)" fontWeight={weight}>{t}</text>
)

const mono = (x: number, y: number, t: string, size = 9,
              fill = C.text, anchor: 'start' | 'middle' = 'middle') => (
  <text x={x} y={y} fontSize={size} fill={fill} textAnchor={anchor}
        fontFamily="var(--mono)">{t}</text>
)

const arrow = (x1: number, y1: number, x2: number, y2: number,
               stroke = C.line, dash?: string) => (
  <line x1={x1} y1={y1} x2={x2} y2={y2} stroke={stroke} strokeWidth={1.4}
        strokeDasharray={dash} markerEnd="url(#di-arrow)" />
)

const Defs = () => (
  <defs>
    <marker id="di-arrow" viewBox="0 0 10 10" refX="9" refY="5"
            markerWidth="5" markerHeight="5" orient="auto-start-reverse">
      <path d="M 0 0 L 10 5 L 0 10 z" fill={C.line} />
    </marker>
  </defs>
)

/* ------------------------------------------------------- 1. acquisition */

/**
 * How bytes get out of the org. Four surfaces, and the reason there are four
 * rather than one: no single Salesforce API exposes all of this.
 */
function AcquisitionDiagram() {
  return (
    <svg viewBox="0 0 900 330" className="diagram" role="img"
         aria-label="Four API surfaces used to read the org">
      <Defs />

      {box(20, 130, 120, 70, C.soft, C.accent)}
      {label(80, 158, 'Salesforce org', 11, C.accent, 'middle', 600)}
      {mono(80, 176, 'My Domain host')}

      {/* token */}
      {box(190, 20, 190, 52, C.surface)}
      {label(285, 40, 'OAuth 2.0 token', 10, C.bright, 'middle', 600)}
      {mono(285, 58, 'client_credentials')}
      {arrow(140, 150, 190, 60)}

      {/* the four surfaces */}
      {[
        ['Tooling API', '/tooling/query', 'setup metadata', 95],
        ['REST Data API', '/query', 'records, telemetry', 152],
        ['composite/batch', '25 subrequests', 'packs the above', 209],
        ['Metadata API', 'sf CLI, file tree', 'source on disk', 266],
      ].map(([t, q, note, y]) => (
        <g key={t as string}>
          {box(430, y as number, 210, 44)}
          {label(440, (y as number) + 19, t as string, 10, C.bright, 'start', 600)}
          {mono(440, (y as number) + 34, q as string, 9, C.accent, 'start')}
          {label(660, (y as number) + 27, note as string, 9.5, C.text, 'start')}
          {arrow(380, 46, 430, (y as number) + 22)}
        </g>
      ))}

      {label(285, 96, 'The token is held in Python.', 9.5, C.text)}
      {label(285, 112, 'The CLI is handed one; it is never', 9.5, C.text)}
      {label(285, 128, 'scraped from the CLI, which cannot', 9.5, C.text)}
      {label(285, 144, 'reveal it without a human at a prompt.', 9.5, C.text)}
    </svg>
  )
}

/* ------------------------------------------------------ 2. reachability */

/** What the graph stage computes, and why a path beats a count. */
function ReachabilityDiagram() {
  const node = (x: number, y: number, t: string, sub: string,
                kind: 'entry' | 'mid' | 'dead') => (
    <g key={t}>
      {box(x, y, 150, 42,
           kind === 'entry' ? C.soft : C.sunken,
           kind === 'entry' ? C.accent : kind === 'dead' ? C.unused : C.line,
           kind === 'dead' ? '4 3' : undefined)}
      {label(x + 75, y + 19, t, 10, kind === 'dead' ? C.unused : C.bright,
             'middle', 600)}
      {mono(x + 75, y + 33, sub, 8.5)}
    </g>
  )

  return (
    <svg viewBox="0 0 900 250" className="diagram" role="img"
         aria-label="Reachability from entry points">
      <Defs />
      {label(90, 22, 'ENTRY POINTS', 9.5, C.accent, 'middle', 600)}
      {label(90, 36, 'run or are seen', 9, C.text)}

      {node(15, 50, 'CustomerOrderTrigger', 'fires on DML', 'entry')}
      {node(15, 110, 'Weekly Revenue', 'report', 'entry')}
      {node(15, 170, 'Order_Approval', 'active flow', 'entry')}

      {node(280, 80, 'OrderFulfilment', 'ApexClass', 'mid')}
      {node(280, 160, 'Order_Total__c', 'CustomField', 'mid')}

      {node(545, 120, 'PricingHelper', 'ApexClass', 'mid')}
      {node(545, 195, 'Legacy_Code__c', 'no inbound edge', 'dead')}

      {arrow(165, 71, 280, 95)}
      {arrow(165, 131, 280, 175)}
      {arrow(165, 191, 280, 185)}
      {arrow(430, 101, 545, 135)}
      {arrow(430, 181, 545, 145)}

      {label(760, 100, 'REACHABLE', 10, C.used, 'middle', 600)}
      {label(760, 116, 'something that runs', 9, C.text)}
      {label(760, 130, 'can get here', 9, C.text)}

      {label(760, 190, 'UNREACHABLE', 10, C.unused, 'middle', 600)}
      {label(760, 206, 'candidate only if every', 9, C.text)}
      {label(760, 220, 'collector also came back', 9, C.text)}
      {label(760, 234, 'empty and complete', 9, C.text)}

      <line x1={700} y1={40} x2={700} y2={230} stroke={C.line}
            strokeDasharray="3 4" />
    </svg>
  )
}

/* ------------------------------------------------------------ the page */

const CLAMPS: [string, string][] = [
  ['R0 / R1 — out of scope', 'fixed at 100; no scoring runs'],
  ['R3 — Tier-A evidence of use', 'at least 80'],
  ['R4 — runtime or data evidence only', 'between 60 and 75'],
  ['R3a — layout-only, no data', 'at most 70'],
  ['R9 — nothing anywhere, coverage complete', 'at least 70'],
  ['R7 — weak signal only', 'at most 65'],
  ['R5 / R6 / R8 — review', 'at most 60'],
  ['R2 — completeness gate failed', 'at most 55'],
]

/** Kept as a plain string so the alignment survives editing. */
const SCORE_EXAMPLE = `  +15  static references     searched 721 files, nothing
  +15  record data           no record holds a value
  -10  dependency API        inconclusive (beta, blind to reports)
  +15  config data           no API name stored as data
  +15  reachability          no entry point can reach it
  +15  delete rehearsal      Salesforce raised no objection
  ---
   65  sum
 + 50  base
  115  ->  rule R9 requires at least 70  ->  bounded to 100`

const STAGES: [string, string, string, string][] = [
  ['S00', 'org.connect', 'Token, org identity, API budget, and a capability probe for Event Monitoring, the Dependency API, field history and code coverage.', '~10'],
  ['S10', 'inventory', 'Enumerate components and build the alias table every later stage matches against.', '~60'],
  ['S15', 'source.retrieve', 'Pull the metadata tree to disk once, so reference searching costs nothing after this.', '~20'],
  ['S20', 'index', 'Three extraction layers over every retrieved file, resolved against the alias table.', '0'],
  ['S25', 'collect', 'Eight collectors, each recording what it found and what it did not. Six write evidence; two raise uncertainty flags instead.', '~200'],
  ['S30', 'graph', 'Nodes, edges, entry points, then breadth-first reachability.', '0'],
  ['S40', 'classify', 'Rules R0–R9 over the evidence. No model participates.', '0'],
  ['S50', 'rehearse', 'Validate-only destructive deploy. Deletes nothing.', '~5'],
  ['S60', 'narrate', 'Plain-language summaries of verdicts already decided.', '0'],
  ['S70', 'report', 'XLSX, Markdown, JSON, and a destructiveChanges.xml that is never deployed.', '0'],
]

const COLLECTORS: [string, string, string, string][] = [
  ['C10', 'Static references',
   'Token sweep, string literals and merge fields across every retrieved file, matched against the alias table.',
   'Local files — no API'],
  ['C20', 'Record data',
   'One multi-aggregate SOQL per object: SELECT COUNT(Id), COUNT(f1), COUNT(f2)… — O(objects), not O(fields). Types that reject COUNT() get an existence probe instead.',
   'REST Data API'],
  ['C30', 'Dependency API',
   'MetadataComponentDependency. Beta, 2 000-row cap, and blind to several types — so a hit is evidence and a miss is not.',
   'Tooling API'],
  ['C40', 'Runtime execution',
   'AsyncApexJob for batch/queueable/schedulable runs, CronTrigger for scheduled jobs, ApexCodeCoverageAggregate for test execution.',
   'REST Data + Tooling'],
  ['C50', 'Recent change',
   'Created or modified inside the configured window. A half-built feature looks exactly like a dead one, so this only ever raises a flag.',
   'From inventory'],
  ['C60', 'Dynamic Apex',
   'Scan for getGlobalDescribe, Database.query, .get(, .put(, Type.forName. Every object such a class touches is tainted.',
   'Local files — no API'],
  ['C80', 'Config data',
   'Enumerate Custom Metadata types and Custom Settings, read every row, sweep all text values against the alias table.',
   'REST Data API'],
  ['C90', 'Delete rehearsal',
   'Generate destructiveChanges.xml and run sf project deploy start --dry-run. Salesforce’s own dependency checker names any blocker.',
   'Metadata API via CLI'],
]

export function DocsInternals() {
  return (
    <>
      <DocSection title="Technical reference">
        <p className="lead">
          Everything above answers whether a verdict can be trusted. What follows
          answers how it was produced — which API was called, with which query,
          and what an empty result was allowed to mean.
        </p>
      </DocSection>

      {/* ---------------------------------------------------- acquisition */}
      <DocSection title="How data leaves the org">
        <p className="lead">
          Four surfaces, because no single Salesforce API exposes all of it.
          Reports live on one, Apex definitions on another, and validation rules
          on neither — they exist only as files.
        </p>
        <AcquisitionDiagram />

        <h3>Authentication</h3>
        <p>
          A Connected App with the client-credentials flow. The backend posts{' '}
          <code>grant_type=client_credentials</code> to the org's token endpoint
          and holds the result in memory. Client-credentials tokens carry no{' '}
          <code>expires_in</code>, so a conservative time-to-live is applied and
          the token is refreshed behind a lock — concurrent stages would
          otherwise stampede the token endpoint.
        </p>
        <p className="note">
          The token never comes <em>from</em> the CLI. <code>sf org display --json</code>{' '}
          returns <code>[REDACTED]</code> and <code>sf org auth show-access-token</code>{' '}
          demands an interactive confirmation, so neither can be automated. The
          flow runs the other way: Python obtains the token and hands it to the
          CLI with <code>sf org login access-token</code>. That is also what lets
          you point the tool at an org nobody has logged into on this machine.
        </p>

        <h3>The four surfaces</h3>
        <div className="twrap">
          <table className="doc-table">
            <thead>
              <tr><th>Surface</th><th>Endpoint</th><th>Carries</th></tr>
            </thead>
            <tbody>
              {[
                ['Tooling API', '/services/data/vXX/tooling/query',
                 'Definitions: ApexClass, ApexTrigger, CustomField, ValidationRule, Flow, Layout, MetadataComponentDependency'],
                ['REST Data API', '/services/data/vXX/query',
                 'Records and runtime telemetry: Report, Dashboard, EmailTemplate, AsyncApexJob, CronTrigger, EventLogFile, and every custom object’s rows'],
                ['Composite batch', '/services/data/vXX/composite/batch',
                 'Up to 25 subrequests per HTTP call, with no five-query sublimit. This is how a few hundred probes cost a few dozen calls'],
                ['Metadata API', 'sf project retrieve start',
                 'The source tree itself. The only way to see validation rules, workflow rules, approval processes and report definitions'],
              ].map(([a, b, c]) => (
                <tr key={a}>
                  <td><b>{a}</b></td><td><code>{b}</code></td><td>{c}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <p className="note">
          <code>/services/data/vXX/limits</code> is read separately, and is what
          the API budget meter reports.
        </p>
      </DocSection>

      {/* -------------------------------------------------------- routing */}
      <DocSection title="Routing, and the failure it prevents">
        <p className="lead">
          Querying <code>Report</code> through the Tooling API returns{' '}
          <code>INVALID_TYPE</code>. So does querying <code>ValidationRule</code>{' '}
          through the Data API. If that error is swallowed anywhere in the
          collector chain it becomes <b>“zero references found”</b> — which is
          exactly how a live component gets deleted.
        </p>
        <p>
          So every query is routed through an explicit registry before it is
          sent, and a mismatch raises a fatal error rather than returning an
          empty result. There are three categories, and the third is the one
          that is easy to get wrong:
        </p>
        <div className="twrap">
          <table className="doc-table">
            <thead>
              <tr><th>Category</th><th>Examples</th><th>Consequence of getting it wrong</th></tr>
            </thead>
            <tbody>
              <tr>
                <td><b>Tooling only</b></td>
                <td><code>ApexClass, CustomField, ValidationRule, Flow, Layout, FlexiPage</code></td>
                <td>Data API returns INVALID_TYPE</td>
              </tr>
              <tr>
                <td><b>Data only</b></td>
                <td><code>Report, Dashboard, EmailTemplate, AsyncApexJob, CronTrigger, ListView</code></td>
                <td>Tooling API returns INVALID_TYPE</td>
              </tr>
              <tr>
                <td><b>Neither — files only</b></td>
                <td><code>SharingRules, ReportType, ApprovalProcess, Workflow, FieldSet, Letterhead</code></td>
                <td>
                  Not SOQL-queryable at all. Listing these as queryable makes the
                  probe report “not present in this org”, which is wrong in the
                  dangerous direction
                </td>
              </tr>
            </tbody>
          </table>
        </div>
        <p className="note strong">
          The registry is verified against a live org rather than against
          documentation, because documentation was wrong.{' '}
          <code>CustomPermission</code>, <code>DuplicateRule</code> and{' '}
          <code>MatchingRule</code> all read as setup metadata and are all served
          by the Data API. All three were wrong in the first version of the
          registry, which is why the probe script exists.
        </p>
      </DocSection>

      {/* --------------------------------------------------------- budget */}
      <DocSection title="The API budget">
        <p className="lead">
          Every call is leased from a governor before it is made. The point is
          not politeness — it is that exhausting the org's daily limit is an
          outage for everything else using that org.
        </p>
        <ul className="limits">
          <li>
            <b>A safety floor.</b> The run refuses to spend below a configured
            reserve of the daily allowance, so the org is never left with zero
            headroom.
          </li>
          <li>
            <b>Planned before spent.</b> Each stage declares an estimate and the
            plan is checked against what remains, so a run that cannot finish
            fails at the start rather than halfway through.
          </li>
          <li>
            <b>Composite accounting.</b> A batch of 25 subrequests is one call
            against the limit, and is costed as one.
          </li>
          <li>
            <b>A circuit breaker.</b> Rate-limit responses pause the lease queue
            rather than retrying into the wall.
          </li>
        </ul>
      </DocSection>

      {/* --------------------------------------------------------- stages */}
      <DocSection title="The ten stages">
        <p className="lead">
          Sequential, each writing its output to Postgres before the next starts.
          Call counts are typical for a mid-size org, not guarantees.
        </p>
        <div className="twrap">
          <table className="doc-table">
            <thead>
              <tr><th>#</th><th>Stage</th><th>What it does</th><th>API calls</th></tr>
            </thead>
            <tbody>
              {STAGES.map(([n, key, what, cost]) => (
                <tr key={key}>
                  <td><code>{n}</code></td>
                  <td><b>{key}</b></td>
                  <td>{what}</td>
                  <td><code>{cost}</code></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <p className="note">
          The four stages that cost nothing — index, graph, classify, narrate —
          are where most of the analysis happens. That is deliberate: metadata is
          pulled to disk once, and everything after reads local files.
        </p>
      </DocSection>

      {/* ------------------------------------------------------- inventory */}
      <DocSection title="Inventory and the alias table">
        <p>
          Inventory enumerates what exists and, more importantly, builds the{' '}
          <b>alias table</b> — every string by which a component can legitimately
          be named. A field called <code>Order_Total__c</code> appears in Apex as{' '}
          <code>Order_Total__c</code>, in a report as{' '}
          <code>Order__c.Order_Total__c</code>, in a Flow as a merge field, and in
          a layout under its label. Matching only the API name misses most real
          references, and a missed reference is a false UNUSED.
        </p>
        <div className="twrap">
          <table className="doc-table">
            <thead>
              <tr><th>Queried</th><th>Via</th><th>For</th></tr>
            </thead>
            <tbody>
              {[
                ['CustomField', 'Tooling', 'Id, DeveloperName, TableEnumOrId, NamespacePrefix — every custom field and its owning object'],
                ['ApexClass', 'Tooling', 'Name and Body — the body is fetched here so the indexer never has to go back for it'],
                ['ApexTrigger', 'Tooling', 'Name and Body, plus the object it fires on'],
                ['EntityDefinition', 'Tooling', 'Which objects exist, custom and standard, and whether they are queryable'],
                ['FieldDefinition', 'Tooling', 'Field-level detail the CustomField row does not carry'],
              ].map(([a, b, c]) => (
                <tr key={a}>
                  <td><code>{a}</code></td><td>{b}</td><td>{c}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <p className="note">
          Namespaced components from managed packages are identified here and
          marked out of scope: they cannot be deleted from this org anyway.
        </p>
      </DocSection>

      {/* -------------------------------------------------------- retrieve */}
      <DocSection title="Retrieving the metadata tree">
        <p>
          A manifest is generated and passed to{' '}
          <code>sf project retrieve start --manifest package.xml</code>. Around
          fifty types are wildcarded. <code>CustomObject</code> is the force
          multiplier: one entry brings back fields, validation rules, list views,
          web links, field sets, compact layouts and record types together.
        </p>
        <h3>Folder-scoped types need two passes</h3>
        <p>
          Reports, dashboards, email templates and documents live inside folders,
          and asking for <code>Report</code> directly returns <b>nothing at
          all</b> — not an error, just an empty result. The folder type has to be
          listed first, then the contents of each folder.
        </p>
        <p className="note strong">
          This is a silent failure with an expensive consequence: retrieve zero
          reports, and every field used only by a report looks unreferenced. So
          the stage reconciles what it asked for against what arrived, and raises
          a coverage warning when a requested type returns no files. That warning
          reaches the verdict — it is why a run can finish as <em>partial
          coverage</em>.
        </p>
        <p className="note">
          One related trap, worth naming because it costs more than it looks: an
          unknown type name fails the <em>entire manifest chunk</em>, not just
          that type. The reporting-snapshot type is <code>AnalyticSnapshot</code>,
          not <code>ReportingSnapshot</code> as the documentation implies, and
          getting it wrong silently costs every other type sharing the chunk.
        </p>
      </DocSection>

      {/* ----------------------------------------------------------- index */}
      <DocSection title="Indexing: three layers, unioned">
        <p className="lead">
          Every retrieved file is read by three independent extractors. Their
          results are unioned, never intersected — a parse failure in one must
          not delete a reference another found.
        </p>
        <div className="rules">
          <div className="rule">
            <span className="rule-id">L1</span>
            <span className="rule-cond">
              <b>Structural.</b> Parse the format properly — XML elements, Apex
              constructs. Precise locators, so the evidence can name a line.
            </span>
            <span className="rule-arrow">→</span>
            <span className="tier a">Tier A</span>
          </div>
          <div className="rule">
            <span className="rule-id">L2</span>
            <span className="rule-cond">
              <b>Literals.</b> Every string literal and text node, matched against
              the alias table. This is the highest-value guard against a false
              UNUSED: it catches <code>sObj.get('Legacy_Code__c')</code>,{' '}
              <code>Database.query('SELECT …')</code> and Aura's{' '}
              <code>component.get("c.x")</code> with no semantic understanding at
              all.
            </span>
            <span className="rule-arrow">→</span>
            <span className="tier a">Tier A</span>
          </div>
          <div className="rule">
            <span className="rule-id">L3</span>
            <span className="rule-cond">
              <b>Tokens.</b> Word-boundary tokens over the whole file. Broad
              recall, deliberately imprecise — an exact alias hit is Tier A, a
              label or partial match is Tier C.
            </span>
            <span className="rule-arrow">→</span>
            <span className="tier c">A / C</span>
          </div>
        </div>
        <p className="note">
          Code is separated from comments first, so a field name mentioned only
          in a comment never counts as a use.
        </p>
      </DocSection>

      {/* ------------------------------------------------------ collectors */}
      <DocSection title="The collectors, in detail">
        <p className="lead">
          Each answers one question, is blind to the others' conclusions, and
          writes a row whether or not it found anything. None of them returns
          “used” or “unused” — a single collector is not entitled to a verdict.
        </p>
        <div className="twrap">
          <table className="doc-table">
            <thead>
              <tr><th>ID</th><th>Collector</th><th>Method</th><th>Reads</th></tr>
            </thead>
            <tbody>
              {COLLECTORS.map(([id, name, method, api]) => (
                <tr key={id}>
                  <td><code>{id}</code></td>
                  <td><b>{name}</b></td>
                  <td>{method}</td>
                  <td><code>{api}</code></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>

        <h3>Two of them deserve singling out</h3>
        <div className="two-col">
          <div className="col-card good">
            <h4>C80 — API names stored as data</h4>
            <ul>
              <li>
                A Custom Metadata row holding{' '}
                <code>Source_Field__c: 'Legacy_Code__c'</code> is load-bearing at
                runtime, and <em>no metadata parser will ever find it</em>.
              </li>
              <li>
                Every static analyser — including everything else in this
                codebase — would call that field unreferenced and recommend
                deleting it.
              </li>
              <li>
                So the rows themselves are read and their text values swept
                against the alias table.
              </li>
            </ul>
          </div>
          <div className="col-card good">
            <h4>C90 — asking Salesforce directly</h4>
            <ul>
              <li>
                A <code>destructiveChanges.xml</code> is generated and submitted
                with <code>--dry-run</code> and{' '}
                <code>--test-level NoTestRun</code>.
              </li>
              <li>
                Validate-only: Salesforce checks the request and reports the
                outcome without committing it. Nothing is deleted, and it is safe
                against production.
              </li>
              <li>
                This is the only server-validated signal here, so it outranks the
                rest. A refusal names the exact blocker — including references
                our parsers missed for reasons nobody thought of.
              </li>
            </ul>
          </div>
        </div>
        <p className="note strong">
          Five outcomes are recorded, and the last three are what keep the system
          honest: <code>EVIDENCE_OF_USE</code>, <code>NO_EVIDENCE_FOUND</code>,{' '}
          <code>INCONCLUSIVE</code>, <code>NOT_APPLICABLE</code>,{' '}
          <code>FAILED</code>. Collapsing the last three into “found nothing”
          would turn <em>we could not check</em> into <em>there is nothing
          here</em> — which is precisely how a live component becomes a deletion
          candidate.
        </p>
      </DocSection>

      {/* --------------------------------------------- reading the evidence */}
      <DocSection title="Reading a component's evidence">
        <p className="lead">
          Open any component and the first thing shown is a row per collector —
          every one that ran, including the ones that found nothing. The
          component list summarises the same thing as a strip of circles.
        </p>

        <h3>What the circles mean</h3>
        <p>
          One circle per collector. They are counts rather than a sequence, so
          four hollow circles means four checks searched and came back empty.
        </p>
        <div className="twrap">
          <table className="doc-table">
            <thead>
              <tr><th>Circle</th><th>Means</th><th>Recorded as</th></tr>
            </thead>
            <tbody>
              <tr>
                <td><span className="strip"><i className="dot hit" /></span> filled</td>
                <td>A collector found something that uses this</td>
                <td><code>EVIDENCE_OF_USE</code></td>
              </tr>
              <tr>
                <td><span className="strip"><i className="dot clean" /></span> hollow</td>
                <td>
                  A collector looked properly and found nothing. This is the
                  claim a deletion rests on, which is why it is shown rather
                  than left blank
                </td>
                <td><code>NO_EVIDENCE_FOUND</code></td>
              </tr>
              <tr>
                <td><span className="strip"><i className="dot unclear" /></span> teal</td>
                <td>
                  A collector looked, but its answer cannot be trusted — or the
                  check does not apply here. Never counted as absence
                </td>
                <td><code>INCONCLUSIVE</code> / <code>NOT_APPLICABLE</code></td>
              </tr>
              <tr>
                <td><span className="strip"><i className="dot gap" /></span> red</td>
                <td>
                  A check that could not run at all. One of these means the
                  component cannot be called UNUSED — rule R2 catches it first
                </td>
                <td>a coverage gap</td>
              </tr>
            </tbody>
          </table>
        </div>
        <p className="note strong">
          Hollow versus teal is the distinction that matters. "We looked and
          found nothing" supports a deletion; "we could not check" does not,
          and collapsing the two is how a live component becomes a candidate.
        </p>

        <h3>What each row carries</h3>
        <ul className="limits">
          <li><b>Result</b> — one of the five above.</li>
          <li><b>Tier</b> — how much the finding is worth: A, B, C or D.</li>
          <li>
            <b>Weight</b> — a multiplier for partial signals, such as a
            reference that exists only on a page layout.
          </li>
          <li>
            <b>Payload</b> — the specifics: the literal query used, how many
            artifacts were searched, the reachability path that was walked.
            This is what makes a verdict checkable rather than merely stated.
          </li>
        </ul>
      </DocSection>

      {/* ------------------------------------------------------- confidence */}
      <DocSection title="What the confidence score means">
        <p className="lead">
          A 0–100 number for <b>sorting the review queue</b>. It never decides a
          verdict — the rules do that, and they run first. Ranking a queue
          wrongly wastes someone's morning; deciding a verdict wrongly deletes
          production metadata, so the two are kept apart in the code rather
          than by convention.
        </p>

        <h3>How it is calculated</h3>
        <p>
          It starts at 50, every collector moves it, and the rule that fired
          then clamps the result. The final number is bounded to 0–100.
        </p>
        <div className="rules">
          <div className="rule">
            <span className="rule-id">+30</span>
            <span className="rule-cond">
              per collector reporting <code>EVIDENCE_OF_USE</code>, multiplied
              by that finding&rsquo;s weight
            </span>
          </div>
          <div className="rule">
            <span className="rule-id">+15</span>
            <span className="rule-cond">
              per collector reporting <code>NO_EVIDENCE_FOUND</code>
            </span>
          </div>
          <div className="rule">
            <span className="rule-id">&minus;10</span>
            <span className="rule-cond">
              per collector reporting <code>INCONCLUSIVE</code>
            </span>
          </div>
          <div className="rule">
            <span className="rule-id">&minus;30</span>
            <span className="rule-cond">per uncertainty flag</span>
          </div>
        </div>
        <p className="note">
          A clean search <em>raising</em> the score reads oddly until you see
          what the number measures. It is not "how likely is this used" — it is
          how sure we are of <em>this verdict</em>. A search that came back
          empty makes an UNUSED verdict more trustworthy, not less.
        </p>

        <h3>Then the rule clamps it</h3>
        <div className="twrap">
          <table className="doc-table">
            <thead><tr><th>Rule that fired</th><th>Clamp</th></tr></thead>
            <tbody>
              {CLAMPS.map(([rule, clamp]) => (
                <tr key={rule}><td>{rule}</td><td><code>{clamp}</code></td></tr>
              ))}
            </tbody>
          </table>
        </div>

        <h3>A worked example</h3>
        <p>
          A custom field where five collectors searched and found nothing, and
          the Dependency API came back inconclusive:
        </p>
        <pre className="cmd">{SCORE_EXAMPLE}</pre>

        <p className="note strong">
          Two things this number is not. It is not comparable across verdicts:
          100 on a USED component and 100 on an UNUSED one express confidence in
          two different claims, so sorting only means something within a single
          verdict. And it is not a probability — nothing here is calibrated
          against outcomes, so reading 70 as "70% likely" would be inventing
          precision that does not exist.
        </p>
      </DocSection>

      {/* ----------------------------------------------------------- graph */}
      <DocSection title="Building the dependency graph">
        <p className="lead">
          Counting references answers “how many things mention this?”, which is
          not the question. The question is whether the component can be reached
          from anything that actually runs, or that a person actually sees.
        </p>
        <ReachabilityDiagram />

        <h3>How it is built</h3>
        <ol className="numbered">
          <li>
            <b>Nodes.</b> One per component, carrying its type and the evidence
            already collected.
          </li>
          <li>
            <b>Edges.</b> One per resolved reference from the index — source file
            to referenced component — so an edge means “this artifact names that
            component”.
          </li>
          <li>
            <b>Entry points.</b> Marked structurally, by type and by Apex
            annotation.
          </li>
          <li>
            <b>Reachability.</b> A breadth-first sweep from every entry point.
            Each reached node records its distance and the node it was reached
            from, which is what makes a path printable afterwards.
          </li>
        </ol>

        <div className="two-col">
          <div className="col-card good">
            <h4>Entry points — they run, or are seen</h4>
            <ul>
              <li>Triggers, active Flows, workflow rules, validation rules</li>
              <li>Scheduled jobs</li>
              <li>Reports, dashboards, list views, quick actions, email templates</li>
              <li>Visualforce pages</li>
              <li>Exposed LWC / Aura (<code>isExposed</code> / global access)</li>
              <li>
                Apex exposed as <code>@AuraEnabled</code>,{' '}
                <code>@InvocableMethod</code>, <code>@RestResource</code>,{' '}
                <code>webservice</code>,{' '}
                <code>Schedulable</code>, <code>Batchable</code>,{' '}
                <code>Queueable</code>
              </li>
            </ul>
          </div>
          <div className="col-card bad">
            <h4>Not entry points — placement, not execution</h4>
            <ul>
              <li>Page layouts, FlexiPages, compact layouts — presentation for fields
                  (FlexiPage placement of an LWC/Aura is Tier-A use via static index,
                  not a graph root)</li>
              <li>Profiles and permission sets — who <em>could</em> see it</li>
              <li>An object's own definition file — a declaration</li>
              <li>Tabs and applications — navigation for fields; UI placement is C10</li>
              <li>Private (non-exposed) LWC / Aura — need a placer or importer</li>
            </ul>
          </div>
        </div>
        <p className="note">
          That distinction is the same one the classifier applies per component,
          expressed structurally. Every custom field gets a layout entry and
          field-level security the moment it is created; counting those as use
          marks essentially everything USED and finds nothing — which is worse
          than useless, because it looks authoritative.
        </p>
        <p className="note strong">
          The payoff is explainability. “5 binding references” tells a reviewer
          nothing. <code>Order_Total__c ← OrderFulfilmentService ←
          CustomerOrderTrigger [entry point: fires on DML]</code> tells them
          exactly why it is alive, and what would have to change for it not to
          be.
        </p>
      </DocSection>

      {/* -------------------------------------------------------- classify */}
      <DocSection title="Classification, rule by rule">
        <p className="lead">
          No model, no weighted sum, no learned threshold. Rules are evaluated in
          order, first match wins, and the rule that fired is stored with the
          verdict.
        </p>
        <div className="rules">
          {([
            ['R0', 'Managed package or namespaced', 'OUT OF SCOPE', 'scope'],
            ['R1', 'Standard object or standard field', 'OUT OF SCOPE', 'scope'],
            ['R2', 'Completeness gate failed — a required collector did not run', 'NEEDS REVIEW', 'review'],
            ['R3a', 'Layout-only Tier-A reference, and no record data', 'UNUSED', 'unused'],
            ['R3', 'Any other Tier-A evidence of use', 'USED', 'used'],
            ['R4', 'No Tier A, but Tier B — the org actually did something with it', 'USED', 'used'],
            ['R5', 'Apex entry point or test class with no observed caller', 'NEEDS REVIEW', 'review'],
            ['R6', 'Any Tier-D uncertainty flag', 'NEEDS REVIEW', 'review'],
            ['R5b', 'Apex unreachable from any entry point', 'UNUSED', 'unused'],
            ['R7', 'Weak or inconclusive evidence only', 'NEEDS REVIEW', 'review'],
            ['R8', 'Referenced by something that is not itself UNUSED (post-pass)', 'NEEDS REVIEW', 'review'],
            ['R9', 'Nothing anywhere, and coverage was complete', 'UNUSED', 'unused'],
          ] as const).map(([id, cond, verdict, kind]) => (
            <div className="rule" key={id}>
              <span className="rule-id">{id}</span>
              <span className="rule-cond">{cond}</span>
              <span className="rule-arrow">→</span>
              <span className={`rule-verdict ${kind}`}>{verdict}</span>
            </div>
          ))}
        </div>
        <p className="note strong">
          UNUSED can come from R3a (layout-only fields), R5b (unreachable Apex),
          or R9 (clean no-evidence). Ambiguity — flags, entry points, incomplete
          coverage — resolves upward into review. R8 is a post-pass after every
          component has a provisional verdict.
        </p>
        <p className="note">
          Confidence is a 0–100 number used to sort the review queue. It never
          decides a verdict. Ranking a queue wrongly wastes someone's morning;
          deciding a verdict wrongly deletes production metadata, so the two are
          kept strictly apart.
        </p>
      </DocSection>

      {/* ------------------------------------------------------ boundaries */}
      <DocSection title="Where the analysis provably stops">
        <p className="lead">
          Each of these is a real boundary, not a caveat added for modesty. They
          are the reason the completeness gate exists.
        </p>
        <div className="twrap">
          <table className="doc-table">
            <thead>
              <tr><th>Limit</th><th>Effect on a verdict</th></tr>
            </thead>
            <tbody>
              {[
                ['MetadataComponentDependency is Beta, caps at 2 000 rows, and does not cover reports, dashboards, validation rules, workflows, email templates or approval processes',
                 'A hit is trusted; a miss is recorded as inconclusive and never counted as absence'],
                ['Event Monitoring is a paid add-on. Without it, external API traffic cannot be observed',
                 'A component called only by an integration cannot be proven used — the coverage caveat says so explicitly'],
                ['Apex built at runtime from strings cannot be resolved statically',
                 'Every object such a class touches is flagged, which suppresses UNUSED'],
                ['The analysis sees what the API user can see',
                 'A non-administrator bounds coverage, and the run reports that bound'],
                ['Retrieval that silently returns nothing for a type',
                 'Reconciled and raised as a coverage warning, which forces affected components to review'],
              ].map(([a, b]) => (
                <tr key={a}><td>{a}</td><td>{b}</td></tr>
              ))}
            </tbody>
          </table>
        </div>
        <p className="note strong">
          When any required collector fails to complete, affected components
          cannot reach UNUSED at all — R2 catches them first. A partial analysis
          produces fewer deletion candidates, never less reliable ones.
        </p>
      </DocSection>
    </>
  )
}
