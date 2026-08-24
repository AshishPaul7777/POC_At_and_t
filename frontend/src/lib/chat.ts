/**
 * Chat client: REST for state, SSE for the live turn.
 *
 * Kept separate from `stream.ts`. That module is a singleton tuned for one
 * pipeline run — a 20k ring buffer, rAF-batched repaints, `Last-Event-ID`
 * resume. A chat turn is a different shape: short, per-thread, and its history
 * comes from the database rather than from replaying an event log. Sharing the
 * singleton would mean a thread and a run could not be watched at once.
 *
 * The reconnect story still holds. A turn runs in the backend, so reopening the
 * stream replays everything it has emitted and then tails; a reload rejoins
 * rather than restarting.
 */

export type Role = 'user' | 'assistant' | 'system'

export interface ToolCall {
  tool_name: string
  args: Record<string, unknown>
  ok: boolean
  result: Record<string, unknown>
  duration_ms: number | null
}

export interface Message {
  id: number
  seq: number
  role: Role
  content: string
  input_tokens: number | null
  output_tokens: number | null
  stop_reason: string | null
  created_at: string
  tool_calls: ToolCall[]
}

export interface Thread {
  id: string
  run_id: string | null
  title: string
  state: string
  created_at: string
  updated_at: string
  message_count?: number
}

export interface Finding {
  id: number
  status: 'proposed' | 'accepted' | 'rejected'
  recommendation: string
  rationale: string
  citations: unknown[]
  api_name: string
  current_verdict: string | null
}

export interface ThreadDetail {
  thread: Thread
  messages: Message[]
  findings: Finding[]
  running: boolean
}

export interface Skill {
  name: string
  title: string
  when: string
}

/** A tool invocation as it appears while the turn is still running. */
export interface LiveTool {
  name: string
  args: Record<string, unknown>
  ok?: boolean
  summary?: string
  duration_ms?: number
  running: boolean
}

async function j<T>(url: string, init?: RequestInit): Promise<T> {
  const r = await fetch(url, init)
  if (!r.ok) {
    const body = await r.json().catch(() => ({}))
    throw new Error(body.detail ?? `HTTP ${r.status}`)
  }
  return r.json() as Promise<T>
}

export const chatApi = {
  skills: () => j<Skill[]>('/api/chat/skills'),
  threads: () => j<Thread[]>('/api/chat/threads'),
  thread: (id: string) => j<ThreadDetail>(`/api/chat/threads/${id}`),

  create: (runId?: string | null, title?: string) =>
    j<Thread>('/api/chat/threads', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ run_id: runId ?? null, title }),
    }),

  send: (id: string, content: string, context?: Record<string, unknown>) =>
    j<{ thread_id: string }>(`/api/chat/threads/${id}/messages`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ content, context }),
    }),

  cancel: (id: string) =>
    j<{ cancelled: boolean }>(`/api/chat/threads/${id}/cancel`, { method: 'POST' }),
}

export interface TurnHandlers {
  onText: (text: string) => void
  onToolStart: (name: string, args: Record<string, unknown>) => void
  onToolEnd: (name: string, ok: boolean, summary: string, ms: number) => void
  onFinished: (stopReason: string, inTok: number, outTok: number) => void
  onError: (message: string) => void
  onEnd: () => void
}

/**
 * Watch a thread's in-flight turn. Returns a closer.
 *
 * `stream.end` arrives when the backend has nothing running, which is also how
 * a client that opened the stream too late learns to stop waiting.
 */
export function watchTurn(threadId: string, h: TurnHandlers): () => void {
  const es = new EventSource(`/api/chat/threads/${threadId}/stream`)
  let closed = false

  const close = () => {
    if (closed) return
    closed = true
    es.close()
  }

  const on = (name: string, fn: (d: any) => void) =>
    es.addEventListener(name, (e) => {
      try {
        fn(JSON.parse((e as MessageEvent).data))
      } catch {
        /* a malformed frame must not tear down the stream */
      }
    })

  on('text', (d) => h.onText(d.text ?? ''))
  on('tool.started', (d) => h.onToolStart(d.name, d.args ?? {}))
  on('tool.finished', (d) =>
    h.onToolEnd(d.name, !!d.ok, d.summary ?? '', d.duration_ms ?? 0))
  on('turn.finished', (d) =>
    h.onFinished(d.stop_reason ?? 'end_turn', d.input_tokens ?? 0, d.output_tokens ?? 0))
  on('error', (d) => h.onError(d.message ?? 'unknown error'))
  on('stream.end', () => { h.onEnd(); close() })

  // A server-side close is normal once a turn ends. Treat it as the end of the
  // stream rather than an error, and do not let EventSource auto-reconnect into
  // a thread that is no longer running.
  es.onerror = () => {
    if (es.readyState === EventSource.CLOSED) { h.onEnd(); close() }
  }

  return close
}
