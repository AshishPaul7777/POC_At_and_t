"""Build the dependency graph and compute reachability from entry points.

    .venv\\Scripts\\python.exe scripts\\run_graph.py [alias]
"""

from __future__ import annotations

import asyncio
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import text                              # noqa: E402

from app.db.session import engine, session_scope         # noqa: E402
from app.pipeline.graph import (                         # noqa: E402
    build_graph,
    compute_reachability,
    persist_reachability,
)
from app.salesforce.connections import load_registry     # noqa: E402


async def main() -> int:
    conn = load_registry().get(sys.argv[1] if len(sys.argv) > 1 else None)
    async with session_scope() as s:
        row = (await s.execute(text(
            "SELECT id FROM runs WHERE org_alias = :a ORDER BY created_at DESC LIMIT 1"
        ), {"a": conn.alias})).first()
    if not row:
        print("no run found — run scripts/setup_org.py first")
        return 1
    run_id = str(row.id)
    print(f"run: {run_id}\n")

    g = await build_graph(run_id)
    stats = compute_reachability(g)
    await persist_reachability(run_id, g)

    print("=== graph ===")
    print(f"  nodes            : {len(g.nodes)}")
    print(f"  edges            : {sum(len(v) for v in g.out.values())}")
    print(f"  entry points     : {stats['entry_points']}")
    print(f"  reachable nodes  : {stats['reachable']}")
    print(f"  UNREACHABLE in-scope components : {stats['unreachable_components']}")

    print("\n=== entry points by type (what actually runs or is seen) ===")
    for t, n in Counter(
        node.ctype for node in g.nodes.values() if node.is_entry_point
    ).most_common(12):
        print(f"  {t:28} {n:>4}")

    print("\n=== NOT entry points (placement, not execution) ===")
    for t, n in Counter(
        node.ctype for node in g.nodes.values()
        if node.kind == "artifact" and not node.is_entry_point
    ).most_common(8):
        reason = next((x.entry_reason for x in g.nodes.values()
                       if x.ctype == t and x.entry_reason), None)
        print(f"  {t:28} {n:>4}" + (f"   ({reason})" if reason else ""))

    print("\n=== why each USED component is used (path to an entry point) ===")
    shown = 0
    for node in sorted(g.nodes.values(),
                       key=lambda n: (n.distance or 0), reverse=True):
        if node.kind != "component" or not node.reachable or not node.in_scope:
            continue
        if node.ctype not in ("CustomField", "ApexClass"):
            continue
        path = g.path_to_root(node.key)
        if len(path) < 2:
            continue
        chain = "  <-  ".join(f"{p['label']} [{p['type']}]" for p in path)
        print(f"\n  {node.label}")
        print(f"    {chain}")
        root = path[-1]
        if root.get("entry_reason"):
            print(f"    ENTRY POINT: {root['entry_reason']}")
        shown += 1
        if shown >= 5:
            break

    print("\n=== UNREACHABLE in-scope components ===")
    print("  Nothing that runs or is seen depends on these.")
    unreachable = [n for n in g.nodes.values()
                   if n.kind == "component" and n.in_scope and not n.reachable]
    by_type: dict[str, list] = {}
    for n in unreachable:
        by_type.setdefault(n.ctype, []).append(n)
    for t, items in sorted(by_type.items()):
        print(f"\n  {t} ({len(items)}):")
        for n in sorted(items, key=lambda x: x.label)[:14]:
            inbound = len(g.inc.get(n.key, ()))
            note = f"  ({inbound} inbound ref(s), none from a live entry point)" \
                if inbound else "  (no references at all)"
            print(f"    - {n.label}{note}")
        if len(items) > 14:
            print(f"    ... and {len(items) - 14} more")

    await engine.dispose()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
