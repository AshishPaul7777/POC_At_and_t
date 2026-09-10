import { memo, useMemo } from 'react'
import type { FileRef, FileView } from '../../lib/api'
import {
  APEX_KIND_HINT,
  APEX_KIND_LABEL,
  type ApexClassKind,
} from '../../lib/apexGroup'
import { explainHighlight } from '../../lib/explain'
import { VERDICT_DEFINITION, confidenceBand, verdictClass, verdictLabel } from '../../lib/verdict'
import { useWindow } from './useWindow'

/** Must match --code-row-h in styles.css, or windowing drifts from the render. */
const ROW_H = 20

/** The verdict sentence a hover card leads with, built once per component. */
const verdictLine = (r: FileRef) =>
  `${verdictLabel(r.verdict)}${r.verdict ? ` -- ${VERDICT_DEFINITION[r.verdict]}` : ''}`
  + (r.confidence != null
      ? `\n${confidenceBand(r.confidence)} confidence (${Math.round(r.confidence)})`
      : '')

const Line = memo(function Line(
  { text, refs, defVerdict, onPick }: {
    text: string
    refs: FileRef[]
    defVerdict: string | null
    onPick: (componentId: number) => void
  },
) {
  const parts: React.ReactNode[] = []
  let at = 0
  for (const r of refs) {
    if (r.col > at) parts.push(text.slice(at, r.col))
    parts.push(
      <span key={`${r.col}-${r.component_id}`}
            className={`hl ${verdictClass(r.verdict)} r-${r.region}`}
            title={explainHighlight(r, verdictLine(r))}
            onClick={(e) => { e.stopPropagation(); onPick(r.component_id) }}>
        {text.slice(r.col, r.end_col)}
      </span>,
    )
    at = Math.max(at, r.end_col)
  }
  if (at < text.length) parts.push(text.slice(at))

  return (
    <div className="code-line" data-def={defVerdict ?? undefined}>
      {parts.length ? parts : text || ' '}
    </div>
  )
})

export function CodeView(
  { view, onPick, revealLine }: {
    view: FileView
    onPick: (componentId: number) => void
    revealLine: number | null
  },
) {
  const lines = useMemo(() => view.content.split('\n'), [view.content])

  // References grouped by line, each group sorted by column. Done once per
  // file rather than per render pass of a line.
  const byLine = useMemo(() => {
    const m = new Map<number, FileRef[]>()
    for (const r of view.references) {
      const list = m.get(r.line)
      if (list) list.push(r)
      else m.set(r.line, [r])
    }
    for (const list of m.values()) list.sort((a, b) => a.col - b.col)
    return m
  }, [view.references])

  // Which lines belong to a definition, so the gutter can show whose they are.
  const defOf = useMemo(() => {
    const m = new Map<number, string>()
    for (const d of view.defines) {
      if (d.start_line == null) continue
      // The whole-file span of a class must not paint over its methods'
      // narrower spans, so wider spans are laid down first.
      const end = d.end_line ?? d.start_line
      for (let i = d.start_line; i <= end; i++) {
        const existing = m.get(i)
        const wider = end - d.start_line
        if (existing === undefined || wider < 9999) m.set(i, d.verdict ?? 'UNCLASSIFIED')
      }
    }
    return m
  }, [view.defines])

  const w = useWindow(lines.length, ROW_H)

  const classKinds = useMemo(() => {
    const cls = view.defines.find((d) => d.ctype === 'ApexClass' && d.apex_kinds?.length)
    return cls?.apex_kinds ?? []
  }, [view.defines])

  // Jumping to a component's definition from elsewhere in the app.
  useMemo(() => {
    if (revealLine != null) w.scrollTo(revealLine)
  }, [revealLine, w])

  return (
    <div className="code-view">
      <div className="code-head">
        <code>{view.path}</code>
        {classKinds.map((k) => (
          <span key={k} className={`tree-tag kind-${k}`}
                title={APEX_KIND_HINT[k as ApexClassKind] ?? k}>
            {APEX_KIND_LABEL[k as ApexClassKind] ?? k}
          </span>
        ))}
        <span className="spacer" />
        <span className="tb-hint">{view.line_count} lines</span>
        {view.stale && (
          <span className="badge UNUSED" title={view.stale_reason ?? undefined}>
            may not match this run
          </span>
        )}
      </div>

      {view.defines.length > 0 && (
        <div className="code-defines">
          {view.defines.map((d, i) => (
            <span key={i} className={`badge ${verdictClass(d.verdict)}`}
                  title={d.span_source
                    ? `lines ${(d.start_line ?? 0) + 1}-${(d.end_line ?? 0) + 1}`
                      + ` (located by ${d.span_source.replaceAll('_', ' ')})`
                    : 'no line span could be proved for this one'}>
              {d.api_name.split('.').pop()}
            </span>
          ))}
        </div>
      )}

      {/* Never silently drop a reference we could not place: a coloured line
          reads as authoritative, so the gaps have to be as visible as the hits. */}
      {view.notes.map((n, i) => <div className="caveat small" key={i}>{n}</div>)}
      {view.unlocated.length > 0 && (
        <div className="caveat small">
          Not shown on any line:{' '}
          {view.unlocated.map((u) => u.api_name).join(', ')}.{' '}
          {view.unlocated[0].why}
        </div>
      )}

      <div className="code-scroll" ref={w.ref} onScroll={w.onScroll}>
        <div style={{ height: w.totalHeight, position: 'relative' }}>
          <div style={{ transform: `translateY(${w.padTop}px)` }}>
            {lines.slice(w.first, w.last).map((text, i) => {
              const ln = w.first + i
              return (
                <div className="code-row" key={ln}>
                  <span className="code-gutter">{ln + 1}</span>
                  <Line text={text} refs={byLine.get(ln) ?? []}
                        defVerdict={defOf.get(ln) ?? null} onPick={onPick} />
                </div>
              )
            })}
          </div>
        </div>
      </div>
    </div>
  )
}
