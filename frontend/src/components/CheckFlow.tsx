import { useEffect, useRef, useState } from 'react'
import type { Detail, Evidence, Verdict } from '../lib/api'
import {
  AI_NARRATION_STEP_ID,
  aiNarrationStepMeta,
  collectorChecks,
  collectorDetail,
  collectorName,
  collectorQuestion,
  collectorsForType,
  explainAiNarration,
  explainDecision,
  explainExternalEntry,
  EXTERNAL_ENTRY_STEP_ID,
  externalEntryStepMeta,
  flagCheckOutcome,
  isFlagOnlyCollector,
  notApplicableReason,
  signalSentence,
  unusedFlavorLabel,
} from '../lib/explain'
import { verdictLabel } from '../lib/verdict'

type RefRow = Detail['references'][number]

/** One concrete finding shown under a check-flow step. */
export interface Finding {
  key: string
  label: string
  meta?: string
  /** Open inventoried component (detail / explorer). */
  componentId?: number | null
  /** Relative workspace path when no component id. */
  filePath?: string | null
}

type StepState =
  | 'hit' | 'clear' | 'na' | 'incomplete' | 'surface' | 'verdict'
  | 'flagged' | 'noflag'

interface Step {
  id: string
  label: string
  state: StepState
  question?: string
  checks?: string
  detail?: string
  outcome: string
  naReason?: string
  findings?: Finding[]
  findingsTitle?: string
  verdict?: Verdict | string | null
  rule?: string | null
  because?: string
  flavorLabel?: string | null
}

function stepState(ev: Evidence | undefined, gap: boolean): StepState {
  if (gap) return 'incomplete'
  if (!ev) return 'na'
  if (ev.result === 'EVIDENCE_OF_USE') return 'hit'
  if (ev.result === 'NO_EVIDENCE_FOUND') return 'clear'
  if (ev.result === 'NOT_APPLICABLE') return 'na'
  return 'incomplete'
}

function stateLabel(s: StepState, collectorId?: string): string {
  switch (s) {
    case 'hit': return 'Found use'
    case 'clear': return 'Nothing found'
    // Delete rehearsal skips components that aren't deletion candidates by
    // design, not because the check doesn't apply to their type — "not
    // required" reflects that distinction instead of implying a mismatch.
    case 'na': return collectorId === 'C90_delete_rehearsal' ? 'Not required' : 'Not applicable'
    case 'incomplete': return 'Incomplete'
    case 'surface': return 'Surface found'
    case 'verdict': return 'Verdict'
    case 'flagged': return 'Flagged'
    case 'noflag': return 'No flag'
  }
}

/** Deduped inbound static refs (self already dropped by the API). */
function uniqueStaticRefs(refs: RefRow[]): RefRow[] {
  const seen = new Set<string>()
  const out: RefRow[] = []
  for (const r of refs) {
    const key = `${r.metadata_type}|${r.member_name.toLowerCase()}`
    if (seen.has(key)) continue
    seen.add(key)
    out.push(r)
  }
  return out
}

