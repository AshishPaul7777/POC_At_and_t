"""Thread state: persistence, live turn buffers, cancellation.

The browser is a viewer, exactly as it is for a pipeline run. A turn executes in
the backend and keeps running whether or not anyone is watching, so closing the
tab mid-investigation wastes nothing and a reload rejoins in progress.

Reconnection uses a buffer rather than a durable event log. Every event of an
in-flight turn is held in memory keyed by thread; a client that attaches late is
sent the buffer and then tails. Persisting each token would multiply writes to
buy resilience against a backend crash -- and a crashed turn is re-runnable, so
there is nothing worth protecting.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from typing import Any

import structlog
from sqlalchemy import text

from app.agent.loop import AgentTurn
from app.agent.skills import expand
from app.db.session import session_scope

log = structlog.get_logger()

#: Live turns, keyed by thread id. Absence means nothing is executing.
_LIVE: dict[str, "LiveTurn"] = {}


@dataclass
class LiveTurn:
    thread_id: str
    events: list[dict] = field(default_factory=list)
    subscribers: list[asyncio.Queue] = field(default_factory=list)
    cancelled: bool = False
    done: bool = False
    task: asyncio.Task | None = None

    def publish(self, ev: dict) -> None:
        self.events.append(ev)
        for q in list(self.subscribers):
            try:
                q.put_nowait(ev)
            except asyncio.QueueFull:
                # A stalled reader must not hold up the turn; it will catch up
                # from `events` when it reconnects.
                log.warning("chat_subscriber_slow", thread=self.thread_id)

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=1000)
        for ev in self.events:          # replay what it missed, then tail
            q.put_nowait(ev)
        self.subscribers.append(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        if q in self.subscribers:
            self.subscribers.remove(q)


def live(thread_id: str) -> LiveTurn | None:
    return _LIVE.get(thread_id)


# -- persistence --------------------------------------------------------------

async def create_thread(run_id: str | None = None,
                        title: str = "New conversation") -> dict:
    async with session_scope() as s:
        row = (await s.execute(text("""
            INSERT INTO chat_thread (run_id, title) VALUES (:r, :t)
            RETURNING id::text AS id, run_id::text AS run_id, title, state,
                      created_at, updated_at
        """), {"r": run_id, "t": title})).mappings().first()
    return dict(row)


async def list_threads(limit: int = 50) -> list[dict]:
    async with session_scope() as s:
        rows = (await s.execute(text("""
            SELECT t.id::text AS id, t.run_id::text AS run_id, t.title, t.state,
                   t.created_at, t.updated_at,
                   (SELECT count(*) FROM chat_message m WHERE m.thread_id = t.id)
                     AS message_count
              FROM chat_thread t ORDER BY t.updated_at DESC LIMIT :lim
        """), {"lim": limit})).mappings().all()
    return [dict(r) for r in rows]


async def get_thread(thread_id: str) -> dict | None:
    async with session_scope() as s:
        thread = (await s.execute(text("""
            SELECT id::text AS id, run_id::text AS run_id, title, state,
                   created_at, updated_at
              FROM chat_thread WHERE id = :t
        """), {"t": thread_id})).mappings().first()
        if not thread:
            return None
        messages = (await s.execute(text("""
            SELECT id, seq, role, content, input_tokens, output_tokens,
                   stop_reason, created_at
              FROM chat_message WHERE thread_id = :t ORDER BY seq
        """), {"t": thread_id})).mappings().all()
        calls = (await s.execute(text("""
            SELECT c.message_id, c.ordinal, c.tool_name, c.args, c.ok,
                   c.result, c.duration_ms
              FROM chat_tool_call c
              JOIN chat_message m ON m.id = c.message_id
             WHERE m.thread_id = :t ORDER BY c.message_id, c.ordinal
        """), {"t": thread_id})).mappings().all()
        findings = (await s.execute(text("""
            SELECT f.id, f.status, f.recommendation, f.rationale, f.citations,
                   c.api_name, cl.label::text AS current_verdict
              FROM agent_finding f
              JOIN components c ON c.id = f.component_id
              LEFT JOIN classifications cl
                ON cl.component_id = c.id AND cl.run_id = f.run_id
             WHERE f.thread_id = :t ORDER BY f.id
        """), {"t": thread_id})).mappings().all()

    by_msg: dict[int, list[dict]] = {}
    for c in calls:
        by_msg.setdefault(c["message_id"], []).append(dict(c))
    return {
        "thread": dict(thread),
        "messages": [{**dict(m), "tool_calls": by_msg.get(m["id"], [])}
                     for m in messages],
        "findings": [dict(f) for f in findings],
        "running": thread_id in _LIVE and not _LIVE[thread_id].done,
    }


async def _next_seq(s, thread_id: str) -> int:
    n = await s.scalar(text(
        "SELECT coalesce(max(seq), 0) + 1 FROM chat_message WHERE thread_id = :t"),
        {"t": thread_id})
    return int(n or 1)


async def add_message(thread_id: str, role: str, content: str, *,
                      input_tokens: int | None = None,
                      output_tokens: int | None = None,
                      stop_reason: str | None = None,
                      tool_calls: list | None = None) -> int:
    async with session_scope() as s:
        seq = await _next_seq(s, thread_id)
        mid = await s.scalar(text("""
            INSERT INTO chat_message (thread_id, seq, role, content,
                                      input_tokens, output_tokens, stop_reason)
            VALUES (:t, :q, :r, :c, :i, :o, :sr) RETURNING id
        """), {"t": thread_id, "q": seq, "r": role, "c": content,
               "i": input_tokens, "o": output_tokens, "sr": stop_reason})
        for i, tc in enumerate(tool_calls or []):
            await s.execute(text("""
                INSERT INTO chat_tool_call (message_id, ordinal, tool_name, args,
                                            ok, result, duration_ms)
                VALUES (:m, :i, :n, CAST(:a AS jsonb), :ok,
                        CAST(:res AS jsonb), :ms)
            """), {"m": mid, "i": i, "n": tc.name,
                   "a": json.dumps(tc.args, default=str),
                   "ok": tc.ok,
                   # A summary, not the payload: a grep can return megabytes and
                   # the point of keeping this is auditability, not caching.
                   "res": json.dumps(_summary_of(tc), default=str),
                   "ms": tc.duration_ms})
        await s.execute(text(
            "UPDATE chat_thread SET updated_at = now() WHERE id = :t"),
            {"t": thread_id})
    return int(mid)


def _summary_of(tc: Any) -> dict:
    payload = (tc.result or {}).get("result")
    if isinstance(payload, dict):
        keep = {k: v for k, v in payload.items()
                if k in ("error", "note", "count", "match_count", "row_count",
                         "total", "files_scanned", "verdicts", "run_id",
                         "truncated", "changed")}
        return keep or {"ok": tc.ok}
    if isinstance(payload, list):
        return {"rows": len(payload)}
    return {"ok": tc.ok}


async def _set_state(thread_id: str, state: str) -> None:
    async with session_scope() as s:
        await s.execute(text(
            "UPDATE chat_thread SET state = :s, updated_at = now() WHERE id = :t"),
            {"s": state, "t": thread_id})


# -- execution ----------------------------------------------------------------

async def start_turn(thread_id: str, user_message: str,
                     context: dict | None = None) -> LiveTurn:
    """Persist the user's message and run the reply as a background task."""
    existing = _LIVE.get(thread_id)
    if existing and not existing.done:
        raise ValueError("a reply is already in progress on this thread")

    # Stored verbatim so the transcript shows what the user typed; the
    # expansion is what the model receives.
    await add_message(thread_id, "user", user_message)
    await _set_state(thread_id, "RUNNING")

    turn = LiveTurn(thread_id=thread_id)
    _LIVE[thread_id] = turn

    thread = await get_thread(thread_id)
    history = [{"role": m["role"], "content": m["content"]}
               for m in (thread or {}).get("messages", []) if m["content"]]
    if history and history[-1]["role"] == "user":
        history[-1] = {"role": "user", "content": expand(user_message)}
    ctx = dict(context or {})
    ctx.setdefault("run_id", (thread or {}).get("thread", {}).get("run_id"))

    async def _go() -> None:
        agent = AgentTurn(history=history, context=ctx,
                          should_cancel=lambda: turn.cancelled)
        result = None
        try:
            async for ev in agent.run():
                if ev["type"] == "done":
                    result = ev["result"]
                    break
                turn.publish(ev)
        except Exception as e:                       # pragma: no cover
            log.exception("chat_turn_failed", thread=thread_id)
            turn.publish({"type": "error", "message": str(e)[:400]})

        if result is not None:
            await add_message(thread_id, "assistant", result.text,
                              input_tokens=result.input_tokens,
                              output_tokens=result.output_tokens,
                              stop_reason=result.stop_reason,
                              tool_calls=result.tool_calls)
            turn.publish({"type": "turn.finished",
                          "stop_reason": result.stop_reason,
                          "input_tokens": result.input_tokens,
                          "output_tokens": result.output_tokens})
        else:
            turn.publish({"type": "turn.finished", "stop_reason": "error"})

        await _set_state(thread_id, "IDLE")
        turn.done = True
        for q in list(turn.subscribers):
            q.put_nowait({"type": "stream.end"})

    turn.task = asyncio.create_task(_go(), name=f"chat:{thread_id}")
    return turn


def cancel_turn(thread_id: str) -> bool:
    t = _LIVE.get(thread_id)
    if not t or t.done:
        return False
    # Cooperative: the loop checks between iterations, so an in-flight tool call
    # finishes rather than leaving a half-written trace.
    t.cancelled = True
    return True
