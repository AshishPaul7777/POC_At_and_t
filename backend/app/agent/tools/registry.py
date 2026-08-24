"""The one place a tool is defined.

Two adapters read this registry and neither owns it: the chat agent binds these
schemas onto the LangChain model, and ``mcp_server.py`` re-exports the same
functions over MCP. Writing a tool twice is how the two surfaces drift apart, so
they share a single definition or they share nothing.

Two invariants are enforced here rather than trusted to prompt wording:

* ``write=True`` marks a tool that mutates. Exactly one exists
  (``propose_finding``), it writes to ``agent_finding`` only, and MCP never
  exposes it -- accepting a proposal requires the human gate in the app.
* A failing tool returns a structured error instead of raising. An agent that
  loses its loop to a stack trace cannot recover or explain itself; one that is
  handed ``{"error": ...}`` can say what went wrong and try something else.
"""

from __future__ import annotations

import inspect
import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

import structlog

log = structlog.get_logger()

#: Tool results are fed back into the model as text. A single pathological row
#: can otherwise swallow the context window, so every result is truncated and
#: the truncation is announced -- silently dropping data would let the model
#: reason confidently from a partial answer.
#:
#: Sized from a measured failure, not a guess. At 24k this was ~6k tokens per
#: result, and because the whole history is resent on every iteration the input
#: cost grew quadratically: one 21-tool turn billed 167,472 input tokens and
#: exhausted a 100k/window gateway quota on its own.
MAX_RESULT_CHARS = 6_000


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    schema: dict[str, Any]
    fn: Callable[..., Awaitable[Any]]
    write: bool = False
    tags: tuple[str, ...] = field(default_factory=tuple)


REGISTRY: dict[str, Tool] = {}


def tool(name: str, description: str, schema: dict[str, Any], *,
         write: bool = False, tags: tuple[str, ...] = ()) -> Callable:
    """Register an async function as an agent tool."""

    def deco(fn: Callable[..., Awaitable[Any]]) -> Callable[..., Awaitable[Any]]:
        if not inspect.iscoroutinefunction(fn):
            raise TypeError(f"tool {name} must be async")
        if name in REGISTRY:
            raise ValueError(f"duplicate tool name: {name}")
        REGISTRY[name] = Tool(name=name, description=description, schema=schema,
                              fn=fn, write=write, tags=tags)
        return fn

    return deco


def obj(**properties: dict[str, Any]) -> dict[str, Any]:
    """Shorthand for an all-optional object schema."""
    return {"type": "object", "properties": properties, "required": []}


def required(schema: dict[str, Any], *names: str) -> dict[str, Any]:
    return {**schema, "required": list(names)}


# -- adapters -----------------------------------------------------------------

def all_tools(*, include_write: bool = True) -> list[Tool]:
    return [t for t in REGISTRY.values() if include_write or not t.write]


def anthropic_schemas(*, include_write: bool = True) -> list[dict[str, Any]]:
    """Shape accepted by ``model.bind_tools`` for the Anthropic provider."""
    return [
        {"name": t.name, "description": t.description, "input_schema": t.schema}
        for t in all_tools(include_write=include_write)
    ]


# -- invocation ---------------------------------------------------------------

async def call(name: str, args: dict[str, Any] | None = None) -> dict[str, Any]:
    """Execute a tool by name. Never raises."""
    t = REGISTRY.get(name)
    if t is None:
        return {"error": f"no such tool: {name}",
                "available": sorted(REGISTRY)}
    try:
        result = await t.fn(**(args or {}))
    except TypeError as e:
        # Wrong arguments is a prompt-level mistake the model can correct, so
        # hand back the schema rather than a bare message.
        log.info("tool_bad_args", tool=name, detail=str(e)[:200])
        return {"error": f"bad arguments: {e}", "schema": t.schema}
    except Exception as e:
        log.warning("tool_failed", tool=name, error=str(e)[:300])
        return {"error": f"{type(e).__name__}: {e}"}
    return {"ok": True, "result": result}


def render(result: dict[str, Any]) -> str:
    """Serialise a tool result for the model, bounded in size."""
    text = json.dumps(result, default=str, ensure_ascii=False)
    if len(text) <= MAX_RESULT_CHARS:
        return text
    return (text[:MAX_RESULT_CHARS]
            + f'\n... [truncated at {MAX_RESULT_CHARS} chars; '
              'narrow the query to see the rest]')
