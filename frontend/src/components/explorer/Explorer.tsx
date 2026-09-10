import { useCallback, useEffect, useMemo, useState } from 'react'
import { api, type FileView, type Tree, type TreeNode } from '../../lib/api'
import { FileTree, UnmappedPanel } from './FileTree'
import { CodeView } from './CodeView'
import { ancestorsOf, shape, visibleRows } from './explorerModel'

/**
 * The org as a file tree, with the analysis painted onto it.
 *
 * All explorer state lives here rather than in App. App.tsx is already 700
 * lines with twenty-odd `useState` calls, and adding tree expansion, the open
 * file and scroll position to it would leave this feature's state owned by
 * nobody. Only `runId` and a callback for "open this component in the detail
 * panel" cross the boundary.
 */
export function Explorer(
  { runId, onOpenComponent, revealComponentId, refreshToken = 0 }: {
    runId: string
    onOpenComponent: (componentId: number) => void
    /** Set when another view asks to reveal a component here. */
    revealComponentId: number | null
    /** Bumped when a run finishes so the tree reloads (runId alone does not). */
    refreshToken?: number
  },
) {
  const [tree, setTree] = useState<Tree | null>(null)
  const [expanded, setExpanded] = useState<Set<string>>(new Set())
  const [filter, setFilter] = useState('')
  const [activePath, setActivePath] = useState<string | null>(null)
  const [file, setFile] = useState<FileView | null>(null)
  const [revealLine, setRevealLine] = useState<number | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)

  useEffect(() => {
    setTree(null); setFile(null); setActivePath(null); setError(null)
    api.tree(runId).then(setTree).catch((e) => setError(String(e)))
  }, [runId, refreshToken])

  const shaped = useMemo(() => shape(tree?.nodes ?? []), [tree])
  const rows = useMemo(
    () => visibleRows(shaped, expanded, filter), [shaped, expanded, filter])

  const toggle = useCallback((path: string) => {
    setExpanded((was) => {
      const next = new Set(was)
      if (next.has(path)) next.delete(path)
      else next.add(path)
      return next
    })
  }, [])

  const openPath = useCallback((path: string, line: number | null = null) => {
    setActivePath(path)
    setRevealLine(line)
    setLoading(true)
    setError(null)
    api.file(runId, path)
      .then(setFile)
      .catch((e) => { setFile(null); setError(String(e)) })
      .finally(() => setLoading(false))
  }, [runId])

  const openNode = useCallback((n: TreeNode) => openPath(n.path), [openPath])

  // "Reveal in explorer", driven from the components table or the graph. The
  // component may legitimately have no file -- standard-object fields never do
  // -- so the failure path says why rather than doing nothing.
  useEffect(() => {
    if (revealComponentId == null || !tree) return
    let cancelled = false
    api.location(runId, revealComponentId).then((loc) => {
      if (cancelled) return
      if (!loc.path) {
        setError(loc.why ?? 'This component has no file in the retrieved metadata.')
        return
      }
      setExpanded((was) => new Set([...was, ...ancestorsOf(shaped, loc.path!)]))
      openPath(loc.path, loc.start_line)
    }).catch(() => {})
    return () => { cancelled = true }
  }, [revealComponentId, tree, runId, shaped, openPath])

  if (error && !tree) return <div className="pad"><div className="caveat">{error}</div></div>
  if (!tree) return <div className="pad"><div className="empty">Loading the file tree...</div></div>

  return (
    <div className="explorer">
      <div className="ex-tree">
        <div className="ex-tree-head">
          <input placeholder="filter files..." value={filter}
                 onChange={(e) => setFilter(e.target.value)} />
          <span className="tb-hint">{tree.totals.files} files</span>
        </div>

        {!tree.workspace.available && (
          <div className="caveat small">
            The retrieved files for {tree.org_alias} are no longer on disk, so
            nothing can be opened. The tree still reflects what this run saw.
          </div>
        )}
        {tree.workspace.available && !tree.workspace.matches_run && (
          <div className="caveat small">{tree.workspace.reason}</div>
        )}

        <FileTree rows={rows} activePath={activePath}
                  onToggle={toggle} onOpen={openNode} />

        <UnmappedPanel groups={tree.unmapped}
                       onPick={(g) => setError(
                         `${g} was not retrieved as a file, so it has nothing to`
                         + ' open. Its components are still analysed and appear'
                         + ' in Components Overview.')} />
      </div>

      <div className="ex-code">
        {error && <div className="caveat">{error}</div>}
        {loading && <div className="empty">Opening...</div>}
        {!file && !loading && !error && (
          <div className="empty">
            Pick a file. Lines are coloured by what the analysis concluded about
            the components in them; hover a highlight for the reasoning.
          </div>
        )}
        {file && !loading && (
          <CodeView view={file} onPick={onOpenComponent} revealLine={revealLine} />
        )}
      </div>
    </div>
  )
}
