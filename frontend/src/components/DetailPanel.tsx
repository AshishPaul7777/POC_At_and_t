import type { Detail } from '../lib/api'

/** Human wording for each collector, so a reviewer need not decode ids. */
const COLLECTOR_LABEL: Record<string, string> = {
  C10_static_index: 'Static references',
  C20_data_population: 'Record data',
  C30_dependency_api: 'Salesforce dependency API',
  C40_runtime: 'Runtime execution',
  C50_temporal: 'Recent changes',
  C60_dynamic_apex: 'Dynamic Apex',
  C70_reachability: 'Reachability from entry points',
}

const RESULT_LABEL: Record<string, string> = {
  EVIDENCE_OF_USE: 'FOUND',
  NO_EVIDENCE_FOUND: 'NOTHING FOUND',
  INCONCLUSIVE: 'INCONCLUSIVE',
  NOT_APPLICABLE: 'UNAVAILABLE',
  FAILED: 'FAILED',
}

export function DetailPanel({ detail }: { detail: Detail | null }) {
  if (!detail) {
    return <div className="empty">Select a component to see its full evidence trail.</div>
  }

  const { component: c, classification: cl, evidence, gaps, flags, references, llm } = detail
  const path = evidence.find((e) => e.collector_id === 'C70_reachability')?.payload as
    | { path?: string[]; entry_reason?: string; reachable?: boolean }
    | undefined

  return (
    <>
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 4 }}>
        <code style={{ fontSize: 14 }}>{String(c.api_name)}</code>
        {cl && <span className={`badge ${cl.label}`}>{cl.label.replaceAll('_', ' ')}</span>}
        {cl && <span style={{ color: 'var(--text-faint)', fontSize: 11 }}>
          confidence {Math.round(cl.confidence)}
        </span>}
      </div>
      <div className="sub">
        {String(c.ctype)}
        {c.parent_object ? ` on ${String(c.parent_object)}` : ''}
        {c.last_modified_date ? ` . modified ${String(c.last_modified_date).slice(0, 10)}` : ''}
      </div>

      {cl?.reason_codes?.length ? (
        <div className="sub" style={{ color: 'var(--text)' }}>
          {cl.reason_codes.join(' . ')}
        </div>
      ) : null}

      {/* Why it is alive, as a chain rather than a count. */}
      {path?.reachable && path.path?.length ? (
        <>
          <h2>Why this is used</h2>
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

      {/* Positive AND negative evidence at equal visual weight. A verdict of
          "nothing found" is only trustworthy if you can see where we looked. */}
      <h2>Evidence ({evidence.length} collectors)</h2>
      <div className="sub">
        Every collector reports each time, including when it finds nothing.
        &ldquo;Searched and found nothing&rdquo; is a different claim from
        &ldquo;never checked&rdquo;.
      </div>
      {evidence.map((e) => (
        <div className="ev" key={e.collector_id}>
          <div className="ev-head">
            <span className="ev-name">{COLLECTOR_LABEL[e.collector_id] ?? e.collector_id}</span>
            <span className={`ev-result ${e.result}`}>
              {RESULT_LABEL[e.result] ?? e.result}
            </span>
            {e.tier && (
              <span style={{ fontSize: 10, color: 'var(--text-faint)' }}>tier {e.tier}</span>
            )}
          </div>
          {e.method && <div className="ev-method">{e.method}</div>}
          {e.unavailable_reason && (
            <div className="ev-method" style={{ color: 'var(--unused)' }}>
              {e.unavailable_reason}
            </div>
          )}
          <div className="ev-payload">{JSON.stringify(e.payload, null, 1)}</div>
        </div>
      ))}

      {flags.length > 0 && (
        <>
          <h2>Uncertainty flags</h2>
          <div className="sub">These suppress an UNUSED verdict outright.</div>
          {flags.map((f, i) => (
            <div className="caveat" key={i}>
              <strong>{f.code}</strong>
              <div style={{ color: 'var(--text-dim)', marginTop: 3 }}>{f.detail}</div>
            </div>
          ))}
        </>
      )}

      {gaps.length > 0 && (
        <>
          <h2>Coverage gaps</h2>
          <div className="sub">
            A collector that could not run. Absence of evidence is not evidence of absence.
          </div>
          {gaps.map((g, i) => (
            <div className="caveat" key={i}>
              <strong>{g.collector_id}</strong> - {g.reason}
              {g.detail && <div style={{ color: 'var(--text-dim)' }}>{g.detail}</div>}
            </div>
          ))}
        </>
      )}

      {references.length > 0 && (
        <>
          <h2>Referenced by ({references.length})</h2>
          <div className="twrap">
            <table>
              <thead>
                <tr><th>Source</th><th>Type</th><th>Tier</th><th>Match</th></tr>
              </thead>
              <tbody>
                {references.slice(0, 40).map((r, i) => (
                  <tr key={i} style={{ cursor: 'default' }}>
                    <td><code>{r.member_name}</code>{r.is_test && ' (test)'}</td>
                    <td style={{ color: 'var(--text-dim)' }}>{r.metadata_type}</td>
                    <td style={{ color: r.tier === 'A' ? 'var(--used)' : 'var(--text-faint)' }}>
                      {r.tier}
                    </td>
                    <td style={{ color: 'var(--text-faint)' }}>{r.match_kind}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
    </>
  )
}
