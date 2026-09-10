/**
 * Per-component explanations for the evidence strip and the confidence score.
 *
 * Built from the component's own numbers rather than being static help text:
 * "5 searched and found nothing, 1 could not answer" tells a reviewer something
 * about the row in front of them, where a legend only tells them what a colour
 * means. The About Me page carries the general explanation; these are the
 * specific one.
 *
 * Both surfaces that show these numbers -- the component list and the detail
 * panel -- read them from here, so the two can never explain the same figure
 * differently.
 */

import type { Verdict } from './api'

/**
 * Collector ids as a person would name them.
 *
 * Shared with the evidence list rather than duplicated: the tooltip explaining
 * a score and the rows below it were naming the same collectors two different
 * ways, one friendly and one raw. C80 and C90 were missing entirely, so the
 * two newest checks showed as bare ids.
 */
export interface SignalMeta {
  /** Familiar label on the check-flow node. */
  name: string
  /** The one question it answers. */
  question: string
  /** What we look at (shown in the check-flow tip). */
  checks: string
  /** Extra positive detail for the tip — what a hit/miss means in practice. */
  detail: string
}

/**
 * Collector names stay familiar; tip copy explains the method in positive terms.
 */
export const SIGNAL: Record<string, SignalMeta> = {
  C10_static_index: {
    name: 'Static references',
    question: 'Does anything in the org name this component?',
    checks: 'We scan active retrieved Apex, LWC, Flows, layouts, and other metadata for the API name and its aliases (inactive Apex/Flows/rules are skipped).',
    detail: 'A hit means an active file or metadata artifact literally names this component.',
  },
  C20_data_population: {
    name: 'Record data',
    question: 'Do any records actually store a value here?',
    checks: 'We run aggregate SOQL (or an existence probe) against live org data for this field or object.',
    detail: 'A hit means records in Salesforce currently hold values for this component.',
  },
  C30_dependency_api: {
    name: "Salesforce's dependency API",
    question: 'Does Salesforce itself report anything depending on this?',
    checks: 'We query MetadataComponentDependency (Tooling API; component-level edges for classes, LWC, Flows, layouts — not Apex methods).',
    detail: 'A hit is a platform-reported dependency. Nothing found means no matching edge in the sampled Dependency API rows.',
  },
  C40_runtime: {
    name: 'Runtime execution',
    question: 'Has this code actually run?',
    checks: 'We look at AsyncApexJob, CronTrigger, coverage, and Event Monitoring (Apex*, LightningInteraction/PageView, RestApi, UniqueQuery, DatabaseSave, ApexRestApi).',
    detail: 'A hit means Salesforce recorded execution, UI use, object/field access, or a callout for this component.',
  },
  C50_temporal: {
    name: 'Recent changes',
    question: 'Was this built or changed too recently to judge?',
    checks: 'We compare CreatedDate and LastModifiedDate to the recency window set for this run (default 90 days; configurable next to Run analysis).',
    detail: 'Raises an informational flag only — it does not force Needs review or block Unused by itself.',
  },
  C60_dynamic_apex: {
    name: 'Dynamic Apex',
    question: 'Does code assemble names at runtime, where we cannot see them?',
    checks: 'We scan for patterns such as Database.query, Type.forName, .get( / .put(, and getGlobalDescribe.',
    detail: 'This check raises an uncertainty flag on the blast radius — it does not record Found / Nothing found evidence.',
  },
  C70_reachability: {
    name: 'Reachable from a starting point',
    question: 'Can anything a user or system triggers lead here?',
    checks: 'We walk the dependency graph from live entry roots (triggers, Flows, UI pages, and similar) to this component.',
    detail: 'A hit means there is an in-org path from something that runs or is seen to this component.',
  },
  C80_config_data: {
    name: 'Names stored as data',
    question: 'Is this name held in configuration rows that code reads?',
    checks: 'We read Custom Metadata and Custom Settings rows and match text values against the alias table.',
    detail: 'A hit means the API name appears as stored configuration data that application code can read.',
  },
  C90_delete_rehearsal: {
    name: 'Delete rehearsal',
    question: 'Will Salesforce accept a delete of this, without doing it?',
    checks: 'We run a validate-only Metadata deploy of a destructive change for deletion candidates.',
    detail: 'A clean rehearsal means the platform’s own checker accepted the proposed delete in dry-run mode.',
  },
}

/** Kept as a name-only map: several call sites want just the label. */
export const COLLECTOR_LABEL: Record<string, string> = Object.fromEntries(
  Object.entries(SIGNAL).map(([id, m]) => [id, m.name]))

/** Friendly name, falling back to the id so nothing renders as blank. */
export const collectorName = (id: string) => SIGNAL[id]?.name ?? id

/** The question, or nothing -- callers must not print a placeholder. */
export const collectorQuestion = (id: string) => SIGNAL[id]?.question ?? ''

