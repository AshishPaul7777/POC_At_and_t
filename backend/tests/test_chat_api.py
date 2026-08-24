"""Chat API: persistence, streaming, reconnect, cancellation.

Uses the session layer directly rather than an HTTP client, because the property
worth testing is that a turn is durable and re-attachable -- which lives in
``app.agent.session``, not in the routing.

Every test here talks to the real gateway, so they are skipped when the LLM is
switched off. That is deliberate: mocking the model would test the mock, and the
failure modes that actually bite (tool schemas the gateway rejects, replies with
no tool calls) only appear against the real thing.
"""

from __future__ import annotations

import asyncio

import pytest

from app.agent import session as chat
from app.agent.skills import BY_NAME, expand
from app.config import get_settings


@pytest.fixture
async def thread():
    t = await chat.create_thread(title="test thread")
    return t["id"]


def _needs_llm():
    if not get_settings().llm_enabled:
        pytest.skip("LLM_ENABLED=false")


async def _drain(thread_id: str, timeout: float = 300.0) -> list[dict]:
    """Follow a turn to completion the way the SSE endpoint does."""
    turn = chat.live(thread_id)
    assert turn is not None, "no live turn"
    q = turn.subscribe()
    events: list[dict] = []
    try:
        async with asyncio.timeout(timeout):
            while True:
                ev = await q.get()
                events.append(ev)
                if ev["type"] in ("stream.end",):
                    return events
    finally:
        turn.unsubscribe(q)


# -- skills -------------------------------------------------------------------

def test_slash_command_expands_to_its_procedure():
    out = expand("/explain")
    assert out == BY_NAME["explain"].prompt


def test_slash_command_keeps_its_argument():
    out = expand("/explain Goal.OrigGoalId__c")
    assert "Goal.OrigGoalId__c" in out and BY_NAME["explain"].prompt in out


def test_unknown_slash_command_is_left_alone():
    assert expand("/nope do a thing") == "/nope do a thing"


def test_plain_text_is_untouched():
    assert expand("why is this unused?") == "why is this unused?"


# -- threads ------------------------------------------------------------------

async def test_thread_round_trips(thread):
    data = await chat.get_thread(thread)
    assert data["thread"]["id"] == thread
    assert data["messages"] == [] and data["running"] is False


async def test_missing_thread_returns_none():
    assert await chat.get_thread("00000000-0000-0000-0000-000000000000") is None


# -- turns --------------------------------------------------------------------

async def test_turn_persists_message_and_tool_trace(thread):
    _needs_llm()
    await chat.start_turn(thread, "How many components are UNUSED in the latest run?")
    events = await _drain(thread)

    kinds = [e["type"] for e in events]
    assert "turn.finished" in kinds

    data = await chat.get_thread(thread)
    roles = [m["role"] for m in data["messages"]]
    assert roles == ["user", "assistant"], roles

    reply = data["messages"][1]
    assert reply["content"].strip(), "assistant replied with nothing"
    # The question cannot be answered without consulting the database, so a
    # reply with no tool calls means the model answered from thin air.
    assert reply["tool_calls"], "expected at least one tool call"
    assert reply["output_tokens"], "token usage was not recorded"


async def test_user_message_is_stored_verbatim_not_expanded(thread):
    """The transcript must show what the user typed, not the injected prompt.

    Asserted immediately: the user message is persisted synchronously by
    start_turn, so there is no need to wait out a full agent run to check it.
    """
    _needs_llm()
    await chat.start_turn(thread, "/coverage")
    data = await chat.get_thread(thread)
    assert data["messages"][0]["content"] == "/coverage"
    assert BY_NAME["coverage"].prompt not in data["messages"][0]["content"]

    chat.cancel_turn(thread)
    await _drain(thread)


async def test_second_turn_is_refused_while_one_is_running(thread):
    _needs_llm()
    await chat.start_turn(thread, "List the runs.")
    with pytest.raises(ValueError):
        await chat.start_turn(thread, "And again.")
    await _drain(thread)


async def test_late_subscriber_receives_the_whole_trace(thread):
    """A browser that reloads mid-turn must see what it missed, not just the tail."""
    _needs_llm()
    await chat.start_turn(thread, "Summarise the latest run.")

    # Wait for real activity rather than guessing at a duration: the first
    # model round trip can take several seconds, and a fixed sleep either
    # flakes or wastes time.
    turn = chat.live(thread)
    assert turn is not None
    async with asyncio.timeout(90):
        while not turn.events:
            await asyncio.sleep(0.1)
    already = len(turn.events)

    q = turn.subscribe()                     # attaches late
    replayed = [q.get_nowait() for _ in range(already)]
    assert [e["type"] for e in replayed] == [e["type"] for e in turn.events[:already]]
    turn.unsubscribe(q)
    await _drain(thread)


async def test_cancel_stops_the_turn(thread):
    _needs_llm()
    await chat.start_turn(thread, "/triage")
    await asyncio.sleep(1.5)
    assert chat.cancel_turn(thread) is True
    await _drain(thread)
    assert chat.live(thread).done is True


async def test_cancel_on_an_idle_thread_is_a_no_op(thread):
    assert chat.cancel_turn(thread) is False


# -- triage pacing -------------------------------------------------------------

def test_triage_investigates_one_component_then_stops():
    """The default must not burn the whole queue -- and the whole quota."""
    out = expand("/triage")
    assert "ONE component" in out
    assert "STOP and wait" in out


def test_triage_all_is_a_separate_opt_in():
    out = expand("/triage-all")
    assert "EVERY component" in out and "STOP and wait" not in out


def test_triage_all_spelled_with_a_space():
    """'/triage all' is how a person types it; it must not mean one."""
    assert expand("/triage all") == expand("/triage-all")
    assert expand("/triage everything") == expand("/triage-all")


def test_triage_with_other_arguments_still_means_one():
    out = expand("/triage Report_View__c")
    assert "ONE component" in out and "Report_View__c" in out