function findingsForCollector(id: string, detail: Detail): {
  title: string
  items: Finding[]
} | null {
  const ev = detail.evidence.find((e) => e.collector_id === id)
  const payload = (ev?.payload ?? {}) as Record<string, unknown>

  if (id === 'C10_static_index') {
    const refs = uniqueStaticRefs(detail.references).slice(0, 20)
    if (!refs.length) return null
    return {
      title: 'References found',
      items: refs.map((r, i) => ({
        key: `c10-${r.metadata_type}-${r.member_name}-${i}`,
        label: r.member_name,
        meta: `${r.metadata_type} · ${r.tier === 'A' ? 'binding' : r.tier === 'B' ? 'observed' : 'weak'}`,
        componentId: r.from_component_id,
        filePath: r.file_path,
      })),
    }
  }

  if (id === 'C30_dependency_api') {
    const by = (payload.referenced_by as { by?: string; by_type?: string }[] | undefined) ?? []
    if (!by.length) return null
    const seen = new Set<string>()
    const items: Finding[] = []
    for (const row of by) {
      const name = row.by || '?'
      const typ = row.by_type || 'Unknown'
      const key = `${typ}|${name}`.toLowerCase()
      if (seen.has(key)) continue
      seen.add(key)
      items.push({
        key: `c30-${key}`,
        label: name,
        meta: typ,
      })
    }
    return items.length ? { title: 'Dependencies (Salesforce)', items } : null
  }

  if (id === 'C20_data_population') {
    const items: Finding[] = []
    if (payload.populated != null) {
      items.push({
        key: 'c20-pop',
        label: `${payload.populated} of ${payload.total ?? '?'} records populated`,
        meta: payload.pct != null ? `${payload.pct}%` : undefined,
      })
    } else if (payload.record_count != null) {
      items.push({
        key: 'c20-count',
        label: `${payload.record_count} records on object`,
      })
    } else if (payload.has_data === true) {
      items.push({ key: 'c20-any', label: 'At least one record holds a value' })
    }
    return items.length ? { title: 'Data found', items } : null
  }

  if (id === 'C40_runtime') {
    const items: Finding[] = []
    const entity = typeof payload.entity_name === 'string' ? payload.entity_name : ''
    if (entity) {
      items.push({ key: 'c40-entity', label: entity, meta: 'entity' })
    }
    if (Number(payload.async_runs) > 0) {
      items.push({
        key: 'c40-async',
        label: `${payload.async_runs} async job run(s)`,
      })
    }
    if (payload.scheduled) {
      items.push({ key: 'c40-sched', label: 'Listed on a scheduled job' })
    }
    if (Number(payload.event_log_executions) > 0) {
      items.push({
        key: 'c40-exec',
        label: `${payload.event_log_executions} event-log execution(s)`,
        meta: payload.event_log_last_seen
          ? `last ${String(payload.event_log_last_seen)}` : undefined,
      })
    }
    if (Number(payload.event_log_triggers) > 0) {
      items.push({
        key: 'c40-trig',
        label: `${payload.event_log_triggers} event-log trigger fire(s)`,
      })
    }
    if (Number(payload.event_log_callouts) > 0) {
      items.push({
        key: 'c40-call',
        label: `${payload.event_log_callouts} event-log callout(s)`,
      })
    }
    if (Number(payload.event_log_ui_views) > 0) {
      items.push({
        key: 'c40-ui',
        label: `${payload.event_log_ui_views} UI interaction(s)`,
      })
    }
    if (Number(payload.event_log_object_accesses) > 0) {
      items.push({
        key: 'c40-obj',
        label: `${payload.event_log_object_accesses} object access(es)`,
      })
    }
    if (Number(payload.event_log_field_refs) > 0) {
      items.push({
        key: 'c40-fld',
        label: `${payload.event_log_field_refs} field reference(s) in queries`,
      })
    }
    if (Number(payload.event_log_rest_hits) > 0) {
      items.push({
        key: 'c40-rest',
        label: `${payload.event_log_rest_hits} Apex REST hit(s)`,
      })
    }
    const samples = (payload.event_log_samples as string[] | undefined) ?? []
    for (const s of samples.slice(0, 5)) {
      items.push({ key: `c40-s-${s}`, label: s, meta: 'sample' })
    }
    return items.length ? { title: 'Runtime activity', items } : null
  }

  if (id === 'C50_temporal') {
    const flag = detail.flags.find((f) => f.code === 'RECENTLY_CHANGED')
    if (!flag) return null
    const src = flag.source_ref ?? {}
    const items: Finding[] = [{
      key: 'c50-changed',
      label: src.last_changed
        ? `Last changed ${String(src.last_changed)}`
        : (flag.detail || 'Recently changed'),
      meta: src.window_days != null
        ? `${src.window_days}-day window`
        : undefined,
    }]
    return { title: 'Recency', items }
  }

  if (id === 'C60_dynamic_apex') {
    const flag = detail.flags.find((f) => f.code === 'DYNAMIC_APEX_IN_SCOPE')
    if (!flag) return null
    const sources = (flag.source_ref?.sources as {
      api_name?: string
      component_id?: number | null
      file_path?: string | null
    }[] | undefined) ?? []
    const seen = new Set<string>()
    const items: Finding[] = []
    for (const s of sources) {
      const name = s.api_name || '?'
      if (seen.has(name.toLowerCase())) continue
      seen.add(name.toLowerCase())
      items.push({
        key: `c60-${name}`,
        label: name,
        meta: 'dynamic Apex',
        componentId: s.component_id,
        filePath: s.file_path,
      })
    }
    if (!items.length && flag.detail) {
      items.push({ key: 'c60-detail', label: flag.detail })
    }
    return items.length ? { title: 'Dynamic Apex sources', items } : null
  }

  if (id === 'C70_reachability') {
    const path = (payload.path as string[] | undefined) ?? []
    if (path.length) {
      return {
        title: 'Path from entry point',
        items: [{
          key: 'c70-path',
          label: path.join(' → '),
          meta: payload.entry_point_type
            ? String(payload.entry_point_type) : undefined,
        }],
      }
    }
    if (payload.self_entry_point) {
      return {
        title: 'Entry surface',
        items: [{
          key: 'c70-self',
          label: String(payload.entry_reason || 'Self entry point'),
        }],
      }
    }
    return null
  }

  if (id === 'C80_config_data') {
    const found = (payload.found_in as {
      object?: string; field?: string; value?: string
    }[] | undefined) ?? []
    if (!found.length) return null
    const seen = new Set<string>()
    const items: Finding[] = []
    for (const row of found) {
      const key = `${row.object}.${row.field}=${row.value}`.toLowerCase()
      if (seen.has(key)) continue
      seen.add(key)
      items.push({
        key: `c80-${key}`,
        label: String(row.value ?? '?'),
        meta: [row.object, row.field].filter(Boolean).join('.'),
      })
    }
    return items.length ? { title: 'Stored as config data', items } : null
  }

  if (id === 'C90_delete_rehearsal') {
    const items: Finding[] = []
    if (payload.rehearsal) {
      items.push({
        key: 'c90-res',
        label: String(payload.rehearsal),
        meta: payload.server_validated ? 'server-validated' : undefined,
      })
    }
    if (payload.blocker) {
      items.push({
        key: 'c90-block',
        label: String(payload.blocker),
        meta: 'blocker',
      })
    }
    if (payload.salesforce_said) {
      items.push({
        key: 'c90-sf',
        label: String(payload.salesforce_said).slice(0, 160),
        meta: 'Salesforce',
      })
    }
    return items.length ? { title: 'Rehearsal result', items } : null
  }

  return null
}

