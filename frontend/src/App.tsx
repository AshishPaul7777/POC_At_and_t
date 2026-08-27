import { useCallback, useEffect, useRef, useState } from 'react'
import { Dashboard } from './components/Dashboard'
import { DetailPanel } from './components/DetailPanel'
import { Docs } from './components/Docs'
import { ExecutiveSummary } from './components/ExecutiveSummary'
import { Graph } from './components/Graph'
import { Admin } from './components/Admin'
import { Assistant } from './components/Assistant'
import { Login } from './components/Login'
import { auth, type Me } from './lib/auth'
import { parseHash } from './lib/anchors'
import { Nav, type View } from './components/Nav'
import { Pipeline, type SnapshotStage } from './components/Pipeline'
import {
  api,
  type ComponentRow,
  type Detail,
  type GraphPayload,
  type Run,
  type Summary,
  type Verdict,
} from './lib/api'
import { stream } from './lib/stream'
import { applyTheme, initialTheme, THEMES } from './lib/theme'

const VERDICTS: Verdict[] = ['UNUSED', 'NEEDS_REVIEW', 'USED', 'OUT_OF_SCOPE']

/**
 * Human-readable run label.
 *
 * The previous version read `cf84543f - DEGRADED - 6 unused - 11/8/2026`, which
 * fails three ways: a truncated UUID means nothing to a person, DEGRADED reads
 * as a fault when it only means some evidence was unavailable, and
 * toLocaleString renders a day/month order that is ambiguous with the US one.
 */
function runLabel(r: Run): string {
  const when = r.started_at ? relative(new Date(r.started_at)) : 'unknown time'
  const state = {
    SUCCEEDED: 'complete',
    DEGRADED: 'complete, partial coverage',
    FAILED: 'failed',
    CANCELLED: 'cancelled',
    RUNNING: 'running now',
    QUEUED: 'queued',
  }[r.state] ?? r.state.toLowerCase()
  const found = r.unused != null && r.state !== 'RUNNING'
    ? ` - ${r.unused} unused, ${r.needs_review ?? 0} to review`
    : ''
  return `${when} - ${state}${found}`
}

function relative(d: Date): string {
  const mins = Math.round((Date.now() - d.getTime()) / 60000)
  if (mins < 1) return 'just now'
  if (mins < 60) return `${mins} min ago`
  const hrs = Math.round(mins / 60)
  if (hrs < 24) return `${hrs}h ago`
  const days = Math.round(hrs / 24)
  if (days < 7) return `${days}d ago`
  // Month name, never a numeric order that could be read either way round.
  return d.toLocaleDateString(undefined, { day: 'numeric', month: 'short', year: 'numeric' })
}

const TITLES: Record<View, string> = {
  summary: 'Executive Summary',
  dashboard: 'Components Overview',
  pipeline: 'Pipeline',
  graph: 'Dependencies',
  report: 'Deliverables',
  assistant: 'Agent Iris',
  docs: 'About Me',
  admin: 'Access',
}

/** How many clicks on the wordmark reveal the engineering views. */
const REVEAL_CLICKS = 5

/** Guards the hash: a junk fragment must not leave the app on no view at all. */
const VIEWS = new Set(Object.keys(TITLES))

