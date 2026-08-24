/**
 * In-app documentation.
 *
 * Written for someone who has to defend a deletion to a colleague: it explains
 * what is analysed, how a verdict is reached, and — given equal weight — what
 * this tool structurally cannot know. A cleanup tool that only advertises its
 * strengths is one nobody should trust.
 */

/* The diagrams read the same custom properties as the rest of the app, so they
   follow a theme switch and can never disagree with the UI beside them about
   what a verdict colour is. Hardcoding these meant the drawings stayed dark --
   and on a white brand, unreadable. */
import { DocsInternals } from './DocsInternals'
import { DocSection } from './DocSection'

const C = {
  used: 'var(--used)', unused: 'var(--unused)', review: 'var(--review)',
  scope: 'var(--scope)', accent: 'var(--accent)', ai: 'var(--ai)',
  line: 'var(--border-strong)', text: 'var(--text-dim)', bright: 'var(--text)',
  surface: 'var(--surface)', sunken: 'var(--sunken)', soft: 'var(--accent-soft)',
  aiSoft: 'var(--ai-soft)',
}

function ArchitectureDiagram() {
  const box = (x: number, y: number, w: number, h: number, fill = C.sunken,
               stroke = C.line) => (
    <rect x={x} y={y} width={w} height={h} rx={6} fill={fill} stroke={stroke} />
  )
  const label = (x: number, y: number, t: string, size = 10,
                 fill = C.bright, anchor: 'start' | 'middle' = 'middle') => (
    <text x={x} y={y} fontSize={size} fill={fill} textAnchor={anchor}
          fontFamily="var(--ui)">{t}</text>
  )
  const arrow = (d: string, colour = C.line) => (
    <path d={d} fill="none" stroke={colour} strokeWidth="1.4" markerEnd="url(#ah)" />
  )

  return (
    <svg viewBox="0 0 880 400" className="diagram" role="img"
         aria-label="System architecture">
      <defs>
        <marker id="ah" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6"
                markerHeight="6" orient="auto">
          <path d="M0 0 L10 5 L0 10 z" fill={C.line} />
        </marker>
      </defs>

      {/* Salesforce */}
      {box(20, 30, 170, 150, C.soft, C.accent)}
      {label(105, 52, 'Your Salesforce org', 11, C.accent)}
      {label(105, 76, 'Tooling + REST API', 9, C.text)}
      {label(105, 94, 'Metadata API', 9, C.text)}
      {label(105, 112, 'sf CLI (retrieve, dry-run)', 9, C.text)}
      {label(105, 140, 'read-only', 9, C.used)}
      {label(105, 158, 'nothing is ever deleted', 8, C.text)}

      {/* Backend */}
      {box(250, 20, 380, 250)}
      {label(440, 42, 'Backend  ·  FastAPI + orchestrator', 11)}

      {box(268, 58, 165, 60, C.surface)}
      {label(350, 78, 'Rate-limit governor', 9)}
      {label(350, 94, 'meters every call, 24h budget', 8, C.text)}
      {label(350, 108, 'circuit breaker on limit', 8, C.text)}

      {box(447, 58, 165, 60, C.surface)}
      {label(529, 78, 'Collectors (8)', 9)}
      {label(529, 94, 'each records hits AND misses', 8, C.text)}
      {label(529, 108, 'independent, never share state', 8, C.text)}

      {box(268, 132, 165, 58, C.surface)}
      {label(350, 152, 'Dependency graph', 9)}
      {label(350, 168, 'reachability from entry points', 8, C.text)}

      {box(447, 132, 165, 58, C.surface)}
      {label(529, 152, 'Rule engine', 9)}
      {label(529, 168, 'deterministic. no model decides', 8, C.text)}

      {box(268, 204, 165, 50, C.surface)}
      {label(350, 226, 'Report builder', 9)}
      {label(350, 241, 'xlsx · md · json · delete pkg', 8, C.text)}

      {box(447, 204, 165, 50, C.aiSoft, C.ai)}
      {label(529, 226, 'AI narration', 9, C.ai)}
      {label(529, 241, 'explains, never decides', 8, C.text)}

      {/* Postgres */}
      {box(250, 292, 380, 76)}
      {label(440, 314, 'PostgreSQL  ·  20 tables', 11)}
      {label(440, 334, 'components · evidence · evidence_gaps · classifications', 8, C.text)}
      {label(440, 350, 'events (gapless sequence, durable replay)', 8, C.text)}

      {/* Frontend */}
      {box(690, 60, 170, 200)}
      {label(775, 82, 'Browser', 11)}
      {label(775, 108, 'Overview', 9, C.text)}
      {label(775, 128, 'Pipeline (live SSE)', 9, C.text)}
      {label(775, 148, 'Components + evidence', 9, C.text)}
      {label(775, 168, 'Dependency graph', 9, C.text)}
      {label(775, 188, 'Report export', 9, C.text)}
      {label(775, 218, 'a viewer only', 9, C.used)}
      {label(775, 234, 'closing the tab', 8, C.text)}
      {label(775, 247, 'does not stop a run', 8, C.text)}

      {arrow('M190 105 L248 105')}
      {arrow('M440 270 L440 290')}
      {arrow('M632 150 L688 150')}
      {arrow('M688 200 L634 250', C.accent)}
      {label(660, 196, 'SSE', 8, C.accent, 'start')}
    </svg>
  )
}

