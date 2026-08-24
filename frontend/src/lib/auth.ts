/**
 * Auth client.
 *
 * The session is an httpOnly cookie, so there is no token to store or attach —
 * `credentials: 'same-origin'` is the whole mechanism. That also means script
 * on the page cannot read the session, and EventSource authenticates for free.
 */

export type Role = 'admin' | 'user'

export interface Me {
  authenticated: boolean
  email?: string
  role?: Role
  is_superuser?: boolean
}

export interface AppUser {
  id: number
  email: string
  role: Role
  is_active: boolean
  is_superuser: boolean
  created_at: string
  created_by: string | null
  last_login_at: string | null
}

async function call<T>(url: string, init?: RequestInit): Promise<T> {
  const r = await fetch(url, { credentials: 'same-origin', ...init })
  if (!r.ok) {
    const body = await r.json().catch(() => ({}))
    throw new Error(body.detail ?? `HTTP ${r.status}`)
  }
  return r.json() as Promise<T>
}

const json = (body: unknown): RequestInit => ({
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify(body),
})

export const auth = {
  me: () => call<Me>('/api/auth/me'),

  config: () => call<{ superuser_configured: boolean }>('/api/auth/config'),

  login: (email: string, password: string) =>
    call<{ email: string; role: Role }>('/api/auth/login', json({ email, password })),

  logout: () => call<{ ok: boolean }>('/api/auth/logout', { method: 'POST' }),

  changePassword: (current_password: string, new_password: string) =>
    call<{ ok: boolean }>('/api/auth/password',
      json({ current_password, new_password })),

  users: () => call<AppUser[]>('/api/auth/users'),

  createUser: (email: string, password: string, role: Role) =>
    call<AppUser>('/api/auth/users', json({ email, password, role })),

  updateUser: (id: number, patch: Partial<{ role: Role; is_active: boolean; password: string }>) =>
    call<AppUser>(`/api/auth/users/${id}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(patch),
    }),

  deleteUser: (id: number) =>
    call<{ ok: boolean }>(`/api/auth/users/${id}`, { method: 'DELETE' }),
}
