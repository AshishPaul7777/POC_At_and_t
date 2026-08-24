const pptxgen = require('pptxgenjs')

const pres = new pptxgen()
pres.layout = 'LAYOUT_WIDE'          // 13.3 x 7.5 — set BEFORE any slide
pres.author = 'Org Cleanup Analyzer'
pres.title = 'Salesforce Org Cleanup'

/* Palette taken from the product's own semantic colours, so the deck and the
   tool speak the same visual language: amber = a finding, green = validated,
   violet = machine-written, teal = interactive. */
const C = {
  ink: '0F1620', surface: '1A2430', card: '223040', line: '32435A',
  text: 'F2F5F8', dim: 'A8B4C4', faint: '76849A',
  teal: '29B6D8', amber: 'F0A030', green: '3FBF92', violet: '9B84F7',
  red: 'F0555A', white: 'FFFFFF',
}
const H = 'Arial', B = 'Calibri'

const W = 13.33, HT = 7.5, M = 0.62

/* ---------- helpers ---------- */
function dark(s) { s.background = { color: C.ink } }
function light(s) { s.background = { color: C.white } }

function title(s, t, onDark = true, sub) {
  s.addText(t, {
    x: M, y: 0.44, w: W - M * 2, h: 0.78, fontSize: 34, bold: true,
    color: onDark ? C.text : C.ink, fontFace: H, margin: 0,
  })
  if (sub) {
    s.addText(sub, {
      x: M, y: 1.22, w: W - M * 2, h: 0.44, fontSize: 14.5,
      color: onDark ? C.dim : '55606E', fontFace: B, margin: 0,
    })
  }
}

/** Numbered disc — the repeated motif across the deck. */
function disc(s, x, y, n, col = C.teal, d = 0.42) {
  s.addShape(pres.ShapeType.ellipse, {
    x, y, w: d, h: d, fill: { color: col },
  })
  s.addText(String(n), {
    x, y, w: d, h: d, fontSize: d > 0.5 ? 15 : 12.5, bold: true,
    color: C.ink, align: 'center', valign: 'middle', fontFace: H, margin: 0,
  })
}

function card(s, x, y, w, h, fill = C.surface, line = C.line) {
  s.addShape(pres.ShapeType.roundRect, {
    x, y, w, h, rectRadius: 0.06,
    fill: { color: fill }, line: { color: line, width: 1 },
  })
}

function pageNo(s, n) {
  s.addText(String(n), {
    x: W - 0.85, y: HT - 0.52, w: 0.4, h: 0.3, fontSize: 10,
    color: C.faint, align: 'right', fontFace: B, margin: 0,
  })
}

let page = 0
function slide(onDark = true) {
  const s = pres.addSlide()
  onDark ? dark(s) : light(s)
  page++
  if (page > 1) {
    s.addText(String(page), {
      x: W - 0.85, y: HT - 0.44, w: 0.4, h: 0.3, fontSize: 10,
      color: onDark ? C.faint : 'A0A8B4', align: 'right', fontFace: B, margin: 0,
    })
  }
  return s
}

/* =====================================================================
   1 — Title
   ===================================================================== */
{
  const s = slide()
  s.addShape(pres.ShapeType.ellipse, {
    x: 9.4, y: -1.5, w: 6.4, h: 6.4,
    fill: { color: C.teal, transparency: 88 }, line: { color: C.ink, width: 0 },
  })
  s.addShape(pres.ShapeType.ellipse, {
    x: 10.9, y: 3.1, w: 3.6, h: 3.6,
    fill: { color: C.amber, transparency: 90 }, line: { color: C.ink, width: 0 },
  })
  s.addText('ORG CLEANUP', {
    x: M, y: 2.02, w: 8, h: 0.34, fontSize: 13.5, bold: true,
    color: C.teal, charSpacing: 3, fontFace: H, margin: 0,
  })
  s.addText('Find what nobody uses.\nRemove it safely.', {
    x: M, y: 2.44, w: 8.4, h: 2.16, fontSize: 42, bold: true,
    color: C.text, fontFace: H, lineSpacing: 50, margin: 0,
  })
  s.addText(
    'A deterministic pipeline with AI where it helps, human judgement where it '
    + 'matters, and Salesforce itself as the final arbiter.',
    { x: M, y: 4.78, w: 7.6, h: 0.9, fontSize: 15, color: C.dim,
      fontFace: B, lineSpacing: 22, margin: 0 })
  s.addText('Multi-signal analysis  ·  Evidence-first  ·  Automated remediation', {
    x: M, y: 6.2, w: 9, h: 0.34, fontSize: 12.5, color: C.faint,
    fontFace: B, margin: 0,
  })
  s.addNotes('The product finds unused Salesforce metadata and takes it all the '
    + 'way to a reviewed pull request. The emphasis throughout is on being '
    + 'defensible: every verdict can be explained, and the risky step is '
    + 'validated by Salesforce rather than inferred.')
}

/* =====================================================================
   2 — Problem
   ===================================================================== */
{
  const s = slide(false)
  title(s, 'Mature orgs accumulate debt nobody touches', false,
    'The cost is continuous, and it compounds quietly.')

  const items = [
    ['Fields nobody populates', 'Added for a project that shipped differently, or never shipped. They stay on layouts, in page loads, in every describe call.'],
    ['Objects with no consumer', 'A discontinued process leaves its schema behind. Reports still list it. Nobody remembers why.'],
    ['Apex that nothing calls', 'Service classes orphaned by a refactor. Still compiled, still counted in coverage, still maintained.'],
    ['Automation running on dead data', 'Flows and rules firing against fields no business process reads.'],
  ]
  items.forEach(([h, d], i) => {
    const y = 1.92 + i * 1.24
    disc(s, M, y + 0.06, i + 1, C.amber, 0.4)
    s.addText(h, { x: M + 0.62, y, w: 6.2, h: 0.34, fontSize: 16, bold: true,
      color: C.ink, fontFace: H, margin: 0 })
    s.addText(d, { x: M + 0.62, y: y + 0.36, w: 6.4, h: 0.72, fontSize: 12.5,
      color: '5A6472', fontFace: B, lineSpacing: 17, margin: 0 })
  })

  card(s, 7.72, 1.9, 5.0, 4.72, 'F4F7FA', 'DDE4EC')
  s.addText('What it costs', { x: 8.06, y: 2.18, w: 4.3, h: 0.34, fontSize: 15,
    bold: true, color: C.ink, fontFace: H, margin: 0 })
  const costs = [
    ['Slower deploys', 'every field is validated, packaged, and deployed forever'],
    ['Longer test runs', 'dead Apex still needs coverage to deploy anything'],
    ['Onboarding friction', 'new engineers cannot tell live from abandoned'],
    ['Risk concentration', 'nobody will delete it, because nobody can prove it is safe'],
  ]
  costs.forEach(([h, d], i) => {
    const y = 2.68 + i * 0.98
    s.addText(h, { x: 8.06, y, w: 4.3, h: 0.28, fontSize: 13.5, bold: true,
      color: C.ink, fontFace: B, margin: 0 })
    s.addText(d, { x: 8.06, y: y + 0.3, w: 4.3, h: 0.56, fontSize: 11.5,
      color: '6B7686', fontFace: B, lineSpacing: 15, margin: 0 })
  })
  s.addNotes('The reason this debt persists is not that teams do not notice it. '
    + 'It is that nobody can prove a component is safe to delete, so the '
    + 'rational choice is always to leave it.')
}

