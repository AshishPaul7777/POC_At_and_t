import { useCallback, useEffect, useState } from 'react'
import { auth, type AppUser, type Role } from '../lib/auth'

/**
 * User administration.
 *
 * Two roles only: `admin` can manage users, `user` can do everything else. The
 * superuser row is rendered read-only because the backend refuses to demote,
 * deactivate or delete it — showing controls that always fail would be worse
 * than showing none.
 */
export function Admin({ me }: { me: { email?: string } }) {
  const [users, setUsers] = useState<AppUser[]>([])
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [role, setRole] = useState<Role>('user')

  const load = useCallback(async () => {
    try {
      setUsers(await auth.users())
      setError(null)
    } catch (e) {
      setError(String(e).replace(/^Error:\s*/, ''))
    }
  }, [])

  useEffect(() => { load() }, [load])

  const act = async (fn: () => Promise<unknown>, ok: string) => {
    setBusy(true); setError(null); setNotice(null)
    try {
      await fn()
      setNotice(ok)
      await load()
    } catch (e) {
      setError(String(e).replace(/^Error:\s*/, ''))
    } finally {
      setBusy(false)
    }
  }

  const create = (e: React.FormEvent) => {
    e.preventDefault()
    act(async () => {
      await auth.createUser(email.trim(), password, role)
      setEmail(''); setPassword(''); setRole('user')
    }, `${email.trim()} added`)
  }

  const suggest = () => {
    // Generated in the browser and shown once. An admin inventing passwords by
    // hand is the most likely way a weak one gets in.
    const bytes = crypto.getRandomValues(new Uint8Array(18))
    setPassword(btoa(String.fromCharCode(...bytes)).replace(/[+/=]/g, '').slice(0, 20))
  }

  return (
    <div className="pad">
      <section className="hero">
        <div className="hero-top">
          <span className="hero-chip">ACCESS</span>
          <span className="spacer" />
          <span className="hero-elapsed">{users.length} accounts</span>
        </div>
        <h1>Who can use this</h1>
        <p>
          Admins manage accounts. Everyone else can run the analysis, read the
          evidence and use the assistant — there is nothing here worth hiding
          from someone already trusted to see the org's metadata.
        </p>
      </section>

      {error && <div className="admin-note bad">{error}</div>}
      {notice && <div className="admin-note good">{notice}</div>}

      <div className="cards">
        <section className="card wide">
          <h3>Add a user</h3>
          <p className="why">
            Passwords are stored as scrypt hashes and are never recoverable —
            copy it before you save, and send it over something other than email.
          </p>
          <form onSubmit={create} className="admin-form">
            <label className="field">
              <span>Email</span>
              <input type="email" required value={email}
                     onChange={(e) => setEmail(e.target.value)} />
            </label>
            <label className="field">
              <span>Password <em>(12 characters minimum)</em></span>
              <div className="field-row">
                <input type="text" required minLength={12} value={password}
                       onChange={(e) => setPassword(e.target.value)} />
                <button type="button" className="ghost" onClick={suggest}>
                  Generate
                </button>
              </div>
            </label>
            <label className="field">
              <span>Role <em>(admins also manage accounts)</em></span>
              <select value={role} onChange={(e) => setRole(e.target.value as Role)}>
                <option value="user">User</option>
                <option value="admin">Admin</option>
              </select>
            </label>
            <div className="admin-actions">
              <button className="primary" type="submit" disabled={busy}>
                Add user
              </button>
            </div>
          </form>
        </section>

        <section className="card wide">
          <h3>Accounts</h3>
          <div className="twrap">
            <table>
              <thead>
                <tr>
                  <th>Email</th><th>Role</th><th>Status</th>
                  <th>Last sign-in</th><th></th>
                </tr>
              </thead>
              <tbody>
                {users.map((u) => {
                  const self = u.email === me.email
                  return (
                    <tr key={u.id} style={{ cursor: 'default' }}>
                      <td>
                        <code>{u.email}</code>
                        {u.is_superuser && <span className="pill muted">superuser</span>}
                        {self && !u.is_superuser && <span className="pill muted">you</span>}
                      </td>
                      <td>
                        {u.is_superuser ? (
                          <span className="badge USED">admin</span>
                        ) : (
                          <select value={u.role} disabled={busy}
                                  onChange={(e) => act(
                                    () => auth.updateUser(u.id, { role: e.target.value as Role }),
                                    `${u.email} is now ${e.target.value}`)}>
                            <option value="user">user</option>
                            <option value="admin">admin</option>
                          </select>
                        )}
                      </td>
                      <td>
                        <span className={`badge ${u.is_active ? 'USED' : 'OUT_OF_SCOPE'}`}>
                          {u.is_active ? 'active' : 'disabled'}
                        </span>
                      </td>
                      <td>
                        {u.last_login_at
                          ? new Date(u.last_login_at).toLocaleString()
                          : <span className="tb-hint">never</span>}
                      </td>
                      <td style={{ textAlign: 'right', whiteSpace: 'nowrap' }}>
                        {!u.is_superuser && (
                          <>
                            <button className="linkish" disabled={busy}
                                    onClick={() => act(
                                      () => auth.updateUser(u.id, { is_active: !u.is_active }),
                                      `${u.email} ${u.is_active ? 'disabled' : 'enabled'}`)}>
                              {u.is_active ? 'disable' : 'enable'}
                            </button>
                            {!self && (
                              <button className="linkish" disabled={busy}
                                      style={{ marginLeft: 12, color: 'var(--danger)' }}
                                      onClick={() => {
                                        if (!confirm(`Delete ${u.email}? This cannot be undone.`)) return
                                        act(() => auth.deleteUser(u.id), `${u.email} deleted`)
                                      }}>
                                delete
                              </button>
                            )}
                          </>
                        )}
                      </td>
                    </tr>
                  )
                })}
                {!users.length && (
                  <tr><td colSpan={5} className="ins-empty">No accounts yet.</td></tr>
                )}
              </tbody>
            </table>
          </div>
        </section>
      </div>
    </div>
  )
}
