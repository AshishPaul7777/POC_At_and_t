import { useEffect, useMemo, useRef, useState, useSyncExternalStore } from 'react'
import { stream, type RunEvent } from '../lib/stream'

interface StageDef { key: string; title: string; detail: string; critical: boolean }
export interface SnapshotStage {
  key: string; title: string | null; state: string
  skip_reason: string | null
  started_at: string | null; finished_at: string | null
}
interface StageState {
  key: string; title: string; detail: string; critical: boolean
  state: 'PENDING' | 'RUNNING' | 'SUCCEEDED' | 'DEGRADED' | 'FAILED' | 'SKIPPED'
  note?: string; reason?: string; durationMs?: number
  result?: Record<string, any>; progress?: { done: number; total: number | null }
}

const GLYPH: Record<string, string> = {
  PENDING: '', RUNNING: '', SUCCEEDED: '✓', DEGRADED: '!',
  FAILED: '✕', SKIPPED: '–',
}

/**
 * Fold the durable snapshot and the event log into per-stage state.
 *
 * The snapshot is applied FIRST and the events layer on top. That ordering is
 * what makes a reload lossless: stage state lives in Postgres, so a browser that
 * missed the events — because it was closed, or reloaded, or opened for the
 * first time mid-run — still shows exactly where the run is. Reducing from
 * events alone would show an empty pipeline for a run that is half finished.
 */
function reduce(events: RunEvent[], snap?: SnapshotStage[] | null) {
  let defs: StageDef[] = []
  const stages = new Map<string, StageState>()

  for (const s of snap ?? []) {
    stages.set(s.key, {
      key: s.key,
      title: s.title ?? s.key,
      detail: '',
      critical: true,
      state: s.state as StageState['state'],
      reason: s.skip_reason ?? undefined,
      durationMs: s.started_at && s.finished_at
        ? new Date(s.finished_at).getTime() - new Date(s.started_at).getTime()
        : undefined,
    })
  }
  const caveats: string[] = []
  let budget: Record<string, any> | null = null
  let verdicts: Record<string, number> | null = null
  let runState: string | null = null
  const collectors = new Map<string, {
    status?: string; hits?: number; evidence?: number; gaps?: number
  }>()

  for (const e of events) {
    const p = e.payload || {}
    switch (e.etype) {
      case 'run.started':
        defs = p.stages || []
        for (const d of defs) {
          stages.set(d.key, { ...d, state: 'PENDING' })
        }
        break
      case 'run.finished':
        runState = p.state
        break
      case 'stage.started': {
        const s = stages.get(e.stage!) ?? {
          key: e.stage!, title: p.title ?? e.stage!, detail: p.detail ?? '',
          critical: false, state: 'PENDING' as const,
        }
        stages.set(e.stage!, { ...s, state: 'RUNNING', note: undefined })
        break
      }
      case 'stage.note': {
        const s = stages.get(e.stage!)
        if (s) stages.set(e.stage!, { ...s, note: p.text })
        break
      }
      case 'stage.progress': {
        const s = stages.get(e.stage!)
        if (s) stages.set(e.stage!, {
          ...s, progress: { done: p.done, total: p.total ?? null },
          note: p.collector ? `${p.collector}` : s.note,
        })
        break
      }
      case 'stage.finished': {
        const s = stages.get(e.stage!)
        if (s) {
          const { state, duration_ms, ...rest } = p
          stages.set(e.stage!, {
            ...s, state: state ?? 'SUCCEEDED', durationMs: duration_ms,
            result: rest, note: undefined,
          })
        }
        break
      }
      case 'stage.failed': {
        const s = stages.get(e.stage!)
        if (s) stages.set(e.stage!, {
          ...s, state: 'FAILED', reason: p.error, durationMs: p.duration_ms,
        })
        break
      }
      case 'stage.skipped': {
        const s = stages.get(e.stage!)
        if (s) stages.set(e.stage!, { ...s, state: 'SKIPPED', reason: p.reason })
        break
      }
      case 'collector.started':
        // Registered on start so a running collector is visible while it works,
        // instead of the list filling in only as each one finishes.
        if (!collectors.has(p.collector)) collectors.set(p.collector, {})
        break
      case 'collector.finished':
        collectors.set(p.collector, {
          status: p.status, hits: p.hits,
          evidence: p.evidence, gaps: p.gaps,
        })
        break
      case 'budget.tick':
        budget = p
        break
      case 'coverage.caveat':
        if (p.text && !caveats.includes(p.text)) caveats.push(p.text)
        break
      case 'verdicts':
        verdicts = p as Record<string, number>
        break
    }
  }
  return {
    stages: defs.length ? defs.map((d) => stages.get(d.key)!) : [...stages.values()],
    caveats, budget, verdicts, runState, collectors: [...collectors.entries()],
  }
}