function FlowDiagram() {
  const steps = [
    ['Connect', 'discover edition, budget,\nwhat this org supports'],
    ['Inventory', 'every field, object, Apex\n+ the alias table'],
    ['Retrieve', 'metadata to disk once,\nso search costs nothing'],
    ['Index', 'code vs comments vs literals,\nresolve every mention'],
    ['Collect', '8 collectors, each logging\nfound AND not-found'],
    ['Graph', 'reachability from anything\nthat runs or is seen'],
    ['Classify', 'deterministic rules,\nfirst match wins'],
    ['Rehearse', 'ask Salesforce if the\ndelete would succeed'],
    ['Narrate', 'AI explains verdicts\nalready decided'],
    ['Report', 'xlsx, markdown, json,\ndestructiveChanges.xml'],
  ]
  const W = 168, H = 78, GAP = 12, PER = 5
  return (
    <svg viewBox={`0 0 ${PER * (W + GAP)} ${2 * (H + 46)}`} className="diagram"
         role="img" aria-label="Pipeline flow">
      <defs>
        <marker id="fa" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6"
                markerHeight="6" orient="auto">
          <path d="M0 0 L10 5 L0 10 z" fill={C.line} />
        </marker>
      </defs>
      {steps.map(([title, sub], i) => {
        const row = Math.floor(i / PER), col = i % PER
        const x = col * (W + GAP), y = row * (H + 46) + 10
        const optional = i >= 7
        return (
          <g key={title}>
            <rect x={x} y={y} width={W} height={H} rx={6}
                  fill={optional ? C.soft : C.sunken}
                  stroke={optional ? C.line : C.accent} strokeWidth={optional ? 1 : 1.2}
                  strokeDasharray={optional ? '4 3' : undefined} />
            <text x={x + 12} y={y + 22} fontSize="11" fill={C.bright}
                  fontFamily="var(--ui)" fontWeight="600">
              {i + 1}. {title}
            </text>
            {String(sub).split('\n').map((line, j) => (
              <text key={j} x={x + 12} y={y + 40 + j * 13} fontSize="8.5" fill={C.text}
                    fontFamily="var(--ui)">{line}</text>
            ))}
            {optional && (
              <text x={x + W - 10} y={y + 22} fontSize="7.5" fill={C.text}
                    textAnchor="end" fontFamily="var(--ui)">optional</text>
            )}
            {col < PER - 1 && i < steps.length - 1 && (
              <path d={`M${x + W} ${y + H / 2} L${x + W + GAP - 2} ${y + H / 2}`}
                    stroke={C.line} strokeWidth="1.3" markerEnd="url(#fa)" />
            )}
            {col === PER - 1 && i < steps.length - 1 && (
              <path d={`M${x + W / 2} ${y + H} L${x + W / 2} ${y + H + 20}
                        L${W / 2} ${y + H + 20} L${W / 2} ${y + H + 44}`}
                    fill="none" stroke={C.line} strokeWidth="1.3" markerEnd="url(#fa)" />
            )}
          </g>
        )
      })}
    </svg>
  )
}

