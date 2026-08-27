/**
 * The executive report, embedded.
 *
 * An iframe rather than inlined markup, deliberately. The report is a
 * self-extracting bundle that ships its own reset, its own font stack and a
 * dark full-page background; dropped into this document it would fight the
 * app's stylesheet in both directions. The iframe gives it the isolated
 * document it was built for, and same-origin means no CSP or cookie problems.
 *
 * The file is a build artifact, not source: `npm run rebrand-report` reads the
 * original bundle and writes public/executive-report.html with the client name
 * and wordmark replaced.
 */

import { useEffect, useLayoutEffect, useRef, useState } from 'react'

const SRC = '/executive-report.html'

/**
 * The width the report's layout assumes.
 *
 * Its hero puts the findings donut in an absolutely positioned box, so the
 * donut does not push the prose out of the way -- below about 1220px the
 * verdict paragraph runs straight underneath it. Measured: 12 overlapping
 * line boxes at 1000px, 8 at 1180px, none at 1220px and above.
 *
 * Rather than guess at the report's breakpoints and patch its CSS, the frame
 * is given the width the design was drawn for and scaled down to fit. The
 * layout is then always the one that was designed, just smaller.
 */
const DESIGN_WIDTH = 1280

export function ExecutiveSummary() {
  const frame = useRef<HTMLIFrameElement>(null)
  const wrap = useRef<HTMLDivElement>(null)
  const [state, setState] = useState<'loading' | 'ready' | 'missing'>('loading')
  const [box, setBox] = useState({ w: 0, h: 0 })

  useEffect(() => {
    // A 404 still fires onLoad — the browser loaded *something*, just not the
    // report — so the presence check is a HEAD request rather than the event.
    let alive = true
    fetch(SRC, { method: 'HEAD' })
      .then((r) => { if (alive) setState(r.ok ? 'ready' : 'missing') })
      .catch(() => { if (alive) setState('missing') })
    return () => { alive = false }
  }, [])

  /**
   * Measure before the first paint, once.
   *
   * This has to happen before the frame loads, not after. The report positions
   * its hero donut with script at load time and never recomputes, so widening
   * the frame afterwards left the donut placed for the old width and the text
   * ran under it again -- the very bug the scaling exists to fix. Deciding the
   * layout width up front means the report only ever lays out once.
   */
  useLayoutEffect(() => {
    const el = wrap.current
    if (!el) return
    const r = el.getBoundingClientRect()
    setBox({ w: Math.round(r.width), h: Math.round(r.height) })
  }, [])

  /**
   * Follow later size changes -- a window resize, the sidebar collapsing.
   *
   * Deliberately NOT a per-render measurement. That version deadlocked React
   * with "maximum update depth exceeded": the frame's layout width is the
   * design width even while it paints scaled, which overflowed the column and
   * raised a horizontal scrollbar, and the scrollbar stole height, which
   * re-rendered the frame, which moved the scrollbar. `.exec-wrap` clips now
   * so that path is closed, and driving this from observers rather than from
   * rendering means a render can no longer schedule a render.
   */
  useEffect(() => {
    const el = wrap.current
    if (!el) return
    const measure = () => {
      const r = el.getBoundingClientRect()
      const next = { w: Math.round(r.width), h: Math.round(r.height) }
      setBox((prev) =>
        // A pixel of jitter is not worth a re-render.
        Math.abs(prev.w - next.w) < 2 && Math.abs(prev.h - next.h) < 2 ? prev : next)
    }
    window.addEventListener('resize', measure)
    // The sidebar changes this element's width without a window resize, and RO
    // is the only thing that sees it. Its callbacks ride the rendering
    // lifecycle, so a host that is not compositing never fires it -- which is
    // acceptable, because nothing there is being looked at.
    const ro = typeof ResizeObserver === 'function' ? new ResizeObserver(measure) : null
    ro?.observe(el)
    return () => {
      window.removeEventListener('resize', measure)
      ro?.disconnect()
    }
  }, [])

  if (state === 'missing') {
    return (
      <div className="pad">
        <section className="card">
          <h3>The executive report is not built</h3>
          <p className="why">
            <code>public/executive-report.html</code> is generated from the
            source bundle and is not committed. Build it with:
          </p>
          <pre className="cmd">npm run rebrand-report -- &lt;path-to-source.html&gt;</pre>
        </section>
      </div>
    )
  }

  // Decided once, from the first measurement, and never revisited: changing
  // the frame's layout width after load would reflow the report without
  // re-running the script that placed its chart.
  const designed = box.w > 0 && box.w < DESIGN_WIDTH
  // The scale, by contrast, is safe to follow a resize -- it is a paint-time
  // transform and reflows nothing.
  const scale = designed ? box.w / DESIGN_WIDTH : 1

  return (
    <div className="exec-wrap" ref={wrap}>
      {state === 'loading' && <div className="exec-loading">Loading report…</div>}
      <iframe
        ref={frame}
        className="exec-frame"
        src={SRC}
        title="Executive summary"
        style={designed ? {
          width: DESIGN_WIDTH,
          // Undo the scale so the frame still fills the height it was given;
          // without this the report would end short of the bottom.
          height: box.h / scale,
          transform: `scale(${scale})`,
          transformOrigin: 'top left',
        } : undefined}
        onLoad={() => setState('ready')}
        /* No sandbox attribute, deliberately.
         *
         * The report needs scripts to unpack itself and same-origin to reach
         * its own assets, and `allow-scripts allow-same-origin` together is
         * not a sandbox at all -- the framed document can simply remove the
         * attribute from its own frame element. The browser says so out loud
         * in a console warning. Writing it anyway would buy nothing and imply
         * a protection that is not there. */
      />
    </div>
  )
}
