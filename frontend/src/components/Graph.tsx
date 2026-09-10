import cytoscape from 'cytoscape'
// @ts-expect-error - fcose ships no bundled types
import fcose from 'cytoscape-fcose'
import { useCallback, useEffect, useRef, useState } from 'react'
import type { GraphPayload } from '../lib/api'
import { explainDecision, explainReasons } from '../lib/explain'

cytoscape.use(fcose)

/**
 * Shape encodes WHAT a node is; colour encodes its STATE.
 *
 * Two independent channels rather than one overloaded palette: you can see at a
 * glance that a diamond is a flow and that it is amber, without having to
 * remember which of eight colours meant "flow". It also means the graph stays
 * readable in greyscale and for colour-blind viewers, since shape alone
 * distinguishes every category.
 */
const SHAPE: Record<string, string> = {
  // Things that execute
  ApexTrigger: 'triangle',
  Flow: 'diamond',
  FlowDefinition: 'diamond',
  Workflow: 'diamond',
  ValidationRule: 'vee',
  ApprovalProcess: 'diamond',
  // Code
  ApexClass: 'hexagon',
  ApexMethod: 'hexagon',
  ApexPage: 'hexagon',
  ApexComponent: 'hexagon',
  // UI
  LightningComponentBundle: 'round-rectangle',
  AuraDefinitionBundle: 'round-rectangle',
  FlexiPage: 'round-rectangle',
  Layout: 'rectangle',
  CompactLayout: 'rectangle',
  QuickAction: 'round-tag',
  // Reporting
  Report: 'star',
  Dashboard: 'star',
  ReportType: 'star',
  EmailTemplate: 'round-tag',
  // Schema — the things we may delete
  CustomObject: 'ellipse',
  StandardObject: 'ellipse',
  CustomField: 'ellipse',
  // Permission / structure
  Profile: 'barrel',
  PermissionSet: 'barrel',
  PermissionSetGroup: 'barrel',
  SharingRules: 'barrel',
  CustomApplication: 'pentagon',
  CustomTab: 'pentagon',
}

const LEGEND_SHAPES: [string, string][] = [
  ['triangle', 'Trigger'],
  ['diamond', 'Flow / Workflow'],
  ['hexagon', 'Apex'],
  ['round-rectangle', 'UI component'],
  ['star', 'Report / Dashboard'],
  ['ellipse', 'Object / Field'],
  ['rectangle', 'Layout'],
  ['barrel', 'Permissions'],
]

type LayoutName = 'fcose' | 'concentric' | 'breadthfirst' | 'circle' | 'grid'

interface Neighbour { id: string; label: string; type: string; entry?: boolean }
interface Classification {
  label: string; confidence: number; reasons: string[]
  prereqs: number; gaps: boolean
}
interface Hovered {
  label: string; type: string; kind: string
  verdict: string | null; cls: Classification | null
  entryPoint: boolean; entryReason: string | null
  reachable: boolean; distance: number | null; inScope: boolean
  x: number; y: number
}

/**
 * Reason codes are translated in `explain.ts` so Graph and DetailPanel never
 * disagree on wording.
 */
interface Inspected {
  id: string; componentId: number | null; label: string; type: string; kind: string
  verdict: string | null; reachable: boolean; entryPoint: boolean
  entryReason: string | null; distance: number | null
  inbound: Neighbour[]; outbound: Neighbour[]; path: string[]
}

/** Centre on a node and re-trigger its selection, so the panel follows a click. */
function jump(cy: cytoscape.Core | null, id: string) {
  if (!cy) return
  const n = cy.getElementById(id)
  if (!n || n.empty()) return
  cy.animate({ center: { eles: n }, zoom: Math.max(cy.zoom(), 0.9) }, { duration: 220 })
  n.emit('tap')
}

