import { memo } from 'react'
import type { TreeNode, VerdictCounts } from '../../lib/api'
import {
  APEX_KIND_HINT,
  APEX_KIND_LABEL,
  type ApexClassKind,
} from '../../lib/apexGroup'
import { VERDICT_DEFINITION, VERDICT_LABEL, verdictClass } from '../../lib/verdict'
import type { Row } from './explorerModel'
import { totalOf } from './explorerModel'

/**
 * Verdict mix as a micro-bar, reusing the dot strip already in styles.css.
 *
 * Deliberately not a background colour on the row. With "unused" as the worst
 * verdict, one dead field would paint its object folder, which would paint the
 * root, and every folder ends up the same colour at exactly the zoom level
 * where the tree should be carrying the most information.
 */
function VerdictBar({ counts }: { counts: VerdictCounts }) {
  const total = totalOf(counts)
  if (!total) return null
  const unused = counts.UNUSED ?? 0
  return (
    <span className="tree-mix" title={Object.entries(counts)
      .map(([v, n]) => `${VERDICT_LABEL[v as never] ?? v}: ${n}`).join('\n')}>
      {(['USED', 'UNUSED', 'NEEDS_REVIEW', 'OUT_OF_SCOPE', 'UNCLASSIFIED'] as const)
        .map((v) => {
          const n = counts[v] ?? 0
          if (!n) return null
          return <i key={v} className={`mix ${v}`}
                    style={{ flexGrow: n }} />
        })}
      {unused > 0 && <b className="tree-unused">{unused}</b>}
    </span>
  )
}

interface Props {
  rows: Row[]
  activePath: string | null
  onToggle: (path: string) => void
  onOpen: (node: TreeNode) => void
}

const TreeRow = memo(function TreeRow(
  { row, active, onToggle, onOpen }: {
    row: Row
    active: boolean
    onToggle: (path: string) => void
    onOpen: (node: TreeNode) => void
  },
) {
  const { node, depth, expanded, hasChildren } = row
  const isFolder = node.kind === 'folder'
  return (
    <div className={`tree-row${active ? ' active' : ''}`}
         style={{ paddingLeft: 8 + depth * 13 }}
         title={node.components
           ? `${node.components} analysed component(s) here`
           : undefined}
         onClick={() => (isFolder ? onToggle(node.path) : onOpen(node))}>
      <span className="tree-caret">
        {isFolder ? (hasChildren ? (expanded ? '▾' : '▸') : '') : ''}
      </span>
      <span className={`tree-name${isFolder ? ' folder' : ''}`}
            data-verdict={node.kind === 'file' ? (node.dominant ?? '') : ''}>
        {node.name}
      </span>
      {(node.apex_kinds ?? []).map((k) => (
        <span key={k} className={`tree-tag kind-${k}`}
              title={APEX_KIND_HINT[k as ApexClassKind] ?? k}>
          {APEX_KIND_LABEL[k as ApexClassKind] ?? k}
        </span>
      ))}
      {!node.apex_kinds?.length && node.is_test && (
        <span className="tree-tag">test</span>
      )}
      <VerdictBar counts={node.counts} />
    </div>
  )
})

export function FileTree({ rows, activePath, onToggle, onOpen }: Props) {
  if (rows.length === 0) {
    return <div className="empty">No file matches.</div>
  }
  return (
    <div className="tree">
      {rows.map((row) => (
        <TreeRow key={row.node.path} row={row} active={row.node.path === activePath}
                 onToggle={onToggle} onOpen={onOpen} />
      ))}
    </div>
  )
}

/**
 * Components with no file of their own.
 *
 * A wildcard CustomObject retrieve returns custom objects only, so Account,
 * Case, Contact and Lead never arrive as files -- and a third of this org's
 * custom fields live on them. Without this panel the explorer would read as
 * "the org is clean" for precisely the components most likely to be deletable.
 */
export function UnmappedPanel(
  { groups, onPick }: {
    groups: { group: string; ctype: string; components: number; counts: VerdictCounts }[]
    onPick: (group: string) => void
  },
) {
  if (!groups.length) return null
  return (
    <div className="unmapped">
      <div className="unmapped-head">Not retrieved as files</div>
      <div className="sub">
        Standard objects are not returned by a wildcard retrieve, so their fields
        have no file to open. They are still analysed.
      </div>
      {groups.map((g) => (
        <div className="tree-row" key={g.group} onClick={() => onPick(g.group)}
             title={`${g.components} ${g.ctype} component(s) on ${g.group}`}>
          <span className="tree-caret" />
          <span className="tree-name">{g.group}</span>
          <VerdictBar counts={g.counts} />
        </div>
      ))}
    </div>
  )
}

export { VerdictBar }
export const verdictTitle = (v: string | null) =>
  v && v !== 'UNCLASSIFIED' ? VERDICT_DEFINITION[v as never] : undefined
export { verdictClass }
