"""The tool-calling loop.

Invoke the model, run whatever tools it asked for, feed the results back, repeat
until it answers in prose. Everything interesting is emitted as it happens so the
browser can render a live trace rather than a spinner.

Three deliberate choices:

* **Tools run concurrently within a turn.** The model routinely asks for a grep
  and a database read together; running them in series doubles the wait for no
  benefit. Results are returned in request order regardless, because Anthropic
  requires one ``tool_result`` per ``tool_use`` and pairing them by position is
  what keeps that correct.
* **A failing tool is reported to the model, not raised.** It can then try
  something else or explain the limitation. Killing the turn on a bad argument
  would make the agent brittle in exactly the situations it exists for.
* **Bounded by iterations and by tool calls.** An agent that greps in a loop is
  a cost incident, and a cap that is hit is reported honestly rather than
  presented as a finished answer.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from typing import Any

import structlog

from app.agent import prompts
from app.agent.tools import anthropic_schemas, call as call_tool, render
from app.llm.client import LLMClient

log = structlog.get_logger()

MAX_ITERATIONS = 8
MAX_TOOL_CALLS = 14

#: Tool results older than this many iterations are replaced with a stub before
#: the next request. The whole message list is resent every iteration, so a
#: result kept in full is paid for again on each subsequent call -- the reason a
#: 21-tool turn billed 167,472 input tokens. The model has already consumed an
#: old result to produce its intermediate reasoning; re-sending the bytes buys
#: nothing.
KEEP_FULL_RESULTS_FOR = 2

#: Hard ceiling per turn. Gateways meter tokens per window (100k here), and one
#: runaway turn must not spend the whole allowance and lock out every other
#: user of the key.
MAX_INPUT_TOKENS_PER_TURN = 60_000

_SHARED: LLMClient | None = None


def shared_client() -> LLMClient:
    """One client per process.

    ``_temperature_ok`` is instance state discovered from a failed call, so a
    fresh client per turn would rediscover it -- and pay a wasted round trip --
    at the start of every conversation.
    """
    global _SHARED
    if _SHARED is None:
        _SHARED = LLMClient()
    return _SHARED


@dataclass
class ToolRecord:
    name: str
    args: dict[str, Any]
    ok: bool
    result: Any
    duration_ms: int


@dataclass
class TurnResult:
    text: str = ""
    tool_calls: list[ToolRecord] = field(default_factory=list)
    input_tokens: int = 0
    output_tokens: int = 0
    stop_reason: str = "end_turn"
    error: str | None = None


class AgentTurn:
    """One user message worth of work, streamed."""

    def __init__(self, *, history: list[dict], context: dict | None = None,
                 llm: LLMClient | None = None,
                 should_cancel: Callable[[], bool] | None = None) -> None:
        self._history = history
        self._context = context or {}
        self._llm = llm or shared_client()
        self._cancelled = should_cancel or (lambda: False)

    async def run(self) -> AsyncIterator[dict]:
        """Yield events: text, tool.started, tool.finished, done, error."""
        from langchain_core.messages import (
            AIMessage,
            HumanMessage,
            SystemMessage,
            ToolMessage,
        )

        result = TurnResult()
        system = prompts.SYSTEM + prompts.render_context(self._context)

        msgs: list[Any] = [SystemMessage(content=system)]
        for m in self._history:
            if m["role"] == "user":
                msgs.append(HumanMessage(content=m["content"]))
            else:
                msgs.append(AIMessage(content=m["content"]))

        try:
            model = self._llm.bind(anthropic_schemas())
        except Exception as e:
            log.warning("agent_model_unavailable", error=str(e)[:300])
            yield {"type": "error", "message": f"model unavailable: {e}"}
            return

        for iteration in range(MAX_ITERATIONS):
            if self._cancelled():
                result.stop_reason = "cancelled"
                break

            if result.input_tokens > MAX_INPUT_TOKENS_PER_TURN:
                result.stop_reason = "token_budget"
                yield {"type": "text",
                       "text": ("\n\n_Stopped: this turn reached its token "
                                "budget. Ask a narrower question, or continue "
                                "in a new message._")}
                break

            # Trim all but the most recent results before resending.
            tool_msgs = sum(1 for m in msgs if type(m).__name__ == "ToolMessage")
            if tool_msgs > KEEP_FULL_RESULTS_FOR:
                _compact(msgs, tool_msgs - KEEP_FULL_RESULTS_FOR)

            try:
                resp = await model.ainvoke(msgs)
            except Exception as e:
                # The gateway accepts `temperature` at construction and rejects
                # it at invoke, so this is the only place it can be discovered.
                # Repair once and rebind rather than losing the turn.
                if self._llm.is_rate_limit(e):
                    # Retrying is pointless: the window is measured in minutes.
                    # Stop cleanly so the partial answer and its tool trace are
                    # still persisted, and say when the quota returns.
                    msg = self._llm.describe_rate_limit(e)
                    log.warning("agent_rate_limited", detail=msg)
                    yield {"type": "error", "message": msg}
                    result.error, result.stop_reason = msg, "rate_limited"
                    break

                repaired = (self._llm.is_temperature_error(e)
                            and await self._llm.repair_temperature())
                if repaired:
                    try:
                        model = self._llm.bind(anthropic_schemas())
                        resp = await model.ainvoke(msgs)
                    except Exception as e2:
                        log.warning("agent_invoke_failed", error=str(e2)[:300])
                        yield {"type": "error", "message": str(e2)[:400]}
                        result.error, result.stop_reason = str(e2)[:400], "error"
                        break
                else:
                    log.warning("agent_invoke_failed", error=str(e)[:300])
                    yield {"type": "error", "message": str(e)[:400]}
                    result.error, result.stop_reason = str(e)[:400], "error"
                    break

            usage = getattr(resp, "usage_metadata", None) or {}
            result.input_tokens += usage.get("input_tokens") or 0
            result.output_tokens += usage.get("output_tokens") or 0

            text = _text_of(resp)
            if text:
                result.text += ("\n\n" if result.text else "") + text
                yield {"type": "text", "text": text}

            calls = list(getattr(resp, "tool_calls", None) or [])
            if not calls:
                result.stop_reason = "end_turn"
                break

            if len(result.tool_calls) + len(calls) > MAX_TOOL_CALLS:
                calls = calls[: max(0, MAX_TOOL_CALLS - len(result.tool_calls))]
                result.stop_reason = "tool_budget"

            for c in calls:
                yield {"type": "tool.started", "name": c["name"],
                       "args": c.get("args") or {}}

            records = await asyncio.gather(
                *(self._execute(c) for c in calls))

            msgs.append(AIMessage(content=resp.content, tool_calls=calls))
            for c, rec in zip(calls, records, strict=True):
                result.tool_calls.append(rec)
                yield {"type": "tool.finished", "name": rec.name, "ok": rec.ok,
                       "duration_ms": rec.duration_ms,
                       "summary": _summarise(rec)}
                # One tool_result per tool_use, matched by id and in order --
                # the API rejects the turn otherwise.
                msgs.append(ToolMessage(content=render(rec.result),
                                        tool_call_id=c["id"]))

            if result.stop_reason == "tool_budget":
                yield {"type": "text",
                       "text": ("\n\n_Stopped after "
                                f"{MAX_TOOL_CALLS} tool calls. Ask a narrower "
                                "question to continue._")}
                break
        else:
            result.stop_reason = "max_iterations"

        yield {"type": "done", "result": result}

    async def _execute(self, c: dict) -> ToolRecord:
        started = time.monotonic()
        out = await call_tool(c["name"], c.get("args") or {})
        ms = int((time.monotonic() - started) * 1000)
        ok = bool(out.get("ok")) and "error" not in (out.get("result") or {})
        log.info("agent_tool", tool=c["name"], ok=ok, ms=ms)
        return ToolRecord(name=c["name"], args=c.get("args") or {}, ok=ok,
                          result=out, duration_ms=ms)


def _compact(msgs: list, keep_from: int) -> None:
    """Shrink tool results older than `keep_from` in place.

    Only the *content* is replaced -- never the message itself. Anthropic
    requires one tool_result per tool_use, so dropping a ToolMessage would make
    the whole conversation invalid.
    """
    from langchain_core.messages import ToolMessage

    seen = 0
    for m in msgs:
        if not isinstance(m, ToolMessage):
            continue
        seen += 1
        if seen > keep_from:
            break
        if len(m.content) > 300:
            m.content = (m.content[:200]
                         + " ... [earlier result trimmed to save context; "
                           "call the tool again if you need it]")


def _text_of(resp: Any) -> str:
    content = getattr(resp, "content", "")
    if isinstance(content, str):
        return content.strip()
    parts = []
    for block in content or []:
        if isinstance(block, str):
            parts.append(block)
        elif isinstance(block, dict) and block.get("type") == "text":
            parts.append(block.get("text", ""))
    return "".join(parts).strip()


def _summarise(rec: ToolRecord) -> str:
    """A one-line result for the collapsed trace in the UI."""
    payload = (rec.result or {}).get("result")
    if isinstance(payload, dict):
        if err := payload.get("error"):
            return str(err)[:160]
        for key in ("match_count", "count", "row_count", "total", "changed"):
            if key in payload:
                n = payload[key]
                return f"{n} {key.replace('_', ' ')}"
        if "verdicts" in payload:
            return ", ".join(f"{k} {v}" for k, v in payload["verdicts"].items())
        if "component" in payload:
            return str(payload["component"].get("verdict") or "found")
    if isinstance(payload, list):
        return f"{len(payload)} rows"
    return "ok" if rec.ok else "failed"
