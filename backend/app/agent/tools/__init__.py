"""Agent tool registry.

Importing this package registers every tool. Both adapters -- the in-process
chat agent and the MCP server -- import it for that side effect, so neither can
accidentally see a different tool set from the other.
"""

from app.agent.tools import analysis, org, workspace  # noqa: F401
from app.agent.tools.registry import (  # noqa: F401
    REGISTRY,
    Tool,
    all_tools,
    anthropic_schemas,
    call,
    render,
)

__all__ = ["REGISTRY", "Tool", "all_tools", "anthropic_schemas", "call", "render"]
