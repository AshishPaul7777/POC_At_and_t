/**
 * The AI Library — the Salesforce Custom Skill Suite page, embedded verbatim.
 *
 * `public/ai-library.html` is a byte-for-byte copy of the source file and is
 * deliberately not touched: not the Bounteous branding, not the logo, not the
 * fonts. It is shown in an iframe so its own reset, gradient hero and sidenav
 * cannot collide with this app's stylesheet in either direction.
 *
 * No scaling, unlike the executive report. That page pins a chart with absolute
 * positioning and breaks below ~1220px; this one is a max-width 1400px flex
 * layout with its own 860px breakpoint, so it reflows on its own and the frame
 * just gives it the space it has.
 */

import { useEffect, useState } from 'react'

const SRC = '/ai-library.html'

export function AiLibrary() {
  const [state, setState] = useState<'loading' | 'ready' | 'missing'>('loading')

  useEffect(() => {
    // A 404 still fires onLoad -- the browser loaded something, just not this
    // page -- so presence is checked with a request rather than the event.
    let alive = true
    fetch(SRC, { method: 'HEAD' })
      .then((r) => { if (alive) setState(r.ok ? 'ready' : 'missing') })
      .catch(() => { if (alive) setState('missing') })
    return () => { alive = false }
  }, [])

  if (state === 'missing') {
    return (
      <div className="pad">
        <section className="card">
          <h3>The AI Library page is missing</h3>
          <p className="why">
            <code>frontend/public/ai-library.html</code> is not present. It is
            committed to the repository, so a checkout should have it — if this
            is a deployed build, the image predates it.
          </p>
        </section>
      </div>
    )
  }

  return (
    <div className="embed-wrap">
      {state === 'loading' && <div className="exec-loading">Loading…</div>}
      <iframe
        className="embed-frame"
        src={SRC}
        title="AI Library"
        onLoad={() => setState('ready')}
      />
    </div>
  )
}
