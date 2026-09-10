import type { ComponentRow, Summary, Verdict } from '../lib/api'
import {
  type ApexClassKind,
  countByVerdict,
  groupApex,
  groupApexByKind,
} from '../lib/apexGroup'
import { VERDICT_DEFINITION, verdictClass, verdictLabel } from '../lib/verdict'

/**
 * Results overview — written for a cleanup decision, not for engineers.
 * Every click opens Components with a matching filter.
 */

const COLOUR: Record<string, string> = {
  USED: 'var(--used)',
  UNUSED: 'var(--unused)',
  NEEDS_REVIEW: 'var(--review)',
  OUT_OF_SCOPE: 'var(--scope)',
}

const TYPE_LABEL: Record<string, string> = {
  ApexClass: 'Apex classes',
  ApexMethod: 'Apex methods',
  ApexTrigger: 'Triggers',
  CustomField: 'Custom fields',
  CustomObject: 'Custom objects',
  StandardObject: 'Standard objects',
  LightningComponentBundle: 'Lightning web components',
  AuraDefinitionBundle: 'Aura components',
}

const TYPE_HINT: Record<string, string> = {
  ApexClass: 'Code that runs in Salesforce',
  ApexMethod: 'Individual methods inside classes',
  ApexTrigger: 'Code that fires when records change',
  CustomField: 'Extra fields on your objects',
  CustomObject: 'Custom tables you created',
  StandardObject: 'Built-in Salesforce objects',
  LightningComponentBundle: 'Lightning web UI components',
  AuraDefinitionBundle: 'Aura UI components',
}

export type OverviewFilter = {
  verdict?: Verdict | 'ALL'
  ctype?: string
  mode?: 'list' | 'apex'
  focusClass?: string
  apexKind?: ApexClassKind
}

function typeLabel(ctype: string): string {
  return TYPE_LABEL[ctype] ?? ctype.replace(/([a-z])([A-Z])/g, '$1 $2')
}

/** Turn technical coverage notes into one short plain sentence, or drop them. */
function plainWarning(raw: string): string | null {
  const t = raw.toLowerCase()
  if (t.includes('event monitoring')) {
    return 'We cannot see traffic from outside Salesforce (integrations, page views). Some items marked unused may still be called from there.'
  }
  if (t.includes('dependency api')) {
    return 'Salesforce’s Dependency API did not return edges here, so we relied on our other checks.'
  }
  if (/^c\d+_/i.test(raw.trim())) return null
  if (raw.length > 180) return null
  return raw
}

function Donut({
  used, unused, review, outOfScope, onSlice,
}: {
  used: number
  unused: number
  review: number
  outOfScope: number
  onSlice: (v: Verdict) => void
}) {
  const slices: { key: Verdict; n: number; label: string; tip: string }[] = [
    { key: 'UNUSED', n: unused, label: 'Likely unused', tip: 'Open likely unused' },
    { key: 'NEEDS_REVIEW', n: review, label: 'Needs a decision', tip: 'Open items to decide' },
    { key: 'USED', n: used, label: 'Still in use', tip: 'Open items still in use' },
    { key: 'OUT_OF_SCOPE', n: outOfScope, label: 'Not ours to delete', tip: 'Open out-of-scope items' },
  ]
  const total = slices.reduce((a, s) => a + s.n, 0) || 1
  const r = 68
  const c = 2 * Math.PI * r
  let offset = 0

  return (
    <div className="ov-donut-wrap">
      <svg className="ov-donut" viewBox="0 0 180 180" role="img"
           aria-label="Results breakdown">
        <circle cx="90" cy="90" r={r} fill="none" stroke="var(--hover)"
                strokeWidth="28" />
        {slices.map((s) => {
          if (!s.n) return null
          const len = (s.n / total) * c
          const dash = `${len} ${c - len}`
          const el = (
            <circle key={s.key} cx="90" cy="90" r={r} fill="none"
                    stroke={COLOUR[s.key]} strokeWidth="28"
                    strokeDasharray={dash} strokeDashoffset={-offset}
                    transform="rotate(-90 90 90)"
                    className="ov-donut-slice"
                    style={{ cursor: 'pointer' }}
                    onClick={() => onSlice(s.key)}>
              <title>{s.tip}</title>
            </circle>
          )
          offset += len
          return el
        })}
        <text x="90" y="86" textAnchor="middle" className="ov-donut-n">
          {total}
        </text>
        <text x="90" y="106" textAnchor="middle" className="ov-donut-l">
          items
        </text>
      </svg>
      <ul className="ov-donut-legend">
        {slices.map((s) => (
          <li key={s.key}>
            <button type="button" className="ov-legend-btn"
                    onClick={() => onSlice(s.key)}
                    title={VERDICT_DEFINITION[s.key]}>
              <i className="swatch" style={{ background: COLOUR[s.key] }} />
              <span className="ov-legend-label">{s.label}</span>
              <strong>{s.n}</strong>
            </button>
          </li>
        ))}
      </ul>
    </div>
  )
}

