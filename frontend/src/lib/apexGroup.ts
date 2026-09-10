import type { ComponentRow, Verdict } from '../lib/api'

/** Worst-first, so what needs attention sorts to the top. */
export const APEX_SEVERITY: Record<string, number> = {
  UNUSED: 0, NEEDS_REVIEW: 1, USED: 2, OUT_OF_SCOPE: 3,
}

export interface ApexGroup {
  cls: ComponentRow
  methods: ComponentRow[]
}

/** `AccountController.getTop` -> `getTop`. */
export const apexShortName = (api: string) =>
  api.includes('.') ? api.slice(api.indexOf('.') + 1) : api

export function groupApex(rows: ComponentRow[]): {
  groups: ApexGroup[]
  triggers: ComponentRow[]
  orphans: ComponentRow[]
} {
  const classes = rows.filter((r) => r.ctype === 'ApexClass')
  const triggers = rows.filter((r) => r.ctype === 'ApexTrigger')
  const methods = rows.filter((r) => r.ctype === 'ApexMethod')

  const byParent = new Map<number, ComponentRow[]>()
  const classIds = new Set(classes.map((c) => c.id))
  const orphans: ComponentRow[] = []
  for (const m of methods) {
    if (m.parent_id == null || !classIds.has(m.parent_id)) {
      orphans.push(m)
      continue
    }
    const list = byParent.get(m.parent_id)
    if (list) list.push(m)
    else byParent.set(m.parent_id, [m])
  }

  const groups: ApexGroup[] = classes.map((cls) => ({
    cls,
    methods: (byParent.get(cls.id) ?? [])
      .sort((a, b) => (APEX_SEVERITY[a.verdict ?? ''] ?? 9)
        - (APEX_SEVERITY[b.verdict ?? ''] ?? 9)
        || a.api_name.localeCompare(b.api_name)),
  }))

  groups.sort((a, b) =>
    (APEX_SEVERITY[a.cls.verdict ?? ''] ?? 9)
      - (APEX_SEVERITY[b.cls.verdict ?? ''] ?? 9)
    || attentionScore(b) - attentionScore(a)
    || a.cls.api_name.localeCompare(b.cls.api_name))

  return { groups, triggers, orphans }
}

/** Higher = more unused/review methods — for Overview sorting. */
export function attentionScore(g: ApexGroup): number {
  return g.methods.reduce((n, m) => {
    if (m.verdict === 'UNUSED') return n + 2
    if (m.verdict === 'NEEDS_REVIEW') return n + 1
    return n
  }, g.cls.verdict === 'UNUSED' ? 3 : g.cls.verdict === 'NEEDS_REVIEW' ? 1 : 0)
}

export function unusedMethodCount(g: ApexGroup): number {
  return g.methods.filter((m) => m.verdict === 'UNUSED').length
}

export function countByVerdict(rows: ComponentRow[]): Partial<Record<Verdict, number>> {
  const out: Partial<Record<Verdict, number>> = {}
  for (const r of rows) {
    if (!r.verdict) continue
    out[r.verdict] = (out[r.verdict] ?? 0) + 1
  }
  return out
}

/** How the class is exposed — primary role for Overview buckets. */
export type ApexClassKind =
  | 'test'
  | 'rest'
  | 'scheduled'
  | 'batch'
  | 'queueable'
  | 'email'
  | 'invocable'
  | 'aura'
  | 'webservice'
  | 'entry'
  | 'regular'

export const APEX_KIND_ORDER: ApexClassKind[] = [
  'rest', 'scheduled', 'batch', 'queueable', 'invocable', 'aura',
  'email', 'webservice', 'entry', 'test', 'regular',
]

export const APEX_KIND_LABEL: Record<ApexClassKind, string> = {
  rest: 'REST',
  scheduled: 'Scheduled',
  batch: 'Batch',
  queueable: 'Queueable',
  invocable: 'Invocable',
  aura: 'Aura / LWC',
  email: 'Email handler',
  webservice: 'SOAP webservice',
  entry: 'Other entry point',
  test: 'Test',
  regular: 'Regular',
}

