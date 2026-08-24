/**
 * Run event stream.
 *
 * Deliberately framework-free: no React import anywhere in this file. Events
 * land in a module-level ring buffer and a rAF loop bumps a single version
 * counter, so React re-renders at a bounded rate no matter how fast events
 * arrive. A long run emits thousands; letting each one become a state update
 * would make the UI unusable exactly when it matters most.
 *
 * Keeping the ordering, replay and reconnect logic here also makes it testable
 * without a DOM.
 */

export interface RunEvent {
  seq: number
  ts: string | null
  etype: string
  stage: string | null
  payload: Record<string, any>
}

const CAPACITY = 20_000

class Ring {
  private buf: (RunEvent | undefined)[] = new Array(CAPACITY)
  private head = 0
  size = 0
  highWater = 0

  push(e: RunEvent): boolean {
    // Duplicates are harmless and expected: a browser reconnect replays from
    // Last-Event-ID, which can overlap what we already hold.
    if (e.seq <= this.highWater) return false
    this.buf[this.head] = e
    this.head = (this.head + 1) % CAPACITY
    if (this.size < CAPACITY) this.size++
    this.highWater = e.seq
    return true
  }

  toArray(): RunEvent[] {
    const out: RunEvent[] = []
    const start = this.size < CAPACITY ? 0 : this.head
    for (let i = 0; i < this.size; i++) {
      const e = this.buf[(start + i) % CAPACITY]
      if (e) out.push(e)
    }
    return out
  }

  clear() {
    this.buf = new Array(CAPACITY)
    this.head = 0
    this.size = 0
    this.highWater = 0
  }
}

export type ConnState = 'idle' | 'connecting' | 'live' | 'reconnecting' | 'closed' | 'error'

interface State {
  version: number
  conn: ConnState
  runId: string | null
  attempt: number
  gap: boolean
}

const ring = new Ring()
const state: State = { version: 0, conn: 'idle', runId: null, attempt: 0, gap: false }
const listeners = new Set<() => void>()

let es: EventSource | null = null
let dirty = false
let rafHandle = 0
let retryTimer = 0

function notify() {
  state.version++
  for (const l of listeners) l()
}

function scheduleFlush() {
  if (dirty) return
  dirty = true
  rafHandle = requestAnimationFrame(() => {
    dirty = false
    notify()
  })
}

export const stream = {
  subscribe(cb: () => void) {
    listeners.add(cb)
    return () => listeners.delete(cb)
  },
  getSnapshot() {
    return state.version
  },
  events(): RunEvent[] {
    return ring.toArray()
  },
  conn(): ConnState {
    return state.conn
  },
  /** Which run the buffer currently holds, so callers can avoid a needless
   *  reopen -- open() clears the ring, which would discard a live log. */
  runId(): string | null {
    return state.runId
  },
  attempt(): number {
    return state.attempt
  },
  hasGap(): boolean {
    return state.gap
  },
  highWater(): number {
    return ring.highWater
  },

  /** Connect, replaying anything after `fromSeq`. */
  open(runId: string, fromSeq = 0) {
    stream.close()
    ring.clear()
    state.runId = runId
    state.conn = 'connecting'
    state.attempt = 0
    state.gap = false
    notify()
    connect(runId, fromSeq)
  },

  close() {
    if (retryTimer) { clearTimeout(retryTimer); retryTimer = 0 }
    if (rafHandle) { cancelAnimationFrame(rafHandle); rafHandle = 0; dirty = false }
    if (es) { es.close(); es = null }
    if (state.conn !== 'idle') { state.conn = 'closed'; notify() }
  },
}

function connect(runId: string, fromSeq: number) {
  // Native EventSource is enough here: the browser handles reconnect and sends
  // Last-Event-ID automatically, and the endpoint needs no auth header.
  es = new EventSource(`/api/runs/${runId}/events?from_seq=${fromSeq}`)

  es.onopen = () => {
    state.conn = 'live'
    state.attempt = 0
    notify()
  }

  es.onmessage = (m) => handle(m)
  // Named events do not reach onmessage, so each type is bound explicitly.
  for (const t of [
    'run.started', 'run.finished', 'run.cancel_requested',
    'stage.started', 'stage.finished', 'stage.failed', 'stage.skipped',
    'stage.note', 'stage.progress',
    'collector.started', 'collector.finished',
    'budget.tick', 'coverage.caveat', 'verdicts',
    'rehearsal.blocked', 'report.ready', 'heartbeat',
  ]) {
    es.addEventListener(t, (e) => handle(e as MessageEvent, t))
  }

  es.onerror = () => {
    if (!es) return
    if (es.readyState === EventSource.CLOSED) {
      state.conn = 'reconnecting'
      state.attempt++
      notify()
      es.close()
      es = null
      // Backoff with a ceiling. The browser would retry on its own, but only
      // after a CLOSED transition, so this covers the terminal case too.
      const delay = Math.min(15_000, 1000 * 2 ** Math.min(state.attempt, 4))
      retryTimer = window.setTimeout(
        () => connect(runId, ring.highWater),
        delay,
      ) as unknown as number
    } else {
      state.conn = 'reconnecting'
      notify()
    }
  }
}

function handle(m: MessageEvent, etype?: string) {
  if (etype === 'heartbeat') return
  let parsed: any
  try {
    parsed = JSON.parse(m.data)
  } catch {
    return
  }
  const seq = Number(parsed.seq ?? m.lastEventId ?? 0)
  const ev: RunEvent = {
    seq,
    ts: parsed.ts ?? null,
    etype: etype ?? parsed.etype ?? 'message',
    stage: parsed.stage ?? null,
    payload: parsed.payload ?? {},
  }
  // A jump larger than one means the server could not serve part of the range.
  // Surfaced rather than hidden: a silent hole in the log is worse than a
  // visible one, because it looks like nothing happened.
  if (seq > 0 && ring.highWater > 0 && seq > ring.highWater + 1) state.gap = true
  if (ring.push(ev)) scheduleFlush()
  if (ev.etype === 'run.finished') {
    state.conn = 'closed'
    if (es) { es.close(); es = null }
    notify()
  }
}