/** What the check actually inspects (check-flow tip). */
export const collectorChecks = (id: string, opts?: { recentChangeDays?: number | null }) => {
  if (id === 'C50_temporal' && opts?.recentChangeDays != null) {
    return `We compare CreatedDate and LastModifiedDate to a ${opts.recentChangeDays}-day recency window (set for this run next to Run analysis).`
  }
  return SIGNAL[id]?.checks ?? ''
}

/** Positive extra detail for the check-flow tip. */
export const collectorDetail = (id: string, opts?: { recentChangeDays?: number | null }) => {
  if (id === 'C50_temporal' && opts?.recentChangeDays != null) {
    return `Anything touched in the last ${opts.recentChangeDays} days gets an informational recent-change flag only — it does not force Needs review or block Unused by itself.`
  }
  return SIGNAL[id]?.detail ?? ''
}

export interface EvidenceCounts {
  hits: number
  clean: number
  /** INCONCLUSIVE and NOT_APPLICABLE combined. The component list only has the
   *  sum; the detail panel passes the two apart, below. */
  unclear: number
  gaps: number
  flags?: number
  /** Split out where the caller has the individual rows. */
  inconclusive?: number
  notApplicable?: number
  /** Drives the "why not more" line. Omit it and that line is left off. */
  ctype?: string | null
}

/**
 * Why a component shows fewer circles than there are collectors.
 *
 * The count varies and the UI gave no hint why, which reads as inconsistency
 * rather than as scoping. Three separate reasons, all legitimate:
 *
 *   * Two collectors never draw a circle at all. Recent changes and dynamic
 *     Apex raise uncertainty flags -- Tier D, a reason to distrust an UNUSED
 *     verdict rather than evidence for or against use.
 *   * Two are type-specific. "Does any record hold a value?" is meaningless
 *     for an Apex class; "has this executed?" is meaningless for a field.
 *   * The delete rehearsal costs API calls and touches the org, so it only
 *     runs against components that are already deletion candidates.
 */
function whyNotMore(ctype: string): string[] {
  const out: string[] = []
  const apex = ctype.startsWith('Apex')

  if (apex) out.push('Record data — an Apex component holds no records')
  else out.push('Runtime execution — a field or object does not execute')

  if (ctype === 'ApexMethod') {
    out.push('Delete rehearsal — a method is not deployable on its own, so'
      + ' Salesforce cannot be asked about deleting one')
  }
  return out
}

/** Plural without the "(s)" that makes generated prose look generated. */
const n = (count: number, one: string, many = `${one}s`) =>
  `${count} ${count === 1 ? one : many}`

export function explainEvidence(c: EvidenceCounts): string {
  const total = c.hits + c.clean + c.unclear
  const lines: string[] = [
    `${n(total, 'collector')} ran against this component.`,
    '',
  ]

  if (c.hits) lines.push(`● ${n(c.hits, 'collector')} found a reference to it`)
  if (c.clean) lines.push(`○ ${n(c.clean, 'collector')} searched and found nothing`)
  // NOT_APPLICABLE is not "could not answer" -- it is "this question does not
  // apply here", which for a method's delete rehearsal is the normal case.
  if (c.inconclusive) {
    lines.push(`◍ ${n(c.inconclusive, 'collector')} looked but could not give`
      + ` a trustworthy answer, so it counts as neither`)
  }
  if (c.notApplicable) {
    lines.push(`◍ ${n(c.notApplicable, 'check')} does not apply to this kind of`
      + ` component`)
  }
  if (c.unclear && !c.inconclusive && !c.notApplicable) {
    // The list has only the combined count, so this covers both cases at once
    // and has to agree in number with whichever it turned out to be.
    lines.push(`◍ ${n(c.unclear, 'collector')}`
      + `${c.unclear === 1 ? ' either could not give a usable answer or does'
                           : ' either could not give a usable answer or do'}`
      + ` not apply here`)
  }
  if (c.gaps) {
    lines.push(`✕ ${n(c.gaps, 'check')} could not run at all — while that is`
      + ` true this cannot be called unused`)
  }
  if (c.flags) {
    lines.push(`⚑ ${n(c.flags, 'uncertainty flag')} raised — something about`
      + ` this component resists static analysis`)
  }

  if (c.ctype) {
    lines.push('')
    lines.push('Not every check applies to every component:')
    for (const reason of whyNotMore(c.ctype)) lines.push(`  · ${reason}`)
    lines.push('  · Recent changes and dynamic Apex raise uncertainty flags'
      + ' rather than evidence, so they never appear here')
    if (!c.ctype.startsWith('ApexMethod')) {
      lines.push('  · The delete rehearsal runs only for deletion candidates')
    }
  }

  lines.push('')
  if (c.hits) {
    lines.push('Finding a use is proof. Not finding one is only absence.')
  } else if (c.unclear || c.gaps) {
    // Do not claim a clean sweep when part of the sweep came back unusable --
    // that is the difference between "found nothing" and "could not look".
    lines.push('Nothing was found, but not every check could answer, so this'
      + ' absence is not proof on its own.')
  } else {
    lines.push('Nothing was found — which is only meaningful because every'
      + ' check that applies here completed.')
  }
  return lines.join('\n')
}