/* =====================================================================
   3 — The asymmetry
   ===================================================================== */
{
  const s = slide()
  title(s, 'Why this is hard, and why tools fail at it', true,
    'The two failure directions do not cost the same. Everything follows from that.')

  card(s, M, 2.1, 5.9, 2.5, C.surface, C.red)
  s.addText('A wrong "unused"', { x: M + 0.42, y: 2.42, w: 5, h: 0.36,
    fontSize: 19, bold: true, color: C.red, fontFace: H, margin: 0 })
  s.addText('Someone deletes a live field. Production breaks. Data is gone after '
    + '15 days. Trust in the tool is gone permanently.',
    { x: M + 0.42, y: 2.9, w: 5.06, h: 1.3, fontSize: 14, color: C.dim,
      fontFace: B, lineSpacing: 20, margin: 0 })

  card(s, 6.86, 2.1, 5.85, 2.5, C.surface, C.line)
  s.addText('A wrong "needs review"', { x: 7.28, y: 2.42, w: 5, h: 0.36,
    fontSize: 19, bold: true, color: C.green, fontFace: H, margin: 0 })
  s.addText('An engineer spends five minutes reading the evidence and moves on. '
    + 'Nothing breaks. Nothing is lost.',
    { x: 7.28, y: 2.9, w: 5.0, h: 1.3, fontSize: 14, color: C.dim,
      fontFace: B, lineSpacing: 20, margin: 0 })

  card(s, M, 4.92, 12.09, 1.62, '1E2A38', C.amber)
  s.addText('The governing rule', { x: M + 0.42, y: 5.16, w: 3.4, h: 0.3,
    fontSize: 12.5, bold: true, color: C.amber, charSpacing: 1,
    fontFace: H, margin: 0 })
  s.addText('Every ambiguity resolves toward review. The product is deliberately '
    + 'biased against confidence — and that bias is what makes its confident '
    + 'answers worth acting on.',
    { x: M + 0.42, y: 5.5, w: 11.2, h: 0.8, fontSize: 15, color: C.text,
      fontFace: B, lineSpacing: 21, margin: 0 })
  s.addNotes('Most cleanup tools optimise for finding the most items. That is the '
    + 'wrong objective. One false positive destroys confidence in every other '
    + 'finding, so the design target is a small set of findings you can act on '
    + 'without checking.')
}

/* =====================================================================
   4 — What "used" means
   ===================================================================== */
{
  const s = slide(false)
  title(s, 'The definition that decides everything', false,
    '"Used" means a business process touches it. Not that something mentions it.')

  card(s, M, 2.02, 5.95, 4.35, 'F2FAF6', 'BFE6D4')
  s.addText('Counts as use', { x: M + 0.4, y: 2.3, w: 4.6, h: 0.34,
    fontSize: 16, bold: true, color: '1B7A55', fontFace: H, margin: 0 })
  const yes = ['Apex, triggers, invocable methods', 'Flows, workflow rules, approval processes',
    'Validation rules and formulas', 'Reports, dashboards, list views',
    'LWC, Aura, Visualforce', 'Email templates and merge fields',
    'Real data in real records', 'API names stored in config records']
  yes.forEach((t, i) => {
    s.addText(t, { x: M + 0.4, y: 2.76 + i * 0.44, w: 5.1, h: 0.36,
      fontSize: 12.5, color: '2B3A46', bullet: true, fontFace: B, margin: 0 })
  })

  card(s, 6.86, 2.02, 5.85, 4.35, 'FDF6EC', 'F0D5AC')
  s.addText('Does NOT count as use', { x: 7.26, y: 2.3, w: 4.6, h: 0.34,
    fontSize: 16, bold: true, color: '9A6209', fontFace: H, margin: 0 })
  const no = [
    ['Page layouts', 'every custom field gets one at creation'],
    ['Field-level security', 'means someone COULD see it, not that anyone does'],
    ['Its own definition file', 'a declaration, not a use'],
    ['Tabs and applications', 'navigation structure'],
    ['Comments and dead code', 'a stale mention is not a reference'],
    ['Test code alone', 'delete it together with what it tests'],
  ]
  no.forEach(([h, d], i) => {
    const y = 2.78 + i * 0.6
    s.addText(h, { x: 7.26, y, w: 5.0, h: 0.26, fontSize: 13, bold: true,
      color: '2B3A46', fontFace: B, margin: 0 })
    s.addText(d, { x: 7.26, y: y + 0.26, w: 5.0, h: 0.26, fontSize: 11,
      color: '7A6A52', fontFace: B, margin: 0 })
  })

  s.addText('Measured on a real org: counting layout presence as use produced 0 '
    + 'findings. Separating the two produced 25.',
    { x: M, y: 6.56, w: 12.1, h: 0.4, fontSize: 13, italic: true,
      color: '55606E', fontFace: B, margin: 0 })
  s.addNotes('This distinction is the entire product. A tool that counts mere '
    + 'presence as use marks nearly everything used and finds nothing — which is '
    + 'worse than useless, because it looks authoritative.')
}

/* =====================================================================
   5 — Scope
   ===================================================================== */
{
  const s = slide()
  title(s, 'Scope: the whole org, not three component types', true,
    'Fields, objects and Apex are where value concentrates. They are not the boundary.')

  const groups = [
    ['Schema', C.teal, ['Custom objects', 'Custom fields', 'Record types', 'Field sets', 'Picklist value sets', 'Custom metadata types']],
    ['Code', C.green, ['Apex classes', 'Apex triggers', 'Apex methods', 'Visualforce pages', 'Lightning web components', 'Aura bundles']],
    ['Automation', C.amber, ['Flows and versions', 'Workflow rules', 'Validation rules', 'Approval processes', 'Assignment & escalation', 'Duplicate & matching rules']],
    ['Presentation', C.violet, ['Page layouts', 'Lightning pages', 'Quick actions', 'Buttons and links', 'Compact layouts', 'Path assistants']],
    ['Reporting', C.teal, ['Reports', 'Dashboards', 'Report types', 'List views', 'Reporting snapshots', 'Email templates']],
    ['Access & integration', C.green, ['Profiles', 'Permission sets', 'Custom permissions', 'Connected apps', 'Named credentials', 'Platform events']],
  ]
  groups.forEach(([name, col, items], i) => {
    const cx = M + (i % 3) * 4.08
    const cy = 1.96 + Math.floor(i / 3) * 2.42
    card(s, cx, cy, 3.85, 2.22)
    s.addShape(pres.ShapeType.ellipse, { x: cx + 0.3, y: cy + 0.28, w: 0.2, h: 0.2,
      fill: { color: col } })
    s.addText(name, { x: cx + 0.6, y: cy + 0.2, w: 3.1, h: 0.34, fontSize: 14.5,
      bold: true, color: C.text, fontFace: H, margin: 0 })
    items.forEach((t, j) => {
      s.addText(t, { x: cx + 0.32, y: cy + 0.66 + j * 0.25, w: 3.4, h: 0.24,
        fontSize: 10.5, color: C.dim, fontFace: B, margin: 0 })
    })
  })
  s.addNotes('Coverage matters for correctness, not just completeness. A field '
    + 'referenced only by a report we never retrieved looks unused — so the '
    + 'breadth of what we index directly determines how many false positives '
    + 'we produce.')
}

