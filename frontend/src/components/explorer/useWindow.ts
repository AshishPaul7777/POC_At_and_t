import { useCallback, useEffect, useRef, useState } from 'react'

/**
 * Fixed-height row windowing, in about forty lines and no dependency.
 *
 * An `.object` file in this org is 292 lines and a real one can be 18,000. That
 * is 18,000 React elements on every keystroke elsewhere in the app, which is a
 * reconciliation problem rather than a paint problem -- so `content-visibility`
 * does not fix it, and `react-window` would be a dependency for one screen.
 *
 * The whole trick is that a code view can enforce one row height: lines do not
 * wrap, they scroll sideways.
 */
export function useWindow(count: number, rowHeight: number, overscan = 12) {
  const ref = useRef<HTMLDivElement | null>(null)
  const [scrollTop, setScrollTop] = useState(0)
  const [viewport, setViewport] = useState(600)
  const frame = useRef(0)

  useEffect(() => {
    const el = ref.current
    if (!el) return
    const ro = new ResizeObserver(() => setViewport(el.clientHeight || 600))
    ro.observe(el)
    setViewport(el.clientHeight || 600)
    return () => ro.disconnect()
  }, [])

  const onScroll = useCallback(() => {
    // Coalesced to one update per frame: the scroll event fires far faster
    // than React can usefully re-render, and every extra pass is dropped work.
    if (frame.current) return
    frame.current = requestAnimationFrame(() => {
      frame.current = 0
      setScrollTop(ref.current?.scrollTop ?? 0)
    })
  }, [])

  useEffect(() => () => { if (frame.current) cancelAnimationFrame(frame.current) }, [])

  const first = Math.max(0, Math.floor(scrollTop / rowHeight) - overscan)
  const visible = Math.ceil(viewport / rowHeight) + overscan * 2
  const last = Math.min(count, first + visible)

  /** Put a row on screen, leaving a third of the viewport above it. */
  const scrollTo = useCallback((index: number) => {
    const el = ref.current
    if (!el) return
    el.scrollTop = Math.max(0, index * rowHeight - el.clientHeight / 3)
  }, [rowHeight])

  return { ref, onScroll, first, last, scrollTo,
           padTop: first * rowHeight, totalHeight: count * rowHeight }
}