/**
 * What the rule that fired does to the score.
 *
 * Mirrors the clamps in backend/app/pipeline/classify.py. If a clamp changes
 * there, this goes stale silently -- the reason the text below names the rule,
 * so a mismatch is at least visible to anyone reading both.
 */
const CLAMP: Record<string, string> = {
  R0: 'fixed at 100 — out of scope, so no scoring runs',
  R1: 'fixed at 100 — out of scope, so no scoring runs',
  R2: 'capped at 55 — a required check did not complete',
  R3: 'floored at 80 — a binding reference was found',
  R3a: 'capped at 70 — on a layout, but nothing uses it',
  R4: 'held between 60 and 75 — runtime or data evidence only',
  R5: 'capped at 60 — needs a human',
  R5b: 'held between 70 and 85 — unreachable from any entry point',
  R6: 'capped at 60 — an uncertainty flag is set',
  R7: 'capped at 65 — the only signals were weak ones',
  R8: 'capped at 60 — still referenced by something not itself unused',
  R9: 'floored at 70 — nothing found anywhere, coverage complete',
}

export interface ConfidenceInput {
  confidence: number
  verdict: string | null
  /** From rule_trace[0].rule. Absent in the component list. */
  rule?: string | null
  /** From score_breakdown. Absent in the component list. */
  breakdown?: { collector: string; contribution: number; why: string }[] | null
}

/**
 * Each verdict as a predicate, so the sentence reads.
 *
 * Deriving it from the enum gave "this component is needs review", which is
 * the sort of thing generated prose says and a person never would.
 */
const PREDICATE: Record<string, string> = {
  USED: 'is in use',
  UNUSED: 'is unused',
  NEEDS_REVIEW: 'needs a human decision',
  OUT_OF_SCOPE: 'is out of scope',
}

export function explainConfidence(c: ConfidenceInput): string {
  const score = Math.round(c.confidence)
  const predicate = (c.verdict && PREDICATE[c.verdict]) ?? 'has this verdict'
  const lines: string[] = [
    `${score} of 100 — how sure we are that this component ${predicate}.`,
    '',
  ]

  if (c.breakdown?.length) {
    const sum = c.breakdown.reduce((a, s) => a + s.contribution, 0)
    lines.push('Starts at 50, then each collector moves it:')
    for (const s of c.breakdown) {
      const sign = s.contribution >= 0 ? '+' : '−'
      lines.push(`  ${sign}${Math.abs(s.contribution)}  `
        + `${collectorName(s.collector)} — ${s.why}`)
    }
    lines.push(`  = 50 ${sum >= 0 ? '+' : '−'} ${Math.abs(sum)} → ${50 + sum}`)
    lines.push('')
  }

  const clamp = c.rule ? CLAMP[c.rule] : undefined
  if (clamp) {
    lines.push(`Rule ${c.rule}: ${clamp}. Result: ${score}.`, '')
  } else if (c.rule) {
    lines.push(`Rule ${c.rule} decided the verdict.`, '')
  } else if (!c.breakdown?.length) {
    // The component list carries the score but not the working. Say where the
    // working is, rather than leaving a gap where it would have been.
    lines.push('Open the component for the collector-by-collector calculation.', '')
  }

  lines.push('This ranks the review queue. It never decides the verdict, it is'
    + ' not a probability, and it is only comparable with components carrying'
    + ' the same verdict.')
  return lines.join('\n')
}

/** How a match was found, in the words a reviewer would use. */
const MATCH_KIND: Record<string, string> = {
  token_sweep: 'The exact API name appears here.',
  string_literal: 'The name appears inside a quoted string -- this is how'
    + ' access built at runtime gets caught at all.',
  merge_field: 'A merge field binds to it here.',
  comment_mention: 'Mentioned in a comment. A reason to look, never proof that'
    + ' anything uses it.',
}

/** What the match is worth, which is a different question from how it was found. */
const TIER_MEANING: Record<string, string> = {
  A: 'Binding: deleting the component would break this file.',
  B: 'Observed: the org actually did this.',
  C: 'Weak: consistent with use, nowhere near proof.',
  D: 'Not evidence -- a reason to distrust an unused verdict.',
}

/**
 * One highlighted range, explained on hover.
 *
 * Lives here rather than in the code view because the detail panel and the code
 * view are explaining the same edge, and this file exists so that two surfaces
 * cannot describe the same figure differently.
 */
export function explainHighlight(r: {
  api_name: string | null
  ctype: string | null
  verdict: string | null
  confidence: number | null
  tier: string | null
  match_kind: string
  region: string
  also?: number[]
}, verdictLine: string): string {
  const lines = [
    `${r.api_name ?? 'unknown'}${r.ctype ? ` (${r.ctype})` : ''}`,
    verdictLine,
    '',
  ]
  if (r.region === 'definition') {
    lines.push('This line is part of the component\'s own definition, not a'
      + ' reference to it.')
  } else {
    lines.push(MATCH_KIND[r.match_kind] ?? r.match_kind)
    const tier = r.tier ? TIER_MEANING[r.tier] : undefined
    if (tier) lines.push(tier)
  }
  if (r.also?.length) {
    lines.push('', `Also matches ${r.also.length} other component`
      + `${r.also.length === 1 ? '' : 's'} with this name -- same field name on`
      + ' a different object.')
  }
  return lines.join('\n')
}