/* =====================================================================
   6 — Architecture choice: the comparison
   ===================================================================== */
{
  const s = slide(false)
  title(s, 'Agent, workflow, or hybrid?', false,
    'The architecture decision that determines whether this is trustworthy.')

  const cols = [
    ['Pure agent', 'E8ECF2', '8A94A4', [
      ['Non-deterministic', 'the same org yields different answers run to run'],
      ['Token cost repeats', 'every run re-reads every component from scratch'],
      ['Silent omissions', 'no way to know which checks it chose to skip'],
      ['Unauditable', '"it decided" is not a defensible reason to delete'],
      ['No coverage guarantee', 'cannot prove a source was searched'],
    ]],
    ['Pure workflow', 'E8ECF2', '8A94A4', [
      ['Fully deterministic', 'reproducible, explainable, cheap to re-run'],
      ['Rigid', 'cannot read intent from unfamiliar code'],
      ['No narrative', 'produces codes, not explanations a reviewer can use'],
      ['Brittle at the edges', 'dynamic and config-driven patterns defeat it'],
      ['Cannot refactor', 'finding is not the same as fixing'],
    ]],
    ['Hybrid — our approach', 'F2FAF6', C.green, [
      ['Deterministic core', 'rules decide every verdict; results are reproducible'],
      ['AI where it reads', 'summarising intent, drafting refactors'],
      ['Bounded token cost', 'AI sees only what survived the deterministic filter'],
      ['Provable coverage', 'every check records that it ran, hit or miss'],
      ['Human in the loop', 'people approve; agents execute the approved change'],
    ]],
  ]
  cols.forEach(([name, bg, ln, rows], i) => {
    const x = M + i * 4.08
    card(s, x, 1.96, 3.85, 4.62, bg, ln)
    s.addText(name, { x: x + 0.3, y: 2.2, w: 3.3, h: 0.36, fontSize: 15.5,
      bold: true, color: i === 2 ? '1B7A55' : '3A4553', fontFace: H, margin: 0 })
    rows.forEach(([h, d], j) => {
      const y = 2.72 + j * 0.76
      s.addText(h, { x: x + 0.3, y, w: 3.3, h: 0.26, fontSize: 12, bold: true,
        color: '2B3A46', fontFace: B, margin: 0 })
      s.addText(d, { x: x + 0.3, y: y + 0.26, w: 3.3, h: 0.46, fontSize: 10.5,
        color: '6B7686', fontFace: B, lineSpacing: 13, margin: 0 })
    })
  })
  s.addNotes('The argument against a pure agent is not that models are weak. It '
    + 'is that deletion needs an audit trail. If a reviewer asks "how do you '
    + 'know nothing uses this", the answer has to be a list of places searched, '
    + 'not a claim of having looked.')
}

/* =====================================================================
   7 — Why hybrid, stated plainly
   ===================================================================== */
{
  const s = slide()
  title(s, 'Each part does what only it can do', true)

  const rows = [
    [C.teal, 'Deterministic checks', 'Search, count, traverse, compare. Exhaustive and repeatable. Records what it searched AND what it did not find.'],
    [C.teal, 'Rule-based classification', 'Ordered rules, first match wins. No score is ever allowed to cross into a deletion verdict.'],
    [C.violet, 'AI review', 'Reads code and explains intent. Can raise a concern that downgrades a verdict — never promote one.'],
    [C.green, 'Server validation', 'Salesforce itself is asked whether the delete would succeed. The only non-inferred signal in the system.'],
    [C.amber, 'Human approval', 'A person sees the evidence and decides. Nothing is removed without this.'],
    [C.teal, 'Removal agent', 'Executes the approved change: deletes metadata, refactors callers, opens a pull request.'],
  ]
  rows.forEach(([col, h, d], i) => {
    const y = 1.78 + i * 0.87
    disc(s, M, y + 0.08, i + 1, col, 0.4)
    s.addText(h, { x: M + 0.62, y: y - 0.02, w: 3.3, h: 0.32, fontSize: 15,
      bold: true, color: C.text, fontFace: H, margin: 0 })
    s.addText(d, { x: M + 3.98, y: y - 0.02, w: 8.4, h: 0.72, fontSize: 12.5,
      color: C.dim, fontFace: B, lineSpacing: 17, margin: 0 })
  })
  s.addNotes('Read down the left column: the sequence moves from cheap and '
    + 'exhaustive to expensive and judgemental. Anything the deterministic layer '
    + 'can settle never reaches the model, which is what keeps token cost '
    + 'proportional to findings rather than to org size.')
}

/* =====================================================================
   8 — System architecture

   Paired deliberately with the pipeline slide that follows: this one is the
   static structure, that one is the runtime flow. Both are light so they read
   as a single "how it is built" section.
   ===================================================================== */
{
  const s = slide(false)
  title(s, 'System architecture', false,
    'Four layers, one org connection, and a database that is the single source of truth for every run.')

  /* ---- left: the layer stack ---- */
  const layers = [
    ['Browser', C.teal, 'React 19 · TypeScript · Vite',
      'Dashboard  ·  live pipeline console  ·  dependency graph (Cytoscape)  ·  review queue  ·  in-app docs'],
    ['API', C.teal, 'FastAPI, fully async',
      'Runs, components and evidence over REST  ·  SSE stream with Last-Event-ID replay  ·  report download'],
    ['Orchestrator', C.amber, 'Stage runner + event bus',
      'Ten stages, each recording its own state  ·  single writer coroutine keeps the event log gapless  ·  one active run per org'],
    ['Analysis', C.green, 'The pipeline modules',
      'Inventory  ·  retrieve  ·  index  ·  nine collectors  ·  reachability graph  ·  classifier  ·  narrator  ·  report builder'],
    ['Storage', C.violet, 'Postgres 16 · Redis · disk',
      'Nineteen tables including negative evidence and coverage gaps  ·  retrieved metadata cached on disk'],
  ]
  layers.forEach(([name, col, tech, detail], i) => {
    const y = 1.92 + i * 1.0
    card(s, M, y, 8.72, 0.88, i % 2 ? 'F6F8FB' : 'FFFFFF', 'DDE4EC')
    s.addShape(pres.ShapeType.ellipse, { x: M + 0.28, y: y + 0.35, w: 0.19, h: 0.19,
      fill: { color: col } })
    s.addText(name, { x: M + 0.6, y: y + 0.13, w: 1.75, h: 0.3, fontSize: 14.5,
      bold: true, color: C.ink, fontFace: H, margin: 0 })
    s.addText(tech, { x: M + 0.6, y: y + 0.44, w: 2.1, h: 0.28, fontSize: 10.5,
      color: '8A94A4', fontFace: B, margin: 0 })
    s.addText(detail, { x: M + 2.86, y: y + 0.16, w: 5.6, h: 0.58, fontSize: 11,
      color: '5A6472', fontFace: B, lineSpacing: 15, margin: 0 })
    if (i < layers.length - 1) {
      s.addShape(pres.ShapeType.triangle, {
        x: M + 4.42, y: y + 0.92, w: 0.2, h: 0.13,
        fill: { color: 'B0BCCA' }, rotate: 180,
      })
    }
  })

  /* ---- right: what the system talks to ---- */
  s.addText('EXTERNAL', { x: 9.66, y: 1.94, w: 3, h: 0.26, fontSize: 10,
    bold: true, color: '8A94A4', charSpacing: 2, fontFace: H, margin: 0 })

  const ext = [
    ['Salesforce org', C.teal, 2.3, 2.0,
      'REST  ·  Tooling  ·  Metadata\nsf CLI for retrieve and\nvalidate-only deploy\n\nRead once per run, under a\ngoverned API budget'],
    ['LLM gateway', C.violet, 4.36, 1.44,
      'Provider-agnostic.\nSees only what survived the\ndeterministic filter — never\nthe whole org'],
    ['Git & pull request', C.green, 5.9, 1.0,
      'Where the removal agent\nlands its change'],
  ]
  ext.forEach(([name, col, y, h, body]) => {
    card(s, 9.66, y, 3.05, h, 'F6F8FB', 'DDE4EC')
    s.addShape(pres.ShapeType.ellipse, { x: 9.94, y: y + 0.24, w: 0.19, h: 0.19,
      fill: { color: col } })
    s.addText(name, { x: 10.26, y: y + 0.16, w: 2.4, h: 0.3, fontSize: 13,
      bold: true, color: C.ink, fontFace: H, margin: 0 })
    s.addText(body, { x: 9.94, y: y + 0.52, w: 2.6, h: h - 0.6, fontSize: 10,
      color: '6B7686', fontFace: B, lineSpacing: 13, margin: 0 })
  })

  s.addNotes('Two properties are worth calling out. First, the database is the '
    + 'source of truth rather than in-memory state — a browser refresh, a '
    + 'reconnect, or a second viewer all rejoin the same live run. Second, the '
    + 'org is read once into a local workspace, so the expensive analysis '
    + 'stages cost no API calls at all and can be re-run freely.')
}

