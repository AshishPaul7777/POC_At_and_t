/**
 * Tree shaping, kept out of the rendering code so it can be reasoned about.
 *
 * The server returns a flat node list rather than a nested one -- it is smaller
 * on the wire, and the code pane needs a flat array to window over anyway -- so
 * the parent/child structure is rebuilt here, once per load.
 */
import type { TreeNode, VerdictCounts } from '../../lib/api'

/** Worst first. Used for tie-breaking a colour, never for choosing one. */
export const SEVERITY = ['UNUSED', 'NEEDS_REVIEW', 'USED', 'OUT_OF_SCOPE', 'UNCLASSIFIED']

export interface Shaped {
  byPath: Map<string, TreeNode>
  children: Map<string | null, TreeNode[]>
}

export function shape(nodes: TreeNode[]): Shaped {
  const byPath = new Map<string, TreeNode>()
  const children = new Map<string | null, TreeNode[]>()
  for (const n of nodes) byPath.set(n.path, n)
  for (const n of nodes) {
    const list = children.get(n.parent)
    if (list) list.push(n)
    else children.set(n.parent, [n])
  }
  // Folders before files, then alphabetical -- the order every file browser
  // uses, so nobody has to learn this one.
  for (const list of children.values()) {
    list.sort((a, b) =>
      (a.kind === b.kind ? 0 : a.kind === 'folder' ? -1 : 1)
      || a.name.localeCompare(b.name))
  }
  return { byPath, children }
}

export interface Row {
  node: TreeNode
  depth: number
  expanded: boolean
  hasChildren: boolean
}

/**
 * The rows actually on screen.
 *
 * Bounded by what is expanded rather than by how many files exist, which is why
 * the tree needs no virtualisation: a collapsed tree is a few dozen rows however
 * large the org is.
 */
export function visibleRows(
  s: Shaped, expanded: Set<string>, filter: string,
): Row[] {
  const q = filter.trim().toLowerCase()
  const keep = q ? matching(s, q) : null

  const out: Row[] = []
  const walk = (parent: string | null, depth: number) => {
    for (const node of s.children.get(parent) ?? []) {
      if (keep && !keep.has(node.path)) continue
      const kids = s.children.get(node.path) ?? []
      const hasChildren = kids.length > 0
      // A search result is useless collapsed, so matching folders open
      // themselves rather than making the reader click down to the hit.
      const open = expanded.has(node.path) || (!!q && hasChildren)
      out.push({ node, depth, expanded: open, hasChildren })
      if (open) walk(node.path, depth + 1)
    }
  }
  walk(null, 0)
  return out
}

/** Paths to keep for a filter: every match, plus the folders leading to it. */
function matching(s: Shaped, q: string): Set<string> {
  const keep = new Set<string>()
  for (const node of s.byPath.values()) {
    if (!node.name.toLowerCase().includes(q)) continue
    keep.add(node.path)
    let p = node.parent
    while (p) {
      if (keep.has(p)) break
      keep.add(p)
      p = s.byPath.get(p)?.parent ?? null
    }
  }
  return keep
}

/** Ancestor paths of a file, so revealing it can open the way down to it. */
export function ancestorsOf(s: Shaped, path: string): string[] {
  const out: string[] = []
  let p = s.byPath.get(path)?.parent ?? null
  while (p) {
    out.push(p)
    p = s.byPath.get(p)?.parent ?? null
  }
  return out
}

export const totalOf = (c: VerdictCounts) =>
  Object.values(c).reduce((a, b) => a + (b ?? 0), 0)