const num = (v: unknown): number | null =>
  typeof v === 'number' && Number.isFinite(v) ? v : null

/** Dependency API rows are `{ by, by_type }` — never coerce with String(). */
function formatDependencyRef(raw: unknown): string {
  if (raw == null) return ''
  if (typeof raw === 'string') return raw
  if (typeof raw !== 'object') return String(raw)
  const o = raw as Record<string, unknown>
  const name = typeof o.by === 'string' ? o.by
    : typeof o.name === 'string' ? o.name
    : typeof o.member_name === 'string' ? o.member_name
    : null
  const type = typeof o.by_type === 'string' ? o.by_type
    : typeof o.type === 'string' ? o.type
    : typeof o.metadata_type === 'string' ? o.metadata_type
    : null
  if (name && type) return `${name} (${type})`
  if (name) return name
  if (type) return type
  return ''
}

/**
 * One collector's finding, in a sentence, using its own numbers.
 *
 * The detail panel used to print `JSON.stringify(payload)` under every
 * collector. That is the most precise thing we could show and close to the
 * least useful: `{"binding_references":4,"sources":4}` requires the reader to
 * already know what a binding reference is. The raw payload is still there, one
 * disclosure down; this is what the panel leads with.
 *
 * Every branch is driven by keys the collectors genuinely write -- see
 * `backend/app/pipeline/collectors.py` and `collectors_data.py`. Where a number
 * is absent the sentence drops it rather than printing a zero we never got.
 */
export function signalSentence(
  collectorId: string,
  result: string,
  payload: Record<string, unknown> | null | undefined,
): string {
  const p = payload ?? {}
  const found = result === 'EVIDENCE_OF_USE'

  // Dependency API misses are NO_EVIDENCE_FOUND ("Nothing found").
  if (result === 'FAILED') return 'This check could not run at all.'
  if (result === 'INCONCLUSIVE') {
    return 'Looked, but could not give a trustworthy answer, so it counts as neither.'
  }
  if (result === 'NOT_APPLICABLE' && collectorId !== 'C90_delete_rehearsal') {
    return 'This check does not apply to this kind of component.'
  }

  switch (collectorId) {
    case 'C10_static_index': {
      const binding = num(p.binding_references)
      const weak = num(p.weak_references)
      const layout = num(p.layout_references)
      if (found && binding) {
        const types = Array.isArray(p.binding_source_types)
          ? (p.binding_source_types as unknown[]).map(String) : []
        const where = types.length ? ` (${types.slice(0, 4).join(', ')})` : ''
        return `Named directly in ${n(binding, 'place')}${where}.`
          + ' Deleting it would break them.'
      }
      if (weak) {
        return `The name appears in ${n(weak, 'place')}, but nothing shown`
          + ' actually uses it -- a test, a permission set, or an inactive'
          + ' automation.'
      }
      if (layout) {
        return `On ${n(layout, 'page layout')}, but nothing reads or writes it.`
          + ' Every field gets a layout when it is created.'
      }
      const searched = num(p.artifacts_searched)
      return searched
        ? `Searched ${n(searched, 'retrieved file')}; the name appears in none of them.`
        : 'The name does not appear anywhere in the retrieved metadata.'
    }

    case 'C20_data_population': {
      const records = num(p.record_count)
      if (records != null) {
        return records > 0
          ? `The object holds ${n(records, 'record')}.`
          : 'The object holds no records at all.'
      }
      if (p.probe === 'existence') {
        return p.has_data
          ? 'At least one record holds a value here.'
          : 'No record holds a value here.'
      }
      const populated = num(p.populated)
      const total = num(p.total)
      const pct = num(p.pct)
      if (populated != null && total != null) {
        return populated > 0
          ? `${populated} of ${n(total, 'record')} hold a value here`
            + `${pct != null ? ` (${pct}%)` : ''}.`
          : `None of the ${n(total, 'record')} checked hold a value here.`
      }
      return found ? 'Records hold a value here.' : 'No record holds a value here.'
    }

    case 'C30_dependency_api': {
      const edgesRaw = num(p.dependency_edges)
      if (found && edgesRaw) {
        const rawList = Array.isArray(p.referenced_by) ? p.referenced_by as unknown[] : []
        const by = [...new Set(rawList.map(formatDependencyRef).filter(Boolean))]
        // Older runs counted the same edge once per lookup key (name + Id +
        // short name), so collapse duplicates for both the list and the count.
        const dupeExtra = Math.max(0, rawList.length - by.length)
        const edges = Math.max(by.length || 1, edgesRaw - dupeExtra)
        return `Salesforce reports ${n(edges, 'dependency', 'dependencies')} on this`
          + `${by.length ? `, including ${by.slice(0, 3).join(', ')}` : ''}.`
      }
      return 'Nothing found in the Dependency API for this component.'
    }

    case 'C40_runtime': {
      const entity = typeof p.entity_name === 'string' && p.entity_name
        ? p.entity_name : null
      const runs = num(p.async_runs) ?? 0
      const cov = num(p.lines_covered) ?? 0
      const elog = (num(p.event_log_executions) ?? 0)
        + (num(p.event_log_triggers) ?? 0)
        + (num(p.event_log_callouts) ?? 0)
        + (num(p.event_log_ui_views) ?? 0)
        + (num(p.event_log_object_accesses) ?? 0)
        + (num(p.event_log_field_refs) ?? 0)
        + (num(p.event_log_rest_hits) ?? 0)
      const who = entity ? `${entity}: ` : ''
      if (found) {
        const bits: string[] = []
        if (runs > 0) bits.push(n(runs, 'recorded background run'))
        if (p.scheduled) bits.push('a scheduled job')
        if (elog > 0) bits.push(n(elog, 'event-log row'))
        return `${who}Has actually executed: ${bits.join(' and ') || 'execution on record'}.`
      }
      return `${who}No background, scheduled, or event-log execution on record`
        + `${cov ? `; ${n(cov, 'line')} covered by tests` : ''}.`
        + ' Ordinary Apex called from a page leaves no trace here without'
        + ' Event Monitoring, so this alone proves little.'
    }

    case 'C70_reachability': {
      const hops = num(p.hops_from_entry_point)
      if (p.reachable) {
        const entry = typeof p.entry_point === 'string' ? p.entry_point : null
        return 'Reachable from something a user or system can trigger'
          + `${entry ? ` (${entry})` : ''}`
          + `${hops != null ? `, ${n(hops, 'step')} away` : ''}.`
      }
      return 'Nothing a user or system can trigger leads here.'
    }

    case 'C80_config_data': {
      const refs = num(p.config_references)
      if (found && refs) {
        return `The API name is stored as data in ${n(refs, 'configuration row')}.`
          + ' Code reads that row at runtime, so this is load-bearing even though'
          + ' no metadata mentions it.'
      }
      const rows = num(p.rows_scanned)
      return rows
        ? `Not named in any of the ${n(rows, 'configuration row')} scanned.`
        : 'Not named in any configuration data.'
    }

    case 'C90_delete_rehearsal': {
      if (p.rehearsal === 'PASSED') {
        return 'Salesforce accepted a validate-only delete, so the platform sees'
          + ' nothing blocking removal. Nothing was actually deleted.'
      }
      if (p.rehearsal === 'BLOCKED') {
        return 'Salesforce refused a validate-only delete -- something still'
          + ' depends on this.'
      }
      if (result === 'NOT_APPLICABLE') {
        return 'A method cannot be deleted on its own, so the platform cannot be'
          + ' asked about it. Its class was rehearsed instead.'
      }
      return 'The rehearsal did not run here -- it only runs for components that'
        + ' already look unused.'
    }

    default:
      return found ? 'Found something.' : 'Found nothing.'
  }
}

