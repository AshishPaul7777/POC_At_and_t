"""Run event bus.

Every state change in a run becomes a durable, ordered event. That ordering is
the whole difficulty, and it is why events funnel through a single writer.

``BIGSERIAL`` under concurrency produces gaps and out-of-order commits: writer A
takes seq 100, writer B takes 101 and commits first, and a reader polling
``seq > last_seen`` sees 101, advances past it, and **permanently misses 100**.
Late-joiner replay would then be silently lossy — the worst kind of broken, since
the UI looks fine and just omits things.

So one coroutine per run drains a queue and allocates each sequence number in the
same transaction as the insert (see ``append_event`` in schema.sql). Serial by
construction; commit order equals sequence order.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from dataclasses import dataclass, field

import structlog
from sqlalchemy import text

from app.db.session import session_scope

log = structlog.get_logger()

#: Progress events are coalesced to this rate per stage. A shard loop can emit
#: thousands per second; nobody can read that, and storing it would bury the
#: lifecycle events that actually matter.
PROGRESS_MIN_INTERVAL_S = 0.4


@dataclass
class Event:
    etype: str
    stage_key: str | None = None
    payload: dict = field(default_factory=dict)


class EventBus:
    """Per-run event writer. Create one, ``start()``, then ``emit()`` freely."""

    def __init__(self, run_id: str) -> None:
        self.run_id = run_id
        self._q: asyncio.Queue[Event | None] = asyncio.Queue(maxsize=2000)
        self._task: asyncio.Task | None = None
        self._last_progress: dict[str, float] = {}

    async def start(self) -> None:
        self._task = asyncio.create_task(self._drain(), name=f"events:{self.run_id}")

    async def stop(self) -> None:
        if not self._task:
            return
        await self._q.put(None)
        with contextlib.suppress(asyncio.CancelledError):
            await self._task

    def emit(self, etype: str, stage_key: str | None = None, **payload) -> None:
        """Fire-and-forget. Never blocks the pipeline and never raises.

        A full queue drops the event rather than stalling the analysis:
        telemetry must never be able to hold up real work, and the domain tables
        remain the source of truth for anything that matters.
        """
        ev = Event(etype, stage_key, payload)
        try:
            self._q.put_nowait(ev)
        except asyncio.QueueFull:
            log.warning("event_dropped", etype=etype, stage=stage_key)

    def progress(self, stage_key: str, done: int, total: int | None = None,
                 **extra) -> None:
        """Rate-limited progress. Safe to call in a tight loop."""
        import time
        now = time.monotonic()
        last = self._last_progress.get(stage_key, 0.0)
        final = total is not None and done >= total
        if not final and now - last < PROGRESS_MIN_INTERVAL_S:
            return
        self._last_progress[stage_key] = now
        self.emit("stage.progress", stage_key, done=done, total=total, **extra)

    async def _drain(self) -> None:
        while True:
            ev = await self._q.get()
            if ev is None:
                return
            try:
                async with session_scope() as s:
                    await s.execute(
                        text("SELECT append_event(:r, :t, :sk, CAST(:p AS jsonb))"),
                        {"r": self.run_id, "t": ev.etype, "sk": ev.stage_key,
                         "p": json.dumps(ev.payload, default=str)})
            except Exception:
                # A failed event write must not kill the run.
                log.exception("event_write_failed", etype=ev.etype)


async def read_events(run_id: str, from_seq: int = 0, limit: int = 2000) -> list[dict]:
    async with session_scope() as s:
        rows = (await s.execute(text("""
            SELECT seq, ts, etype, stage_key, payload FROM events
             WHERE run_id = :r AND seq > :s ORDER BY seq LIMIT :lim
        """), {"r": run_id, "s": from_seq, "lim": limit})).mappings().all()
    return [dict(r) for r in rows]


async def snapshot(run_id: str) -> dict:
    """Full materialised state, plus the sequence it is current as of.

    A late joiner takes this and then tails from ``as_of_seq``. Replaying from
    zero would be unbounded and slow; snapshot-plus-delta is neither.
    """
    async with session_scope() as s:
        run = (await s.execute(text("""
            SELECT id::text, org_alias, org_id, state::text AS state, api_version,
                   run_as_username, started_at, finished_at, last_seq, config,
                   budget_plan, error
              FROM runs WHERE id = :r
        """), {"r": run_id})).mappings().first()
        if not run:
            return {}
        stages = (await s.execute(text("""
            SELECT key, title, state::text AS state, ordinal, shards_total,
                   shards_done, shards_failed, api_calls_used, skip_reason,
                   started_at, finished_at, error
              FROM stages WHERE run_id = :r ORDER BY ordinal
        """), {"r": run_id})).mappings().all()
        counters = (await s.execute(text("""
            SELECT cl.label::text AS label, count(*) n
              FROM classifications cl WHERE cl.run_id = :r GROUP BY 1
        """), {"r": run_id})).all()
        comps = await s.scalar(text(
            "SELECT count(*) FROM components WHERE run_id = :r AND in_scope"),
            {"r": run_id})
    return {
        "run": dict(run),
        "as_of_seq": run["last_seq"],
        "stages": [dict(x) for x in stages],
        "verdicts": {r.label: r.n for r in counters},
        "components": comps or 0,
    }
