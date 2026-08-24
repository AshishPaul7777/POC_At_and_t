/**
 * Deep links into a view.
 *
 * The app has no router — the view is component state — so a shared URL has to
 * carry both the view and the place inside it. The hash does that:
 *
 *     http://vm/#docs/building-the-dependency-graph
 *
 * Hash rather than a path because it needs no server-side rewrite: nginx serves
 * the SPA at / and never sees the fragment, so a pasted link cannot 404.
 */

export interface Target {
  view: string
  anchor: string
}

/** Stable id for a heading. Stable is the whole point — these get shared. */
export function slugify(title: string): string {
  return title
    .toLowerCase()
    .replace(/[’']/g, '')          // "cannot" and "can't" should not differ by a quote
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '')
    .slice(0, 60)
}

export function parseHash(hash: string = window.location.hash): Target | null {
  const raw = hash.replace(/^#/, '')
  if (!raw) return null
  const [view, ...rest] = raw.split('/')
  if (!view) return null
  return { view, anchor: rest.join('/') }
}

export function linkTo(view: string, anchor: string): string {
  const { origin, pathname, search } = window.location
  return `${origin}${pathname}${search}#${view}/${anchor}`
}

/**
 * Copy text, on any origin.
 *
 * navigator.clipboard is gated behind a secure context, and this is deployed
 * over plain HTTP on a VM — so on the deployment that matters most the modern
 * API is not merely blocked, it is `undefined`. The execCommand path is
 * deprecated and still the only thing that works there.
 */
export async function copyText(text: string): Promise<boolean> {
  if (window.isSecureContext && navigator.clipboard) {
    try {
      await navigator.clipboard.writeText(text)
      return true
    } catch {
      // Permission refused, or a browser that lies about isSecureContext.
      // Fall through rather than reporting failure to the user.
    }
  }

  try {
    const ta = document.createElement('textarea')
    ta.value = text
    ta.setAttribute('readonly', '')
    // Off-screen but focusable. display:none or visibility:hidden would make
    // the selection empty and the copy silently a no-op.
    ta.style.cssText = 'position:fixed;top:0;left:-9999px;opacity:0'
    document.body.appendChild(ta)
    ta.select()
    ta.setSelectionRange(0, text.length)   // iOS ignores select() alone
    const ok = document.execCommand('copy')
    document.body.removeChild(ta)
    return ok
  } catch {
    return false
  }
}