// ---------------------------------------------------------------------------
// Verdict decision cards — plain language from reason_codes / rule_trace
// ---------------------------------------------------------------------------

/** Reason codes are precise but written for the database. Translate for UI. */
const REASON_WHY: [RegExp, string][] = [
  [/HAS_USED_METHOD/,
    'This class is in use because at least one of its methods is in use'],
  [/LAYOUT_ONLY_NO_DATA/,
    'Only a layout references it, and no record holds a value'],
  [/UNREACHABLE/,
    'Nothing that runs or is seen can reach it'],
  [/NO_EVIDENCE_FROM_ANY_COLLECTOR/,
    'Every check ran and none found anything'],
  [/REACHABLE_FROM_ENTRY_POINT/,
    'Reachable from something that runs or is seen'],
  [/BINDING_REFERENCE/,
    'Something references it in a way that would break a deploy'],
  [/RUNTIME_OR_DATA_EVIDENCE/,
    'No static reference, but real data or runtime activity exists'],
  [/UNCALLED_ENTRY_POINT/,
    'Externally invocable — its caller may live outside the org'],
  [/EXPOSED_UI_NO_PLACEMENT/,
    'Exposed in Lightning/Experience but no FlexiPage, tab, app, or import was found'],
  [/TEST_ONLY/,
    'Only test code touches it; delete it with what it tests'],
  [/BLOCKED_BY_DEPENDENT/,
    'Something that is not itself unused still references it'],
  [/DYNAMIC_APEX_IN_SCOPE/,
    'Reachable from Apex that builds names at runtime'],
  [/INSUFFICIENT_EVIDENCE/,
    'A required check could not run, so nothing is proven'],
  [/WEAK_SIGNAL/,
    'Only permissions, labels, comments or inactive automation'],
  [/PRESENCE_ONLY_NO_DATA/,
    'Only layout or permission-set presence, and no record data'],
  [/LAYOUT_ONLY_NO_DATA/,
    'Only on page layouts, and no record data'],
  [/RECENTLY_CHANGED/,
    'Changed recently — may be in progress rather than abandoned'],
  [/STALE_OR_TEST_ONLY/,
    'Referenced only by inactive or test consumers'],
  [/MANAGED_PACKAGE/,
    'Belongs to an installed package and cannot be deleted'],
  [/STANDARD_OBJECT|STANDARD/,
    'A standard Salesforce component, not deletable'],
  [/LLM_FLAGGED/,
    'The AI raised a concern worth a human look'],
  [/LLM_CONFIRMS_USE/,
    'The AI confirmed use from evidence already collected'],
  [/PART_OF_ORPHANED_CLUSTER/,
    'Part of a closed group of unused items that reference each other'],
]

