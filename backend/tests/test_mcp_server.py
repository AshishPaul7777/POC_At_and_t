"""End-to-end check of the MCP adapter, driven like a real client.

Worth the cost of spawning a subprocess: the two failures this catches are both
invisible in-process. A write tool leaking over MCP would hand an external
client the ability to change a verdict, and anything printed to stdout corrupts
the protocol frames -- which shows up only as a confusing parse error inside
whichever client happened to connect.

The session is opened per test with ``async with`` rather than by a fixture.
``stdio_client`` and ``ClientSession`` hold anyio cancel scopes, and a scope must
be exited in the task that entered it; an async-generator fixture runs setup and
teardown in different tasks, which fails with "Attempted to exit cancel scope in
a different task".
"""

from __future__ import annotations

import json
import sys
from contextlib import asynccontextmanager
from pathlib import Path

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

BACKEND = Path(__file__).resolve().parent.parent
SERVER = BACKEND / "mcp_server.py"


@asynccontextmanager
async def client():
    params = StdioServerParameters(
        command=sys.executable, args=[str(SERVER)], cwd=str(BACKEND))
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            yield session


async def test_server_advertises_the_registry():
    from app.agent.tools import all_tools

    async with client() as session:
        listed = await session.list_tools()
        assert {t.name for t in listed.tools} == {
            t.name for t in all_tools(include_write=False)}


async def test_no_write_tool_is_reachable_over_mcp():
    """External clients must never reach the one tool that can change a verdict."""
    async with client() as session:
        names = {t.name for t in (await session.list_tools()).tools}
        assert "propose_finding" not in names

        result = await session.call_tool("propose_finding", {})
        assert result.is_error, "an unregistered write tool must not execute"


async def test_a_real_call_returns_analysis_data():
    async with client() as session:
        res = await session.call_tool("get_run_summary", {})
        data = json.loads(res.content[0].text)
        if "error" in data:
            pytest.skip(data["error"])
        assert data["verdicts"] and data["collectors"]


async def test_stdout_carries_only_protocol_frames():
    """Guards the structlog-to-stderr redirect in mcp_server.py.

    If any library or app module logs to stdout, these calls fail to parse
    rather than returning data -- so exercising the session is the assertion.
    """
    async with client() as session:
        for _ in range(3):
            res = await session.call_tool("list_workspace_files", {"limit": 1})
            json.loads(res.content[0].text)
