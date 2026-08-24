import type { ReactNode } from 'react'

/**
 * A small markdown renderer for agent replies.
 *
 * Deliberately not `marked` + `dompurify`. Those would mean rendering model
 * output through `dangerouslySetInnerHTML`, and the text it formats includes
 * arbitrary strings pulled out of a customer's org — field labels, Apex
 * comments, SOQL. Building React elements instead means there is no HTML
 * injection path at all, and it keeps the one typographic rule this product
 * depends on under our control: anything quoted from the org is monospace,
 * anything the model wrote is proportional.
 *
 * Handles what the agent actually emits: headings, bullet and numbered lists,
 * pipe tables, fenced code, inline code and bold. Anything else falls through
 * as a paragraph, which degrades to readable text rather than to markup.
 */

function inline(text: string): ReactNode[] {
  return text.split(/(`[^`]+`|\*\*[^*]+\*\*)/g).map((piece, i) => {
    if (piece.startsWith('`') && piece.endsWith('`') && piece.length > 2) {
      return <code key={i}>{piece.slice(1, -1)}</code>
    }
    if (piece.startsWith('**') && piece.endsWith('**') && piece.length > 4) {
      return <b key={i}>{piece.slice(2, -2)}</b>
    }
    return <span key={i}>{piece}</span>
  })
}

const isTableRow = (l: string) => l.trim().startsWith('|') && l.trim().endsWith('|')
const isDivider = (l: string) => /^\|[\s:|-]+\|$/.test(l.trim())
const cells = (l: string) =>
  l.trim().replace(/^\||\|$/g, '').split('|').map((c) => c.trim())

export function Markdown({ text }: { text: string }) {
  const lines = (text || '').split('\n')
  const out: ReactNode[] = []
  let i = 0
  let key = 0

  while (i < lines.length) {
    const line = lines[i]

    if (!line.trim()) { i++; continue }

    // fenced code
    if (line.trim().startsWith('```')) {
      const body: string[] = []
      i++
      while (i < lines.length && !lines[i].trim().startsWith('```')) {
        body.push(lines[i]); i++
      }
      i++
      out.push(<pre key={key++}><code>{body.join('\n')}</code></pre>)
      continue
    }

    // heading
    const h = line.match(/^(#{1,4})\s+(.*)$/)
    if (h) {
      const level = h[1].length
      // Rendered as a single styled class rather than h1..h4: this sits inside
      // a chat bubble, where a real h1 would outrank the page's own headings.
      out.push(
        <div className={`md-h md-h${level}`} key={key++}>{inline(h[2])}</div>,
      )
      i++
      continue
    }

    // table
    if (isTableRow(line) && i + 1 < lines.length && isDivider(lines[i + 1])) {
      const head = cells(line)
      i += 2
      const rows: string[][] = []
      while (i < lines.length && isTableRow(lines[i])) {
        rows.push(cells(lines[i])); i++
      }
      out.push(
        <div className="twrap" key={key++}>
          <table className="md-table">
            <thead><tr>{head.map((c, j) => <th key={j}>{inline(c)}</th>)}</tr></thead>
            <tbody>
              {rows.map((r, j) => (
                <tr key={j}>{r.map((c, k) => <td key={k}>{inline(c)}</td>)}</tr>
              ))}
            </tbody>
          </table>
        </div>,
      )
      continue
    }

    // lists
    const bullet = /^\s*[-*]\s+(.*)$/
    const numbered = /^\s*\d+[.)]\s+(.*)$/
    if (bullet.test(line) || numbered.test(line)) {
      const ordered = numbered.test(line)
      const items: string[] = []
      while (i < lines.length) {
        const m = lines[i].match(ordered ? numbered : bullet)
        if (!m) break
        items.push(m[1]); i++
      }
      const Tag = ordered ? 'ol' : 'ul'
      out.push(
        <Tag className="md-list" key={key++}>
          {items.map((it, j) => <li key={j}>{inline(it)}</li>)}
        </Tag>,
      )
      continue
    }

    // paragraph: gather until a blank line or the start of another block
    const para: string[] = []
    while (i < lines.length && lines[i].trim()
           && !/^(#{1,4}\s|```|\s*[-*]\s|\s*\d+[.)]\s)/.test(lines[i])
           && !isTableRow(lines[i])) {
      para.push(lines[i]); i++
    }
    if (para.length) out.push(<p key={key++}>{inline(para.join(' '))}</p>)
  }

  return <>{out}</>
}