function recentDaysFromDetail(detail: Detail): number | null {
  const fromRun = detail.run_config?.recent_change_days
  if (typeof fromRun === 'number') return fromRun
  const flag = detail.flags.find((f) => f.code === 'RECENTLY_CHANGED')
  const fromFlag = flag?.source_ref?.window_days
  return typeof fromFlag === 'number' ? fromFlag : null
}

export function CheckFlow(
  { detail, onOpenComponent, onReveal }: {
    detail: Detail
    onOpenComponent?: (componentId: number) => void
    onReveal?: (componentId: number) => void
  },
) {
  const ctype = String(detail.component.ctype ?? '')
  const cl = detail.classification
  const byId = Object.fromEntries(detail.evidence.map((e) => [e.collector_id, e]))
  const recentDays = recentDaysFromDetail(detail)
  const tipOpts = { recentChangeDays: recentDays }
  const decision = cl
    ? explainDecision({
        label: cl.label,
        reason_codes: cl.reason_codes,
        rule_trace: cl.rule_trace,
        ctype,
      })
    : null

  const steps: Step[] = collectorsForType(ctype).map((id) => {
    const ev = byId[id]
    const gap = detail.gaps.find((g) => g.collector_id === id)
    const found = findingsForCollector(id, detail)

    if (isFlagOnlyCollector(id)) {
      if (gap) {
        return {
          id,
          label: collectorName(id),
          state: 'incomplete' as const,
          question: collectorQuestion(id),
          checks: collectorChecks(id, tipOpts),
          detail: collectorDetail(id, tipOpts),
          outcome: gap.detail || gap.reason,
          naReason: gap.reason,
        }
      }
      const { flagged, outcome } = flagCheckOutcome(id, detail.flags, tipOpts)
      return {
        id,
        label: collectorName(id),
        state: flagged ? 'flagged' as const : 'noflag' as const,
        question: collectorQuestion(id),
        checks: collectorChecks(id, tipOpts),
        detail: collectorDetail(id, tipOpts),
        outcome,
        findings: found?.items,
        findingsTitle: found?.title,
      }
    }

    const state = stepState(ev, !!gap && !ev)
    let outcome = ''
    let naReason: string | undefined
    if (gap && !ev) {
      outcome = gap.detail || gap.reason
      naReason = gap.reason
    } else if (ev) {
      outcome = signalSentence(id, ev.result, ev.payload)
      if (ev.result === 'NOT_APPLICABLE') {
        naReason = (ev.payload?.note as string)
          || notApplicableReason(id, ctype)
      }
    } else {
      outcome = notApplicableReason(id, ctype)
      naReason = outcome
    }

    const showFindings = state === 'hit' || state === 'flagged'
      || (id === 'C70_reachability' && !!found?.items.length)

    return {
      id,
      label: collectorName(id),
      state,
      question: collectorQuestion(id),
      checks: collectorChecks(id, tipOpts),
      detail: collectorDetail(id, tipOpts),
      outcome,
      naReason,
      findings: showFindings ? found?.items : undefined,
      findingsTitle: showFindings ? found?.title : undefined,
    }
  })

  const entryMeta = externalEntryStepMeta(ctype)
  const entryExplain = explainExternalEntry({
    attrs: (detail.component.attrs as Record<string, unknown> | null | undefined) ?? null,
    reason_codes: cl?.reason_codes,
    ctype,
  })
  if (entryMeta && entryExplain) {
    steps.push({
      id: EXTERNAL_ENTRY_STEP_ID,
      label: entryMeta.name,
      state: entryExplain.isEntry ? 'surface' : 'clear',
      question: entryMeta.question,
      checks: entryMeta.checks,
      detail: entryMeta.detail,
      outcome: entryExplain.outcome,
      because: entryExplain.firedR5
        ? 'Rule R5: external entry surfaces stay Needs review when in-org callers are absent and Event Monitoring is unavailable.'
        : entryExplain.isEntry
          ? 'This surface can keep the component in Needs review when nothing inside the org calls it.'
          : undefined,
      rule: entryExplain.firedR5 ? 'R5' : null,
    })
  }

  const aiMeta = aiNarrationStepMeta()
  const aiExplain = explainAiNarration({
    llm: detail.llm,
    flags: detail.flags,
    reason_codes: cl?.reason_codes,
  })
  if (aiExplain) {
    steps.push({
      id: AI_NARRATION_STEP_ID,
      label: aiMeta.name,
      state: aiExplain.state,
      question: aiMeta.question,
      checks: aiMeta.checks,
      detail: aiMeta.detail,
      outcome: aiExplain.outcome,
      because: aiExplain.because,
      findings: aiExplain.findings.length ? aiExplain.findings : undefined,
      findingsTitle: aiExplain.findings.length ? 'AI reasons' : undefined,
      rule: aiExplain.state === 'hit' || aiExplain.state === 'flagged'
        ? 'S40'
        : null,
    })
  }

  if (cl) {
    steps.push({
      id: 'verdict',
      label: verdictLabel(cl.label),
      state: 'verdict',
      outcome: decision?.headline ?? '',
      because: decision?.because,
      rule: decision?.rule,
      verdict: cl.label,
      flavorLabel: decision?.flavor ? unusedFlavorLabel(decision.flavor) : null,
    })
  }

  const [hoverId, setHoverId] = useState<string | null>(null)
  const [lockedId, setLockedId] = useState<string | null>(null)
  const rootRef = useRef<HTMLDivElement>(null)
  const activeId = lockedId ?? hoverId
  const active = steps.find((s) => s.id === activeId) ?? null

  useEffect(() => {
    if (!lockedId) return
    const onDoc = (e: MouseEvent) => {
      if (!rootRef.current?.contains(e.target as Node)) setLockedId(null)
    }
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setLockedId(null)
    }
    document.addEventListener('mousedown', onDoc)
    document.addEventListener('keydown', onKey)
    return () => {
      document.removeEventListener('mousedown', onDoc)
      document.removeEventListener('keydown', onKey)
    }
  }, [lockedId])

  const openFinding = (f: Finding) => {
    if (f.componentId != null && onOpenComponent) {
      onOpenComponent(f.componentId)
      setLockedId(null)
      return
    }
    if (f.componentId != null && onReveal) {
      onReveal(f.componentId)
      setLockedId(null)
    }
  }

  return (
    <div className={`check-flow ${lockedId ? 'is-locked' : ''}`} ref={rootRef}>
      <h2>How we checked</h2>
      <p className="why">
        Hover a step for what it asks, what we check, and what we found.
        Click to pin.
      </p>
      <div className="cf-track" role="list">
        {steps.map((s, i) => (
          <div key={s.id} className="cf-item" role="listitem">
            {i > 0 && <span className="cf-arrow" aria-hidden>→</span>}
            <button
              type="button"
              className={`cf-node state-${s.state}${s.state === 'verdict' && s.verdict ? ` v-${String(s.verdict).toLowerCase()}` : ''} ${activeId === s.id ? 'is-active' : ''} ${lockedId && lockedId !== s.id ? 'is-dim' : ''}`}
              onMouseEnter={() => { if (!lockedId) setHoverId(s.id) }}
              onMouseLeave={() => { if (!lockedId) setHoverId(null) }}
              onClick={() => setLockedId((was) => was === s.id ? null : s.id)}
              aria-pressed={lockedId === s.id}
              title={s.detail || s.question || stateLabel(s.state, s.id)}
            >
              <span className="cf-label">{s.label}</span>
              <span className="cf-state">{stateLabel(s.state, s.id)}</span>
            </button>
          </div>
        ))}
      </div>

      {active && (
        <div className={`cf-tip ${lockedId ? 'locked' : ''}`} role="dialog">
          <div className="cf-tip-head">
            <strong>{active.label}</strong>
            {lockedId && (
              <button type="button" className="linkish"
                      onClick={() => setLockedId(null)}>unpin</button>
            )}
          </div>
          {active.question && (
            <p className="cf-q"><em>Asks:</em> {active.question}</p>
          )}
          {active.checks && (
            <p className="cf-checks"><em>We check:</em> {active.checks}</p>
          )}
          {active.detail && (
            <p className="cf-detail"><em>Means:</em> {active.detail}</p>
          )}
          <p className="cf-out"><em>Result:</em> {active.outcome}</p>
          {active.because && (
            <p className="cf-because"><em>Because:</em> {active.because}</p>
          )}
          {active.flavorLabel && (
            <span className="cf-flavor">{active.flavorLabel}</span>
          )}
          {active.rule && (
            <p className="cf-rule">Rule {active.rule}</p>
          )}
          {active.naReason && active.state === 'na' && (
            <p className="cf-na"><em>Why skipped:</em> {active.naReason}</p>
          )}
          {active.findings && active.findings.length > 0 && (
            <div className="cf-refs">
              <div className="cf-refs-h">{active.findingsTitle || 'What we found'}</div>
              <ul>
                {active.findings.map((f) => {
                  const clickable = f.componentId != null
                    && (!!onOpenComponent || !!onReveal)
                  return (
                    <li key={f.key}>
                      <button
                        type="button"
                        className={`cf-ref ${clickable ? '' : 'disabled'}`}
                        disabled={!clickable}
                        title={clickable
                          ? 'Open this reference'
                          : f.filePath
                            ? f.filePath
                            : 'No component link for this finding'}
                        onClick={() => openFinding(f)}
                      >
                        <code>{f.label}</code>
                        {f.meta && <span>{f.meta}</span>}
                        {f.filePath && !clickable && (
                          <span className="cf-path" title={f.filePath}>
                            {f.filePath.split('/').slice(-2).join('/')}
                          </span>
                        )}
                      </button>
                    </li>
                  )
                })}
              </ul>
            </div>
          )}
        </div>
      )}
    </div>
  )
}
