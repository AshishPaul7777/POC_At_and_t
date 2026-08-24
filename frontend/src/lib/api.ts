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
  flags: { code: string; severity: string; detail: string | null }[]
  references: {
    metadata_type: string
    member_name: string
    tier: string
    match_kind: string
    is_active: boolean
    is_test: boolean
  }[]
  llm: {
    provider: string
    model: string
    business_purpose: string | null
    evidence_recap: string | null
    risk_note: string | null
    status: string
    generated_at: string
    prompt_sha256: string
    request: { system: string; user: string } | null
    input_tokens: number | null
    output_tokens: number | null
  } | null
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
  components: (id: string, p: { verdict?: string; ctype?: string; q?: string }) => {
    const qs = new URLSearchParams()
    if (p.verdict) qs.set('verdict', p.verdict)
    if (p.ctype) qs.set('ctype', p.ctype)
    if (p.q) qs.set('q', p.q)
    return get<ComponentRow[]>(`/api/runs/${id}/components?${qs}`)
  },
  detail: (id: string, cid: number) => get<Detail>(`/api/runs/${id}/components/${cid}`),
  graph: (id: string, maxNodes = 400) =>
    get<GraphPayload>(`/api/runs/${id}/graph?max_nodes=${maxNodes}`),
}
