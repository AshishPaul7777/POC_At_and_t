import { useEffect, useMemo, useState } from 'react'
import type { ComponentRow } from '../lib/api'
import {
  bindExamples,
  exampleVerdictLabel,
  sectionBounds,
  type BoundExample,
} from '../lib/examples'
import { VERDICT_DEFINITION } from '../lib/verdict'

export function Examples({
  rows,
  onOpenComponent,
}: {
  rows: ComponentRow[]
  onOpenComponent: (row: ComponentRow) => void
}) {
  const bound = useMemo(() => bindExamples(rows), [rows])
  const groups = useMemo(() => sectionBounds(bound), [bound])
  const liveScenarioCount = bound.filter((b) => b.components.length > 0).length
  const liveComponentCount = bound.reduce((n, b) => n + b.components.length, 0)

  const [openSections, setOpenSections] = useState<Record<string, boolean>>({})
  const [openCards, setOpenCards] = useState<Record<string, boolean>>({})

  useEffect(() => {
    setOpenSections((prev) => {
      const next = { ...prev }
      for (const g of groups) {
        if (next[g.section.id] === undefined) next[g.section.id] = true
      }
      return next
    })
  }, [groups])

  const toggleSection = (id: string) => {
    setOpenSections((s) => ({ ...s, [id]: !s[id] }))
  }
  const toggleCard = (id: string) => {
    setOpenCards((s) => ({ ...s, [id]: !s[id] }))
  }

  return (
    <div className="examples-page pad">
      <header className="examples-hero">
        <h2>Classification examples</h2>
        <p>
          Real scenarios from this run — how different evidence combinations
          turn into Used, Unused, or Needs review. Each card can list several
          matching components; open any one to inspect the full check flow.
        </p>
        <p className="examples-meta">
          {liveScenarioCount} of {bound.length} scenarios have live matches
          · {liveComponentCount} example component
          {liveComponentCount === 1 ? '' : 's'}
          {rows.length ? ` · ${rows.length} in run` : ''}
        </p>
      </header>

      {!rows.length && (
        <div className="empty">Run an analysis first — examples are drawn from the current run.</div>
      )}

      {groups.map(({ section, items }) => {
        const open = openSections[section.id] !== false
        const live = items.reduce((n, i) => n + i.components.length, 0)
        return (
          <section key={section.id} className="ex-section">
            <button
              type="button"
              className="ex-section-h"
              aria-expanded={open}
              onClick={() => toggleSection(section.id)}
            >
              <span className="ex-chevron" data-open={open} aria-hidden />
              <span className="ex-section-titles">
                <strong>{section.title}</strong>
                <span className="ex-section-blurb">{section.blurb}</span>
              </span>
              <span className="ex-section-count">
                {live} example{live === 1 ? '' : 's'}
              </span>
            </button>

            {open && (
              <div className="ex-cards">
                {items.map((item) => (
                  <ExampleCard
                    key={item.scenario.id}
                    item={item}
                    expanded={!!openCards[item.scenario.id]}
                    onToggle={() => toggleCard(item.scenario.id)}
                    onOpen={onOpenComponent}
                  />
                ))}
              </div>
            )}
          </section>
        )
      })}
    </div>
  )
}

function ExampleCard({
  item,
  expanded,
  onToggle,
  onOpen,
}: {
  item: BoundExample
  expanded: boolean
  onToggle: () => void
  onOpen: (row: ComponentRow) => void
}) {
  const { scenario, components } = item
  const verdict = components[0]?.verdict ?? scenario.verdict
  const n = components.length

  return (
    <article className={`ex-card v-${String(verdict).toLowerCase()}`}>
      <button
        type="button"
        className="ex-card-h"
        aria-expanded={expanded}
        onClick={onToggle}
      >
        <span className="ex-chevron" data-open={expanded} aria-hidden />
        <span className="ex-card-main">
          <span className="ex-card-title">{scenario.title}</span>
          <span className="ex-card-about">{scenario.about}</span>
        </span>
        <span className="ex-card-badges">
          {n > 0 && (
            <span className="ex-count-pill">{n} live</span>
          )}
          <span className={`badge ${verdict}`}>{exampleVerdictLabel(verdict)}</span>
        </span>
      </button>

      {expanded && (
        <div className="ex-card-body">
          <p className="ex-explain">{scenario.explanation}</p>
          <p className="ex-verdict-hint">{VERDICT_DEFINITION[verdict]}</p>

          {n > 0 ? (
            <div className="ex-live-list">
              <div className="ex-live-label">
                Live example{n === 1 ? '' : 's'} in this run
              </div>
              <ul className="ex-live-items">
                {components.map((c) => (
                  <li key={c.id} className="ex-live-item">
                    <div className="ex-live-item-main">
                      <code className="mono">{c.api_name}</code>
                      {(c.reason_codes?.length ?? 0) > 0 && (
                        <div className="ex-codes">
                          {c.reason_codes!.slice(0, 3).map((code) => (
                            <span key={code} className="ex-reason">{code}</span>
                          ))}
                        </div>
                      )}
                    </div>
                    <button
                      type="button"
                      className="primary ex-open"
                      onClick={() => onOpen(c)}
                    >
                      Open
                    </button>
                  </li>
                ))}
              </ul>
            </div>
          ) : (
            <p className="ex-none">
              No matching component in this run. The scenario still describes
              how the classifier behaves when this pattern appears.
            </p>
          )}
        </div>
      )}
    </article>
  )
}
