"""Search and read the retrieved metadata on disk.

This is the agent's cheapest instrument and often its most decisive: the org was
pulled to ``WORKSPACE_DIR`` once during the run, so searching it costs zero
Salesforce API calls and can be repeated freely. A NEEDS_REVIEW case is usually
settled by finding -- or failing to find -- a name in these files.

Every path is confined to the workspace root. The confinement is resolved with
``Path.resolve()`` and checked with ``is_relative_to``, so ``../`` and symlinks
cannot escape: an agent that can read arbitrary files on the host is a very
different security proposition from one that can read a metadata dump.
"""

from __future__ import annotations

import asyncio
import re
from pathlib import Path
from typing import Any

import structlog

from app.agent.tools.registry import obj, required, tool
from app.config import get_settings

log = structlog.get_logger()

MAX_MATCHES = 200
MAX_LINE = 400
MAX_READ_LINES = 400
#: Binary and archive noise that would waste a scan. The retrieve step leaves
#: zips and extracted trees side by side.
SKIP_SUFFIXES = {".zip", ".png", ".jpg", ".jpeg", ".gif", ".pdf", ".jar",
                 ".woff", ".woff2", ".ttf", ".ico", ".eot"}


def _root() -> Path:
    return get_settings().workspace.resolve()


def _safe(rel: str) -> Path:
    """Resolve a workspace-relative path, refusing anything outside it."""
    root = _root()
    p = (root / rel).resolve()
    if not p.is_relative_to(root):
        raise ValueError(f"path escapes the workspace: {rel!r}")
    return p


def _walk(root: Path, glob: str | None) -> list[Path]:
    pattern = glob or "**/*"
    return [p for p in root.glob(pattern)
            if p.is_file() and p.suffix.lower() not in SKIP_SUFFIXES]


def _grep_sync(pattern: str, glob: str | None, ignore_case: bool) -> dict[str, Any]:
    root = _root()
    if not root.exists():
        return {"error": f"workspace does not exist: {root}. Run the retrieve "
                         "stage first."}
    try:
        rx = re.compile(pattern, re.IGNORECASE if ignore_case else 0)
    except re.error as e:
        return {"error": f"bad regex: {e}"}

    files = _walk(root, glob)
    matches: list[dict] = []
    scanned = 0
    for f in files:
        scanned += 1
        try:
            text = f.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        if not rx.search(text):
            continue
        for i, line in enumerate(text.splitlines(), 1):
            if rx.search(line):
                matches.append({
                    "path": str(f.relative_to(root)).replace("\\", "/"),
                    "line": i,
                    "text": line.strip()[:MAX_LINE],
                })
                if len(matches) >= MAX_MATCHES:
                    break
        if len(matches) >= MAX_MATCHES:
            break

    return {
        "pattern": pattern,
        "files_scanned": scanned,
        "match_count": len(matches),
        "truncated": len(matches) >= MAX_MATCHES,
        "matches": matches,
        # Stated explicitly: for this tool, zero is a result, not a failure. It
        # is the evidence a deletion rests on and must never read as "no answer".
        "note": ("No occurrence found in any retrieved metadata file."
                 if not matches else None),
    }


@tool("grep_workspace",
      "Regex-search every retrieved metadata file for a string -- an API name, a "
      "SOQL fragment, a label. Costs no Salesforce API calls. A zero-match result "
      "is meaningful evidence of absence, not a failure.",
      required(obj(pattern={"type": "string", "description": "Python regex"},
                   glob={"type": "string",
                         "description": "optional, e.g. '**/*.cls' or '**/objects/**'"},
                   ignore_case={"type": "boolean", "description": "default true"}),
               "pattern"),
      tags=("workspace",))
async def grep_workspace(pattern: str, glob: str | None = None,
                         ignore_case: bool = True) -> dict[str, Any]:
    # Scanning hundreds of files is blocking IO; keep the event loop free so
    # streaming to the browser does not stutter mid-search.
    return await asyncio.to_thread(_grep_sync, pattern, glob, ignore_case)


@tool("read_workspace_file",
      "Read a slice of one retrieved metadata file. Use the path returned by "
      "grep_workspace or list_workspace_files.",
      required(obj(path={"type": "string", "description": "workspace-relative"},
                   start_line={"type": "integer", "description": "1-based, default 1"},
                   line_count={"type": "integer", "description": "default 200, max 400"}),
               "path"),
      tags=("workspace",))
async def read_workspace_file(path: str, start_line: int = 1,
                              line_count: int = 200) -> dict[str, Any]:
    from app.pipeline.narrate import _redact

    try:
        p = _safe(path)
    except ValueError as e:
        return {"error": str(e)}
    if not p.is_file():
        return {"error": f"no such file: {path}"}

    text = await asyncio.to_thread(p.read_text, "utf-8", "ignore")
    lines = text.splitlines()
    start = max(1, start_line)
    n = max(1, min(line_count, MAX_READ_LINES))
    chunk = lines[start - 1: start - 1 + n]
    return {
        "path": path,
        "total_lines": len(lines),
        "start_line": start,
        "returned_lines": len(chunk),
        "content": _redact("\n".join(chunk)),
    }


@tool("list_workspace_files",
      "List retrieved metadata files, optionally filtered by glob. Use it to "
      "discover what metadata types the retrieve actually returned.",
      obj(glob={"type": "string", "description": "e.g. '**/*.cls'"},
          limit={"type": "integer", "description": "default 100, max 500"}),
      tags=("workspace",))
async def list_workspace_files(glob: str | None = None,
                               limit: int = 100) -> dict[str, Any]:
    root = _root()
    if not root.exists():
        return {"error": f"workspace does not exist: {root}"}
    files = await asyncio.to_thread(_walk, root, glob)
    rel = sorted(str(f.relative_to(root)).replace("\\", "/") for f in files)
    return {"total": len(rel), "files": rel[: max(1, min(limit, 500))]}