function VerdictDiagram() {
  const rows = [
    ['R0/R1', 'managed package, or standard component', 'OUT OF SCOPE', C.scope],
    ['R2', 'a required check could not run', 'NEEDS REVIEW', C.review],
    ['R3a', 'only a layout references it, and no data exists', 'UNUSED', C.unused],
    ['R3', 'something binding references it', 'USED', C.used],
    ['R4', 'no reference, but real data or runtime activity', 'USED', C.used],
    ['R5', 'externally invocable, or test-only code', 'NEEDS REVIEW', C.review],
    ['R5b', 'code nothing can reach', 'UNUSED', C.unused],
    ['R6', 'an uncertainty flag is set', 'NEEDS REVIEW', C.review],
    ['R7', 'only weak or ambiguous signal', 'NEEDS REVIEW', C.review],
    ['R8', 'something not-unused still references it', 'NEEDS REVIEW', C.review],
    ['R9', 'nothing found anywhere, every check ran', 'UNUSED', C.unused],
  ]
  return (
    <div className="rules">
      {rows.map(([rule, cond, verdict, colour]) => (
        <div className="rule" key={rule}>
          <code className="rule-id">{rule}</code>
          <span className="rule-cond">{cond}</span>
          <span className="rule-arrow">→</span>
          <span className="rule-verdict" style={{ color: colour, borderColor: colour }}>
            {verdict}
          </span>
        </div>
      ))}
    </div>
  )
}

