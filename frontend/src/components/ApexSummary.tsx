import type { ComponentRow, Verdict } from '../lib/api'
import {
  APEX_SEVERITY,
  type ApexClassKind,
  apexClassKind,
  apexShortName,
  groupApex,
} from '../lib/apexGroup'
import { VERDICT_DEFINITION, verdictClass, verdictLabel } from '../lib/verdict'

/**
 * Apex at the granularity the analysis actually works at.
 *
 * The pipeline classifies every method separately from its class -- the 21 Aug
 * run found 12 dead methods against 6 dead classes -- but the flat component
 * table shows both as peers, so "this class is in use, and three of its methods
 * are not" is invisible unless you happen to sort by name and read carefully.
 * Most cleanup tools stop at the class, and this is the part worth showing.
 */

function Badge({ v }: { v: Verdict | null }) {
  return (
    <span className={`badge ${verdictClass(v)}`}
          title={v ? VERDICT_DEFINITION[v] : undefined}>
      {verdictLabel(v)}
    </span>
  )
}

export function ApexSummary(
  { rows, query, selected, kind, onOpen }: {
    rows: ComponentRow[]
    query: string
    selected: number | null
    kind?: ApexClassKind | null
    onOpen: (id: number) => void
  },
) {
  const q = query.trim().toLowerCase()
  let { groups, triggers, orphans } = groupApex(rows)

  if (kind) {
    groups = groups.filter((g) => apexClassKind(g.cls, g.methods) === kind)
    // Kind filter is class-role only; hide triggers/orphans when scoped.
    triggers = []
    orphans = []
  }

  // Search matches the class OR any of its methods, and keeps the class when a
  // method matches -- a method name is meaningless without its class.
  if (q) {
    groups = groups
      .map((g) => g.cls.api_name.toLowerCase().includes(q)
        ? g
        : { ...g, methods: g.methods.filter((m) => m.api_name.toLowerCase().includes(q)) })
      .filter((g) => g.cls.api_name.toLowerCase().includes(q) || g.methods.length > 0)
  }

  const trig = q
    ? triggers.filter((t) => t.api_name.toLowerCase().includes(q))
    : triggers
  trig.sort((a, b) => (APEX_SEVERITY[a.verdict ?? ''] ?? 9) - (APEX_SEVERITY[b.verdict ?? ''] ?? 9)
    || a.api_name.localeCompare(b.api_name))

  const orphanRows = q
    ? orphans.filter((m) => m.api_name.toLowerCase().includes(q))
    : orphans

  if (groups.length === 0 && trig.length === 0 && orphanRows.length === 0) {
    return <div className="empty">No Apex matches this search.</div>
  }

  return (
    <div className="apex-summary">
      {groups.map(({ cls, methods: ms }) => {
        const dead = ms.filter((m) => m.verdict === 'UNUSED').length
        return (
          <div className="apex-card" key={cls.id}>
            <div className={`apex-cls${selected === cls.id ? ' sel' : ''}`}
                 onClick={() => onOpen(cls.id)}>
              <code>{cls.api_name}</code>
              <Badge v={cls.verdict} />
              <span className="apex-count">
                {ms.length === 0
                  ? 'no methods analysed'
                  : dead > 0
                    ? `${dead} of ${ms.length} methods unused`
                    : `${ms.length} method${ms.length === 1 ? '' : 's'}`}
              </span>
            </div>
            {ms.length > 0 && (
              <ul className="apex-methods">
                {ms.map((m) => (
                  <li key={m.id} className={selected === m.id ? 'sel' : undefined}
                      onClick={() => onOpen(m.id)}>
                    <code>{apexShortName(m.api_name)}</code>
                    <Badge v={m.verdict} />
                  </li>
                ))}
              </ul>
            )}
          </div>
        )
      })}

      {trig.length > 0 && (
        <>
          <h2>Triggers</h2>
          {trig.map((t) => (
            <div className="apex-card" key={t.id}>
              <div className={`apex-cls${selected === t.id ? ' sel' : ''}`}
                   onClick={() => onOpen(t.id)}>
                <code>{t.api_name}</code>
                <Badge v={t.verdict} />
                <span className="apex-count">
                  {t.parent_object ? `on ${t.parent_object}` : ''}
                </span>
              </div>
            </div>
          ))}
        </>
      )}

      {orphanRows.length > 0 && (
        <>
          <h2>Methods with no class in this run</h2>
          <div className="sub">
            Their parent was not retrieved, so they are listed on their own
            rather than dropped.
          </div>
          {orphanRows.map((m) => (
            <div className="apex-card" key={m.id}>
              <div className={`apex-cls${selected === m.id ? ' sel' : ''}`}
                   onClick={() => onOpen(m.id)}>
                <code>{m.api_name}</code>
                <Badge v={m.verdict} />
              </div>
            </div>
          ))}
        </>
      )}
    </div>
  )
}