export const APEX_KIND_HINT: Record<ApexClassKind, string> = {
  rest: 'Classes with @RestResource or HTTP verb methods',
  scheduled: 'Implements Schedulable',
  batch: 'Implements Batchable',
  queueable: 'Implements Queueable',
  invocable: 'Has @InvocableMethod (Flow / Process)',
  aura: 'Has @AuraEnabled methods',
  email: 'Inbound email handler',
  webservice: 'SOAP webservice methods',
  entry: 'Externally invocable, but not one of the buckets above',
  test: 'Test classes',
  regular: 'Ordinary classes with no external entry surface',
}

/**
 * One primary role per class. Priority prefers the most specific surface
 * (REST / scheduled / …) over a generic entry flag, then regular.
 */
export function apexClassKind(cls: ComponentRow, methods: ComponentRow[] = []): ApexClassKind {
  const attrs = normalizeAttrs(cls.attrs)
  const stampedList = attrs.apex_kinds
  if (Array.isArray(stampedList) && stampedList.length
      && typeof stampedList[0] === 'string'
      && stampedList[0] in APEX_KIND_LABEL) {
    return stampedList[0] as ApexClassKind
  }
  const stamped = attrs.apex_kind
  if (typeof stamped === 'string' && stamped in APEX_KIND_LABEL) {
    return stamped as ApexClassKind
  }
  if (attrs.is_test === true) return 'test'

  const ifaces = lowerList(attrs.interfaces).map(normIface)
  const classAnns = lowerList(attrs.annotations)
  const methodAnns = methods.flatMap((m) => lowerList(normalizeAttrs(m.attrs).annotations))
  const methodMods = methods.flatMap((m) => lowerList(normalizeAttrs(m.attrs).modifiers))
  const anns = new Set([...classAnns, ...methodAnns])

  if (anns.has('restresource')
    || [...anns].some((a) => a.startsWith('http'))) {
    return 'rest'
  }
  if (ifaces.includes('schedulable')) return 'scheduled'
  if (ifaces.includes('batchable')) return 'batch'
  if (ifaces.includes('queueable')) return 'queueable'
  if (ifaces.includes('inboundemailhandler')) return 'email'
  if (anns.has('invocablemethod')) return 'invocable'
  if (anns.has('auraenabled')) return 'aura'
  if (methodMods.includes('webservice') || anns.has('webservice')) return 'webservice'
  if (attrs.is_entry_point === true) return 'entry'
  return 'regular'
}

function normalizeAttrs(v: unknown): Record<string, unknown> {
  if (!v) return {}
  if (typeof v === 'string') {
    try { return JSON.parse(v) as Record<string, unknown> } catch { return {} }
  }
  if (typeof v === 'object') return v as Record<string, unknown>
  return {}
}

function lowerList(v: unknown): string[] {
  if (!Array.isArray(v)) return []
  return v.map((x) => String(x ?? '').toLowerCase()).filter(Boolean)
}

/** `System.Schedulable` / `Database.Batchable<SObject>` → `schedulable` / `batchable`. */
function normIface(raw: string): string {
  const base = raw.toLowerCase().split('<', 1)[0].trim()
  const parts = base.split('.')
  return parts[parts.length - 1] || base
}

export interface ApexKindBucket {
  kind: ApexClassKind
  label: string
  hint: string
  groups: ApexGroup[]
  classCounts: Partial<Record<Verdict, number>>
  methodCounts: Partial<Record<Verdict, number>>
}

/** Overview buckets: one row per class role that has at least one class. */
export function groupApexByKind(rows: ComponentRow[]): ApexKindBucket[] {
  const { groups } = groupApex(rows)
  const byKind = new Map<ApexClassKind, ApexGroup[]>()
  for (const g of groups) {
    const kind = apexClassKind(g.cls, g.methods)
    const list = byKind.get(kind)
    if (list) list.push(g)
    else byKind.set(kind, [g])
  }

  return APEX_KIND_ORDER
    .filter((k) => (byKind.get(k)?.length ?? 0) > 0)
    .map((kind) => {
      const gs = byKind.get(kind) ?? []
      const methods = gs.flatMap((g) => g.methods)
      return {
        kind,
        label: APEX_KIND_LABEL[kind],
        hint: APEX_KIND_HINT[kind],
        groups: gs,
        classCounts: countByVerdict(gs.map((g) => g.cls)),
        methodCounts: countByVerdict(methods),
      }
    })
}