/* =====================================================================
   9 — Architecture diagram

   High level on purpose: eight boxes and the lines between them. The slide
   before it lists what is inside each layer, so this one only has to answer
   "what talks to what".
   ===================================================================== */
{
  const s = slide()
  title(s, 'How the pieces connect', true,
    'One flow down the left, three systems it reaches out to on the right.')

  const LX = 0.55, LW = 8.0, LMID = LX + LW / 2     // left column
  const RX = 9.55, RW = 3.23                        // right column
  const BUS = 9.15                                  // fan-out spine

  /** A diagram node: accent-bordered rectangle, title, one line of detail. */
  function node(x, y, w, h, accent, name, body) {
    s.addShape(pres.ShapeType.roundRect, {
      x, y, w, h, rectRadius: 0.05,
      fill: { color: C.surface }, line: { color: accent, width: 1.5 },
    })
    s.addText(name, { x: x + 0.28, y: y + 0.13, w: w - 0.56, h: 0.32,
      fontSize: 15, bold: true, color: C.text, fontFace: H, margin: 0 })
    s.addText(body, { x: x + 0.28, y: y + 0.47, w: w - 0.56, h: h - 0.58,
      fontSize: 10.5, color: C.dim, fontFace: B, lineSpacing: 14, margin: 0 })
  }

  function link(x, y, w, h, opts = {}) {
    s.addShape(pres.ShapeType.line, {
      x, y, w, h,
      line: {
        color: opts.color || '6B8095', width: 1.5,
        endArrowType: opts.end === false ? 'none' : 'triangle',
        beginArrowType: opts.begin ? 'triangle' : 'none',
      },
    })
  }

  /* ---------------- the flow down the left ---------------- */
  const stack = [
    [1.92, 0.88, C.teal, 'React SPA',
      'Dashboard, live pipeline console, dependency graph, review queue'],
    [3.02, 0.88, C.teal, 'FastAPI',
      'REST endpoints, plus an SSE stream that survives a page reload'],
    [4.12, 0.88, C.amber, 'Orchestrator',
      'Ten stages, each one recording its own state as it finishes'],
    [5.22, 0.88, C.amber, 'Analysis pipeline',
      'Inventory, retrieve, index, nine collectors, graph, classify, narrate, report'],
    [6.32, 0.75, C.violet, 'Postgres 16  ·  Redis  ·  metadata workspace',
      'Every run, every piece of evidence, and every gap in coverage'],
  ]
  stack.forEach(([y, h, col, name, body]) => node(LX, y, LW, h, col, name, body))

  const hops = [
    [2.80, 'REST  ·  SSE', true],
    [3.90, 'start run', false],
    [5.00, 'run the stages', false],
    [6.10, 'evidence  ·  verdicts', false],
  ]
  hops.forEach(([y, label, both]) => {
    link(LMID, y, 0, 0.22, { begin: both })
    s.addText(label, { x: LMID + 0.22, y: y - 0.02, w: 3.0, h: 0.24,
      fontSize: 8.5, color: C.faint, fontFace: B, margin: 0 })
  })

  /* ---------------- the three systems on the right ---------------- */
  const ext = [
    [1.92, 1.62, C.green, 'Salesforce org',
      'REST, Tooling and Metadata APIs.\n\nRead once per run under a governed '
      + 'API budget, then analysed locally.'],
    [3.79, 1.42, C.violet, 'LLM gateway',
      'Narration only — and only for what already survived the deterministic '
      + 'checks.'],
    [5.46, 1.42, C.green, 'Git  ·  pull request',
      'Where the removal agent lands an approved change for normal code '
      + 'review.'],
  ]
  ext.forEach(([y, h, col, name, body]) => node(RX, y, RW, h, col, name, body))

  // One stub out of the pipeline, up the spine, and into each system.
  link(LX + LW, 5.66, BUS - (LX + LW), 0, { end: false })
  link(BUS, 2.73, 0, 3.44, { end: false })
  ext.forEach(([y, h]) => link(BUS, y + h / 2, RX - BUS, 0))

  // Sits under the right column, clear of the data box and the page number.
  s.addText('every outbound call leaves from the pipeline', {
    x: RX, y: 7.02, w: 2.6, h: 0.24, fontSize: 8.5, italic: true,
    color: C.faint, fontFace: B, margin: 0,
  })

  s.addNotes('Read it top to bottom on the left: a run is started over REST, '
    + 'progress streams back over SSE, and everything the run learns lands in '
    + 'Postgres rather than in memory — which is why a reload rejoins a live '
    + 'run instead of losing it. On the right, note that all three outbound '
    + 'arrows leave from the pipeline box. Nothing else in the system talks to '
    + 'the org, the model, or the repository.')
}

