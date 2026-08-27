import type { ComponentRow, Summary, Verdict } from '../lib/api'

/**
 * Analytics dashboard.
 *
 * Charts are hand-rolled inline SVG rather than a charting library: these forms
 * are simple, and a dependency would bring a default visual language that fights
 * the rest of the app. Every chart states what decision it supports - a chart
 * that supports no decision is decoration, which is the thing this UI must not
 * have.
 */

/* Verdict colours come from the theme, never from literals here. Hard-coding
   them meant the charts kept the old palette after a theme change and silently
   disagreed with the badges beside them about what "unused" looks like. */
const COLOUR: Record<string, string> = {
  USED: 'var(--used)',
  UNUSED: 'var(--unused)',
  NEEDS_REVIEW: 'var(--review)',
  OUT_OF_SCOPE: 'var(--scope)',
}
const ORDER: Verdict[] = ['USED', 'UNUSED', 'NEEDS_REVIEW', 'OUT_OF_SCOPE']

function StackedBar({ counts, height = 22 }: { counts: Partial<Record<Verdict, number>>; height?: number }) {
  const total = ORDER.reduce((a, k) => a + (counts[k] ?? 0), 0) || 1
  let x = 0
  return (
    <svg width="100%" height={height} role="img">
      {ORDER.map((k) => {
        const v = counts[k] ?? 0
        if (!v) return null
        const w = (v / total) * 100
        const el = (
          <rect key={k} x={`${x}%`} y={0} width={`${w}%`} height={height}
                fill={COLOUR[k]} opacity={0.85}>
            <title>{`${k.replaceAll('_', ' ')}: ${v}`}</title>
          </rect>
        )
        x += w
        return el
      })}
    </svg>
  )
}

function HBars({ rows, max, colour }: {
  rows: { label: string; value: number; sub?: string }[]
  max?: number
  colour: string
}) {
  const hi = max ?? Math.max(1, ...rows.map((r) => r.value))
  return (
    <div className="hbars">
      {rows.map((r) => (
        <div className="hbar" key={r.label}>
          <span className="hbar-label" title={r.label}>{r.label}</span>
          <span className="hbar-track">
            <i style={{ width: `${(r.value / hi) * 100}%`, background: colour }} />
          </span>
          <span className="hbar-value">{r.value}{r.sub ? <em> {r.sub}</em> : null}</span>
        </div>
      ))}
      {rows.length === 0 && <div className="empty">Nothing to show.</div>}
    </div>
  )
}

