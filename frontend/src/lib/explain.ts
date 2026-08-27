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

/**
 * Collector ids as a person would name them.
 *
 * Shared with the evidence list rather than duplicated: the tooltip explaining
 * a score and the rows below it were naming the same collectors two different
 * ways, one friendly and one raw. C80 and C90 were missing entirely, so the
 * two newest checks showed as bare ids.
 */
export const COLLECTOR_LABEL: Record<string, string> = {
  C10_static_index: 'Static references',
  C20_data_population: 'Record data',
  C30_dependency_api: 'Salesforce dependency API',
  C40_runtime: 'Runtime execution',
  C50_temporal: 'Recent changes',
  C60_dynamic_apex: 'Dynamic Apex',
  C70_reachability: 'Reachability from entry points',
  C80_config_data: 'API names stored as data',
  C90_delete_rehearsal: 'Delete rehearsal',
}

/** Friendly name, falling back to the id so nothing renders as blank. */
export const collectorName = (id: string) => COLLECTOR_LABEL[id] ?? id

export interface EvidenceCounts {
  hits: number
  clean: number
  unclear: number
  gaps: number
  flags?: number
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
  if (c.unclear) {
    lines.push(`◍ ${n(c.unclear, 'collector')} could not give a trustworthy`
      + ` answer, so it counts as neither`)
  }
  if (c.gaps) {
    lines.push(`✕ ${n(c.gaps, 'check')} could not run at all — while that is`
      + ` true this cannot be called unused`)
  }
  if (c.flags) {
    lines.push(`⚑ ${n(c.flags, 'uncertainty flag')} raised — something about`
      + ` this component resists static analysis`)
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
  R5b: 'no clamp — an unreferenced internal method',
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
