import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Markdown } from './Markdown'
import {
  chatApi,
  watchTurn,
  type LiveTool,
  type Message,
  type Skill,
  type Thread,
} from '../lib/chat'

/**
 * The chat surface.
 *
 * Two rules from the rest of the app carry over, and both are load-bearing:
 *
 *  - Anything quoted from the org — API names, queries, file paths — is
 *    monospace; the agent's own prose is proportional. That distinction is what
 *    stops generated text being read as a verified fact.
 *  - The tool trace is collapsible but never hidden. "Searched and found
 *    nothing" is the claim a deletion rests on, so how the agent reached an
 *    answer is part of the answer.
 */

const SUGGESTIONS = [
  'Summarise the latest run',
  'Which components need review, and why?',
  'What could this run not prove?',
]

function ToolTrace({ tools }: { tools: LiveTool[] }) {
  const [open, setOpen] = useState(false)
  if (!tools.length) return null
  const running = tools.some((t) => t.running)
  return (
    <div className="tool-trace" data-running={running}>
      <button className="tt-head" onClick={() => setOpen((o) => !o)}>
        <span className="tt-caret">{open ? '▾' : '▸'}</span>
        <span className="tt-names">
          {[...new Set(tools.map((t) => t.name))].join(' · ')}
        </span>
        <span className="spacer" />
        <span className="tt-count">
          {running ? 'working…' : `${tools.length} step${tools.length === 1 ? '' : 's'}`}
        </span>
      </button>
      {open && (
        <div className="tt-body">
          {tools.map((t, i) => (
            <div className="tt-row" key={i} data-ok={t.ok !== false}>
              <code>{t.name}</code>
              <span className="tt-args">{summariseArgs(t.args)}</span>
              <span className="spacer" />
              <span className="tt-result">
                {t.running ? '…' : t.summary || (t.ok ? 'ok' : 'failed')}
              </span>
              {t.duration_ms != null && !t.running && (
                <span className="tt-ms">{t.duration_ms}ms</span>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

function summariseArgs(args: Record<string, unknown>): string {
  const parts = Object.entries(args || {}).map(
    ([k, v]) => `${k}=${String(v).slice(0, 40)}`,
  )
  return parts.join(' ').slice(0, 90)
}

export function Assistant({ runId, seed, onSeedConsumed }: {
  runId: string | null
  seed?: { text: string; apiName?: string } | null
  onSeedConsumed?: () => void
}) {
  const [threads, setThreads] = useState<Thread[]>([])
  const [threadId, setThreadId] = useState<string | null>(null)
  const [messages, setMessages] = useState<Message[]>([])
  const [skills, setSkills] = useState<Skill[]>([])
  const [input, setInput] = useState('')
  const [live, setLive] = useState<{ text: string; tools: LiveTool[] } | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [tokens, setTokens] = useState<{ in: number; out: number }>({ in: 0, out: 0 })

  const closerRef = useRef<(() => void) | null>(null)
  const bottomRef = useRef<HTMLDivElement>(null)

  useEffect(() => { chatApi.skills().then(setSkills).catch(() => {}) }, [])
  useEffect(() => { chatApi.threads().then(setThreads).catch(() => {}) }, [])

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ block: 'end' })
  }, [messages.length, live?.text, live?.tools.length])

  const attach = useCallback((id: string) => {
    closerRef.current?.()
    setLive({ text: '', tools: [] })
    setBusy(true)
    closerRef.current = watchTurn(id, {
      onText: (t) => setLive((l) => ({ ...(l ?? { tools: [] }), text: ((l?.text ?? '') + '\n\n' + t).trim(), tools: l?.tools ?? [] })),
      onToolStart: (name, args) =>
        setLive((l) => ({ text: l?.text ?? '', tools: [...(l?.tools ?? []), { name, args, running: true }] })),
      onToolEnd: (name, ok, summary, ms) =>
        setLive((l) => {
          const tools = [...(l?.tools ?? [])]
          // Close the earliest still-running call of this name: within a turn
          // the same tool can be invoked more than once concurrently.
          const i = tools.findIndex((t) => t.name === name && t.running)
          if (i >= 0) tools[i] = { ...tools[i], running: false, ok, summary, duration_ms: ms }
          return { text: l?.text ?? '', tools }
        }),
      onFinished: (_stop, inTok, outTok) =>
        setTokens((t) => ({ in: t.in + inTok, out: t.out + outTok })),
      onError: (m) => setError(m),
      onEnd: () => {
        setBusy(false)
        setLive(null)
        // The durable record is the source of truth; the live buffer was only
        // ever a preview of it.
        chatApi.thread(id).then((d) => setMessages(d.messages)).catch(() => {})
      },
    })
  }, [])

  const open = useCallback(async (id: string) => {
    closerRef.current?.()
    setThreadId(id)
    setError(null)
    const d = await chatApi.thread(id)
    setMessages(d.messages)
    if (d.running) attach(id)
    else { setBusy(false); setLive(null) }
  }, [attach])

  const send = useCallback(async (text: string) => {
    const body = text.trim()
    if (!body || busy) return
    setError(null)
    setInput('')
    try {
      let id = threadId
      if (!id) {
        const t = await chatApi.create(runId, body.slice(0, 60))
        id = t.id
        setThreadId(id)
        setThreads((prev) => [t, ...prev])
      }
      // Optimistic: the backend has stored it, but re-fetching before the
      // stream starts would make the composer feel laggy.
      setMessages((m) => [...m, {
        id: -Date.now(), seq: m.length + 1, role: 'user', content: body,
        input_tokens: null, output_tokens: null, stop_reason: null,
        created_at: new Date().toISOString(), tool_calls: [],
      }])
      await chatApi.send(id, body, runId ? { run_id: runId } : undefined)
      attach(id)
    } catch (e) {
      setError(String(e))
      setBusy(false)
    }
  }, [busy, threadId, runId, attach])

  useEffect(() => {
    if (!seed) return
    setThreadId(null)
    setMessages([])
    send(seed.text)
    onSeedConsumed?.()
  }, [seed])   // eslint-disable-line react-hooks/exhaustive-deps

  const stop = useCallback(async () => {
    if (threadId) await chatApi.cancel(threadId).catch(() => {})
  }, [threadId])

  const slashMatches = useMemo(() => {
    if (!input.startsWith('/')) return []
    const q = input.slice(1).split(' ')[0].toLowerCase()
    return skills.filter((s) => s.name.startsWith(q))
  }, [input, skills])

  return (
    <div className="chat">
      <div className="chat-side">
        <button className="primary full" onClick={() => {
          closerRef.current?.(); setThreadId(null); setMessages([])
          setLive(null); setBusy(false); setError(null)
        }}>
          New conversation
        </button>
        <div className="thread-list">
          {threads.map((t) => (
            <button key={t.id} className="thread-item"
                    data-active={t.id === threadId}
                    onClick={() => open(t.id)}>
              <span className="ti-title">{t.title}</span>
              <span className="ti-meta">{t.message_count ?? 0} messages</span>
            </button>
          ))}
          {!threads.length && <p className="ins-empty">No conversations yet.</p>}
        </div>
      </div>

      <div className="chat-main">
        <div className="chat-thread">
          {!messages.length && !live && (
            <div className="chat-empty">
              <h2>Ask about this org</h2>
              <p className="sub">
                The agent reads the analysis database, greps the retrieved
                metadata, and cites what it finds. It cannot change a verdict.
              </p>
              <div className="suggestions">
                {SUGGESTIONS.map((s) => (
                  <button key={s} className="ghost" onClick={() => send(s)}>{s}</button>
                ))}
              </div>
            </div>
          )}

          {messages.map((m) => (
            <div key={m.id} className={`msg ${m.role}`}>
              {m.role === 'assistant' && m.tool_calls.length > 0 && (
                <ToolTrace tools={m.tool_calls.map((c) => ({
                  name: c.tool_name, args: c.args, ok: c.ok,
                  summary: summariseStored(c.result), duration_ms: c.duration_ms ?? 0,
                  running: false,
                }))} />
              )}
              <div className="msg-body"><Markdown text={m.content} /></div>
            </div>
          ))}

          {live && (
            <div className="msg assistant">
              <ToolTrace tools={live.tools} />
              {live.text
                ? <div className="msg-body"><Markdown text={live.text} /></div>
                : <div className="thinking">Thinking…</div>}
            </div>
          )}

          {error && <div className="chat-error">{error}</div>}
          <div ref={bottomRef} />
        </div>

        <div className="composer">
          {slashMatches.length > 0 && (
            <div className="slash-menu">
              {slashMatches.map((s) => (
                <button key={s.name} onClick={() => setInput(`/${s.name} `)}>
                  <code>/{s.name}</code>
                  <span>{s.when}</span>
                </button>
              ))}
            </div>
          )}
          <div className="composer-row">
            <textarea
              value={input}
              placeholder={busy ? 'Working…' : 'Ask a question, or type / for a command'}
              disabled={busy}
              rows={1}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send(input) }
              }}
            />
            {busy
              ? <button className="ghost" onClick={stop}>Stop</button>
              : <button className="primary" onClick={() => send(input)}
                        disabled={!input.trim()}>Send</button>}
          </div>
          <div className="composer-meta">
            {runId && <span className="ctx-chip">run {runId.slice(0, 8)}</span>}
            <span className="spacer" />
            {(tokens.in > 0 || tokens.out > 0) && (
              <span className="tok">
                {(tokens.in + tokens.out).toLocaleString()} tokens
              </span>
            )}
          </div>
        </div>
      </div>
    </div>
  )
}

function summariseStored(result: Record<string, unknown>): string {
  if (!result) return ''
  if (result.error) return String(result.error).slice(0, 120)
  for (const k of ['match_count', 'count', 'row_count', 'total', 'changed']) {
    if (k in result) return `${result[k]} ${k.replace(/_/g, ' ')}`
  }
  if (result.verdicts && typeof result.verdicts === 'object') {
    return Object.entries(result.verdicts as Record<string, number>)
      .map(([k, v]) => `${k} ${v}`).join(', ')
  }
  return 'ok'
}