export function Dashboard({
  summary, rows, onPick, headerOnly = false,
}: {
  summary: Summary
  rows: ComponentRow[]
  onPick: (verdict: Verdict) => void
  /** Render only as far as the composition chart.
   *
   *  The Components Overview page puts the component table directly beneath
   *  this, so the cards that follow -- coverage, where to start, the review
   *  queue, collectors -- would sit between a heading and the table it
   *  introduces. They remain on their own pages. */
  headerOnly?: boolean
}) {
  const t = summary.totals
  const inScope = (t.USED ?? 0) + (t.UNUSED ?? 0) + (t.NEEDS_REVIEW ?? 0)

  // Cleanup opportunity per object. Sorted by count, never alphabetically -
  // the question is "where do I start", and that is a ranking.
  const byObject = new Map<string, { unused: number; review: number; total: number }>()
  for (const r of rows) {
    if (r.ctype !== 'CustomField' || !r.parent_object) continue
    const e = byObject.get(r.parent_object) ?? { unused: 0, review: 0, total: 0 }
    e.total++
    if (r.verdict === 'UNUSED') e.unused++
    if (r.verdict === 'NEEDS_REVIEW') e.review++
    byObject.set(r.parent_object, e)
  }
  const objectRows = [...byObject.entries()]
    .map(([label, v]) => ({
      label, value: v.unused + v.review, sub: `of ${v.total}`,
      pct: Math.round((100 * (v.unused + v.review)) / Math.max(v.total, 1)),
    }))
    .filter((r) => r.value > 0)
    .sort((a, b) => b.value - a.value)
    .slice(0, 12)

  // Why things need review. This is the actual work queue, grouped.
  const reasons = new Map<string, number>()
  for (const r of rows) {
    if (r.verdict !== 'NEEDS_REVIEW') continue
    for (const code of r.reason_codes ?? []) {
      const key = code.split(' (')[0]
      reasons.set(key, (reasons.get(key) ?? 0) + 1)
    }
  }
  const reasonRows = [...reasons.entries()]
    .map(([label, value]) => ({ label, value }))
    .sort((a, b) => b.value - a.value)
    .slice(0, 8)

  // Collector coverage: a trust instrument, not a progress bar.
  const okCollectors = summary.collectors.filter((c) => c.status === 'OK').length
  const coverage = Math.round((100 * okCollectors) / Math.max(summary.collectors.length, 1))

  const byType = Object.entries(summary.by_type)

  const unused = t.UNUSED ?? 0
  const review = t.NEEDS_REVIEW ?? 0

  return (
    <div className="dash">
      {/* The page answers one question first -- how much is safe to remove --
          and only then shows the working. Leading with a table of counts made
          the reader do that arithmetic themselves. */}
      <section className="hero">
        <div className="hero-top">
          <span className="hero-chip">
            {String(summary.run?.org_alias ?? 'THIS ORG')}
          </span>
          {coverage < 100 && (
            <span className="hero-chip">PARTIAL COVERAGE</span>
          )}
          <span className="spacer" />
          <span className="hero-elapsed">{inScope} components in scope</span>
        </div>
        <h1>
          {unused > 0
            ? `${unused} component${unused === 1 ? '' : 's'} look unused`
            : 'Nothing is confidently unused yet'}
        </h1>
        <p>
          {unused > 0
            ? `Each one was searched by every collector below, including the ones
               that found nothing. ${review} more need a human decision.`
            : `${review} components need a human decision. Nothing reached the
               confidence required to be called unused.`}
        </p>
        <div className="hero-actions">
          <button className="primary" onClick={() => onPick('UNUSED')}>
            Review the {unused} candidates
          </button>
          {review > 0 && (
            <button className="ghost onhero" onClick={() => onPick('NEEDS_REVIEW')}>
              See the {review} to decide
            </button>
          )}
        </div>
      </section>

      <div className="kpis">
        {ORDER.map((k) => (
          <button className={`kpi ${k.toLowerCase()}`} key={k} onClick={() => onPick(k)}>
            <div className="n" style={{ color: COLOUR[k] }}>{t[k] ?? 0}</div>
            <div className="bs-l">{k.replaceAll('_', ' ').toLowerCase()}</div>
            {k !== 'OUT_OF_SCOPE' && inScope > 0 && (
              <div className="pct">
                {Math.round((100 * (t[k] ?? 0)) / inScope)}% of in-scope
              </div>
            )}
          </button>
        ))}
      </div>

      <div className="cards">
        <section className="card wide">
          <h3>Composition by component type</h3>
          <p className="why">
            Where the cleanup opportunity actually is. A type that is almost all
            USED needs no attention.
          </p>
          {byType.map(([ctype, counts]) => {
            const total = ORDER.reduce((a, k) => a + (counts[k] ?? 0), 0)
            return (
              <div className="row-bar" key={ctype}>
                <span className="rb-label">{ctype}</span>
                <span className="rb-bar"><StackedBar counts={counts} /></span>
                <span className="rb-total">{total}</span>
              </div>
            )
          })}
          <div className="legend-inline">
            {ORDER.map((k) => (
              <span key={k}>
                <i className="swatch" style={{ background: COLOUR[k] }} />
                {k.replaceAll('_', ' ')}
              </span>
            ))}
          </div>
        </section>

        {!headerOnly && (<>
        <section className="card">
          <h3>Analysis coverage</h3>
          <p className="why">
            How much to trust this run. Anything below 100% means a check could
            not complete, and those components are held at NEEDS REVIEW.
          </p>
          <div className="big-stat">
            <span className="bs-n"
                  style={{ color: coverage === 100 ? COLOUR.USED : COLOUR.UNUSED }}>
              {coverage}%
            </span>
            <span className="bs-l">
              {okCollectors} of {summary.collectors.length} collectors OK
            </span>
          </div>
          {summary.coverage_caveats.length > 0 ? (
            summary.coverage_caveats.map((c, i) => (
              <div className="caveat small" key={i}>{c}</div>
            ))
          ) : (
            <div className="ok-note">No limitations detected.</div>
          )}
        </section>

        <section className="card">
          <h3>Where to start</h3>
          <p className="why">
            Fields per object needing action. Sorted by volume - one object with
            twelve dead fields is a better first sprint than twelve objects with one.
          </p>
          <HBars rows={objectRows} colour={COLOUR.UNUSED} />
        </section>

        <section className="card">
          <h3>The review queue, by reason</h3>
          <p className="why">
            What a human actually has to decide. Each reason maps to a specific
            question in the report.
          </p>
          <HBars rows={reasonRows} colour={COLOUR.NEEDS_REVIEW} />
        </section>

        <section className="card wide">
          <h3>Collectors</h3>
          <p className="why">
            Each ran independently and recorded what it found <em>and</em> what it
            did not. Their agreement is what makes a verdict trustworthy; no single
            collector decides one.
          </p>
          <div className="coll-grid">
            {summary.collectors.map((c) => (
              <div className="coll" key={c.collector_id} data-status={c.status}>
                <code>{c.collector_id}</code>
                <span className="spacer" />
                <span className="coll-status">{c.status}</span>
                <span className="coll-nums">
                  {c.hits}/{c.artifacts_searched}
                </span>
              </div>
            ))}
          </div>
        </section>
        </>)}
      </div>
    </div>
  )
}
