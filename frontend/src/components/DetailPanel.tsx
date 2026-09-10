import type { Detail, Evidence } from '../lib/api'
import { CheckFlow } from './CheckFlow'
import {
  collectorName,
  collectorQuestion,
  explainConfidence,
  explainDecision,
  explainEvidence,
  signalSentence,
  unusedFlavorLabel,
} from '../lib/explain'
import { VERDICT_DEFINITION, confidenceBand, verdictClass, verdictLabel } from '../lib/verdict'

/** Human wording for each outcome, so a reviewer need not decode ids. */
const RESULT_LABEL: Record<string, string> = {
  EVIDENCE_OF_USE: 'FOUND',
  NO_EVIDENCE_FOUND: 'NOTHING FOUND',
  INCONCLUSIVE: 'INCONCLUSIVE',
  NOT_APPLICABLE: 'DOES NOT APPLY',
  FAILED: 'COULD NOT RUN',
}

/**
 * Which findings lead.
 *
 * Proof of use first, then a clean negative, then the checks that could not
 * answer. Within proof, a binding reference (tier A) outranks a weak mention
 * (tier C) -- otherwise the panel can open with "the name appears in a comment"
 * while an actual deploy-breaking reference sits three rows down.
 */
function rank(e: Evidence): number {
  const base = { EVIDENCE_OF_USE: 0, NO_EVIDENCE_FOUND: 10, INCONCLUSIVE: 20,
                 FAILED: 21, NOT_APPLICABLE: 30 }[e.result] ?? 25
  const tier = { A: 0, B: 1, C: 2, D: 3 }[e.tier ?? ''] ?? 1
  return base + tier
}

const TONE: Record<string, string> = {
  EVIDENCE_OF_USE: 'pro',
  NO_EVIDENCE_FOUND: 'con',
}

type RefRow = Detail['references'][number]