export function Dashboard({
  summary, allRows, onOpenComponents,
}: {
  summary: Summary
  allRows: ComponentRow[]
  onOpenComponents: (filter?: OverviewFilter) => void
}) {
  const t = summary.totals
  const unused = t.UNUSED ?? 0
  const review = t.NEEDS_REVIEW ?? 0
  const used = t.USED ?? 0
  const outOfScope = t.OUT_OF_SCOPE ?? 0
  const org = String(summary.run?.org_alias ?? 'This org')

  // Only the real analysis checks (C10–C90). Inventory probes (C00_*) are
  // plumbing and should not appear on a client-facing overview.
  const analysisChecks = summary.collectors.filter((c) =>
    /^C[1-9]\d_/.test(c.collector_id))
  const weakChecks = analysisChecks.filter((c) =>
    c.status === 'PARTIAL' || c.status === 'UNAVAILABLE' || c.status === 'ERROR')
  const scanOk = weakChecks.length === 0

  const { groups } = groupApex(allRows)
  const classCounts = countByVerdict(groups.map((g) => g.cls))
  const methodRows = allRows.filter((r) => r.ctype === 'ApexMethod')
  const methodCounts = countByVerdict(methodRows)
  const apexKinds = groupApexByKind(allRows)

  // One card per judged type — Apex classes and methods stay separate.
  const kinds = Object.entries(summary.by_type)
    .map(([ctype, counts]) => ({
      ctype,
      used: counts.USED ?? 0,
      unused: counts.UNUSED ?? 0,
      review: counts.NEEDS_REVIEW ?? 0,
      out: counts.OUT_OF_SCOPE ?? 0,
      total: (counts.USED ?? 0) + (counts.UNUSED ?? 0)
        + (counts.NEEDS_REVIEW ?? 0) + (counts.OUT_OF_SCOPE ?? 0),
    }))
    .filter((r) => r.total > 0)
    .sort((a, b) => b.total - a.total)

  const warnings = summary.coverage_caveats
    .map(plainWarning)
    .filter((x): x is string => !!x)

  return (
    <div className="dash overview-page">
      <section className="hero">
        <div className="hero-top">
          <span className="hero-chip">{org}</span>
          {!scanOk && <span className="hero-chip">SOME CHECKS LIMITED</span>}
          <span className="spacer" />
        </div>
        <h1>
          {unused > 0
            ? `${unused} item${unused === 1 ? '' : 's'} look safe to remove`
            : 'Nothing looks safe to remove yet'}
        </h1>
        <p>
          {unused > 0
            ? `No clear use found for these. ${review > 0
                ? `${review} more need someone to decide.`
                : 'Review each one before you delete anything.'}`
            : review > 0
              ? `${review} item${review === 1 ? '' : 's'} need someone to decide.`
              : 'Everything we checked still looks needed, or cannot be deleted here.'}
        </p>
        <div className="hero-actions">
          {unused > 0 && (
            <button type="button" className="primary"
                    onClick={() => onOpenComponents({ verdict: 'UNUSED' })}>
              Show likely unused
            </button>
          )}
          {review > 0 && (
            <button type="button" className={unused > 0 ? 'ghost onhero' : 'primary'}
                    onClick={() => onOpenComponents({ verdict: 'NEEDS_REVIEW' })}>
              Show needs a decision
            </button>
          )}
          <button type="button" className="ghost onhero"
                  onClick={() => onOpenComponents({ verdict: 'ALL' })}>
            Show everything
          </button>
        </div>
      </section>

      <div className="kpis">
        <button type="button" className="kpi unused"
                title={VERDICT_DEFINITION.UNUSED}
                onClick={() => onOpenComponents({ verdict: 'UNUSED' })}>
          <div className="n" style={{ color: COLOUR.UNUSED }}>{unused}</div>
          <div className="bs-l">Likely unused</div>
          <div className="pct">Cleanup candidates</div>
        </button>
        <button type="button" className="kpi needs_review"
                title={VERDICT_DEFINITION.NEEDS_REVIEW}
                onClick={() => onOpenComponents({ verdict: 'NEEDS_REVIEW' })}>
          <div className="n" style={{ color: COLOUR.NEEDS_REVIEW }}>{review}</div>
          <div className="bs-l">Needs a decision</div>
          <div className="pct">Check before deleting</div>
        </button>
        <button type="button" className="kpi used"
                title={VERDICT_DEFINITION.USED}
                onClick={() => onOpenComponents({ verdict: 'USED' })}>
          <div className="n" style={{ color: COLOUR.USED }}>{used}</div>
          <div className="bs-l">Still in use</div>
          <div className="pct">Leave these alone</div>
        </button>
        <button type="button" className="kpi out_of_scope"
                title={VERDICT_DEFINITION.OUT_OF_SCOPE}
                onClick={() => onOpenComponents({ verdict: 'OUT_OF_SCOPE' })}>
          <div className="n" style={{ color: COLOUR.OUT_OF_SCOPE }}>{outOfScope}</div>
          <div className="bs-l">Not ours to delete</div>
          <div className="pct">Packages / platform</div>
        </button>
      </div>

      <div className="cards overview-grid">
        <section className="card">
          <h3>The big picture</h3>
          <p className="why">Click a slice or a row to open that list.</p>
          <Donut used={used} unused={unused} review={review} outOfScope={outOfScope}
                 onSlice={(v) => onOpenComponents({ verdict: v })} />
        </section>

        <section className="card">
          <h3>Scan confidence</h3>
          {scanOk ? (
            <>
              <p className="ov-ok">All main checks finished.</p>
              <p className="why">
                You can trust the unused list as our best view of this org.
                Still review before deleting.
              </p>
            </>
          ) : (
            <>
              <p className="ov-warn">
                {weakChecks.length} check{weakChecks.length === 1 ? '' : 's'} could
                not finish fully.
              </p>
              <p className="why">
                Treat “likely unused” with a bit more care — some usage may be
                invisible to us.
              </p>
            </>
          )}
          {warnings.length > 0 && (
            <div className="caveat-block">
              {warnings.map((w) => (
                <p className="caveat small" key={w}>{w}</p>
              ))}
            </div>
          )}
        </section>

        <section className="card wide">
          <h3>Apex by class type</h3>
          <p className="why">
            Results grouped by how the class is exposed — REST, scheduled,
            batch, ordinary helpers, and so on — not a flat class list.
          </p>
          <div className="ov-apex-stats">
            <span>
              Classes:{' '}
              <strong style={{ color: 'var(--used)' }}>{classCounts.USED ?? 0}</strong> in use
              {' · '}
              <strong style={{ color: 'var(--unused)' }}>{classCounts.UNUSED ?? 0}</strong> unused
              {' · '}
              <strong style={{ color: 'var(--review)' }}>{classCounts.NEEDS_REVIEW ?? 0}</strong> to decide
            </span>
            <span>
              Methods:{' '}
              <strong style={{ color: 'var(--unused)' }}>{methodCounts.UNUSED ?? 0}</strong> unused
              {' · '}
              <strong style={{ color: 'var(--review)' }}>{methodCounts.NEEDS_REVIEW ?? 0}</strong> to decide
            </span>
          </div>
          {apexKinds.length > 0 ? (
            <ul className="ov-apex-list">
              {apexKinds.map((b) => {
                const n = b.groups.length
                const unused = b.classCounts.UNUSED ?? 0
                const review = b.classCounts.NEEDS_REVIEW ?? 0
                const used = b.classCounts.USED ?? 0
                const mUnused = b.methodCounts.UNUSED ?? 0
                return (
                  <li key={b.kind}>
                    <button type="button" className="ov-apex-row ov-apex-kind"
                            title={b.hint}
                            onClick={() => onOpenComponents({
                              mode: 'apex',
                              apexKind: b.kind,
                            })}>
                      <strong className="ov-apex-kind-name">{b.label}</strong>
                      <span className="ov-apex-meta">
                        {n} class{n === 1 ? '' : 'es'}
                      </span>
                      <span className="ov-apex-kind-verdicts">
                        {unused > 0 && (
                          <span className={`badge ${verdictClass('UNUSED')}`}>
                            {unused} unused
                          </span>
                        )}
                        {review > 0 && (
                          <span className={`badge ${verdictClass('NEEDS_REVIEW')}`}>
                            {review} to decide
                          </span>
                        )}
                        {used > 0 && (
                          <span className={`badge ${verdictClass('USED')}`}>
                            {used} in use
                          </span>
                        )}
                        {!unused && !review && !used && (
                          <span className="badge">{verdictLabel(null)}</span>
                        )}
                      </span>
                      <span className="ov-apex-because">
                        {b.hint}
                        {mUnused > 0 ? ` · ${mUnused} method${mUnused === 1 ? '' : 's'} unused` : ''}
                      </span>
                    </button>
                  </li>
                )
              })}
            </ul>
          ) : (
            <p className="why">No Apex classes in this run.</p>
          )}
          <div className="hero-actions" style={{ marginTop: 12 }}>
            <button type="button" className="ghost"
                    onClick={() => onOpenComponents({ mode: 'apex' })}>
              Review Apex
            </button>
          </div>
        </section>

        <section className="card wide">
          <h3>Start here — browse by kind</h3>
          <p className="why">
            Every kind we analysed. Open one to see its items.
          </p>
          <div className="ov-kind-grid">
            {kinds.map((r) => (
              <button type="button" className="ov-kind-card" key={r.ctype}
                      onClick={() => onOpenComponents({
                        ctype: r.ctype,
                        verdict: 'ALL',
                      })}>
                <div className="ov-kind-top">
                  <strong>{typeLabel(r.ctype)}</strong>
                  <span className="ov-kind-total">{r.total}</span>
                </div>
                <p className="ov-kind-hint">
                  {TYPE_HINT[r.ctype] ?? 'Open this list'}
                </p>
                <div className="ov-kind-stats">
                  {r.unused > 0 && (
                    <span style={{ color: 'var(--unused)' }}>{r.unused} unused</span>
                  )}
                  {r.review > 0 && (
                    <span style={{ color: 'var(--review)' }}>{r.review} to decide</span>
                  )}
                  {r.used > 0 && (
                    <span style={{ color: 'var(--used)' }}>{r.used} in use</span>
                  )}
                  {r.out > 0 && (
                    <span style={{ color: 'var(--scope)' }}>{r.out} out of scope</span>
                  )}
                </div>
              </button>
            ))}
          </div>
        </section>
      </div>
    </div>
  )
}