export function explainReasonCode(code: string): string {
  const hit = REASON_WHY.find(([re]) => re.test(code))
  if (hit) return hit[1]
  return code.split(' (')[0].replaceAll('_', ' ').toLowerCase()
}

export function explainReasons(codes: string[] | null | undefined): string[] {
  const out: string[] = []
  for (const r of codes ?? []) {
    const text = explainReasonCode(r)
    if (!out.includes(text)) out.push(text)
  }
  return out
}

export type UnusedFlavor = 'unreachable' | 'no_evidence' | 'layout_only' | 'generic'

export function unusedFlavorLabel(flavor: UnusedFlavor): string {
  switch (flavor) {
    case 'unreachable': return 'Nothing can reach this'
    case 'no_evidence': return 'Checks found nothing'
    case 'layout_only': return 'Only on a layout'
    default: return 'Unused'
  }
}

export interface DecisionInput {
  label: Verdict | string | null | undefined
  reason_codes?: string[] | null
  rule_trace?: { rule: string; why?: string }[] | null
  ctype?: string | null
}

export interface DecisionCard {
  headline: string
  because: string
  flavor: UnusedFlavor | null
  rule: string | null
}

export function explainDecision(input: DecisionInput): DecisionCard {
  const label = input.label ?? null
  const codes = input.reason_codes ?? []
  const primary = codes[0] ?? input.rule_trace?.[0]?.why ?? ''
  const rule = input.rule_trace?.find((t) => t.rule)?.rule
    ?? input.rule_trace?.[0]?.rule
    ?? null
  const because = primary
    ? explainReasonCode(primary)
    : 'No detailed reason was recorded for this verdict.'

  let flavor: UnusedFlavor | null = null
  if (label === 'UNUSED') {
    if (/LAYOUT_ONLY/.test(primary)) flavor = 'layout_only'
    else if (/UNREACHABLE/.test(primary)) flavor = 'unreachable'
    else if (/NO_EVIDENCE/.test(primary)) flavor = 'no_evidence'
    else flavor = 'generic'
  }

  const headlines: Record<string, string> = {
    USED: 'Leave this alone — something still depends on it.',
    UNUSED: 'A deletion candidate — nothing clear depends on it.',
    NEEDS_REVIEW: 'A person needs to decide before you remove this.',
    OUT_OF_SCOPE: 'Not something this tool can delete.',
  }

  return {
    headline: (label && headlines[label]) || 'Not classified yet.',
    because,
    flavor,
    rule,
  }
}

/** Synthetic check-flow step (not a collector): Apex external entry surface. */
export const EXTERNAL_ENTRY_STEP_ID = 'X10_external_entry'

/** Synthetic check-flow step: AI narration / optional verdict nudge. */
export const AI_NARRATION_STEP_ID = 'S40_narration'

export function externalEntryStepMeta(ctype: string): {
  name: string
  question: string
  checks: string
  detail: string
} | null {
  if (ctype !== 'ApexClass' && ctype !== 'ApexMethod') return null
  return {
    name: 'Externally invocable',
    question: 'Can something outside ordinary Apex call sites invoke this?',
    checks: 'We read inventory signals such as @RestResource / HTTP verbs, @AuraEnabled, '
      + '@InvocableMethod, Schedulable / Batchable / Queueable, and webservice.',
    detail: 'A yes means this is an external API, UI, Flow, or platform-job surface. '
      + 'Without Event Monitoring we still cannot see whether traffic has hit it.',
  }
}

export function aiNarrationStepMeta(): {
  name: string
  question: string
  checks: string
  detail: string
} {
  return {
    name: 'AI review',
    question: 'Does a second look at the same evidence change the provisional verdict?',
    checks: 'We send UNUSED and Needs review rows to the LLM with collector results, '
      + 'flags, and references. It explains the component and may nudge the verdict.',
    detail: 'UNUSED may move to Needs review if the model flags a concrete concern. '
      + 'Needs review may move to Used only when listed evidence already shows use. '
      + 'The model never invents Unused.',
  }
}

/**
 * How the AI step should render for this component, or null if narration
 * never ran for it.
 */
