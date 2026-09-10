export type Verdict = 'USED' | 'UNUSED' | 'NEEDS_REVIEW' | 'OUT_OF_SCOPE'

export interface Run {
  id: string
  org_alias: string
  org_id: string
  state: string
  api_version: string
  run_as_username: string | null
  components: number
  started_at?: string | null
  finished_at?: string | null
  unused?: number | null
  needs_review?: number | null
  duration_s?: number | null
  /** Pipeline options captured at start (e.g. recent_change_days). */
  config?: { recent_change_days?: number; [k: string]: unknown } | null
}

export interface Prerequisite {
  step: string
  reason: string
  targets: string[]
  blocking: boolean
}

export interface ComponentRow {
  id: number
  ctype: string
  api_name: string
  parent_object: string | null
  /** The containing component: a method's class. Null for everything else. */
  parent_id: number | null
  /** Inventory attrs (interfaces, annotations, is_entry_point, …). */
  attrs?: Record<string, unknown> | null
  verdict: Verdict | null
  confidence: number | null
  reason_codes: string[] | null
  removal_prerequisites: Prerequisite[]
  hits: number
  clean: number
  unclear: number
  gaps: number
  flags: number
}

export interface Evidence {
  collector_id: string
  result: string
  tier: string | null
  weight: number
  payload: Record<string, unknown>
  method: string | null
  collector_status: string | null
  artifacts_searched: number | null
  unavailable_reason: string | null
}

export interface Detail {
  component: Record<string, unknown>
  classification: {
    label: Verdict
    confidence: number
    reason_codes: string[]
    rule_trace: { rule: string; why: string }[]
    score_breakdown: { collector: string; contribution: number; why: string }[]
    completeness: Record<string, unknown>
    removal_prerequisites: Prerequisite[]
  } | null
  evidence: Evidence[]
  gaps: { collector_id: string; reason: string; detail: string | null }[]
  flags: {
    code: string
    severity: string
    detail: string | null
    source_ref?: Record<string, unknown> | null
  }[]
  references: {
    metadata_type: string
    member_name: string
    tier: string
    match_kind: string
    is_active: boolean
    is_test: boolean
    artifact_id?: number | null
    file_path?: string | null
    from_component_id?: number | null
  }[]
  llm: {
    provider: string
    model: string
    business_purpose: string | null
    evidence_recap: string | null
    risk_note: string | null
    unused_rationale?: string | null
    status: string
    generated_at: string
    prompt_sha256: string
    request: { system: string; user: string } | null
    response?: Record<string, unknown> | null
    input_tokens: number | null
    output_tokens: number | null
  } | null
  /** Options used when this run was started. */
  run_config?: { recent_change_days?: number | null } | null
}

export interface Summary {
  run: Record<string, unknown> | null
  org: Record<string, unknown> | null
  totals: Partial<Record<Verdict, number>>
  by_type: Record<string, Partial<Record<Verdict, number>>>
  collectors: {
    collector_id: string
    collector_family: string
    status: string
    method: string
    artifacts_searched: number
    hits: number
    unavailable_reason: string | null
  }[]
  coverage_caveats: string[]
}

export interface GraphPayload {
  nodes: { data: Record<string, unknown> }[]
  edges: { data: Record<string, unknown> }[]
  truncated: boolean
  total_nodes: number
  shown_nodes: number
  stats: { entry_points: number; reachable: number; unreachable_components: number }
}

async function get<T>(path: string): Promise<T> {
  const r = await fetch(path)
  if (!r.ok) throw new Error(`${r.status} ${r.statusText} — ${path}`)
  return r.json() as Promise<T>
}

export const api = {
  runs: () => get<Run[]>('/api/runs'),
  summary: (id: string) => get<Summary>(`/api/runs/${id}/summary`),
  components: (id: string, p: {
    verdict?: string
    ctype?: string
    q?: string
    /** Repeated collector:result tags (ANDed). */
    ev?: string[]
  }) => {
    const qs = new URLSearchParams()
    if (p.verdict) qs.set('verdict', p.verdict)
    if (p.ctype) qs.set('ctype', p.ctype)
    if (p.q) qs.set('q', p.q)
    for (const tag of p.ev ?? []) qs.append('ev', tag)
    return get<ComponentRow[]>(`/api/runs/${id}/components?${qs}`)
  },
  detail: (id: string, cid: number) => get<Detail>(`/api/runs/${id}/components/${cid}`),
  graph: (id: string, maxNodes = 400) =>
    get<GraphPayload>(`/api/runs/${id}/graph?max_nodes=${maxNodes}`),
  tree: (id: string) => get<Tree>(`/api/runs/${id}/tree`),
  // A query param, not a path segment: a path parameter containing slashes
  // needs the :path converter, and proxies normalise %2F before it arrives.
  file: (id: string, path: string) =>
    get<FileView>(`/api/runs/${id}/file?path=${encodeURIComponent(path)}`),
  location: (id: string, cid: number) =>
    get<Location>(`/api/runs/${id}/components/${cid}/location`),
}

/** Counts keyed by verdict, plus UNCLASSIFIED for rows the pipeline never judged. */
export type VerdictCounts = Partial<Record<Verdict | 'UNCLASSIFIED', number>>

export interface TreeNode {
  path: string
  name: string
  parent: string | null
  kind: 'file' | 'folder'
  metadata_type: string | null
  artifact_id: number | null
  bytes: number | null
  counts: VerdictCounts
  components: number
  dominant: string | null
  is_active: boolean
  is_test: boolean
  /** Apex class role tags (REST, scheduled, …); null/absent for other files. */
  apex_kinds?: string[] | null
}

export interface Tree {
  run_id: string
  org_alias: string
  workspace: { available: boolean; matches_run: boolean; reason: string | null }
  totals: { files: number; folders: number; unmapped_components: number }
  nodes: TreeNode[]
  /** Components with no file: standard-object fields, mostly. */
  unmapped: { group: string; ctype: string; components: number; counts: VerdictCounts }[]
}

/** A placed reference: one line, one column range, one component. */
export interface FileRef {
  line: number
  col: number
  end_col: number
  text: string
  component_id: number
  api_name: string | null
  ctype: string | null
  verdict: Verdict | null
  confidence: number | null
  reason_codes: string[] | null
  tier: string | null
  match_kind: string
  alias_kind: string
  region: string
  also: number[]
}

export interface FileDefine {
  component_id: number | null
  ctype: string
  api_name: string
  verdict: Verdict | null
  confidence: number | null
  reason_codes: string[] | null
  start_line: number | null
  end_line: number | null
  span_source: string | null
  apex_kinds?: string[] | null
}

export interface FileView {
  path: string
  artifact_id: number
  metadata_type: string
  source: string
  stale: boolean
  stale_reason: string | null
  line_count: number
  truncated: boolean
  spans_complete: boolean
  content: string
  defines: FileDefine[]
  references: FileRef[]
  /** Recorded references we could not place. Shown, never dropped. */
  unlocated: { component_id: number; api_name: string | null; verdict: Verdict | null; why: string }[]
  notes: string[]
}

export interface Location {
  path: string | null
  start_line: number | null
  end_line: number | null
  span_source: string | null
  why: string | null
}
