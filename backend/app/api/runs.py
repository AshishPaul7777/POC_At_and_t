"""Run control and live event streaming.

SSE rather than WebSocket: the data flow is strictly server→client, control
actions are ordinary POSTs with real HTTP semantics, and `Last-Event-ID` gives
replay-from-sequence for free rather than as a hand-rolled resume handshake.
"""

from __future__ import annotations

import asyncio
import json

import structlog
from fastapi import APIRouter, HTTPException, Query, Request
from sse_starlette.sse import EventSourceResponse

from app.orchestration import events as ev
from app.orchestration.runner import active_runs, start_run
from app.salesforce.connections import load_registry

log = structlog.get_logger()
router = APIRouter(prefix="/api", tags=["runs"])

HEARTBEAT_S = 15


@router.get("/orgs")
async def orgs() -> list[dict]:
    """Configured orgs. Credentials are never returned."""
    reg = load_registry()
    return [{"alias": c.alias, "instance_url": c.instance_url,
             "api_version": c.api_version,
             "sandbox_like": c.is_sandbox_like} for c in reg.all()]


@router.post("/runs")
async def create(payload: dict | None = None) -> dict:
    """Start a run. Returns immediately; watch progress on the event stream."""
    body = payload or {}
    alias = body.get("alias")
    config = dict(body.get("config") or {})
    # Clamp UI-supplied recency window (days). Used by C50 temporal collector.
    if "recent_change_days" in config:
        try:
            days = int(config["recent_change_days"])
        except (TypeError, ValueError) as e:
            raise HTTPException(400, "recent_change_days must be an integer") from e
        if not 1 <= days <= 3650:
            raise HTTPException(400, "recent_change_days must be between 1 and 3650")
        config["recent_change_days"] = days
    if active_runs():
        # One run at a time, matching the DB's one_active_run_per_org index.
        # Two workers on one org would race on the same tables.
        rid = next(iter(active_runs()))
        raise HTTPException(409, f"a run is already in progress: {rid}")
    try:
        run_id = await start_run(alias, config=config or None)
    except KeyError as e:
        raise HTTPException(400, str(e)) from e
    except Exception as e:
        raise HTTPException(500, f"could not start run: {e}") from e
    return {"run_id": run_id, "stream": f"/api/runs/{run_id}/events"}


@router.get("/runs/active")
async def active() -> dict:
    """The run currently executing, if any.

    Lets a browser reconnect after a reload or a close/reopen without having to
    remember a run id. The work happens in the backend and its progress is
    durable in Postgres, so a client is only ever a viewer — closing the tab
    never affects the run.
    """
    from sqlalchemy import text as _t

    from app.db.session import session_scope

    live = list(active_runs())
    if live:
        snap = await ev.snapshot(live[0])
        return {"run_id": live[0], "active": True, "snapshot": snap}

    # No in-process run. A row still marked RUNNING means a previous server
    # process died mid-run: report it as stale rather than pretending it is live,
    # so the UI can say so instead of waiting forever for events.
    async with session_scope() as s:
        row = (await s.execute(_t("""
            SELECT id::text AS id, state::text AS state FROM runs
             WHERE state IN ('RUNNING','QUEUED','PAUSED','AWAITING_BUDGET')
             ORDER BY created_at DESC LIMIT 1
        """))).mappings().first()
    if row:
        return {"run_id": row["id"], "active": False, "stale": True,
                "note": "this run was interrupted; no worker is executing it"}
    return {"run_id": None, "active": False}


@router.post("/runs/{run_id}/cancel")
async def cancel(run_id: str) -> dict:
    orch = active_runs().get(run_id)
    if not orch:
        raise HTTPException(404, "run is not active")
    orch.cancel()
    # Every viewer sees this as a first-class event rather than a silent status
    # flip, because runs are shared and a status changing under someone reads
    # as a bug.
    orch.bus.emit("run.cancel_requested", None,
                  note="cancellation requested; the current stage finishes first")
    return {"run_id": run_id, "cancelling": True}


@router.get("/runs/{run_id}/snapshot")
async def snapshot(run_id: str) -> dict:
    snap = await ev.snapshot(run_id)
    if not snap:
        raise HTTPException(404, "no such run")
    snap["active"] = run_id in active_runs()
    return snap


@router.get("/runs/{run_id}/events")
async def stream(request: Request, run_id: str, from_seq: int = Query(0)) -> EventSourceResponse:
    """Replay from ``from_seq``, then tail live.

    The client fetches the snapshot first and passes its ``as_of_seq`` here, so
    it never replays history it already has. `Last-Event-ID` is honoured for
    automatic browser reconnects.
    """
    last_header = request.headers.get("last-event-id")
    start_seq = int(last_header) if (last_header or "").isdigit() else from_seq

    async def gen():
        cursor = start_seq
        idle = 0.0
        while True:
            if await request.is_disconnected():
                return
            batch = await ev.read_events(run_id, cursor, limit=500)
            if batch:
                idle = 0.0
                for e in batch:
                    cursor = e["seq"]
                    yield {
                        "id": str(e["seq"]),
                        "event": e["etype"],
                        "data": json.dumps({
                            "seq": e["seq"],
                            "ts": e["ts"].isoformat() if e["ts"] else None,
                            "stage": e["stage_key"],
                            "payload": e["payload"],
                        }, default=str),
                    }
                    if e["etype"] == "run.finished":
                        # Terminal. Closing here rather than polling forever
                        # avoids a stream that looks alive after the work ended.
                        return
                continue

            await asyncio.sleep(0.35)
            idle += 0.35
            if idle >= HEARTBEAT_S:
                idle = 0.0
                # Comment frame: keeps proxies from idling the connection out,
                # and is invisible to EventSource.
                yield {"event": "heartbeat", "data": "{}"}
                if run_id not in active_runs():
                    snap = await ev.snapshot(run_id)
                    state = (snap.get("run") or {}).get("state")
                    if state in ("SUCCEEDED", "DEGRADED", "FAILED", "CANCELLED"):
                        yield {"event": "run.finished",
                               "data": json.dumps({"payload": {"state": state},
                                                   "stage": None})}
                        return

    return EventSourceResponse(gen(), ping=HEARTBEAT_S)
