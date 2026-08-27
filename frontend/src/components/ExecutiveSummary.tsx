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

import { useEffect, useRef, useState } from 'react'

const SRC = '/executive-report.html'

export function ExecutiveSummary() {
  const frame = useRef<HTMLIFrameElement>(null)
  const [state, setState] = useState<'loading' | 'ready' | 'missing'>('loading')

  useEffect(() => {
    // A 404 still fires onLoad — the browser loaded *something*, just not the
    // report — so the presence check is a HEAD request rather than the event.
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

  return (
    <div className="exec-wrap">
      {state === 'loading' && <div className="exec-loading">Loading report…</div>}
      <iframe
        ref={frame}
        className="exec-frame"
        src={SRC}
        title="Executive summary"
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
