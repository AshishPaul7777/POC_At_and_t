import { useEffect, useMemo, useState } from 'react'
import type { Verdict } from '../lib/api'
import { SIGNAL, collectorName, isFlagOnlyCollector } from '../lib/explain'
import {
  VERDICT_DEFINITION,
  VERDICT_LABEL,
  VERDICT_ORDER,
} from '../lib/verdict'

/** Outcomes the Components list can filter on (evidence or flag-only). */
export type EvidenceResultFilter =
  | 'EVIDENCE_OF_USE'
  | 'NO_EVIDENCE_FOUND'
  | 'NOT_APPLICABLE'
  | 'INCONCLUSIVE'
  | 'FAILED'
  | 'FLAGGED'
  | 'NO_FLAG'

export interface CollectorEvidenceTag {
  collector_id: string
  result: EvidenceResultFilter
}

const EVIDENCE_RESULT_OPTIONS: {
  value: EvidenceResultFilter
  label: string
}[] = [
  { value: 'EVIDENCE_OF_USE', label: 'Found' },
  { value: 'NO_EVIDENCE_FOUND', label: 'Nothing found' },
  { value: 'NOT_APPLICABLE', label: 'Not applicable' },
  { value: 'INCONCLUSIVE', label: 'Inconclusive' },
  { value: 'FAILED', label: 'Could not run' },
]

const FLAG_RESULT_OPTIONS: {
  value: EvidenceResultFilter
  label: string
}[] = [
  { value: 'FLAGGED', label: 'Flagged' },
  { value: 'NO_FLAG', label: 'No flag' },
]

const COLLECTOR_OPTIONS = Object.keys(SIGNAL)

type FilterKind = 'status' | 'type' | 'check'

export function evidenceResultLabel(result: EvidenceResultFilter): string {
  return (
    [...EVIDENCE_RESULT_OPTIONS, ...FLAG_RESULT_OPTIONS]
      .find((o) => o.value === result)?.label ?? result
  )
}

export function tagKey(t: CollectorEvidenceTag): string {
  return `${t.collector_id}:${t.result}`
}

function outcomeOptionsFor(collectorId: string) {
  return isFlagOnlyCollector(collectorId)
    ? FLAG_RESULT_OPTIONS
    : EVIDENCE_RESULT_OPTIONS
}

type Chip =
  | { key: string; kind: 'status'; label: string; title?: string; tone: string
      remove: () => void }
  | { key: string; kind: 'type'; label: string; title?: string; tone: string
      remove: () => void }
  | { key: string; kind: 'check'; label: string; title?: string; tone: string
      remove: () => void }

/**
 * One add-control + one chip list for status, type, and check outcomes.
 * Status and type are single tags; checks can stack (ANDed).
 */