const LAYOUTS: Record<LayoutName, { label: string; hint: string; opts: object }> = {
  fcose: {
    label: 'Clustered',
    hint: 'Force-directed. Related components pull together, so tightly-coupled areas become visible.',
    opts: {
      name: 'fcose', animate: false, fit: true, padding: 60,
      nodeSeparation: 135, idealEdgeLength: 105, nodeRepulsion: 14000,
      gravity: 0.3, numIter: 2500,
    },
  },
  breadthfirst: {
    label: 'Hierarchy',
    hint: 'Layered outward from entry points. Best for answering "what does this reach, and how far".',
    opts: {
      name: 'breadthfirst', animate: false, fit: true, padding: 60,
      directed: true, spacingFactor: 1.75, grid: true,
    },
  },
  concentric: {
    label: 'Rings by distance',
    hint: 'Entry points in the centre, each ring one hop further out. Unreachable nodes sit on the outermost ring.',
    opts: {
      name: 'concentric', animate: false, fit: true, padding: 60,
      minNodeSpacing: 38,
      concentric: (n: cytoscape.NodeSingular) =>
        n.data('entryPoint') ? 100 : 100 - (n.data('distance') ?? 20) * 8,
      levelWidth: () => 1,
    },
  },
  circle: {
    label: 'Circle',
    hint: 'Every node on one ring. Useful for spotting isolated nodes with no edges at all.',
    opts: { name: 'circle', animate: false, fit: true, padding: 60, spacingFactor: 1.2 },
  },
  grid: {
    label: 'Grid',
    hint: 'Stable positions, no physics. Easiest for scanning labels methodically.',
    opts: { name: 'grid', animate: false, fit: true, padding: 60, avoidOverlap: true },
  },
}