/* =====================================================================
   10 — Pipeline flow
   ===================================================================== */
{
  const s = slide(false)
  title(s, 'The pipeline', false,
    'Each stage records its own state. A failing optional stage degrades the run; it never silently drops evidence.')

  const stages = [
    ['1', 'Connect', 'Discover edition,\nAPI budget, which\nfeatures exist'],
    ['2', 'Inventory', 'Every component\n+ the alias table\nof every name form'],
    ['3', 'Retrieve', 'Pull metadata to\ndisk once — search\nthen costs nothing'],
    ['4', 'Index', 'Split code from\ncomments; resolve\nevery mention'],
    ['5', 'Collect', 'Nine collectors,\neach logging found\nAND not-found'],
  ]
  const stages2 = [
    ['6', 'Graph', 'Reachability from\neverything that\nruns or is seen'],
    ['7', 'Classify', 'Deterministic rules,\nfirst match wins'],
    ['8', 'Rehearse', 'Ask Salesforce if\nthe delete would\nactually succeed'],
    ['9', 'Review', 'AI summary, then\na human decides'],
    ['10', 'Remediate', 'Agent removes,\nrefactors, opens\na pull request'],
  ]
  const draw = (arr, y, accent) => arr.forEach(([n, h, d], i) => {
    const x = M + i * 2.46
    card(s, x, y, 2.28, 2.02, 'F6F8FB', 'DDE4EC')
    disc(s, x + 0.24, y + 0.22, n, accent, 0.36)
    s.addText(h, { x: x + 0.68, y: y + 0.2, w: 1.5, h: 0.3, fontSize: 13.5,
      bold: true, color: C.ink, fontFace: H, margin: 0 })
    s.addText(d, { x: x + 0.24, y: y + 0.62, w: 1.92, h: 1.24, fontSize: 10,
      color: '65707F', fontFace: B, lineSpacing: 13, margin: 0 })
    if (i < arr.length - 1) {
      s.addShape(pres.ShapeType.triangle, {
        x: x + 2.31, y: y + 0.89, w: 0.15, h: 0.22,
        fill: { color: '9AA8B8' }, rotate: 90,
      })
    }
  })
  draw(stages, 1.98, C.teal)
  draw(stages2, 4.32, C.amber)

  s.addText('Stages 4 to 7 make no API calls at all — the org is read once, then '
    + 'analysed locally.',
    { x: M, y: 6.62, w: 11.3, h: 0.36, fontSize: 12.5, italic: true,
      color: '55606E', fontFace: B, margin: 0 })
  s.addNotes('Stages 1 to 3 are the only ones bounded by the org API limit. '
    + 'Everything expensive happens against a local copy, which is what makes '
    + 'the analysis affordable to re-run.')
}

/* =====================================================================
   9 — Multi-level validation overview
   ===================================================================== */
{
  const s = slide()
  title(s, 'How we prove nothing uses a component', true,
    'Nine independent checks. None of them is allowed to decide alone.')

  const layers = [
    ['Static references', 'Token, literal and merge-field search across all retrieved metadata', C.teal],
    ['Dependency API', 'Salesforce\'s own recorded dependency edges', C.teal],
    ['Record data', 'Aggregate counts — does any record hold a value?', C.green],
    ['Runtime execution', 'Async jobs, scheduled jobs, code coverage', C.green],
    ['Config data', 'API names stored as DATA inside custom metadata records', C.amber],
    ['Reachability graph', 'Can anything that runs or is seen actually reach it?', C.teal],
    ['Dynamic-code taint', 'Quarantine anything runtime-built names could touch', C.amber],
    ['Temporal', 'Recently created? Half-built looks exactly like abandoned', C.amber],
    ['Delete rehearsal', 'Ask Salesforce to validate the delete. Server-verified', C.green],
  ]
  layers.forEach(([h, d, col], i) => {
    const x = M + (i % 3) * 4.08
    const y = 1.94 + Math.floor(i / 3) * 1.58
    card(s, x, y, 3.85, 1.4)
    s.addShape(pres.ShapeType.ellipse, { x: x + 0.28, y: y + 0.26, w: 0.22, h: 0.22,
      fill: { color: col } })
    s.addText(h, { x: x + 0.62, y: y + 0.18, w: 3.05, h: 0.32, fontSize: 13.5,
      bold: true, color: C.text, fontFace: H, margin: 0 })
    s.addText(d, { x: x + 0.3, y: y + 0.62, w: 3.4, h: 0.66, fontSize: 10.5,
      color: C.dim, fontFace: B, lineSpacing: 14, margin: 0 })
  })
  s.addText('Each records its result every time — including when it finds nothing. '
    + '"Searched and found nothing" is a different claim from "never checked", '
    + 'and only the first supports a deletion.',
    { x: M, y: 6.68, w: 11.3, h: 0.4, fontSize: 12.5, italic: true,
      color: C.faint, fontFace: B, margin: 0 })
  s.addNotes('The collectors are deliberately blind to each other. Because they '
    + 'do not share state, their agreement carries real information — if eight '
    + 'independent checks all come back empty, that is meaningful.')
}

/* =====================================================================
   10 — Evidence tiers
   ===================================================================== */
{
  const s = slide(false)
  title(s, 'Not all evidence is worth the same', false,
    'Every finding is graded, and the grade determines what it can justify.')

  const tiers = [
    ['A', 'Binding', C.green, 'Deleting it breaks a deploy or a runtime path.',
      'Apex field access · SOQL select · active Flow element · formula reference · report column · config record value'],
    ['B', 'Runtime', C.teal, 'The org actually did something with it.',
      'Records hold a value · field-history writes · async job ran · scheduled job exists · report run recently'],
    ['C', 'Weak', '8A94A4', 'Consistent with use. Nowhere near proof.',
      'Permission-set access · label-only match · inactive consumer · test-only reference · comment mention'],
    ['D', 'Uncertainty', C.amber, 'Not evidence at all. SUPPRESSES a deletion verdict.',
      'Dynamic Apex present · parse failure · truncated data · a required check could not run'],
  ]
  tiers.forEach(([t, name, col, what, eg], i) => {
    const y = 2.0 + i * 1.18
    card(s, M, y, 12.09, 1.02, i === 3 ? 'FDF6EC' : 'F6F8FB', i === 3 ? 'F0D5AC' : 'DDE4EC')
    s.addShape(pres.ShapeType.ellipse, { x: M + 0.28, y: y + 0.24, w: 0.54, h: 0.54,
      fill: { color: col } })
    s.addText(t, { x: M + 0.28, y: y + 0.24, w: 0.54, h: 0.54, fontSize: 19,
      bold: true, color: C.white, align: 'center', valign: 'middle',
      fontFace: H, margin: 0 })
    s.addText(name, { x: M + 1.0, y: y + 0.16, w: 1.5, h: 0.3, fontSize: 14.5,
      bold: true, color: C.ink, fontFace: H, margin: 0 })
    s.addText(what, { x: M + 1.0, y: y + 0.5, w: 3.9, h: 0.36, fontSize: 11.5,
      color: '5A6472', fontFace: B, margin: 0 })
    s.addText(eg, { x: M + 5.1, y: y + 0.22, w: 6.7, h: 0.62, fontSize: 11,
      color: '6B7686', fontFace: B, lineSpacing: 15, margin: 0 })
  })
  s.addText('A single Tier-A hit outranks every "found nothing". Finding use is '
    + 'proof; not finding it is only absence.',
    { x: M, y: 6.82, w: 11.3, h: 0.36, fontSize: 12.5, italic: true,
      color: '55606E', fontFace: B, margin: 0 })
  s.addNotes('Tier D is the important one. An uncertainty flag is not weak '
    + 'evidence of use — it is a statement that we cannot see clearly, and it '
    + 'blocks a deletion verdict outright.')
}

