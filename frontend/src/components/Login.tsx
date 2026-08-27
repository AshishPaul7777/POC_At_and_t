import { useEffect, useState } from 'react'
import { auth } from '../lib/auth'

/**
 * The sign-in gate.
 *
 * Deliberately says nothing about which accounts exist — the API returns one
 * message for both a wrong password and an unknown address, and this screen
 * must not undo that by phrasing it differently.
 */
export function Login({ onSignedIn }: { onSignedIn: () => void }) {
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [configured, setConfigured] = useState<boolean | null>(null)

  useEffect(() => {
    auth.config().then((c) => setConfigured(c.superuser_configured)).catch(() => {})
  }, [])

  const submit = async (e: React.FormEvent) => {
    e.preventDefault()
    if (busy) return
    setBusy(true)
    setError(null)
    try {
      await auth.login(email.trim(), password)
      onSignedIn()
    } catch (err) {
      setError(String(err).replace(/^Error:\s*/, ''))
      setPassword('')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="login">
      <div className="login-card">
        <div className="login-brand">
          <svg viewBox="0 0 24 24" className="brand-mark" aria-hidden>
            <path d="M12 2l8 4.5v9L12 20l-8-4.5v-9z" fill="none"
                  stroke="currentColor" strokeWidth="1.6" />
            <path d="M12 7l4 2.3v4.4L12 16l-4-2.3V9.3z" fill="currentColor"
                  opacity=".55" />
          </svg>
          <div>
            <h1>AI Health Assessment</h1>
            <p>Sign in to continue</p>
          </div>
        </div>

        <form onSubmit={submit}>
          <label className="field">
            <span>Email</span>
            <input type="email" value={email} autoComplete="username" required
                   autoFocus onChange={(e) => setEmail(e.target.value)} />
          </label>

          <label className="field">
            <span>Password</span>
            <input type="password" value={password} required
                   autoComplete="current-password"
                   onChange={(e) => setPassword(e.target.value)} />
          </label>

          {error && <div className="login-error">{error}</div>}

          {/* A deployment with no superuser configured cannot be signed into at
              all. Saying so here turns a baffling "incorrect password" loop
              into a one-line fix. */}
          {configured === false && (
            <div className="login-warn">
              No superuser is configured on this deployment. Set
              <code>AUTH_SUPERUSER_EMAIL</code> and
              <code>AUTH_SUPERUSER_PASSWORD</code> in <code>.env</code>, then
              restart the backend.
            </div>
          )}

          <button className="primary full" type="submit" disabled={busy}>
            {busy ? 'Signing in…' : 'Sign in'}
          </button>
        </form>
      </div>
    </div>
  )
}