export function ComponentFilterTags({
  verdict,
  onVerdict,
  ctype,
  onCtype,
  typeOptions,
  verdictCounts,
  checkTags,
  onCheckTags,
}: Readonly<{
  verdict: Verdict | 'ALL'
  onVerdict: (v: Verdict | 'ALL') => void
  ctype: string
  onCtype: (t: string) => void
  typeOptions: { type: string; count: number }[]
  verdictCounts: Partial<Record<Verdict, number>>
  checkTags: CollectorEvidenceTag[]
  onCheckTags: (next: CollectorEvidenceTag[]) => void
}>) {
  const [kind, setKind] = useState<FilterKind>('status')
  const [statusVal, setStatusVal] = useState<Verdict>(VERDICT_ORDER[0])
  const [typeVal, setTypeVal] = useState(typeOptions[0]?.type ?? '')
  const [collector, setCollector] = useState(COLLECTOR_OPTIONS[0] ?? '')
  const [result, setResult] = useState<EvidenceResultFilter>('EVIDENCE_OF_USE')

  const outcomeOptions = outcomeOptionsFor(collector)

  useEffect(() => {
    const opts = outcomeOptionsFor(collector)
    setResult((cur) => (opts.some((o) => o.value === cur)
      ? cur
      : (opts[0]?.value ?? 'EVIDENCE_OF_USE')))
  }, [collector])

  // Keep type picker in sync when options load after the first paint.
  const typePick = typeVal && typeOptions.some((o) => o.type === typeVal)
    ? typeVal
    : (typeOptions[0]?.type ?? '')

  const chips: Chip[] = useMemo(() => {
    const out: Chip[] = []
    if (verdict !== 'ALL') {
      out.push({
        key: `status:${verdict}`,
        kind: 'status',
        label: `Status · ${VERDICT_LABEL[verdict]}`,
        title: VERDICT_DEFINITION[verdict],
        tone: `status-${verdict}`,
        remove: () => onVerdict('ALL'),
      })
    }
    if (ctype !== 'ALL') {
      out.push({
        key: `type:${ctype}`,
        kind: 'type',
        label: `Type · ${ctype}`,
        tone: 'type',
        remove: () => onCtype('ALL'),
      })
    }
    for (const t of checkTags) {
      out.push({
        key: tagKey(t),
        kind: 'check',
        label: `${collectorName(t.collector_id)} · ${evidenceResultLabel(t.result)}`,
        tone: `result-${t.result}`,
        remove: () => onCheckTags(checkTags.filter((x) => tagKey(x) !== tagKey(t))),
      })
    }
    return out
  }, [verdict, ctype, checkTags, onVerdict, onCtype, onCheckTags])

  const add = () => {
    if (kind === 'status') {
      onVerdict(statusVal)
      return
    }
    if (kind === 'type') {
      if (!typePick) return
      onCtype(typePick)
      return
    }
    if (!collector) return
    const next: CollectorEvidenceTag = { collector_id: collector, result }
    if (checkTags.some((t) => tagKey(t) === tagKey(next))) return
    onCheckTags([...checkTags, next])
  }

  return (
    <div className="tag-filters">
      <div className="tag-filters-add">
        <select value={kind} onChange={(e) => setKind(e.target.value as FilterKind)}
                aria-label="Filter kind" title="What to filter by">
          <option value="status">Status</option>
          <option value="type">Type</option>
          <option value="check">Check</option>
        </select>

        {kind === 'status' && (
          <select value={statusVal}
                  onChange={(e) => setStatusVal(e.target.value as Verdict)}
                  aria-label="Status" title={VERDICT_DEFINITION[statusVal]}>
            {VERDICT_ORDER.map((v) => (
              <option key={v} value={v}>
                {VERDICT_LABEL[v]}
                {verdictCounts[v] != null ? ` (${verdictCounts[v]})` : ''}
              </option>
            ))}
          </select>
        )}

        {kind === 'type' && (
          <select value={typePick}
                  onChange={(e) => setTypeVal(e.target.value)}
                  aria-label="Component type"
                  disabled={typeOptions.length === 0}>
            {typeOptions.length === 0 && <option value="">No types</option>}
            {typeOptions.map(({ type, count }) => (
              <option key={type} value={type}>{type} ({count})</option>
            ))}
          </select>
        )}

        {kind === 'check' && (
          <>
            <select value={collector} onChange={(e) => setCollector(e.target.value)}
                    aria-label="Collector" title="Collector / check">
              {COLLECTOR_OPTIONS.map((id) => (
                <option key={id} value={id}>{collectorName(id)}</option>
              ))}
            </select>
            <select value={result}
                    onChange={(e) => setResult(e.target.value as EvidenceResultFilter)}
                    aria-label="Outcome" title="Outcome">
              {outcomeOptions.map((o) => (
                <option key={o.value} value={o.value}>{o.label}</option>
              ))}
            </select>
          </>
        )}

        <button type="button" className="ghost" onClick={add}
                title="Add this filter as a tag">
          Add
        </button>
      </div>

      {chips.length > 0 && (
        <ul className="tag-filters-chips">
          {chips.map((c) => (
            <li key={c.key}>
              <button
                type="button"
                className={`ftag ${c.tone}`}
                onClick={c.remove}
                title={c.title ? `${c.title} (click to remove)` : 'Remove filter'}
              >
                <span>{c.label}</span>
                <span className="ftag-x" aria-hidden>×</span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}