/* =====================================================================
   11 — Classification rules
   ===================================================================== */
{
  const s = slide()
  title(s, 'The rules that decide a verdict', true,
    'Evaluated in order. First match wins. No model participates.')

  const rules = [
    ['R0/R1', 'Managed package, or a standard component', 'OUT OF SCOPE', C.faint],
    ['R2', 'A required check could not run', 'NEEDS REVIEW', C.violet],
    ['R3a', 'Only a layout references it, and no record holds a value', 'UNUSED', C.amber],
    ['R3', 'Something references it in a binding way', 'USED', C.green],
    ['R4', 'No reference, but real data or runtime activity', 'USED', C.green],
    ['R5', 'Externally invocable, or test-only code', 'NEEDS REVIEW', C.violet],
    ['R5b', 'Nothing that runs or is seen can reach it', 'UNUSED', C.amber],
    ['R6', 'An uncertainty flag is set', 'NEEDS REVIEW', C.violet],
    ['R7', 'Only permissions, labels or inactive consumers', 'NEEDS REVIEW', C.violet],
    ['R8', 'Something not-itself-unused still references it', 'NEEDS REVIEW', C.violet],
    ['R9', 'Nothing found anywhere, every check ran cleanly', 'UNUSED', C.amber],
  ]
  rules.forEach(([id, cond, verdict, col], i) => {
    const y = 1.84 + i * 0.44
    s.addText(id, { x: M, y, w: 0.72, h: 0.3, fontSize: 11.5, bold: true,
      color: C.faint, fontFace: 'Courier New', margin: 0 })
    s.addText(cond, { x: M + 0.8, y, w: 8.0, h: 0.3, fontSize: 13,
      color: C.dim, fontFace: B, margin: 0 })
    s.addShape(pres.ShapeType.roundRect, {
      x: 9.9, y: y - 0.02, w: 2.0, h: 0.32, rectRadius: 0.05,
      fill: { color: C.ink }, line: { color: col, width: 1 },
    })
    s.addText(verdict, { x: 9.9, y: y - 0.02, w: 2.0, h: 0.32, fontSize: 10,
      bold: true, color: col, align: 'center', valign: 'middle',
      fontFace: H, margin: 0 })
  })
  s.addText('Confidence is a 0–100 number used only to ORDER the review queue. '
    + 'It never decides a verdict.',
    { x: M, y: 6.78, w: 11.3, h: 0.36, fontSize: 12.5, italic: true,
      color: C.faint, fontFace: B, margin: 0 })
  s.addNotes('Because the rules are ordered and explicit, any verdict can be '
    + 'replayed and explained. That is what makes the output defensible in a '
    + 'change-approval conversation.')
}

/* =====================================================================
   12 — Worked example
   ===================================================================== */
{
  const s = slide(false)
  title(s, 'What a finding actually looks like', false,
    'One field, nine checks, and the reasoning a reviewer can audit.')

  s.addText('Inventory_Item__c.Unit_Cost__c', { x: M, y: 1.92, w: 6.4, h: 0.4,
    fontSize: 19, bold: true, color: C.ink, fontFace: 'Courier New', margin: 0 })

  const ev = [
    ['Static references', 'Only the Inventory Item Layout. No Apex, flow or report.', C.amber],
    ['Record data', '0 of 101 records hold a value.', C.amber],
    ['Dependency API', 'One edge, from the same layout.', C.amber],
    ['Reachability', 'Not reachable from anything that runs or is seen.', C.amber],
    ['Config data', 'Not referenced in any custom metadata record.', C.amber],
    ['Delete rehearsal', 'Salesforce validated the delete. It would succeed.', C.green],
  ]
  ev.forEach(([h, d, col], i) => {
    const y = 2.52 + i * 0.62
    s.addShape(pres.ShapeType.ellipse, { x: M + 0.04, y: y + 0.1, w: 0.18, h: 0.18,
      fill: { color: col } })
    s.addText(h, { x: M + 0.38, y, w: 2.5, h: 0.3, fontSize: 12.5, bold: true,
      color: C.ink, fontFace: B, margin: 0 })
    s.addText(d, { x: M + 2.9, y, w: 4.4, h: 0.5, fontSize: 11.5,
      color: '5A6472', fontFace: B, lineSpacing: 14, margin: 0 })
  })

  card(s, 7.72, 1.9, 5.0, 2.28, '1A2430', C.amber)
  s.addText('UNUSED', { x: 8.06, y: 2.14, w: 2.2, h: 0.4, fontSize: 20, bold: true,
    color: C.amber, fontFace: H, margin: 0 })
  s.addText('confidence 70', { x: 10.2, y: 2.24, w: 2.2, h: 0.3, fontSize: 11.5,
    color: C.faint, align: 'right', fontFace: B, margin: 0 })
  s.addText('Rule R3a — on a layout, but nothing references it functionally and '
    + 'no record holds a value.',
    { x: 8.06, y: 2.66, w: 4.3, h: 0.72, fontSize: 12, color: C.dim,
      fontFace: B, lineSpacing: 16, margin: 0 })
  s.addText('Server-validated by Salesforce', { x: 8.06, y: 3.56, w: 4.3, h: 0.3,
    fontSize: 11.5, bold: true, color: C.green, fontFace: B, margin: 0 })

  card(s, 7.72, 4.42, 5.0, 2.2, 'F7F4FE', 'D9CEF9')
  s.addText('AI summary — not independently verified', { x: 8.06, y: 4.64,
    w: 4.3, h: 0.28, fontSize: 10.5, bold: true, color: '5B44B8',
    fontFace: B, margin: 0 })
  s.addText('Field intended to store the unit cost of an inventory item, likely '
    + 'for cost tracking or margin calculations. No code or automation touches '
    + 'it, so intended use beyond display is unconfirmed.',
    { x: 8.06, y: 4.98, w: 4.3, h: 1.44, fontSize: 11.5, italic: true,
      color: '463A66', fontFace: B, lineSpacing: 16, margin: 0 })
  s.addNotes('Note what the reviewer can see: not just the verdict, but every '
    + 'place we looked and what each returned. The AI text is visually separated '
    + 'and labelled so nobody mistakes narrative for a verified fact.')
}

/* =====================================================================
   13 — Negative evidence
   ===================================================================== */
{
  const s = slide()
  title(s, 'The claim that makes a deletion defensible', true)

  card(s, M, 2.0, 5.9, 1.9, C.surface, C.line)
  s.addText('"We searched here and found nothing"', { x: M + 0.4, y: 2.32,
    w: 5.1, h: 0.6, fontSize: 17, bold: true, color: C.green, fontFace: H,
    margin: 0 })
  s.addText('A recorded, checkable fact. Supports a deletion.',
    { x: M + 0.4, y: 3.06, w: 5.1, h: 0.6, fontSize: 13, color: C.dim,
      fontFace: B, margin: 0 })

  card(s, 6.86, 2.0, 5.85, 1.9, C.surface, C.red)
  s.addText('"We never checked"', { x: 7.26, y: 2.32, w: 5.0, h: 0.6,
    fontSize: 17, bold: true, color: C.red, fontFace: H, margin: 0 })
  s.addText('Absence of evidence. Supports nothing at all.',
    { x: 7.26, y: 3.06, w: 5.0, h: 0.6, fontSize: 13, color: C.dim,
      fontFace: B, margin: 0 })

  s.addText('Most tools cannot tell these apart. Ours records both.',
    { x: M, y: 4.16, w: 12, h: 0.4, fontSize: 16, bold: true, color: C.text,
      fontFace: H, margin: 0 })

  const pts = [
    ['Every collector writes a row every time', 'hit or miss, with the literal query it used'],
    ['A failed check becomes a coverage gap', 'and a gap blocks a deletion verdict outright'],
    ['The report leads with its limitations', 'what the run could NOT prove appears before what it found'],
  ]
  pts.forEach(([h, d], i) => {
    const y = 4.78 + i * 0.66
    s.addShape(pres.ShapeType.ellipse, { x: M + 0.04, y: y + 0.1, w: 0.18, h: 0.18,
      fill: { color: C.teal } })
    s.addText(h, { x: M + 0.4, y, w: 4.7, h: 0.3, fontSize: 13.5, bold: true,
      color: C.text, fontFace: B, margin: 0 })
    s.addText(d, { x: 5.9, y, w: 6.6, h: 0.4, fontSize: 12.5, color: C.dim,
      fontFace: B, margin: 0 })
  })
  s.addNotes('This is what a client audit function cares about. The question is '
    + 'never "did you find it" — it is "how would you know if you had missed it".')
}