export function Docs() {
  return (
    <div className="docs">
      <header className="docs-hero">
        <h1>How this works</h1>
        <p>
          This tool finds Salesforce metadata nothing uses, and shows its working
          so you can disagree with it. The governing rule is an asymmetry: a wrong
          <b> unused </b> breaks production, while a wrong <b> needs review </b>
          costs someone five minutes. Every ambiguity resolves toward review.
        </p>
      </header>

      <DocSection title={'What "used" means here'}>
        <p className="lead">
          Used means the component takes part in a real business process —
          something reads it, writes it, decides on it, or shows it to someone
          as part of doing work.
        </p>
        <div className="two-col">
          <div className="col-card good">
            <h4>Counts as use</h4>
            <ul>
              <li>Apex classes and triggers</li>
              <li>Flows, Process Builder, workflow rules</li>
              <li>Validation rules and formulas</li>
              <li>Reports, dashboards, list views</li>
              <li>LWC, Aura, Visualforce</li>
              <li>Email templates</li>
              <li>Real data in real records</li>
              <li>API names stored in config records</li>
            </ul>
          </div>
          <div className="col-card bad">
            <h4>Does not count</h4>
            <ul>
              <li><b>Page layouts</b> — every field gets one at creation</li>
              <li><b>Field-level security</b> — means someone <em>could</em> see
                  it, not that anyone does</li>
              <li><b>Its own definition file</b> — a declaration, not a use</li>
              <li><b>Tabs and apps</b> — navigation structure</li>
              <li><b>Comments</b> — a stale mention is not a reference</li>
              <li><b>Test code alone</b> — delete it with what it tests</li>
            </ul>
          </div>
        </div>
        <p className="note">
          This distinction is the whole product. Counting mere presence as use
          marks nearly everything used and finds nothing — worse than useless,
          because it looks authoritative. Measured on a real org: counting layout
          presence as use produced <b>0 findings</b>; separating the two produced
          <b> 6</b>.
        </p>
      </DocSection>

      <DocSection title="Architecture">
        <ArchitectureDiagram />
        <p className="note">
          The browser is only a viewer. Analysis runs in the backend and its
          progress is durable, so reloading the page — or closing it entirely and
          coming back — never loses anything and never affects the run.
        </p>
      </DocSection>

      <DocSection title="The pipeline">
        <FlowDiagram />
        <p className="note">
          A failing optional stage <b>degrades</b> the run rather than aborting
          it. The missing evidence becomes a recorded gap, and a gap forces
          NEEDS REVIEW downstream — so partial coverage produces caution, never a
          confident wrong answer.
        </p>
      </DocSection>

      <DocSection title="How a verdict is reached">
        <p className="lead">
          Rules are evaluated in order and the first match wins. No model decides
          a verdict, and no weighted score can cross into UNUSED.
        </p>
        <VerdictDiagram />
        <p className="note">
          Confidence is a 0–100 number for <em>sorting the review queue</em>.
          Ranking a queue wrongly wastes a morning; deciding a verdict wrongly
          deletes production metadata, so the two are kept strictly separate.
        </p>
      </DocSection>

      <DocSection title="The eight collectors">
        <p className="lead">
          Each answers one question, independently, and records its result{' '}
          <b>whether or not it found anything</b>. "Searched and found nothing" is
          a different claim from "never checked", and only the first supports a
          deletion.
        </p>
        <div className="twrap">
          <table className="doc-table">
            <thead>
              <tr><th>Collector</th><th>Question</th><th>Evidence tier</th></tr>
            </thead>
            <tbody>
              {[
                ['Static references', 'Is the name mentioned in any metadata?', 'A / C'],
                ['Record data', 'Does any record hold a value?', 'B'],
                ['Dependency API', 'Does Salesforce record a dependency edge?', 'A (positive only)'],
                ['Runtime execution', 'Has this Apex actually run?', 'B'],
                ['Recent changes', 'Was it touched recently?', 'D (flag)'],
                ['Dynamic Apex', 'Could runtime-built code reach it?', 'D (flag)'],
                ['Config data', 'Is the API name stored as data in CMDT?', 'A'],
                ['Reachability', 'Can anything that runs or is seen reach it?', 'A'],
                ['Delete rehearsal', 'Would Salesforce permit the delete?', 'A (server-validated)'],
              ].map(([a, b, c]) => (
                <tr key={a}><td><b>{a}</b></td><td>{b}</td><td><code>{c}</code></td></tr>
              ))}
            </tbody>
          </table>
        </div>
        <div className="tiers">
          <div><span className="tier a">Tier A</span> deleting it breaks a deploy or a runtime path</div>
          <div><span className="tier b">Tier B</span> the org actually did something with it</div>
          <div><span className="tier c">Tier C</span> consistent with use, nowhere near proof</div>
          <div><span className="tier d">Tier D</span> not evidence — an uncertainty flag that <b>suppresses</b> UNUSED</div>
        </div>
      </DocSection>

      <DocSection title="What each page shows">
        <div className="page-guide">
          {[
            ['Overview', 'Verdict counts, analysis coverage, which object to clean first, and the review queue grouped by reason. Click any tile to filter the component list.'],
            ['Pipeline', 'Live progress while a run executes: per-stage state, the API budget meter, and an event log. Limitations appear here as they are discovered, not only at the end.'],
            ['Components', 'Every component with its evidence strip — filled means a collector found something, hollow means it searched and found nothing, red means it could not check. Open one for the full trail, including the exact query each collector used.'],
            ['Dependencies', 'The reachability graph. Cyan nodes are entry points (things that run or are seen), green is reachable, amber is unreachable — those are the candidates.'],
            ['Report', 'Export as XLSX, Markdown, JSON, or a deployable destructiveChanges.xml. The report leads with its own limitations.'],
          ].map(([t, d]) => (
            <div className="pg" key={t}><h4>{t}</h4><p>{d}</p></div>
          ))}
        </div>
      </DocSection>

      <DocSection title="What this tool cannot know" className="limits">
        <p className="lead">
          Stated plainly because a cleanup tool that hides its blind spots is
          dangerous. None of these are bugs; they are structural.
        </p>
        <ul>
          <li><b>Runtime-built names.</b> Apex can assemble a field name from data
            we cannot see. Any class doing this quarantines every object it
            touches — deliberately blunt, because guessing deletes production fields.</li>
          <li><b>External integrations.</b> ETL jobs, middleware and partner apps
            keep their field mappings outside the org entirely.</li>
          <li><b>Unsaved reports.</b> Anything built ad hoc in the report builder
            and never saved leaves no metadata.</li>
          <li><b>Anonymous Apex.</b> Not persisted, so not inspectable.</li>
          <li><b>Managed package internals.</b> Installed code can read your fields
            via dynamic describe.</li>
          <li><b>Drift.</b> Anything created between the analysis and the deletion.</li>
          <li><b>Event Monitoring.</b> Without it, actual runtime reads are
            invisible. The run says so explicitly rather than treating silence as
            absence.</li>
        </ul>
        <p className="note strong">
          Because of these, an UNUSED verdict means: <b>no evidence of use was
          found, and every decisive check ran cleanly.</b> It is not an
          authorisation to delete. Follow the staged procedure in the report —
          deprecate in place, revoke access, observe a full business cycle, then
          delete in small batches.
        </p>
      </DocSection>

      <DocSection title="Where the AI is used, and where it is not">
        <div className="two-col">
          <div className="col-card good">
            <h4>It does</h4>
            <ul>
              <li>Summarise what a component appears to do</li>
              <li>Narrate the evidence chain in plain language</li>
              <li>Raise a concern that <em>downgrades</em> UNUSED to NEEDS REVIEW</li>
            </ul>
          </div>
          <div className="col-card bad">
            <h4>It never</h4>
            <ul>
              <li>Decides a verdict — the rules run first and pass it in read-only</li>
              <li>Confirms an absence — unfalsifiable and the costliest direction to be wrong</li>
              <li>Clears an uncertainty flag</li>
              <li>Touches the deletion path</li>
            </ul>
          </div>
        </div>
        <p className="note">
          Generated prose is shown in violet, used nowhere else, labelled
          unverified, and set in the proportional font while every verified fact
          is monospace. The exact prompt is viewable on each component.
        </p>
      </DocSection>

      <DocsInternals />
    </div>
  )
}