export function Pipeline({ runId, snapshotStages, live, onDone }: {
  runId: string | null
  snapshotStages?: SnapshotStage[] | null
  live: boolean
  onDone: () => void
}) {
  useSyncExternalStore(stream.subscribe, stream.getSnapshot, stream.getSnapshot)
  const events = stream.events()
  const conn = stream.conn()
  const model = useMemo(
    () => reduce(events, snapshotStages),
    [events.length, snapshotStages],
  )
  const logRef = useRef<HTMLDivElement>(null)
  const [follow, setFollow] = useState(true)
  const [levelFilter, setLevelFilter] = useState<'all' | 'milestones'>('all')

  // Only while live. Replaying a finished run also yields run.finished, and
  // treating that as "the run I was watching just ended" would reload the run
  // list and jump the user to the newest run instead of the one they opened.
  useEffect(() => {
    if (live && model.runState) onDone()
  }, [live, model.runState, onDone])

  // Follow-tail that disengages the moment the user scrolls up. Without this,
  // reading anything while a run streams is impossible.
  useEffect(() => {
    if (!follow || !logRef.current) return
    logRef.current.scrollTop = logRef.current.scrollHeight
  }, [events.length, follow])

  const shown = useMemo(() => {
    const list = levelFilter === 'all'
      ? events
      : events.filter((e) => !e.etype.startsWith('stage.progress')
          && e.etype !== 'stage.note' && e.etype !== 'budget.tick')
    return list.slice(-1200)
  }, [events.length, levelFilter])

  if (!runId) {
    return <div className="empty">No run selected.</div>
  }

  const done = model.stages.filter((x) => x && x.state !== 'PENDING').length
  const total = model.stages.length || 10
  const current = model.stages.find((x) => x && x.state === 'RUNNING')
  const v = model.verdicts || {}

  return (
    <div className="pipe">
      <div className="pipe-scroll">
        <section className="hero">
          <div className="hero-top">
            <span className="hero-chip">
              {current ? `STEP ${model.stages.indexOf(current) + 1} OF ${total}`
                       : `${done} OF ${total} COMPLETE`}
            </span>
            {model.runState && (
              <span className="hero-chip">{model.runState}</span>
            )}
            <span className="spacer" />
            <span className="hero-elapsed">
              {!live ? 'Completed'
                : conn === 'live' ? 'Live'
                : conn === 'reconnecting' ? `Reconnecting (${stream.attempt()})`
                : conn}
            </span>
          </div>
          <h1>{current ? current.title : 'Analysis pipeline'}</h1>
          <p>{current ? current.detail
                      : 'Every stage finished. Verdicts below are reproducible '
                        + 'from the evidence each collector recorded.'}</p>
          <div className="hero-bar">
            <i style={{ width: `${Math.round((done / total) * 100)}%` }} />
          </div>
        </section>

        <div className="stat-row">
          <div className="stat">
            <div className="stat-value">{done}<span style={{ fontSize: 17, color: 'var(--text-faint)' }}>/{total}</span></div>
            <div className="stat-label">stages complete</div>
          </div>
          <div className="stat">
            <div className="stat-value unused">{v.UNUSED ?? 0}</div>
            <div className="stat-label">unused</div>
          </div>
          <div className="stat">
            <div className="stat-value review">{v.NEEDS_REVIEW ?? 0}</div>
            <div className="stat-label">need review</div>
          </div>
          <div className="stat">
            <div className="stat-value used">{v.USED ?? 0}</div>
            <div className="stat-label">in use</div>
          </div>
          {model.budget && (
            <div className="stat">
              <div className="stat-value">
                {(model.budget.remaining ?? 0).toLocaleString()}
              </div>
              <div className="stat-label">API calls left</div>
            </div>
          )}
        </div>

        {stream.hasGap() && (
          <div className="panel" style={{ marginBottom: 18 }}>
            <span style={{ color: 'var(--unused)', fontSize: 13 }}>
              Some events could not be replayed, so the activity log has a hole in it.
            </span>
          </div>
        )}

        <div className="pipe-cols">
          <div className="pipe-main">
            <div className="stages-list">
              {model.stages.filter(Boolean).map((st) => (
                <div className={`stage-row ${st.state}`} key={st.key}>
                  <div className="sr-node">{GLYPH[st.state]}</div>
                  <div className="sr-body">
                    <div className="sr-head">
                      <span className="sr-title">{st.title}</span>
                      {!st.critical && <span className="pill muted">optional</span>}
                      <span className="spacer" />
                      {st.durationMs != null && (
                        <span className="sr-dur">{(st.durationMs / 1000).toFixed(1)}s</span>
                      )}
                      <span className={`pill ${st.state}`}>{st.state.toLowerCase()}</span>
                    </div>
                    <p className="sr-detail">{st.detail}</p>
                    {st.state === 'RUNNING' && (
                      <>
                        <div className="sr-bar"><i /></div>
                        {st.note && <p className="sr-note">{st.note}</p>}
                        {st.progress && (
                          <p className="sr-note">
                            {st.progress.done}
                            {st.progress.total ? ` of ${st.progress.total}` : ''}
                          </p>
                        )}
                      </>
                    )}
                    {st.reason && <p className="sr-reason">{st.reason}</p>}
                    {st.result && Object.keys(st.result).length > 0 && (
                      <div className="sr-stats">
                        {Object.entries(st.result).slice(0, 6).map(([k, val]) => (
                          <span key={k}>
                            <b>{String(val)}</b> {k.replace(/_/g, ' ')}
                          </span>
                        ))}
                      </div>
                    )}
                  </div>
                </div>
              ))}
            </div>
          </div>

          <aside className="pipe-side">
            {model.collectors.length > 0 && (
              <section className="panel">
                <div className="panel-head">
                  <h3>Evidence collectors</h3>
                  <span className="panel-count">{model.collectors.length}</span>
                </div>
                <div className="coll-grid">
                  {model.collectors.map(([name, c]) => (
                    <div className="coll" key={name} data-status={c.status ?? 'RUNNING'}>
                      <code>{name}</code>
                      <span className="spacer" />
                      <span className="coll-status">{c.status ?? 'running'}</span>
                      {c.status && <span className="coll-nums">{c.hits ?? 0} hits</span>}
                    </div>
                  ))}
                </div>
              </section>
            )}

            {model.caveats.length > 0 && (
              <section className="panel">
                <div className="panel-head">
                  <h3>Limitations found</h3>
                  <span className="panel-count warn">{model.caveats.length}</span>
                </div>
                {model.caveats.map((c, i) => <p className="caveat" key={i}>{c}</p>)}
              </section>
            )}

            <section className="panel">
              <div className="panel-head">
                <h3>Activity</h3>
                <span className="spacer" />
                <button className="seg" data-active={levelFilter === 'all'}
                        onClick={() => setLevelFilter('all')}>all</button>
                <button className="seg" data-active={levelFilter === 'milestones'}
                        onClick={() => setLevelFilter('milestones')}>key</button>
              </div>
              <div className="log" ref={logRef}
                   onWheel={(e) => { if (e.deltaY < 0) setFollow(false) }}>
                {shown.map((e) => (
                  <div className={`logline ${e.etype.replace('.', '-')}`} key={e.seq}>
                    <span className="etype">{e.etype.split('.').pop()}</span>
                    <span className="pl">{summarise(e)}</span>
                  </div>
                ))}
                {shown.length === 0 && <p className="ins-empty">Nothing yet.</p>}
              </div>
            </section>
          </aside>
        </div>
      </div>
    </div>
  )
}

function summarise(e: RunEvent): string {
  const p = e.payload || {}
  if (e.etype === 'stage.note') return String(p.text ?? '')
  if (e.etype === 'coverage.caveat') return String(p.text ?? '')
  if (e.etype === 'budget.tick') return `remaining ${p.remaining ?? '?'}`
  if (e.etype === 'collector.finished')
    return `${p.collector} → ${p.status} (hits ${p.hits}, evidence ${p.evidence})`
  if (e.etype === 'collector.started') return String(p.collector ?? '')
  if (e.etype === 'stage.progress')
    return `${p.done}${p.total ? `/${p.total}` : ''}${p.collector ? ` ${p.collector}` : ''}`
  if (e.etype === 'verdicts')
    return Object.entries(p).map(([k, v]) => `${k}=${v}`).join('  ')
  if (e.etype === 'run.started') return `${(p.stages || []).length} stages`
  if (e.etype === 'stage.failed') return String(p.error ?? '')
  const keys = Object.keys(p)
  if (!keys.length) return ''
  return keys.slice(0, 6).map((k) => `${k}=${JSON.stringify(p[k])}`).join(' ').slice(0, 220)
}