export function DetailPanel(
  { detail, onReveal, onOpenComponent }: {
    detail: Detail | null
    /** Show this component in the Code Explorer, at its definition. */
    onReveal?: (componentId: number) => void
    /** Open another judged component's detail (e.g. from a reference). */
    onOpenComponent?: (componentId: number) => void
  },
) {
  if (!detail) {
    return <div className="empty">Select a component to see why it was classified.</div>
  }

  const { component: c, classification: cl, evidence, gaps, flags, references, llm } = detail
  const path = evidence.find((e) => e.collector_id === 'C70_reachability')?.payload as
    | { path?: string[]; entry_reason?: string; reachable?: boolean }
    | undefined

  const decision = cl
    ? explainDecision({
        label: cl.label,
        reason_codes: cl.reason_codes,
        rule_trace: cl.rule_trace,
        ctype: String(c.ctype),
      })
    : null

  const openReference = (r: RefRow) => {
    if (r.from_component_id != null) {
      if (onOpenComponent) onOpenComponent(r.from_component_id)
      else if (onReveal) onReveal(r.from_component_id)
    }
  }

  // The headline reasons: at most four, so the section stays scannable. The
  // full list is one disclosure down and nothing is hidden, only demoted.
  const ordered = [...evidence].sort((a, b) => rank(a) - rank(b))
  const lead = ordered.filter(
    (e) => e.result === 'EVIDENCE_OF_USE' || e.result === 'NO_EVIDENCE_FOUND',
  ).slice(0, 4)

  return (
    <>
      <div className="dp-head">
        <code style={{ fontSize: 14 }}>{String(c.api_name)}</code>
        {cl && (
          <span className={`badge ${verdictClass(cl.label)}`}>{verdictLabel(cl.label)}</span>
        )}
        {cl && <span className="has-tip"
                     style={{ color: 'var(--text-faint)', fontSize: 11 }}
                     title={explainConfidence({
                       confidence: cl.confidence,
                       verdict: cl.label,
                       rule: cl.rule_trace?.[0]?.rule,
                       breakdown: cl.score_breakdown,
                     })}>
          {confidenceBand(cl.confidence)} confidence
        </span>}
        {onReveal && typeof c.id === 'number' && (
          <button className="linkish" onClick={() => onReveal(c.id as number)}
                  title="Open the file this is defined in, at its own lines">
            show in code
          </button>
        )}
      </div>
      <div className="sub">
        {String(c.ctype)}
        {c.parent_object ? ` on ${String(c.parent_object)}` : ''}
        {c.last_modified_date ? ` . modified ${String(c.last_modified_date).slice(0, 10)}` : ''}
      </div>

      {cl && decision && (
        <div className={`dp-card v-${String(cl.label).toLowerCase()}`}>
          <div className="dp-card-verdict">{verdictLabel(cl.label)}</div>
          <p className="dp-card-headline">{decision.headline}</p>
          <p className="dp-card-because"><em>Because</em> {decision.because}</p>
          {decision.flavor && (
            <span className="dp-flavor">{unusedFlavorLabel(decision.flavor)}</span>
          )}
          <p className="dp-card-def">{VERDICT_DEFINITION[cl.label]}</p>
        </div>
      )}

      {/* Why a component is out of scope was returned by the API and rendered
          nowhere, so the only way to find out was to ask someone. */}
      {c.out_of_scope_reason ? (
        <div className="dp-verdict" style={{ color: 'var(--text-dim)' }}>
          Reason: {String(c.out_of_scope_reason).replaceAll('_', ' ').toLowerCase()}
        </div>
      ) : null}

      <CheckFlow
        detail={detail}
        onOpenComponent={onOpenComponent}
        onReveal={onReveal}
      />

      {lead.length > 0 && (
        <>
          <h2>Why we think so</h2>
          <ul className="reason-list">
            {lead.map((e) => (
              <li key={e.collector_id} className={`reason ${TONE[e.result] ?? "neutral"}`}>
                <span className="reason-src">{collectorName(e.collector_id)}</span>
                {signalSentence(e.collector_id, e.result, e.payload)}
              </li>
            ))}
            {/* Flags argue neither way -- they only block an unused verdict --
                so they sit with the reasons but read differently. */}
            {flags.slice(0, 3).map((f, i) => (
              <li key={`f${i}`} className="reason flag">
                <span className="reason-src">Uncertainty</span>
                {f.detail || f.code.replaceAll('_', ' ').toLowerCase()}
                {' '}This cannot be called unused while that is true.
              </li>
            ))}
          </ul>
        </>
      )}

      {/* Why it is alive, as a chain rather than a count. */}
      {path?.reachable && path.path?.length ? (
        <>
          <h2>What keeps it alive</h2>
          <div className="ev">
            <div className="mono" style={{ fontSize: 11, lineHeight: 1.7 }}>
              {path.path.map((p, i) => (
                <div key={i} style={{ paddingLeft: i * 12 }}>
                  {i > 0 && <span style={{ color: 'var(--text-faint)' }}>|- </span>}
                  {p}
                </div>
              ))}
            </div>
            {path.entry_reason && (
              <div className="ev-method">Entry point: {path.entry_reason}</div>
            )}
          </div>
        </>
      ) : null}

      {cl?.removal_prerequisites?.length ? (
        <>
          <h2>Before this can be deleted</h2>
          <div className="sub">
            Nothing uses this, but removal is not a single step.
          </div>
          {cl.removal_prerequisites.map((p, i) => (
            <div className="prereq" key={i}>
              <div className="step">
                {i + 1}. {p.step}{' '}
                {p.blocking && <span className="blocking">BLOCKING</span>}
              </div>
              {p.targets?.length > 0 && <div className="tgt">{p.targets.join(', ')}</div>}
              <div className="why">{p.reason}</div>
            </div>
          ))}
        </>
      ) : null}

      {/* AI narration, contained and unmistakably marked.
          Violet is used NOWHERE else in the app, the body is set in the
          proportional UI font while every verified fact is monospace, and the
          header says plainly that it is unverified. A reader who mistakes
          generated prose for a verified finding will act on the wrong thing. */}
      {llm && (
        <div className="ai">
          <div className="ai-head">
            <span className="ai-icon">*</span>
            AI-generated summary - not independently verified
            <span className="ai-model">
              {llm.provider}/{llm.model}
              {llm.status !== 'OK' && ` . ${llm.status}`}
            </span>
          </div>
          {llm.status === 'OK' ? (
            <div className="ai-body">
              {llm.business_purpose && (
                <p><strong>What it appears to do.</strong> {llm.business_purpose}</p>
              )}
              {llm.evidence_recap && (
                <p><strong>Evidence.</strong> {llm.evidence_recap}</p>
              )}
              {llm.risk_note && (
                <p><strong>Argument for keeping it.</strong> {llm.risk_note}</p>
              )}
            </div>
          ) : (
            <div className="ai-body">
              <p>
                No summary generated ({llm.status}). The verdict above is
                unaffected - narration is enrichment, never evidence.
              </p>
            </div>
          )}
          {llm.request && (
            <details className="ai-disclose">
              <summary>Show the exact prompt the model was given</summary>
              <div className="ev-payload">{llm.request.user}</div>
            </details>
          )}
        </div>
      )}

      {references.length > 0 && (
        <>
          <h2>Referenced by ({references.length})</h2>
          <div className="twrap">
            <table>
              <thead>
                <tr><th>Source</th><th>Type</th><th>Strength</th></tr>
              </thead>
              <tbody>
                {references.slice(0, 40).map((r, i) => {
                  const clickable = r.from_component_id != null
                    && (!!onOpenComponent || !!onReveal)
                  return (
                    <tr key={i}
                        className={clickable ? 'ref-row' : undefined}
                        style={{ cursor: clickable ? 'pointer' : 'default' }}
                        title={clickable
                          ? 'Open this reference'
                          : 'No file or component to open'}
                        onClick={() => { if (clickable) openReference(r) }}>
                      <td><code>{r.member_name}</code>{r.is_test && ' (test)'}</td>
                      <td style={{ color: 'var(--text-dim)' }}>{r.metadata_type}</td>
                      <td style={{ color: r.tier === 'A' ? 'var(--used)' : 'var(--text-faint)' }}>
                        {r.tier === 'A' ? 'binding' : r.tier === 'B' ? 'observed' : 'weak'}
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        </>
      )}

      {/* Positive AND negative findings at equal weight, but folded away. A
          verdict of "nothing found" is only trustworthy if you CAN see where we
          looked -- it does not follow that you must look every time. */}
      <details className="disclose">
        <summary className="has-tip" title={explainEvidence({
          hits: evidence.filter((e) => e.result === 'EVIDENCE_OF_USE').length,
          clean: evidence.filter((e) => e.result === 'NO_EVIDENCE_FOUND').length,
          unclear: evidence.filter(
            (e) => e.result === 'INCONCLUSIVE' || e.result === 'NOT_APPLICABLE').length,
          // Here the individual rows are loaded, so the two can be told apart.
          inconclusive: evidence.filter((e) => e.result === 'INCONCLUSIVE').length,
          notApplicable: evidence.filter((e) => e.result === 'NOT_APPLICABLE').length,
          gaps: gaps.length,
          flags: flags.length,
          ctype: String(c.ctype),
        })}>
          Everything we checked ({evidence.length})
        </summary>

        <div className="sub">
          Every check reports each time, including when it finds nothing.
          &ldquo;Searched and found nothing&rdquo; is a different claim from
          &ldquo;never checked&rdquo;.
        </div>
        {ordered.map((e) => (
          <div className="ev" key={e.collector_id}>
            <div className="ev-head">
              <span className="ev-name">{collectorName(e.collector_id)}</span>
              <span className={`ev-result ${e.result}`}>
                {RESULT_LABEL[e.result] ?? e.result}
              </span>
            </div>
            <div className="ev-q">{collectorQuestion(e.collector_id)}</div>
            <div className="ev-a">{signalSentence(e.collector_id, e.result, e.payload)}</div>
            {e.unavailable_reason && (
              <div className="ev-method" style={{ color: 'var(--unused)' }}>
                {e.unavailable_reason}
              </div>
            )}
          </div>
        ))}

        {flags.length > 0 && (
          <>
            <h3>Uncertainty flags</h3>
            <div className="sub">These suppress an unused verdict outright.</div>
            {flags.map((f, i) => (
              <div className="caveat" key={i}>
                <strong>{f.code.replaceAll('_', ' ').toLowerCase()}</strong>
                <div style={{ color: 'var(--text-dim)', marginTop: 3 }}>{f.detail}</div>
              </div>
            ))}
          </>
        )}

        {gaps.length > 0 && (
          <>
            <h3>Checks that could not run</h3>
            <div className="sub">
              Absence of evidence is not evidence of absence.
            </div>
            {gaps.map((g, i) => (
              <div className="caveat" key={i}>
                <strong>{collectorName(g.collector_id)}</strong> - {g.reason}
                {g.detail && <div style={{ color: 'var(--text-dim)' }}>{g.detail}</div>}
              </div>
            ))}
          </>
        )}
      </details>

      {/* The working. Nothing here was removed from the product -- it stopped
          being the first thing a reviewer has to read. */}
      <details className="disclose">
        <summary>Technical detail</summary>

        {cl && (
          <div className="caveat">
            <strong>Confidence score</strong>
            {' '}{Math.round(cl.confidence)} ({confidenceBand(cl.confidence)})
            <div style={{ color: 'var(--text-dim)', marginTop: 3 }}>
              {explainConfidence({
                confidence: cl.confidence,
                verdict: cl.label,
                rule: cl.rule_trace?.[0]?.rule,
                breakdown: cl.score_breakdown,
              })}
            </div>
          </div>
        )}

        {cl?.rule_trace?.length ? (
          <>
            <h3>Which rule decided it</h3>
            {cl.rule_trace.map((r, i) => (
              <div className="caveat" key={i}>
                <strong>{r.rule}</strong> - {r.why}
              </div>
            ))}
          </>
        ) : null}

        {cl?.score_breakdown?.length ? (
          <>
            <h3>How the score was reached</h3>
            <div className="sub">Starts at 50; each check moves it.</div>
            {cl.score_breakdown.map((sb, i) => (
              <div className="caveat" key={i}>
                <strong>{sb.contribution >= 0 ? '+' : '-'}{Math.abs(sb.contribution)}</strong>
                {' '}{collectorName(sb.collector)} - {sb.why}
              </div>
            ))}
          </>
        ) : null}

        {cl?.reason_codes?.length ? (
          <>
            <h3>Reason codes</h3>
            <div className="sub mono">{cl.reason_codes.join(' . ')}</div>
          </>
        ) : null}

        <h3>Raw collector output</h3>
        {ordered.map((e) => (
          <div className="ev" key={e.collector_id}>
            <div className="ev-head">
              <span className="ev-name mono">{e.collector_id}</span>
              <span className={`ev-result ${e.result}`}>{e.result}</span>
              {e.tier && (
                <span style={{ fontSize: 10, color: 'var(--text-faint)' }}>tier {e.tier}</span>
              )}
            </div>
            {e.method && <div className="ev-method">{e.method}</div>}
            <div className="ev-payload">{JSON.stringify(e.payload, null, 1)}</div>
          </div>
        ))}
      </details>
    </>
  )
}
