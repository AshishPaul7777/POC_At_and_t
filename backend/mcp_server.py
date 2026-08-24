"""MCP server exposing the org-cleanup analysis to any MCP client.

Second adapter over ``app/agent/tools/registry.py``. The in-app chat agent binds
the same functions onto the LangChain model; this hands them to Claude Code,
Claude Desktop, or anything else that speaks MCP. Both surfaces therefore answer
from one tool definition, and a fix to a query benefits both.

**Read-only by construction.** Tools marked ``write=True`` are filtered out here.
The only write in the system is ``propose_finding``, and accepting a proposal is
what can change a verdict -- that requires the human gate in the app, so it must
not be reachable from an external client.

Run it:

    cd backend && .venv/Scripts/python.exe mcp_server.py

It speaks stdio, so a client spawns it rather than connecting to a port. Claude
Code config:

    {
      "mcpServers": {
        "org-cleanup": {
          "command": "C:/path/to/backend/.venv/Scripts/python.exe",
          "args": ["C:/path/to/backend/mcp_server.py"]
        }
      }
    }
"""

from __future__ import annotations

import sys
from pathlib import Path

# A client spawns this file directly, so the package root is not on sys.path.
sys.path.insert(0, str(Path(__file__).resolve().parent))

import structlog  # noqa: E402

# stdout carries the MCP frames and nothing else. structlog defaults to stdout,
# so a single log line lands mid-protocol and the client rejects the frame with
# "Invalid JSON: trailing characters". Redirect to stderr before importing any
# app module, because those log on import.
structlog.configure(logger_factory=structlog.PrintLoggerFactory(file=sys.stderr))

from mcp.server import MCPServer  # noqa: E402

from app.agent.tools import all_tools  # noqa: E402

log = structlog.get_logger()

INSTRUCTIONS = """\
Tools for a Salesforce org cleanup analysis: which metadata components are
unused, and the evidence behind each verdict.

Two things to understand before relying on the answers:

1. A verdict comes from deterministic rules over collected evidence, not from a
   model. `get_component` returns the rule that fired in `rule_trace`.
2. Absence of evidence is reported explicitly and is meaningful. A collector
   that searched and found nothing, or a grep with zero matches, is the claim a
   deletion actually rests on -- it is not a failed lookup.

Prefer `grep_workspace` and the stored evidence over `soql_query`: the org was
already retrieved to disk, so searching it costs no Salesforce API budget.
"""


def build() -> MCPServer:
    server = MCPServer(
        name="org-cleanup",
        title="Salesforce Org Cleanup Analyzer",
        instructions=INSTRUCTIONS,
    )
    exposed = all_tools(include_write=False)
    for t in exposed:
        # Schemas are derived from each function's annotations, which the tool
        # definitions already carry -- so the MCP schema cannot drift from the
        # one the chat agent binds.
        server.add_tool(t.fn, name=t.name, description=t.description)
    log.info("mcp_tools_registered", count=len(exposed),
             names=[t.name for t in exposed])
    return server


if __name__ == "__main__":
    build().run(transport="stdio")