/* =====================================================================
   14 — Human in the loop
   ===================================================================== */
{
  const s = slide(false)
  title(s, 'Where the human sits', false,
    'Automation does the exhaustive work. People make the irreversible decision.')

  const flow = [
    ['Machine', C.teal, 'Analyse & classify', 'Nine collectors, deterministic rules, graph reachability. Exhaustive and repeatable.'],
    ['Machine', C.green, 'Server validation', 'Salesforce confirms the delete would succeed. Any refusal names the blocker.'],
    ['AI', C.violet, 'Explain & summarise', 'Plain-language purpose and evidence chain, clearly marked as unverified.'],
    ['HUMAN', C.amber, 'Review & approve', 'Sees every check, including the empty ones. Approves, rejects, or defers each item.'],
    ['Agent', C.teal, 'Execute & raise PR', 'Removes metadata, refactors callers, opens a pull request for normal code review.'],
  ]
  flow.forEach(([who, col, h, d], i) => {
    const y = 1.94 + i * 0.97
    s.addShape(pres.ShapeType.roundRect, {
      x: M, y, w: 1.34, h: 0.62, rectRadius: 0.05,
      fill: { color: who === 'HUMAN' ? col : 'FFFFFF' },
      line: { color: col, width: who === 'HUMAN' ? 0 : 1.4 },
    })
    s.addText(who, { x: M, y, w: 1.34, h: 0.62, fontSize: 10.5, bold: true,
      color: who === 'HUMAN' ? C.ink : '3A4553', align: 'center',
      valign: 'middle', fontFace: H, margin: 0 })
    s.addText(h, { x: M + 1.62, y: y + 0.02, w: 3.3, h: 0.32, fontSize: 14.5,
      bold: true, color: C.ink, fontFace: H, margin: 0 })
    s.addText(d, { x: M + 5.0, y: y + 0.02, w: 7.4, h: 0.6, fontSize: 12,
      color: '5A6472', fontFace: B, lineSpacing: 16, margin: 0 })
  })

  s.addText('Nothing is deleted without an explicit human approval. The agent '
    + 'executes a decision; it never makes one.',
    { x: M, y: 6.86, w: 11.3, h: 0.36, fontSize: 12.5, italic: true,
      color: '55606E', fontFace: B, margin: 0 })
  s.addNotes('Positioning the human after validation and before execution is '
    + 'deliberate: they review a decision that is already evidence-backed and '
    + 'server-checked, so their time goes on judgement rather than verification.')
}

/* =====================================================================
   15 — Remediation agent
   ===================================================================== */
{
  const s = slide()
  title(s, 'From finding to merged change', true,
    'The step most tools stop short of — and where the time is actually saved.')

  const steps = [
    ['Snapshot', 'Retrieve metadata and export affected data to CSV. Committed to git. The rollback artefact.'],
    ['Refactor callers', 'Remove field references from Apex, flows, layouts and reports. The reason a delete usually fails.'],
    ['Generate destructive change', 'Build package.xml and destructiveChanges.xml for the approved set only.'],
    ['Validate again', 'Re-run the check against the current org — it may have changed since analysis.'],
    ['Open a pull request', 'Branch, commit, PR with the evidence trail and rollback plan in the description.'],
    ['Normal code review', 'The team reviews a diff, not a spreadsheet. Merge triggers the existing deploy pipeline.'],
  ]
  steps.forEach(([h, d], i) => {
    const x = M + (i % 2) * 6.24
    const y = 1.94 + Math.floor(i / 2) * 1.6
    card(s, x, y, 5.85, 1.4)
    disc(s, x + 0.28, y + 0.24, i + 1, C.teal, 0.4)
    s.addText(h, { x: x + 0.82, y: y + 0.2, w: 4.7, h: 0.32, fontSize: 14.5,
      bold: true, color: C.text, fontFace: H, margin: 0 })
    s.addText(d, { x: x + 0.3, y: y + 0.64, w: 5.3, h: 0.66, fontSize: 11.5,
      color: C.dim, fontFace: B, lineSpacing: 15, margin: 0 })
  })
  s.addText('The agent works only on components a human approved, and its output '
    + 'is a pull request — reviewable, revertible, and subject to your existing '
    + 'release process.',
    { x: M, y: 6.76, w: 11.3, h: 0.4, fontSize: 12.5, italic: true,
      color: C.faint, fontFace: B, margin: 0 })
  s.addNotes('Refactoring callers is the step that turns a report into a saved '
    + 'week. Removing a field from twelve layouts and four classes by hand is '
    + 'the actual work; finding it was never the bottleneck.')
}

/* =====================================================================
   16 — Risks
   ===================================================================== */
{
  const s = slide(false)
  title(s, 'Risks, and what we do about each', false,
    'The failure modes are known. Every one has a specific control.')

  const risks = [
    ['Runtime-built names', 'A field name built from data we cannot see.',
      'Detect the construct and quarantine every object it could touch. Deliberately blunt.'],
    ['API names stored as data', 'Config records store field names as text.',
      'Dedicated collector queries every custom metadata row and sweeps text values.'],
    ['Partial dependency data', 'Its own dependency data omits reports and rules.',
      'Local parsing is primary. Absence there is never counted as evidence.'],
    ['External integrations', 'Field mappings live outside the org.',
      'Stated as a blind spot; the observation window before deletion is what catches it.'],
    ['Restricted API user', 'Anything invisible to the user looks unused.',
      'The run records who it ran as and warns if that user is not an administrator.'],
    ['Irreversible deletion', 'Fields recoverable 15 days; Apex not at all.',
      'Snapshot, staged deprecation, observation window, small batches, PR-based rollback.'],
  ]
  risks.forEach(([h, r, m], i) => {
    const y = 1.9 + i * 0.82
    card(s, M, y, 12.09, 0.7, i % 2 ? 'F6F8FB' : 'FFFFFF', 'DDE4EC')
    s.addText(h, { x: M + 0.26, y: y + 0.06, w: 2.6, h: 0.28, fontSize: 12.5,
      bold: true, color: C.ink, fontFace: B, margin: 0 })
    s.addText(r, { x: M + 0.26, y: y + 0.33, w: 3.2, h: 0.32, fontSize: 10.5,
      color: '7A8494', fontFace: B, margin: 0 })
    s.addShape(pres.ShapeType.ellipse, { x: M + 3.6, y: y + 0.26, w: 0.18, h: 0.18,
      fill: { color: C.green } })
    s.addText(m, { x: M + 3.94, y: y + 0.16, w: 7.9, h: 0.48, fontSize: 12,
      color: '3A4553', fontFace: B, lineSpacing: 15, margin: 0 })
  })
  s.addNotes('Presenting risks this plainly is a sales advantage, not a '
    + 'liability. The client already knows deletion is dangerous — a vendor who '
    + 'names the dangers is the one they can believe.')
}