export function explainAiNarration(input: {
  llm: {
    status: string
    business_purpose?: string | null
    evidence_recap?: string | null
    risk_note?: string | null
    unused_rationale?: string | null
    response?: Record<string, unknown> | null
    model?: string
    provider?: string
  } | null | undefined
  flags: { code: string; detail: string | null; source_ref?: Record<string, unknown> | null }[]
  reason_codes?: string[] | null
}): {
  state: 'hit' | 'flagged' | 'clear' | 'incomplete'
  outcome: string
  because?: string
  findings: { key: string; label: string; meta?: string }[]
} | null {
  const llm = input.llm
  if (!llm) return null

  const concern = input.flags.find((f) => f.code === 'LLM_FLAGGED_CONCERN')
  const confirms = input.flags.find((f) => f.code === 'LLM_CONFIRMS_USE')
  const resp = (llm.response ?? {}) as Record<string, unknown>
  const findings: { key: string; label: string; meta?: string }[] = []

  if (llm.business_purpose) {
    findings.push({ key: 'ai-purpose', label: llm.business_purpose, meta: 'purpose' })
  }
  if (llm.evidence_recap) {
    findings.push({ key: 'ai-ev', label: llm.evidence_recap, meta: 'evidence' })
  }
  if (llm.risk_note && llm.risk_note.toLowerCase() !== 'none identified') {
    findings.push({ key: 'ai-risk', label: llm.risk_note, meta: 'keep argument' })
  }

  if (llm.status !== 'OK') {
    return {
      state: 'incomplete',
      outcome: `AI narration ${llm.status.toLowerCase()}`
        + (llm.model ? ` (${llm.model})` : '')
        + ' — provisional rule verdict unchanged.',
      findings,
    }
  }

  if (confirms) {
    const why = String(
      confirms.source_ref?.why
      || resp.revise_to_used_why
      || confirms.detail
      || 'Listed evidence already showed use.',
    )
    findings.push({ key: 'ai-decide-used', label: why, meta: 'revised → Used' })
    return {
      state: 'hit',
      outcome: 'AI confirmed Used from already-listed evidence.',
      because: why,
      findings,
    }
  }

  if (concern) {
    const why = String(
      concern.source_ref?.why
      || resp.still_might_matter_why
      || llm.unused_rationale
      || concern.detail
      || 'Concrete concern raised.',
    )
    findings.push({ key: 'ai-decide-review', label: why, meta: 'revised → Needs review' })
    return {
      state: 'flagged',
      outcome: 'AI raised a concern — Unused was held at Needs review.',
      because: why,
      findings,
    }
  }

  const still = resp.still_might_matter === true
  const revise = resp.revise_to_used === true
  if (still && resp.still_might_matter_why) {
    findings.push({
      key: 'ai-why-still',
      label: String(resp.still_might_matter_why),
      meta: 'concern (not applied)',
    })
  }
  if (revise && resp.revise_to_used_why) {
    findings.push({
      key: 'ai-why-used',
      label: String(resp.revise_to_used_why),
      meta: 'use signal (not applied)',
    })
  }

  return {
    state: 'clear',
    outcome: 'AI explained the provisional verdict and did not change it.',
    findings,
  }
}

/**
 * Plain description of why a class/method is treated as an external surface.
 * Used by the check-flow diagram so empty collectors do not look like a
 * contradiction with NEEDS_REVIEW / USED on entry points.
 */
export function explainExternalEntry(input: {
  attrs?: Record<string, unknown> | null
  reason_codes?: string[] | null
  ctype: string
}): {
  isEntry: boolean
  surfaceLabel: string
  outcome: string
  firedR5: boolean
} | null {
  const meta = externalEntryStepMeta(input.ctype)
  if (!meta) return null

  const attrs = input.attrs ?? {}
  const isEntry = attrs.is_entry_point === true
  const firedR5 = (input.reason_codes ?? []).some((c) => /UNCALLED_ENTRY_POINT/.test(c))
  const kinds = Array.isArray(attrs.apex_kinds)
    ? (attrs.apex_kinds as string[]).filter((k) => k && k !== 'regular')
    : attrs.apex_kind && attrs.apex_kind !== 'regular'
      ? [String(attrs.apex_kind)]
      : []
  const anns = Array.isArray(attrs.annotations)
    ? (attrs.annotations as string[]).filter(Boolean)
    : []
  const ifaces = Array.isArray(attrs.interfaces)
    ? (attrs.interfaces as string[]).filter(Boolean)
    : []

  const KIND_UI: Record<string, string> = {
    rest: 'REST', scheduled: 'Scheduled', batch: 'Batch', queueable: 'Queueable',
    invocable: 'Invocable', aura: 'Aura / LWC', email: 'Email handler',
    webservice: 'SOAP webservice', entry: 'other entry point', test: 'Test',
  }
  const bits: string[] = []
  if (kinds.length) {
    bits.push(kinds.map((k) => KIND_UI[k] ?? k).join(', '))
  } else {
    if (anns.length) bits.push(anns.slice(0, 4).join(', '))
    if (ifaces.length) bits.push(ifaces.slice(0, 3).join(', '))
  }
  const surfaceLabel = bits.join(' · ') || 'external API / framework entry'

  if (!isEntry && !firedR5) {
    return {
      isEntry: false,
      surfaceLabel: '',
      firedR5: false,
      outcome: 'Ordinary Apex only — no REST/HTTP, Aura, Invocable, Schedulable/Batchable, or webservice surface recorded.',
    }
  }

  return {
    isEntry: true,
    surfaceLabel,
    firedR5,
    outcome: `Yes — exposed as ${surfaceLabel}. Callers may live outside the org `
      + '(integrations, Lightning, Flow, the scheduler, SOAP/REST clients). '
      + 'Event Monitoring would be needed to see whether traffic has hit this surface.',
  }
}