export default function App() {
  const [run, setRun] = useState<Run | null>(null)
  const [runs, setRuns] = useState<Run[]>([])
  // A shared link carries its view in the hash, so resolve it before the first
  // render rather than switching views a frame later.
  const [view, setView] = useState<View>(() => {
    const t = parseHash()
    return (t && VIEWS.has(t.view) ? t.view : 'summary') as View
  })
  const [summary, setSummary] = useState<Summary | null>(null)
  const [rows, setRows] = useState<ComponentRow[]>([])
  const [allRows, setAllRows] = useState<ComponentRow[]>([])
  const [detail, setDetail] = useState<Detail | null>(null)
  const [selected, setSelected] = useState<number | null>(null)
  const [graph, setGraph] = useState<GraphPayload | null>(null)
  // 'ALL' rather than a verdict: searching for a name while the UNUSED tab was
  // selected returned nothing and read as a broken search, when the component
  // was simply classified something else.
  const [verdict, setVerdict] = useState<Verdict | 'ALL'>('ALL')
  const [ctype, setCtype] = useState<string>('ALL')
  // Engineering views, revealed by clicking the wordmark. Persisted so the
  // reveal survives the reload that a deep link causes.
  const [revealed, setRevealed] = useState(
    () => localStorage.getItem('sfc.internal') === '1',
  )
  // A ref, not state: the count is a gesture being accumulated, nothing
  // renders from it, and keeping it out of an updater is what makes the
  // toggle below safe to run twice.
  const brandClicks = useRef({ n: 0, at: 0 })
  const [query, setQuery] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [starting, setStarting] = useState(false)
  const [activeRunId, setActiveRunId] = useState<string | null>(null)
  const [snapStages, setSnapStages] = useState<SnapshotStage[] | null>(null)
  const [collapsed, setCollapsed] = useState(
    () => window.matchMedia('(max-width: 780px)').matches,
  )
  // Until the user expresses a preference, the sidebar follows the viewport.
  // Without this it latches: a page opened narrow stays icon-only after the
  // window is widened, with no labels and no obvious way to get them back.
  const [collapsePinned, setCollapsePinned] = useState(false)
  // On phones the sidebar is an overlay drawer, so "is it visible" is a
  // separate question from "is it narrow". Closed by default.
  const [navOpen, setNavOpen] = useState(false)
  const [askSeed, setAskSeed] = useState<{ text: string } | null>(null)
  // null = still checking. Rendering the app or the login page before this
  // resolves would flash the wrong one on every load.
  const [me, setMe] = useState<Me | null>(null)
  const [narrow, setNarrow] = useState(
    () => window.matchMedia('(max-width: 780px)').matches,
  )
  const [theme, setTheme] = useState(() => initialTheme())

  useEffect(() => { applyTheme(theme) }, [theme])

  /** Five clicks on the wordmark toggles the engineering views.
   *
   *  The first version incremented inside a setState updater and toggled
   *  `revealed` from within it. React double-invokes updaters in StrictMode
   *  precisely to surface that kind of impurity, so the toggle ran twice and
   *  the views never appeared. The count lives in a ref now and the toggle is
   *  a plain, idempotent state update.
   */
  const onBrandClick = useCallback(() => {
    const now = Date.now()
    const c = brandClicks.current
    // A click far from the last one starts a fresh gesture rather than
    // accumulating towards a reveal nobody asked for.
    c.n = now - c.at > 1500 ? 1 : c.n + 1
    c.at = now
    if (c.n < REVEAL_CLICKS) return
    c.n = 0
    setRevealed((was) => !was)
  }, [])

  // Persist outside the updater: writing storage from inside one is the same
  // impurity that broke this the first time.
  useEffect(() => {
    localStorage.setItem('sfc.internal', revealed ? '1' : '0')
  }, [revealed])

  // Leaving an engineering view visible after it is hidden again would strand
  // the user on a page the nav no longer offers a way back from.
  useEffect(() => {
    // While a run is going, Pipeline is visible whatever the reveal says --
    // otherwise pressing "Run analysis" appears to do nothing for ten minutes.
    if (activeRunId) return
    if (!revealed && (view === 'pipeline' || view === 'graph')) setView('dashboard')
  }, [revealed, view, activeRunId])

  useEffect(() => {
    auth.me()
      .then(setMe)
      .catch(() => setMe({ authenticated: false }))
  }, [])

  // Someone pasting a second link into the same tab changes only the hash,
  // which navigates nothing by itself.
  useEffect(() => {
    const onHash = () => {
      const t = parseHash()
      if (!t) return
      if (VIEWS.has(t.view)) setView(t.view as View)
      if (t.anchor) {
        document.getElementById(t.anchor)
          ?.scrollIntoView({ behavior: 'smooth', block: 'start' })
      }
    }
    window.addEventListener('hashchange', onHash)
    return () => window.removeEventListener('hashchange', onHash)
  }, [])

  useEffect(() => {
    const mq = window.matchMedia('(max-width: 780px)')
    const sync = () => {
      setNarrow(mq.matches)
      if (!mq.matches) setNavOpen(false)
      if (!collapsePinned) setCollapsed(mq.matches)
    }
    mq.addEventListener('change', sync)
    return () => mq.removeEventListener('change', sync)
  }, [collapsePinned])

  useEffect(() => {
    if (!navOpen) return
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') setNavOpen(false) }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [navOpen])
  // The graph reads its colours from CSS variables at construction, so it has to
  // be rebuilt when they change; otherwise it keeps the old palette.
  useEffect(() => { setGraph(null) }, [theme])

  const loadRun = useCallback(async (preferId?: string) => {
    const r = await api.runs()
    setRuns(r)
    if (!r.length) throw new Error('No runs yet. Click "Run analysis".')
    const pick = (preferId && r.find((x) => x.id === preferId)) || r[0]
    setRun(pick)
    // Stage history is loaded HERE rather than in each caller. A finished run's
    // stages live only in the snapshot -- the event stream is closed and there
    // is nothing to replay -- so any path that selects a run without fetching it
    // leaves the Pipeline view blank. That was the bug: the dropdown fetched the
    // snapshot, first load did not.
    try {
      const snap = await fetch(`/api/runs/${pick.id}/snapshot`).then((x) => x.json())
      setSnapStages(snap.stages ?? null)
    } catch {
      setSnapStages(null)   // never leave the previous run's stages on screen
    }
    // Stage rows come from the snapshot, but everything granular -- log lines,
    // per-collector outcomes, budget, coverage caveats -- exists only as events.
    // For a finished run the stream replays the whole log and then closes, so
    // opening it is how history gets its detail back rather than just headings.
    const TERMINAL = ['SUCCEEDED', 'DEGRADED', 'FAILED', 'CANCELLED']
    if (TERMINAL.includes(pick.state) && stream.runId() !== pick.id) {
      stream.open(pick.id, 0)
    }
    return pick
  }, [])

  /**
   * Reattach on mount.
   *
   * The run executes in the backend and its progress is durable, so a browser is
   * only ever a viewer: reloading, or closing the tab and returning, loses
   * nothing and never affects the run. Ask what is executing, hydrate stages
   * from the snapshot, then replay the log from the start — event volume is
   * bounded (~100 per run), so exactness beats cleverness.
   */
  useEffect(() => {
    // Nothing here is reachable while signed out. Waiting for the session check
    // keeps the login page free of a burst of 401s in the console.
    if (!me?.authenticated) return
    let cancelled = false
    ;(async () => {
      try {
        const act = await fetch('/api/runs/active').then((r) => r.json())
        if (cancelled) return
        if (act.run_id && act.active) {
          setActiveRunId(act.run_id)
          setSnapStages(act.snapshot?.stages ?? null)
          stream.open(act.run_id, 0)
          setView('pipeline')
          await loadRun(act.run_id)
          return
        }
        if (act.run_id && act.stale) {
          setError(`Run ${act.run_id.slice(0, 8)} was interrupted - no worker is executing it.`)
        }
        const remembered = localStorage.getItem('sfc.lastRun') || undefined
        await loadRun(remembered)
      } catch (e) {
        if (!cancelled) setError(String(e))
      }
    })()
    return () => { cancelled = true }
  }, [loadRun, me])

  useEffect(() => { if (run) localStorage.setItem('sfc.lastRun', run.id) }, [run])

  // Refresh durable stage state while live, so a stage that finished between
  // reconnects is never left showing as running.
  useEffect(() => {
    if (!activeRunId) return
    const t = setInterval(async () => {
      try {
        const s = await fetch(`/api/runs/${activeRunId}/snapshot`).then((r) => r.json())
        setSnapStages(s.stages ?? null)
      } catch { /* the event stream is the primary channel */ }
    }, 5000)
    return () => clearInterval(t)
  }, [activeRunId])

  const refresh = useCallback(async (id: string) => {
    const [s, all] = await Promise.all([api.summary(id), api.components(id, {})])
    setSummary(s)
    setAllRows(all)
    setGraph(null)
  }, [])

  useEffect(() => { if (run) refresh(run.id).catch(() => setSummary(null)) }, [run, refresh])

  useEffect(() => {
    if (!run) return
    api.components(run.id, {
      verdict: verdict === 'ALL' ? undefined : verdict,
      ctype: ctype === 'ALL' ? undefined : ctype,
      q: query || undefined,
    })
      .then(setRows).catch((e) => setError(String(e)))
  }, [run, verdict, ctype, query, summary])

  useEffect(() => {
    if (!run || view !== 'graph' || graph) return
    api.graph(run.id, 400).then(setGraph).catch((e) => setError(String(e)))
  }, [run, view, graph])

  const open = useCallback((cid: number) => {
    if (!run) return
    setSelected(cid)
    api.detail(run.id, cid).then(setDetail).catch((e) => setError(String(e)))
  }, [run])

  const startRun = useCallback(async () => {
    setStarting(true)
    setError(null)
    try {
      const r = await fetch('/api/runs', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({}),
      })
      const body = await r.json()
      if (!r.ok) throw new Error(body.detail ?? `HTTP ${r.status}`)
      setActiveRunId(body.run_id)
      const snap = await fetch(`/api/runs/${body.run_id}/snapshot`).then((x) => x.json())
      setSnapStages(snap.stages ?? null)
      stream.open(body.run_id, 0)
      setView('pipeline')
      await loadRun(body.run_id)
    } catch (e) {
      setError(String(e))
    } finally {
      setStarting(false)
    }
  }, [loadRun])

  const onRunFinished = useCallback(() => {
    const id = activeRunId
    loadRun(id ?? undefined).then((r) => refresh(r.id)).catch(() => {})
    setActiveRunId(null)
  }, [loadRun, refresh, activeRunId])

  const pickRun = useCallback(async (id: string) => {
    stream.close()
    setActiveRunId(null)
    setDetail(null)
    setSelected(null)
    await loadRun(id)
  }, [loadRun])

  /**
   * Selecting a node must NOT navigate away.
   *
   * It previously jumped straight to the Components list, which unmounted the
   * graph mid-click and made inspecting dependencies impossible — you lost the
   * thing you were looking at in order to read about it. The detail is loaded in
   * the background so the Components view is ready if you choose to go there,
   * but the graph stays put and its inspector does the explaining.
   */
  const onGraphSelect = useCallback((id: string) => {
    if (id.startsWith('component:')) open(Number(id.split(':')[1]))
  }, [open])

  // Before every data-dependent branch: an unauthenticated load fails those
  // fetches, and their error screen would otherwise cover the login page.
  if (me === null) {
    return <div className="boot">Loading…</div>
  }
  if (!me.authenticated) {
    // Reload rather than flipping state: it re-runs every data effect against
    // the new session, which is simpler than making each one re-fetch.
    return <Login onSignedIn={() => window.location.reload()} />
  }

  if (error && !run) {
    return (
      <div className="boot">
        <div className="err">{error}</div>
        <button className="primary" disabled={starting} onClick={startRun}>
          {starting ? 'Starting...' : 'Run analysis'}
        </button>
      </div>
    )
  }
  if (!run) return <div className="loading">Loading...</div>

  const t = summary?.totals ?? {}

  return (
    <div className={`shell ${navOpen ? 'nav-open' : ''}`}>
      <Nav
        view={view}
        onNav={(v) => {
          setView(v)
          setNavOpen(false)
          history.replaceState(null, '', `#${v}`)
        }}
        running={!!activeRunId}
        collapsed={narrow ? false : collapsed}
        onToggle={() => { setCollapsePinned(true); setCollapsed((c) => !c) }}
        counts={{ unused: t.UNUSED ?? 0, review: t.NEEDS_REVIEW ?? 0 }}
        isAdmin={me.role === 'admin'}
        revealed={revealed || !!activeRunId}
        onBrandClick={onBrandClick}
      />
      {/* Tapping away is the gesture people expect from a drawer; without it
          the only way out is the hamburger, which the drawer covers. */}
      <button className="nav-scrim" aria-label="Close menu"
              onClick={() => setNavOpen(false)} tabIndex={navOpen ? 0 : -1} />

      <div className="main">
        <div className="topbar">
          <button className="navtoggle" onClick={() => setNavOpen((o) => !o)}
                  aria-label="Menu" aria-expanded={navOpen}>
            <svg viewBox="0 0 24 24" aria-hidden>
              <path d="M4 7h16M4 12h16M4 17h16" fill="none" stroke="currentColor"
                    strokeWidth="1.8" strokeLinecap="round" />
            </svg>
          </button>
          <div className="crumb">
            {TITLES[view]}
            <small>
              {run.org_alias}
              {summary?.org?.edition ? ` · ${String(summary.org.edition)}` : ''}
              {run.run_as_username ? ` · ${run.run_as_username}` : ''}
            </small>
          </div>
          <span className="spacer" />
          {error && <span className="err-inline" title={error}>{error}</span>}
          <select className="runpick themepick" value={theme}
                  onChange={(e) => setTheme(e.target.value)} title="Theme">
            {THEMES.map((t) => (
              <option key={t.id} value={t.id}>{t.label}</option>
            ))}
          </select>
          {runs.length > 0 && (
            <select className="runpick" value={run.id}
                    onChange={(e) => pickRun(e.target.value)}
                    title="Switch to a previous run">
              {runs.map((r) => (
                <option key={r.id} value={r.id}>{runLabel(r)}</option>
              ))}
            </select>
          )}
          <button className="primary" disabled={starting || !!activeRunId} onClick={startRun}>
            {activeRunId ? 'Running...' : starting ? 'Starting...' : 'Run analysis'}
          </button>
          <div className="whoami" title={`Signed in as ${me.email}`}>
            <span className="whoami-email">{me.email}</span>
            <button className="linkish" onClick={async () => {
              await auth.logout().catch(() => {})
              window.location.reload()
            }}>sign out</button>
          </div>
        </div>

        {/* "Partial coverage" is easy to misread as a failure. Say what was
            missing and that the verdicts still hold, rather than showing a
            status word and leaving the reader to worry. */}
        {run.state === 'DEGRADED' && summary && summary.coverage_caveats.length > 0 && (
          /* One line, not a paragraph. This sits above every page, and at four
             lines it was taking 157px of a 551px viewport permanently -- more
             than the dependency graph got. The full explanation lives on the
             Report page, one click away. */
          <div className="notice">
            <b>Partial coverage</b>
            <span>
              {summary.coverage_caveats.length} check
              {summary.coverage_caveats.length > 1 ? 's' : ''} could not complete;
              affected components are held at needs review.
            </span>
            <button className="linkish" onClick={() => setView('report')}>
              see what was missing
            </button>
          </div>
        )}

        <div className="content">
          {view === 'summary' && <ExecutiveSummary />}

          {view === 'pipeline' && (
            <Pipeline runId={activeRunId ?? run.id} snapshotStages={snapStages}
                      live={!!activeRunId} onDone={onRunFinished} />
          )}

          {view === 'dashboard' && (
            <div className="split">
              <div className="left">
                {summary && (
                  <div className="overview-head">
                    <Dashboard summary={summary} rows={allRows} headerOnly
                               onPick={(v) => setVerdict(v)} />
                  </div>
                )}
                <div className="filters">
                  {/* "All" first and selected by default: the search box below
                      queries within the chosen verdict, so starting on a single
                      verdict made a search for anything else look broken. */}
                  <button data-active={verdict === 'ALL'} onClick={() => setVerdict('ALL')}>
                    All ({VERDICTS.reduce((a, v) => a + (t[v] ?? 0), 0)})
                  </button>
                  {VERDICTS.map((v) => (
                    <button key={v} data-active={verdict === v} onClick={() => setVerdict(v)}>
                      {v.replaceAll('_', ' ')} ({t[v] ?? 0})
                    </button>
                  ))}
                  <select value={ctype} onChange={(e) => setCtype(e.target.value)}
                          title="Component type">
                    <option value="ALL">All types</option>
                    {Object.entries(summary?.by_type ?? {})
                      .sort((a, b) => a[0].localeCompare(b[0]))
                      .map(([type, counts]) => (
                        <option key={type} value={type}>
                          {type} ({Object.values(counts as Record<string, number>)
                            .reduce((x, y) => x + y, 0)})
                        </option>
                      ))}
                  </select>
                  <input placeholder="search any component by name..." value={query}
                         onChange={(e) => setQuery(e.target.value)} />
                  {(verdict !== 'ALL' || ctype !== 'ALL' || query) && (
                    <button className="linkish" onClick={() => {
                      setVerdict('ALL'); setCtype('ALL'); setQuery('')
                    }}>clear</button>
                  )}
                  <span className="tb-hint">{rows.length} shown</span>
                </div>
                <div className="twrap">
                  <table>
                    <thead>
                      <tr>
                        <th>Component</th><th>Type</th>
                        <th title="One marker per collector: filled = found something, hollow = searched and found nothing, red = could not check">
                          Evidence
                        </th>
                        <th>Conf</th>
                      </tr>
                    </thead>
                    <tbody>
                      {rows.map((r) => (
                        <tr key={r.id} data-selected={selected === r.id} onClick={() => open(r.id)}>
                          <td>
                            <code>{r.api_name}</code>
                            {r.removal_prerequisites?.length > 0 && (
                              <span className="prereq-chip">
                                {r.removal_prerequisites.length} step(s) first
                              </span>
                            )}
                          </td>
                          <td style={{ color: 'var(--text-dim)' }}>{r.ctype}</td>
                          <td>
                            <span className="strip">
                              {Array.from({ length: r.hits }).map((_, i) => <i key={`h${i}`} className="dot hit" />)}
                              {Array.from({ length: r.clean }).map((_, i) => <i key={`c${i}`} className="dot clean" />)}
                              {Array.from({ length: r.unclear }).map((_, i) => <i key={`u${i}`} className="dot unclear" />)}
                              {Array.from({ length: r.gaps }).map((_, i) => <i key={`g${i}`} className="dot gap" />)}
                            </span>
                          </td>
                          <td style={{ color: 'var(--text-faint)' }}>
                            {r.confidence != null ? Math.round(r.confidence) : '-'}
                          </td>
                        </tr>
                      ))}
                      {rows.length === 0 && (
                        <tr><td colSpan={4} className="empty">Nothing matches this filter.</td></tr>
                      )}
                    </tbody>
                  </table>
                </div>
              </div>
              <div className="right"><DetailPanel detail={detail} /></div>
            </div>
          )}

          {view === 'graph' && (
            <div className="graph-wrap">
              <div className="legend">
                {/* Read from the theme, exactly as Graph.tsx does when it builds
                    the nodes. Literals here would drift the moment a palette
                    changed and the legend would describe the wrong colours. */}
                <span><i className="swatch" style={{ background: 'var(--accent)' }} /> entry point</span>
                <span><i className="swatch" style={{ background: 'var(--used)' }} /> reachable</span>
                <span><i className="swatch" style={{ background: 'var(--unused)' }} /> unreachable</span>
                <span><i className="swatch" style={{ background: 'var(--scope)' }} /> artifact</span>
                <span className="tb-hint">node size = connections</span>
                {graph && (
                  <span style={{ marginLeft: 'auto' }}>
                    {graph.shown_nodes} of {graph.total_nodes} nodes ·{' '}
                    {graph.stats.entry_points} entry ·{' '}
                    {graph.stats.unreachable_components} unreachable
                  </span>
                )}
              </div>
              {graph
                ? <Graph data={graph} onSelect={onGraphSelect}
                         onOpenComponent={(cid) => { open(cid); setView('dashboard') }} />
                : <div className="loading">Building graph...</div>}
            </div>
          )}

          {view === 'report' && (
            <div className="pad">
              <section className="hero">
                <div className="hero-top">
                  <span className="hero-chip">EXPORT</span>
                  <span className="spacer" />
                  <span className="hero-elapsed">
                    {t.UNUSED ?? 0} deletion candidates
                  </span>
                </div>
                <h1>Take the findings with you</h1>
                <p>
                  Generated server-side from this run. Every export leads with its
                  own coverage limitations, and negative evidence ships alongside
                  positive so a reviewer can audit it rather than take it on trust.
                </p>
              </section>
              <div className="exports">
                {([
                  ['xlsx', 'Working spreadsheet', '7 sheets: summary, coverage & limits, deletion candidates, needs review, all components, evidence detail, remediation', 'cleanup-report.xlsx', false],
                  ['markdown', 'Markdown report', 'Pastes into Confluence, Jira or a pull request description', 'cleanup-report.md', false],
                  ['json', 'Machine contract', 'Versioned schema for scripting or diffing runs', 'cleanup-report.json', false],
                  ['destructive_changes', 'Delete package', 'Deployable destructiveChanges.xml for the UNUSED set. Validate with --dry-run first; this app never deploys it', 'destructiveChangesPost.xml', true],
                ] as const).map(([k, title, desc, file, danger]) => (
                  <a key={k} className={`export-card ${danger ? 'danger' : ''}`}
                     href={`/api/runs/${run.id}/report/${k}`} download>
                    <b>{title}</b>
                    <span>{desc}</span>
                    <code>{file}</code>
                  </a>
                ))}
              </div>

              {summary && summary.coverage_caveats.length > 0 && (
                <>
                  <h2>What this run could not prove</h2>
                  <div className="sub">
                    These appear verbatim in the report. Not errors - the honest
                    bounds of what is knowable in this org.
                  </div>
                  {summary.coverage_caveats.map((c, i) => (
                    <div className="caveat" key={i}>{c}</div>
                  ))}
                </>
              )}
            </div>
          )}

          {view === 'assistant' && (
            <Assistant runId={run.id} seed={askSeed}
                       onSeedConsumed={() => setAskSeed(null)} />
          )}

          {view === 'admin' && me.role === 'admin' && <Admin me={me} />}

          {view === 'docs' && <div className="pad"><Docs /></div>}
        </div>
      </div>
    </div>
  )
}