/* =====================================================================
   17 — Blind spots
   ===================================================================== */
{
  const s = slide()
  title(s, 'What this cannot know', true,
    'Stated openly, because a cleanup tool that hides its limits is dangerous.')

  const items = [
    'Names assembled at runtime from data the analysis cannot observe',
    'External systems whose field mappings live outside the org entirely',
    'Ad-hoc reports built in the report builder and never saved',
    'Anonymous Apex, which is not persisted and cannot be inspected',
    'Managed-package code reading your fields via dynamic describe',
    'Anything created between the analysis and the eventual deletion',
  ]
  items.forEach((t, i) => {
    const y = 1.94 + i * 0.56
    s.addShape(pres.ShapeType.ellipse, { x: M + 0.04, y: y + 0.11, w: 0.16, h: 0.16,
      fill: { color: C.amber } })
    s.addText(t, { x: M + 0.4, y, w: 11.6, h: 0.42, fontSize: 14,
      color: C.dim, fontFace: B, margin: 0 })
  })

  card(s, M, 5.42, 12.09, 1.42, '1E2A38', C.amber)
  s.addText('So an UNUSED verdict means exactly this:', { x: M + 0.42, y: 5.62,
    w: 6, h: 0.3, fontSize: 12, bold: true, color: C.amber, fontFace: H,
    margin: 0 })
  s.addText('No evidence of use was found, and every decisive check ran cleanly. '
    + 'It is a well-supported recommendation — not an authorisation to delete.',
    { x: M + 0.42, y: 5.96, w: 11.2, h: 0.72, fontSize: 14, color: C.text,
      fontFace: B, lineSpacing: 19, margin: 0 })
  s.addNotes('Every limitation here is structural rather than a defect we intend '
    + 'to fix. The staged deletion procedure exists precisely because these '
    + 'blind spots cannot be closed by better analysis.')
}

/* =====================================================================
   18 — Results
   ===================================================================== */
{
  const s = slide(false)
  title(s, 'What it produces', false,
    'Measured on a live org: 131 in-scope components, roughly 90 API calls per run.')

  const stats = [
    ['25', 'confidently unused', C.amber],
    ['13', 'server-validated by Salesforce', C.green],
    ['19', 'need a human decision', '6E8CF5'],
    ['0', 'confirmed false positives', C.ink],
  ]
  stats.forEach(([n, l, col], i) => {
    const x = M + i * 3.11
    card(s, x, 1.96, 2.88, 1.62, 'F6F8FB', 'DDE4EC')
    s.addText(n, { x: x + 0.28, y: 2.12, w: 2.3, h: 0.76, fontSize: 46,
      bold: true, color: col, fontFace: H, margin: 0 })
    s.addText(l, { x: x + 0.28, y: 2.94, w: 2.4, h: 0.5, fontSize: 11.5,
      color: '5A6472', fontFace: B, lineSpacing: 14, margin: 0 })
  })

  s.addChart(pres.ChartType.bar, [{
    name: 'Review queue',
    labels: ['Initial analysis', 'After cluster logic', 'After validation fixes'],
    values: [38, 31, 19],
  }], {
    x: M, y: 3.86, w: 6.1, h: 2.68,
    barDir: 'col', chartColors: [C.teal],
    showTitle: true, title: 'Items needing human review',
    titleFontSize: 13, titleColor: '3A4553',
    showValue: true, dataLabelPosition: 'outEnd', dataLabelFontSize: 12,
    dataLabelColor: '3A4553',
    catAxisLabelColor: '6B7686', valAxisLabelColor: '6B7686',
    catAxisLabelFontSize: 10, valAxisLabelFontSize: 10,
    valGridLine: { color: 'E4E9F0', size: 1 },
    catGridLine: { style: 'none' }, showLegend: false,
  })

  const deliver = ['Working spreadsheet with a full evidence sheet',
    'Markdown report for Confluence or a pull request',
    'Versioned JSON for scripting and run-over-run diffs',
    'Deployable destructiveChanges.xml for the approved set',
    'Interactive dependency graph and review console']
  s.addText('Deliverables', { x: 7.2, y: 3.9, w: 5, h: 0.32, fontSize: 15,
    bold: true, color: C.ink, fontFace: H, margin: 0 })
  deliver.forEach((t, i) => {
    s.addText(t, { x: 7.2, y: 4.36 + i * 0.44, w: 5.5, h: 0.4, fontSize: 12.5,
      color: '5A6472', bullet: true, fontFace: B, margin: 0 })
  })
  s.addNotes('Zero is not a claim of perfection — it is the count of candidates '
    + 'Salesforce REFUSED to delete. Of 15 rehearsed, 13 validated cleanly and '
    + '2 came back inconclusive and stayed in review. That check is what turns '
    + 'a claim into a measurement.')
}

/* =====================================================================
   19 — Close
   ===================================================================== */
{
  const s = slide()
  s.addShape(pres.ShapeType.ellipse, {
    x: -1.9, y: 3.3, w: 6.6, h: 6.6,
    fill: { color: C.teal, transparency: 90 }, line: { color: C.ink, width: 0 },
  })
  s.addText('Why this approach wins', {
    x: M, y: 1.5, w: 9, h: 0.72, fontSize: 34, bold: true, color: C.text,
    fontFace: H, margin: 0,
  })

  const points = [
    ['Reproducible', 'The same org yields the same answer. Findings can be re-checked, diffed and audited.'],
    ['Defensible', 'Every verdict names the places searched, including the ones that came back empty.'],
    ['Bounded cost', 'Deterministic checks do the exhaustive work; the model sees only what survives them.'],
    ['Actionable', 'Ends in a reviewed pull request, not a spreadsheet somebody files away.'],
    ['Honest', 'Says what it cannot know, and holds back rather than guessing.'],
  ]
  points.forEach(([h, d], i) => {
    const y = 2.62 + i * 0.8
    disc(s, 4.9, y + 0.04, i + 1, C.teal, 0.38)
    s.addText(h, { x: 5.44, y: y - 0.04, w: 2.4, h: 0.32, fontSize: 15,
      bold: true, color: C.text, fontFace: H, margin: 0 })
    s.addText(d, { x: 7.9, y: y - 0.04, w: 4.8, h: 0.64, fontSize: 12,
      color: C.dim, fontFace: B, lineSpacing: 16, margin: 0 })
  })

  s.addText('Deterministic where it must be.\nIntelligent where it helps.\nHuman where it counts.',
    { x: M, y: 3.5, w: 4.1, h: 1.8, fontSize: 17, bold: true, color: C.teal,
      fontFace: H, lineSpacing: 28, margin: 0 })
  s.addNotes('Close on the architectural argument, because that is the real '
    + 'differentiator. Anyone can produce a list of suspicious components. '
    + 'Producing one a client will act on is an engineering problem.')
}

pres.writeFile({ fileName: 'org-cleanup-product.pptx' })
  .then(f => console.log('written:', f))
