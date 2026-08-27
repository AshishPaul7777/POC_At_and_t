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

  // Track the space available so the scale follows a window resize or the
  // sidebar collapsing, not just the first paint.
  //
  // Measured in a layout effect with no dependency list rather than through a
  // ResizeObserver. RO is the obvious tool and it is what this used first, but
  // its callbacks are delivered on the rendering lifecycle, so in a host that
  // is not compositing -- a headless pane, a background tab -- it simply never
  // fires and the report renders unscaled. Measuring on every render covers
  // the sidebar case (App re-renders, so this does too) and the resize
  // listener covers the rest.
  useLayoutEffect(() => {
    const el = wrap.current
    if (!el) return
    const r = el.getBoundingClientRect()
    const next = { w: Math.round(r.width), h: Math.round(r.height) }
    // Guarded, or this would loop: setState on every render.
    setBox((prev) => (prev.w === next.w && prev.h === next.h ? prev : next))
  })

  useEffect(() => {
    const onResize = () => {
      const el = wrap.current
      if (!el) return
      const r = el.getBoundingClientRect()
      setBox({ w: Math.round(r.width), h: Math.round(r.height) })
    }
    window.addEventListener('resize', onResize)
    return () => window.removeEventListener('resize', onResize)
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

  // Only ever scale down. Above the design width the report is happy to fill
  // the space, and blowing it up would just make it blurry.
  const scale = box.w > 0 ? Math.min(1, box.w / DESIGN_WIDTH) : 1

  return (
    <div className="exec-wrap" ref={wrap}>
      {state === 'loading' && <div className="exec-loading">Loading report…</div>}
      <iframe
        ref={frame}
        className="exec-frame"
        src={SRC}
        title="Executive summary"
        style={scale < 1 ? {
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