/** Collectors that raise uncertainty flags instead of evidence rows. */
export const FLAG_ONLY_COLLECTORS: Record<string, string[]> = {
  C50_temporal: ['RECENTLY_CHANGED'],
  C60_dynamic_apex: ['DYNAMIC_APEX_IN_SCOPE'],
}

export function isFlagOnlyCollector(collectorId: string): boolean {
  return collectorId in FLAG_ONLY_COLLECTORS
}

export function flagCodesForCollector(collectorId: string): string[] {
  return FLAG_ONLY_COLLECTORS[collectorId] ?? []
}

/** Collectors that apply as diagram steps for a given component type. */
export function collectorsForType(ctype: string): string[] {
  const all = Object.keys(SIGNAL)
  const apex = ctype.startsWith('Apex')
  const ui = ctype === 'LightningComponentBundle' || ctype === 'AuraDefinitionBundle'
  const schema = ctype === 'CustomField' || ctype === 'CustomObject'
  return all.filter((id) => {
    if (id === 'C20_data_population' && (apex || ui)) return false
    if (id === 'C40_runtime' && !(apex || ui || schema)) return false
    if (id === 'C90_delete_rehearsal' && ctype === 'ApexMethod') return false
    return true
  })
}

/** Plain reason a check does not apply to this type (when no evidence row). */
export function notApplicableReason(collectorId: string, ctype: string): string {
  const ui = ctype === 'LightningComponentBundle' || ctype === 'AuraDefinitionBundle'
  if (collectorId === 'C20_data_population' && ctype.startsWith('Apex')) {
    return 'Record data does not apply to Apex — code holds no field values.'
  }
  if (collectorId === 'C20_data_population' && ui) {
    return 'Record data does not apply to UI components — they hold no field values.'
  }
  if (collectorId === 'C40_runtime' && ui) {
    return ''  // C40 runs for LWC/Aura when Event Monitoring is available
  }
  if (collectorId === 'C40_runtime' && (ctype === 'CustomField' || ctype === 'CustomObject')) {
    return ''  // C40 attaches optional EM object/field hits
  }
  if (collectorId === 'C40_runtime' && !ctype.startsWith('Apex')
      && ctype !== 'CustomField' && ctype !== 'CustomObject'
      && !ui) {
    return 'Runtime execution does not apply to this component type.'
  }
  if (collectorId === 'C90_delete_rehearsal' && ctype === 'ApexMethod') {
    return 'A method cannot be deleted on its own, so this check does not run.'
  }
  if (collectorId === 'C90_delete_rehearsal') {
    return 'Delete rehearsal only runs for components that already look unused.'
  }
  return 'This check was not run for this component.'
}

/** Outcome copy for flag-only collectors (Recent changes, Dynamic Apex). */
export function flagCheckOutcome(
  collectorId: string,
  flags: {
    code: string
    detail: string | null
    source_ref?: Record<string, unknown> | null
  }[],
  opts?: { recentChangeDays?: number | null },
): { flagged: boolean; outcome: string } {
  const codes = new Set(flagCodesForCollector(collectorId))
  const hit = flags.find((f) => codes.has(f.code))
  const windowDays = opts?.recentChangeDays
    ?? (hit?.source_ref?.window_days as number | undefined)
  if (hit) {
    if (collectorId === 'C50_temporal') {
      const changed = hit.source_ref?.last_changed
        ? `Last changed ${String(hit.source_ref.last_changed)}.`
        : (hit.detail || 'Changed inside the recency window.')
      const win = windowDays != null
        ? ` Inside the ${windowDays}-day recency window.`
        : ''
      return {
        flagged: true,
        outcome: `${changed}${win} Informational only — does not force Needs review by itself.`,
      }
    }
    return {
      flagged: true,
      outcome: hit.detail
        || 'An uncertainty flag was raised — dynamic Apex could reach this component.',
    }
  }
  if (collectorId === 'C60_dynamic_apex') {
    return {
      flagged: false,
      outcome: 'No dynamic-Apex uncertainty flag on this component — nothing '
        + 'in the blast radius of classes that build names at runtime.',
    }
  }
  if (collectorId === 'C50_temporal') {
    const win = windowDays != null ? `${windowDays}-day ` : ''
    return {
      flagged: false,
      outcome: `No recent-change flag — outside the ${win}recency window.`,
    }
  }
  return { flagged: false, outcome: 'No uncertainty flag from this check.' }
}
