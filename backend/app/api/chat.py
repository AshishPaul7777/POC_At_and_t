"""Chat agent endpoints.

The stream deliberately mirrors the run stream in ``runs.py``: named SSE events,
a heartbeat, and replay of anything the client missed. That lets the frontend
reuse the EventSource client it already has instead of growing a second one.

Sending a message returns immediately. The reply runs in the backend and the
browser watches, so a reload rejoins rather than restarting -- the same contract
the pipeline already offers.
"""

from __future__ import annotations

import asyncio
import json

import structlog
from fastapi import APIRouter, HTTPException, Request
from sse_starlette.sse import EventSourceResponse

from app.agent import session as chat
from app.agent.skills import SKILLS

log = structlog.get_logger()
router = APIRouter(prefix="/api/chat", tags=["chat"])

HEARTBEAT_S = 15


@router.get("/skills")
async def skills() -> list[dict]:
    """Slash commands. Exposed so the composer can autocomplete them rather
    than the user having to know they exist."""
    return [{"name": s.name, "title": s.title, "when": s.when,
             "prompt": s.prompt} for s in SKILLS]


@router.post("/threads")
async def create_thread(payload: dict | None = None) -> dict:
    body = payload or {}
    return await chat.create_thread(
        run_id=body.get("run_id"),
        title=body.get("title") or "New conversation")


@router.get("/threads")
async def list_threads(limit: int = 50) -> list[dict]:
    return await chat.list_threads(limit=limit)


@router.get("/threads/{thread_id}")
async def get_thread(thread_id: str) -> dict:
    data = await chat.get_thread(thread_id)
    if not data:
        raise HTTPException(404, "no such thread")
    return data


@router.post("/threads/{thread_id}/messages")
async def send_message(thread_id: str, payload: dict) -> dict:
    content = (payload or {}).get("content", "").strip()
    if not content:
        raise HTTPException(400, "content is required")
    if not await chat.get_thread(thread_id):
        raise HTTPException(404, "no such thread")
    try:
        await chat.start_turn(thread_id, content,
                              context=(payload or {}).get("context"))
    except ValueError as e:
        raise HTTPException(409, str(e)) from e
    return {"thread_id": thread_id, "stream": f"/api/chat/threads/{thread_id}/stream"}


@router.post("/threads/{thread_id}/cancel")
async def cancel(thread_id: str) -> dict:
    return {"cancelled": chat.cancel_turn(thread_id)}


@router.get("/threads/{thread_id}/stream")
async def stream(request: Request, thread_id: str) -> EventSourceResponse:
    """Watch the in-flight reply.

    Subscribing replays everything the turn has emitted so far, so a browser
    that arrives late -- or returns after a reload -- sees the whole trace
    rather than only what happens next.
    """
    async def gen():
        turn = chat.live(thread_id)
        if turn is None or turn.done:
            # Nothing executing. Say so and close, rather than holding a
            # connection open that will never produce anything.
            yield {"event": "stream.end", "data": json.dumps({"idle": True})}
            return

        q = turn.subscribe()
        try:
            while True:
                if await request.is_disconnected():
                    return
                try:
                    ev = await asyncio.wait_for(q.get(), timeout=HEARTBEAT_S)
                except TimeoutError:
                    yield {"event": "heartbeat", "data": "{}"}
                    continue
                etype = ev.get("type", "message")
                yield {"event": etype, "data": json.dumps(ev, default=str)}
                if etype == "stream.end":
                    return
        finally:
            turn.unsubscribe(q)

    return EventSourceResponse(gen(), ping=HEARTBEAT_S)