export function Graph({
  data, onSelect, onOpenComponent,
}: {
  data: GraphPayload
  onSelect: (id: string, label: string) => void
  /** Explicit navigation, only when the user asks for it. */
  onOpenComponent?: (componentId: number) => void
}) {
  const ref = useRef<HTMLDivElement>(null)
  const cyRef = useRef<cytoscape.Core | null>(null)
  const [layout, setLayout] = useState<LayoutName>('fcose')
  const [showLabels, setShowLabels] = useState(true)
  const [onlyUnreachable, setOnlyUnreachable] = useState(false)
  const [highlight, setHighlight] = useState<string | null>(null)
  // Finding a named component in a few hundred nodes was previously impossible:
  // the toolbar could re-lay-out and re-colour the graph but not answer "where
  // is Customer_Order__c".
  const [search, setSearch] = useState('')
  const [matchCount, setMatchCount] = useState(0)
  const [cursor, setCursor] = useState(0)
  /** Ids of the current matches, in graph order. Written by the effect below. */
  const matchIds = useRef<string[]>([])
  const [inspect, setInspect] = useState<Inspected | null>(null)
  const [hover, setHover] = useState<Hovered | null>(null)

  useEffect(() => {
    if (!ref.current) return
    const css = getComputedStyle(document.documentElement)
    const V = (n: string, fb: string) => css.getPropertyValue(n).trim() || fb
    // Fallbacks are the AT&T light palette, so a missing variable degrades to
    // something on-brand rather than to the dark theme this used to assume.
    const colour = {
      entry: V('--accent', '#0057B8'),
      reachable: V('--used', '#16794C'),
      unreachable: V('--unused', '#B25A00'),
      artifact: V('--scope', '#6B7280'),
      line: V('--border-strong', '#C7CCD3'),
      text: V('--text-dim', '#454B52'),
      bright: V('--text', '#1D2329'),
      bg: V('--bg', '#FFFFFF'),
    }

    // Degree drives node size, so it is computed once here rather than in a
    // style callback that cytoscape re-runs on every repaint.
    const degree = new Map<string, number>()
    for (const e of data.edges as { data: { source: string; target: string } }[]) {
      degree.set(e.data.source, (degree.get(e.data.source) ?? 0) + 1)
      degree.set(e.data.target, (degree.get(e.data.target) ?? 0) + 1)
    }
    const maxDeg = Math.max(1, ...degree.values())
    const nodes = (data.nodes as { data: Record<string, unknown> }[]).map((n) => ({
      ...n,
      data: { ...n.data, deg: degree.get(String(n.data.id)) ?? 0 },
    }))

    /** Area-proportional so a 40-edge hub does not become a dinner plate. */
    const sizeOf = (n: cytoscape.NodeSingular): number => {
      const base = n.data('entryPoint') ? 22
        : (n.data('kind') === 'component' && !n.data('reachable')) ? 19
        : n.data('kind') === 'component' ? 14 : 11
      const lift = Math.sqrt((n.data('deg') ?? 0) / maxDeg) * 16
      return base + lift
    }

    const cy = cytoscape({
      container: ref.current,
      elements: { nodes: nodes as never, edges: data.edges as never },
      style: [
        {
          selector: 'node',
          style: {
            shape: ((n: cytoscape.NodeSingular) =>
              SHAPE[n.data('type')]
              ?? (n.data('kind') === 'component' ? 'ellipse' : 'rectangle')) as never,
            'background-color': (n: cytoscape.NodeSingular) =>
              n.data('entryPoint') ? colour.entry
                : n.data('kind') === 'artifact' ? colour.artifact
                : n.data('reachable') ? colour.reachable
                : colour.unreachable,
            // Category sets a floor; connectivity does the rest. A hub and a
            // leaf looked identical before, which threw away the most useful
            // thing a dependency graph can show.
            width: sizeOf as never,
            height: sizeOf as never,
            // A halo in the page colour. Without it nodes and the edges behind
            // them merge into one mass wherever the graph is dense.
            'border-width': 2,
            'border-color': colour.bg,
            'border-opacity': 1,
            'background-opacity': 0.92,
            // Only label what can actually be read: hubs, entry points and the
            // unreachable components the user came to find. The rest reveal
            // their label on hover.
            label: ((n: cytoscape.NodeSingular) => {
              const worth = n.data('entryPoint')
                || (n.data('kind') === 'component' && !n.data('reachable'))
                || (n.data('deg') ?? 0) >= Math.max(3, maxDeg * 0.25)
              return worth ? n.data('label') : ''
            }) as never,
            color: colour.text,
            'font-size': 9,
            'font-family': 'JetBrains Mono, monospace',
            'text-valign': 'bottom',
            'text-margin-y': 5,
            // Outline rather than a filled box: the boxes tiled into an opaque
            // slab in dense areas and hid the topology underneath.
            'text-outline-color': colour.bg,
            'text-outline-width': 2.5,
            'text-outline-opacity': 1,
            'min-zoomed-font-size': 9,
            'transition-property': 'opacity, width, height, border-color',
            'transition-duration': 140,
          },
        },
        {
          selector: 'edge',
          style: {
            width: (e: cytoscape.EdgeSingular) => (e.data('tier') === 'A' ? 1.3 : 0.7),
            'line-color': colour.line,
            'target-arrow-color': colour.line,
            'target-arrow-shape': 'triangle',
            'arrow-scale': 0.62,
            // Bundled bezier keeps parallel edges between the same pair from
            // overlapping into one thick unreadable line.
            'curve-style': 'bezier',
            'control-point-step-size': 34,
            // Edges are context, nodes are the subject. Quieter than before so
            // the graph reads as points in space rather than a ball of wire.
            opacity: 0.38,
          },
        },
        {
          // Edges from something that executes read as the causal ones, so they
          // are drawn solid and brighter than incidental references.
          selector: 'edge[tier = "A"]',
          style: { opacity: 0.6 },
        },
        // 0.07 was so faint that dimmed nodes vanished and the shape of the
        // graph went with them. Enough to recede, not enough to disappear.
        { selector: '.dim', style: { opacity: 0.16 } },
        {
          selector: '.hl',
          style: {
            'line-color': colour.entry, 'target-arrow-color': colour.entry,
            width: 2.2, opacity: 1, 'z-index': 50,
          },
        },
        {
          selector: 'node.hl',
          style: {
            'border-width': 3, 'border-color': colour.entry,
            color: colour.bright, 'z-index': 60,
            label: 'data(label)',
          },
        },
        {
          // Accent ring, not near-black: on a white brand a black ring reads as
          // a rendering artifact rather than as selection.
          selector: 'node:selected',
          style: {
            'border-width': 3.5, 'border-color': colour.entry, 'z-index': 70,
            label: 'data(label)',
          },
        },
        {
          // Hovering reveals the label for the unlabelled majority, which is
          // where the question "what is this?" is actually asked.
          selector: 'node.hover',
          style: {
            label: 'data(label)', 'border-color': colour.entry,
            'border-width': 3, 'z-index': 80,
          },
        },
        { selector: '.nolabel', style: { label: '' } },
        {
          // Name-search matches: keep the label on so a hit is readable even
          // when it is not a hub (the default label rule would hide it).
          selector: 'node.vhl',
          style: {
            'border-width': 3.5,
            'border-color': colour.entry,
            'z-index': 99,
            opacity: 1,
            label: 'data(label)',
            color: colour.bright,
          },
        },
        {
          // The match the cursor is on — halo so it is unmistakable among peers.
          selector: 'node.vhl-cur',
          style: {
            'border-width': 5,
            'border-color': colour.entry,
            'underlay-color': colour.entry,
            'underlay-padding': 8,
            'underlay-opacity': 0.4,
            'underlay-shape': 'round-rectangle',
            'z-index': 120,
            opacity: 1,
            label: 'data(label)',
            color: colour.bright,
            'font-size': 11,
          },
        },
      ],
      layout: LAYOUTS[layout].opts as never,
      wheelSensitivity: 0.22,
      minZoom: 0.08,
      maxZoom: 4,
    })

    // Hover explains; click investigates. Keeping them separate means you can
    // scan a cluster for what everything IS without losing the node you had
    // selected, which is the common case when reading a dependency chain.
    cy.on('mouseover', 'node', (e) => {
      const n = e.target as cytoscape.NodeSingular
      n.addClass('hover')
      const p = n.renderedPosition()
      setHover({
        label: n.data('label'),
        type: n.data('type'),
        kind: n.data('kind'),
        verdict: n.data('verdict') ?? null,
        cls: n.data('cls') ?? null,
        entryPoint: !!n.data('entryPoint'),
        entryReason: n.data('entryReason') ?? null,
        reachable: !!n.data('reachable'),
        distance: n.data('distance'),
        inScope: n.data('inScope') !== false,
        x: p.x, y: p.y,
      })
      if (ref.current) ref.current.style.cursor = 'pointer'
    })
    cy.on('mouseout', 'node', (e) => {
      // Must mirror the addClass in mouseover, or every node the pointer has
      // ever crossed keeps its label and ring and the graph fills up with them.
      ;(e.target as cytoscape.NodeSingular).removeClass('hover')
      setHover(null)
      if (ref.current) ref.current.style.cursor = 'default'
    })
    // A drag would otherwise leave a tooltip stranded over empty canvas.
    cy.on('pan zoom drag', () => setHover(null))

    cy.on('tap', 'node', (e) => {
      const n = e.target as cytoscape.NodeSingular
      cy.elements().addClass('dim').removeClass('hl')
      // The whole chain, not just immediate neighbours: the question a reviewer
      // is asking is "what keeps this alive", and that is a path, not a
      // one-hop list.
      const chain = n.closedNeighborhood().union(n.predecessors()).union(n.successors())
      chain.removeClass('dim').addClass('hl')

      const inbound = n.incomers('node').map((x) => ({
        id: (x as cytoscape.NodeSingular).data('id'),
        label: (x as cytoscape.NodeSingular).data('label'),
        type: (x as cytoscape.NodeSingular).data('type'),
        entry: (x as cytoscape.NodeSingular).data('entryPoint'),
      }))
      const outbound = n.outgoers('node').map((x) => ({
        id: (x as cytoscape.NodeSingular).data('id'),
        label: (x as cytoscape.NodeSingular).data('label'),
        type: (x as cytoscape.NodeSingular).data('type'),
        entry: (x as cytoscape.NodeSingular).data('entryPoint'),
      }))
      // Walk back to whatever entry point reaches it, so "why is this used" is
      // answerable in the panel rather than by squinting at the canvas.
      const path: string[] = []
      let cur: cytoscape.NodeSingular | undefined = n
      const seen = new Set<string>()
      while (cur && !seen.has(cur.id()) && path.length < 8) {
        seen.add(cur.id())
        path.push(`${cur.data('label')} [${cur.data('type')}]`)
        if (cur.data('entryPoint')) break
        cur = cur.incomers('node').filter((x) =>
          (x as cytoscape.NodeSingular).data('reachable'))[0] as cytoscape.NodeSingular
      }

      setInspect({
        id: n.data('id'), componentId: n.data('componentId') ?? null,
        label: n.data('label'), type: n.data('type'),
        kind: n.data('kind'), verdict: n.data('verdict') ?? null,
        reachable: !!n.data('reachable'), entryPoint: !!n.data('entryPoint'),
        entryReason: n.data('entryReason') ?? null, distance: n.data('distance'),
        inbound, outbound, path: path.reverse(),
      })
      onSelect(n.data('id'), n.data('label'))
    })
    cy.on('tap', (e) => {
      if (e.target === cy) {
        cy.elements().removeClass('dim').removeClass('hl')
        setInspect(null)
      }
    })

    cy.ready(() => { cy.resize(); cy.fit(undefined, 45) })
    const ro = new ResizeObserver(() => { cy.resize(); cy.fit(undefined, 45) })
    ro.observe(ref.current)

    cyRef.current = cy
    ;(window as unknown as { cy?: cytoscape.Core }).cy = cy
    return () => { ro.disconnect(); cy.destroy() }
  }, [data, onSelect, layout])

  // Highlight by name or by classification. Dimming rather than hiding keeps
  // the structure visible: seeing an amber node sitting alone in a dense cluster
  // is the finding, and removing its neighbours would destroy exactly that
  // context. A name search wins over the verdict highlight when both are set --
  // you typed a name because you want that node, not a category.
  useEffect(() => {
    const cy = cyRef.current
    if (!cy) return
    const q = search.trim().toLowerCase()
    cy.batch(() => {
      cy.elements().removeClass('vhl').removeClass('vhl-cur')
      if (!q && !highlight) {
        // Leave click-inspect dimming alone when the user is not searching.
        if (!inspect) cy.elements().removeClass('dim')
        matchIds.current = []
        setMatchCount(0)
        setCursor(0)
        return
      }
      // A typed search replaces the click-inspect view: the question is now
      // "where is this name", not "what did I last tap".
      if (q) setInspect(null)
      cy.elements().addClass('dim').removeClass('hl')
      const matched = q
        ? cy.nodes().filter((n) =>
            String(n.data('label') ?? '').toLowerCase().includes(q))
        : cy.nodes().filter((n) => n.data('verdict') === highlight)
      matched.removeClass('dim').removeClass('nolabel').addClass('vhl')
      matched.connectedEdges().removeClass('dim')
      matched.neighborhood('node').removeClass('dim')
      // Only a name search is steppable; a verdict highlight is a view, not a
      // cursor through a result list.
      matchIds.current = q ? matched.map((n) => n.id()) : []
      setMatchCount(matchIds.current.length)
      setCursor(0)
      cy.nodes().removeClass('vhl-cur')
      if (matchIds.current.length) {
        const first = cy.getElementById(matchIds.current[0])
        if (!first.empty()) first.addClass('vhl-cur')
      }
    })
    // Centre once after the batch so we do not fight layout mid-update. Only
    // when there is a name query — verdict filters are a whole-graph view.
    if (search.trim() && matchIds.current.length) {
      const cy = cyRef.current
      const first = cy?.getElementById(matchIds.current[0])
      if (cy && first && !first.empty()) {
        cy.animate(
          { center: { eles: first }, zoom: Math.max(cy.zoom(), 1.05) },
          { duration: 240 },
        )
      }
    }
  }, [highlight, search, data])

  useEffect(() => {
    const cy = cyRef.current
    if (!cy) return
    cy.batch(() => {
      cy.nodes().forEach((n) => {
        n.toggleClass('nolabel', !showLabels)
        // Keep a node if it IS unreachable, or if it points at something that
        // is: the neighbours are what explain why nothing reaches it.
        const touchesUnreachable = n.connectedEdges().targets()
          .some((t) => (t as cytoscape.NodeSingular).data('reachable') === false)
        const hide = onlyUnreachable
          && !(n.data('kind') === 'component' && !n.data('reachable'))
          && !touchesUnreachable
        n.style('display', hide ? 'none' : 'element')
      })
    })
  }, [showLabels, onlyUnreachable, data])

  /**
   * Move the viewport to the next match.
   *
   * Centres rather than calling `jump`, which emits a tap: opening the
   * inspector would short-circuit the search highlight and drop the dimming.
   */
  const step = useCallback((delta: number) => {
    const ids = matchIds.current
    if (!ids.length) return
    setCursor((was) => {
      const next = (was + delta + ids.length) % ids.length
      const cy = cyRef.current
      if (!cy) return next
      cy.nodes().removeClass('vhl-cur')
      const n = cy.getElementById(ids[next])
      if (!n.empty()) {
        n.addClass('vhl-cur')
        cy.animate(
          { center: { eles: n }, zoom: Math.max(cy.zoom(), 1.05) },
          { duration: 220 },
        )
      }
      return next
    })
  }, [])

  const fit = useCallback(() => cyRef.current?.fit(undefined, 45), [])
  const png = useCallback(() => {
    const cy = cyRef.current
    if (!cy) return
    const a = document.createElement('a')
    a.href = cy.png({ full: true, scale: 2, bg: getComputedStyle(document.documentElement)
      .getPropertyValue('--bg').trim() || '#FFFFFF' })
    a.download = 'dependency-graph.png'
    a.click()
  }, [])

  return (
    <div className="graph-inner">
      <div className="graph-toolbar">
        <select value={layout} title={LAYOUTS[layout].hint}
                onChange={(e) => setLayout(e.target.value as LayoutName)}>
          {Object.entries(LAYOUTS).map(([k, v]) => (
            <option key={k} value={k}>{v.label}</option>
          ))}
        </select>

        <span className="tb-sep" />
        <div className="graph-search">
          <input value={search} placeholder="find a component..."
                 onChange={(e) => setSearch(e.target.value)}
                 onKeyDown={(e) => {
                   if (e.key !== 'Enter') return
                   e.preventDefault()
                   step(e.shiftKey ? -1 : 1)
                 }} />
          {search.trim() && (
            <span className="tb-hint">
              {matchCount ? `${cursor + 1} of ${matchCount}` : 'no match'}
            </span>
          )}
          {search && (
            <>
              <button className="ghost sm" disabled={!matchCount}
                      title="Previous match (Shift+Enter)"
                      onClick={() => step(-1)}>&lt;</button>
              <button className="ghost sm" disabled={!matchCount}
                      title="Next match (Enter)"
                      onClick={() => step(1)}>&gt;</button>
              <button className="linkish" onClick={() => setSearch('')}>clear</button>
            </>
          )}
        </div>

        <span className="tb-sep" />
        <span className="tb-label">Highlight</span>
        {(['UNUSED', 'NEEDS_REVIEW', 'USED', 'OUT_OF_SCOPE'] as const).map((v) => (
          <button key={v} className={`hlbtn ${v}`} data-active={highlight === v}
                  title="Dims everything else rather than hiding it - an isolated
                         amber node inside a dense cluster is itself the finding."
                  onClick={() => setHighlight(highlight === v ? null : v)}>
            {v.replaceAll('_', ' ')}
          </button>
        ))}
        {highlight && (
          <button className="linkish" onClick={() => setHighlight(null)}>clear</button>
        )}

        <span className="spacer" />
        <label className="tb-check">
          <input type="checkbox" checked={showLabels}
                 onChange={(e) => setShowLabels(e.target.checked)} />
          labels
        </label>
        <label className="tb-check">
          <input type="checkbox" checked={onlyUnreachable}
                 onChange={(e) => setOnlyUnreachable(e.target.checked)} />
          unreachable only
        </label>
        <button className="ghost sm" onClick={fit}>Fit</button>
        <button className="ghost sm" onClick={png}>PNG</button>
      </div>

      <div className="graph-canvas">
        <div id="cy" ref={ref} />

        {hover && (
          <div
            className="gtip"
            style={{
              // Flip across the pointer near an edge so the tooltip never runs
              // off the canvas and clip its own content.
              left: hover.x + 16,
              top: hover.y + 14,
              transform: `translate(${hover.x > (ref.current?.clientWidth ?? 0) - 260 ? '-100%' : '0'}, ${hover.y > (ref.current?.clientHeight ?? 0) - 190 ? '-100%' : '0'})`,
            }}
          >
            <div className="gtip-name"><code>{hover.label}</code></div>
            <div className="gtip-row">
              <span className="gtip-k">Type</span>
              <span className="gtip-v">
                {hover.type}
                <em>{hover.kind === 'component' ? ' (a component you could delete)'
                                                : ' (metadata that references things)'}</em>
              </span>
            </div>

            {hover.verdict ? (
              <>
                <div className="gtip-row">
                  <span className="gtip-k">Verdict</span>
                  <span className={`badge ${hover.verdict}`}>
                    {hover.verdict.replaceAll('_', ' ')}
                    {hover.cls ? ` · ${hover.cls.confidence}` : ''}
                  </span>
                </div>
                {hover.cls && (hover.verdict || hover.cls.reasons.length > 0) && (
                  <div className="gtip-why">
                    <span className="gtip-k">Why</span>
                    <ul>
                      {(() => {
                        const d = explainDecision({
                          label: hover.verdict ?? hover.cls.label,
                          reason_codes: hover.cls.reasons,
                        })
                        return [
                          ...(hover.verdict ? [d.headline] : []),
                          d.because,
                          ...(!hover.verdict ? explainReasons(hover.cls.reasons) : []),
                        ].filter((w, i, a) => w && a.indexOf(w) === i)
                      })().map((w, i) => <li key={i}>{w}</li>)}
                    </ul>
                  </div>
                )}
                {hover.cls?.prereqs ? (
                  <div className="gtip-note warn">
                    {hover.cls.prereqs} step(s) needed before it can be deleted
                  </div>
                ) : null}
                {hover.cls?.gaps ? (
                  <div className="gtip-note warn">
                    A check could not run for this component
                  </div>
                ) : null}
              </>
            ) : (
              <div className="gtip-note">
                Not classified - this is metadata that references components,
                not something the analysis judges.
              </div>
            )}

            {hover.entryPoint ? (
              <div className="gtip-note entry">
                Entry point{hover.entryReason ? `: ${hover.entryReason}` : ''}
              </div>
            ) : hover.kind === 'component' && !hover.reachable ? (
              <div className="gtip-note warn">
                Not reachable from anything that runs or is seen
              </div>
            ) : hover.distance != null ? (
              <div className="gtip-note">
                {hover.distance} hop(s) from an entry point
              </div>
            ) : null}

            <div className="gtip-hint">click to inspect its connections</div>
          </div>
        )}
        {inspect && (
          <aside className="inspector">
            <div className="ins-head">
              <code>{inspect.label}</code>
              <button className="linkish" onClick={() => {
                cyRef.current?.elements().removeClass('dim').removeClass('hl')
                setInspect(null)
              }}>close</button>
            </div>
            <div className="ins-meta">
              <span className="chip">{inspect.type}</span>
              {inspect.verdict && (
                <span className={`badge ${inspect.verdict}`}>
                  {inspect.verdict.replaceAll('_', ' ')}
                </span>
              )}
              {inspect.entryPoint && <span className="chip entry">entry point</span>}
              {!inspect.reachable && inspect.kind === 'component' && (
                <span className="chip warn">unreachable</span>
              )}
              {inspect.distance != null && (
                <span className="chip">{inspect.distance} hop(s) from an entry point</span>
              )}
            </div>
            {inspect.entryReason && (
              <p className="ins-note">Entry point because: {inspect.entryReason}</p>
            )}
            {inspect.componentId != null && onOpenComponent && (
              <button className="ghost sm full"
                      onClick={() => onOpenComponent(inspect.componentId!)}>
                Open full evidence trail
              </button>
            )}

            {inspect.path.length > 1 && (
              <>
                <h4>What keeps it alive</h4>
                <div className="ins-path">
                  {inspect.path.map((p, i) => (
                    <div key={i} style={{ paddingLeft: i * 11 }}>
                      {i > 0 && <span className="arrow">|- </span>}{p}
                    </div>
                  ))}
                </div>
              </>
            )}

            <h4>Referenced by ({inspect.inbound.length})</h4>
            {inspect.inbound.length === 0
              ? <p className="ins-empty">Nothing points at this.</p>
              : (
                <ul className="ins-list">
                  {inspect.inbound.slice(0, 30).map((x) => (
                    <li key={x.id}>
                      <button onClick={() => jump(cyRef.current, x.id)}>
                        <code>{x.label}</code>
                        <em>{x.type}</em>
                        {x.entry && <i className="dotmark" title="entry point" />}
                      </button>
                    </li>
                  ))}
                </ul>
              )}

            <h4>References ({inspect.outbound.length})</h4>
            {inspect.outbound.length === 0
              ? <p className="ins-empty">This points at nothing.</p>
              : (
                <ul className="ins-list">
                  {inspect.outbound.slice(0, 30).map((x) => (
                    <li key={x.id}>
                      <button onClick={() => jump(cyRef.current, x.id)}>
                        <code>{x.label}</code>
                        <em>{x.type}</em>
                      </button>
                    </li>
                  ))}
                </ul>
              )}
          </aside>
        )}
      </div>

      <div className="shape-legend">
        <span className="sl-title">Shape = what it is</span>
        {LEGEND_SHAPES.map(([shape, label]) => (
          <span className="sl" key={shape}>
            <ShapeIcon shape={shape} />
            {label}
          </span>
        ))}
      </div>
    </div>
  )
}

/** Small SVG stand-ins so the legend uses the same shape vocabulary as the graph. */
function ShapeIcon({ shape }: { shape: string }) {
  const c = 'var(--text-dim)'
  const p: Record<string, React.ReactElement> = {
    triangle: <polygon points="6,1 11,10 1,10" fill={c} />,
    diamond: <polygon points="6,1 11,6 6,11 1,6" fill={c} />,
    hexagon: <polygon points="6,1 10.5,3.5 10.5,8.5 6,11 1.5,8.5 1.5,3.5" fill={c} />,
    'round-rectangle': <rect x="1.5" y="3" width="9" height="6" rx="2" fill={c} />,
    rectangle: <rect x="1.5" y="3" width="9" height="6" fill={c} />,
    star: <polygon points="6,1 7.3,4.6 11,4.6 8.1,7 9.2,10.6 6,8.4 2.8,10.6 3.9,7 1,4.6 4.7,4.6" fill={c} />,
    ellipse: <ellipse cx="6" cy="6" rx="5" ry="4" fill={c} />,
    barrel: <path d="M2 3h8v6H2z" fill={c} />,
  }
  return <svg viewBox="0 0 12 12" className="sl-icon">{p[shape] ?? p.ellipse}</svg>
}
