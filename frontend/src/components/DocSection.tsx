import { useEffect, useRef, useState } from 'react'
import { copyText, linkTo, parseHash, slugify } from '../lib/anchors'

/**
 * A documentation section with a shareable anchor.
 *
 * The id is derived from the title rather than passed separately, so a section
 * cannot end up with a link that no longer matches what it is called. The cost
 * is that renaming a heading breaks links already shared to it — accepted,
 * because the alternative is hand-maintained ids that silently drift from the
 * headings above them, which is worse and harder to notice.
 */
export function DocSection({
  title, children, className,
}: {
  title: string
  children: React.ReactNode
  className?: string
}) {
  const id = slugify(title)
  const [state, setState] = useState<'idle' | 'copied' | 'failed'>('idle')
  const ref = useRef<HTMLElement>(null)

  /**
   * Scroll here on arrival, if this is the linked section.
   *
   * Owned by the section rather than by App, because App cannot know when the
   * section exists. Its effect depended on the view and the session, but the
   * docs page does not render until the *run* has loaded as well — so on any
   * deployment where the run arrives after the session, the effect fired
   * against an empty DOM and nothing re-ran it. Here the element exists by
   * definition: the effect cannot run before its own element is mounted.
   */
  useEffect(() => {
    const t = parseHash()
    if (t?.view !== 'docs' || t.anchor !== id) return

    // Instant, not smooth. A smooth scroll of several thousand pixels on load
    // is slow, and any wheel touch during it cancels the scroll silently.
    const go = () => ref.current?.scrollIntoView({ block: 'start' })
    const raf = requestAnimationFrame(go)
    // Second pass once layout has settled: inline SVG and long tables change
    // height after first paint, which moves the target out from under us.
    const settle = setTimeout(go, 300)
    return () => { cancelAnimationFrame(raf); clearTimeout(settle) }
  }, [id])

  const copy = async () => {
    const url = linkTo('docs', id)
    const ok = await copyText(url)
    // Update the address bar either way, so the link is always obtainable even
    // if every clipboard route was refused.
    history.replaceState(null, '', `#docs/${id}`)
    setState(ok ? 'copied' : 'failed')
    // No window.prompt fallback: it is unsupported in embedded webviews and
    // some kiosk browsers, where calling it throws rather than doing nothing.
    // On failure the URL is rendered inline below instead, pre-selected.
    if (ok) setTimeout(() => setState('idle'), 1800)
  }

  return (
    <section id={id} className={className} ref={ref}>
      <h2 className="doc-h2">
        {title}
        <button
          className={`anchor-btn ${state}`}
          onClick={copy}
          title={`Copy a link to "${title}"`}
          aria-label={`Copy a link to the section "${title}"`}
        >
          {state === 'copied' ? (
            <>
              <svg viewBox="0 0 24 24" aria-hidden><path d="M20 6L9 17l-5-5" /></svg>
              Copied
            </>
          ) : state === 'failed' ? (
            <>
              <svg viewBox="0 0 24 24" aria-hidden>
                <path d="M12 8v5M12 17h.01" />
                <circle cx="12" cy="12" r="9" />
              </svg>
              Copy failed
            </>
          ) : (
            <>
              <svg viewBox="0 0 24 24" aria-hidden>
                <path d="M10 13a5 5 0 007.5.5l3-3a5 5 0 00-7-7l-1.5 1.5" />
                <path d="M14 11a5 5 0 00-7.5-.5l-3 3a5 5 0 007 7L12 19" />
              </svg>
              Link
            </>
          )}
        </button>
      </h2>
      {state === 'failed' && (
        <p className="anchor-fallback">
          The browser refused clipboard access. Copy it from here:
          <input
            readOnly
            value={linkTo('docs', id)}
            onFocus={(e) => e.currentTarget.select()}
            ref={(el) => el?.select()}
          />
        </p>
      )}
      {children}
    </section>
  )
}
