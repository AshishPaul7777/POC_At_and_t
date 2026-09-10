export type View = 'summary' | 'overview' | 'components' | 'examples' | 'explorer' | 'pipeline'
  | 'graph' | 'report' | 'assistant' | 'docs' | 'library' | 'admin'

interface Item {
  id: View
  label: string
  icon: string
  hint: string
  /** Hidden from non-admins. The API refuses them anyway; this stops the nav
   *  advertising a page that would only 403. */
  adminOnly?: boolean
  /** Hidden until the wordmark is clicked five times. Not a security boundary
   *  -- the routes still work if typed -- just a way to keep engineering views
   *  out of a client demo. */
  internal?: boolean
}

/** Icons are inline SVG paths: no icon-font dependency, and they scale cleanly. */
const ICONS: Record<string, string> = {
  dashboard: 'M3 13h8V3H3v10zm0 8h8v-6H3v6zm10 0h8V11h-8v10zm0-18v6h8V3h-8z',
  overview: 'M3 13h8V3H3v10zm0 8h8v-6H3v6zm10 0h8V11h-8v10zm0-18v6h8V3h-8z',
  pipeline: 'M4 6h16M4 12h10M4 18h6',
  results: 'M4 5h16M4 10h16M4 15h10M4 20h7',
  components: 'M4 5h16M4 10h16M4 15h10M4 20h7',
  examples: 'M9 5H7a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2V7a2 2 0 00-2-2h-2M9 5a2 2 0 002 2h2a2 2 0 002-2M9 5a2 2 0 012-2h2a2 2 0 012 2m-6 9l2 2 4-4',
  graph: 'M5 5a2 2 0 100 4 2 2 0 000-4zm14 0a2 2 0 100 4 2 2 0 000-4zM12 15a2 2 0 100 4 2 2 0 000-4zM7 7h10M6.5 9l4.5 5m6-5l-4.5 5',
  report: 'M6 2h9l5 5v15H6zM14 2v6h6M9 13h8M9 17h8',
  docs: 'M4 4h11a3 3 0 013 3v13H7a3 3 0 01-3-3zM9 9h7M9 13h7',
  assistant: 'M21 12a8 8 0 01-8 8H7l-4 3v-5.5A8 8 0 1121 12z',
  admin: 'M16 21v-2a4 4 0 00-4-4H6a4 4 0 00-4 4v2M9 11a4 4 0 100-8 4 4 0 000 8zm11 10v-2a4 4 0 00-3-3.87M16 3.13a4 4 0 010 7.75',
  summary: 'M14 2H6a2 2 0 00-2 2v16a2 2 0 002 2h12a2 2 0 002-2V8zM14 2v6h6M9 13h6M9 17h6',
  library: 'M4 19.5A2.5 2.5 0 016.5 17H20M4 19.5A2.5 2.5 0 016.5 22H20V2H6.5A2.5 2.5 0 004 4.5z',
  explorer: 'M3 5a2 2 0 012-2h4l2 2h8a2 2 0 012 2v10a2 2 0 01-2 2H5a2 2 0 01-2-2z',
}

const ITEMS: Item[] = [
  { id: 'summary', label: 'Executive Summary', icon: 'summary', hint: 'The report for the client' },
  { id: 'overview', label: 'Overview', icon: 'overview', hint: 'Results analytics for this run' },
  { id: 'components', label: 'Components', icon: 'components', hint: 'Every component and its evidence' },
  { id: 'examples', label: 'Examples', icon: 'examples', hint: 'How verdicts look for real scenarios' },
  { id: 'explorer', label: 'Code Explorer', icon: 'explorer', hint: 'Browse the org as files, coloured by verdict' },
  { id: 'report', label: 'Deliverables', icon: 'report', hint: 'Export the findings' },
  { id: 'assistant', label: 'Agent Iris', icon: 'assistant', hint: 'Ask about this org' },
  { id: 'docs', label: 'About Me', icon: 'docs', hint: 'Method, architecture, limits' },
  { id: 'library', label: 'AI Library', icon: 'library', hint: 'Skills, agents and hooks' },
  // Engineering views. Hidden from the client-facing product and revealed by
  // clicking the wordmark five times -- see `revealed` below.
  { id: 'pipeline', label: 'Pipeline', icon: 'pipeline', hint: 'Live analysis progress', internal: true },
  { id: 'graph', label: 'Dependencies', icon: 'graph', hint: 'What reaches what', internal: true },
  { id: 'admin', label: 'Access', icon: 'admin', hint: 'Who can use this', adminOnly: true },
]

export function Nav({
  view, onNav, running, collapsed, onToggle, counts, isAdmin = false,
  revealed = false, onBrandClick,
}: {
  view: View
  onNav: (v: View) => void
  running: boolean
  collapsed: boolean
  onToggle: () => void
  counts: { unused: number; review: number }
  isAdmin?: boolean
  revealed?: boolean
  onBrandClick?: () => void
}) {
  return (
    <nav className={`nav ${collapsed ? 'collapsed' : ''}`}>
      <div className="nav-brand" onClick={onBrandClick}
           title="AI Health Assessment">
        <svg viewBox="0 0 24 24" className="brand-mark" aria-hidden>
          <path d="M12 2l8 4.5v9L12 20l-8-4.5v-9z" fill="none" stroke="currentColor" strokeWidth="1.6" />
          <path d="M12 7l4 2.3v4.4L12 16l-4-2.3V9.3z" fill="currentColor" opacity=".55" />
        </svg>
        {!collapsed && (
          <span className="brand-text">
            AI Health<em>Assessment</em>
          </span>
        )}
      </div>

      <ul className="nav-list">
        {ITEMS.filter((it) => (!it.adminOnly || isAdmin)
                      && (!it.internal || revealed)).map((it) => (
          <li key={it.id}>
            <button
              className="nav-item"
              data-active={view === it.id}
              onClick={() => onNav(it.id)}
              title={collapsed ? `${it.label} — ${it.hint}` : it.hint}
            >
              <svg viewBox="0 0 24 24" className="nav-icon" aria-hidden>
                <path d={ICONS[it.icon]} fill="none" stroke="currentColor"
                      strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" />
              </svg>
              {!collapsed && <span className="nav-label">{it.label}</span>}
              {it.id === 'pipeline' && running && <i className="nav-dot" title="run in progress" />}
              {!collapsed && it.id === 'components' && counts.unused > 0 && (
                <span className="nav-badge unused">{counts.unused}</span>
              )}
              {!collapsed && it.id === 'components' && counts.unused === 0 && counts.review > 0 && (
                <span className="nav-badge review">{counts.review}</span>
              )}
            </button>
          </li>
        ))}
      </ul>

      <button className="nav-collapse" onClick={onToggle}
              title={collapsed ? 'Expand sidebar' : 'Collapse sidebar'}>
        <svg viewBox="0 0 24 24" aria-hidden>
          <path d={collapsed ? 'M9 6l6 6-6 6' : 'M15 6l-6 6 6 6'} fill="none"
                stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" />
        </svg>
        {!collapsed && <span>Collapse</span>}
      </button>
    </nav>
  )
}
