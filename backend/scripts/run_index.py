"""Stage S20 - index retrieved metadata and produce the first reference edges.

    .venv\\Scripts\\python.exe scripts\\run_index.py [alias]
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import text                              # noqa: E402

from app.config import get_settings                      # noqa: E402
from app.db.session import engine, session_scope         # noqa: E402
from app.pipeline.indexer import index_workspace         # noqa: E402
from app.salesforce.connections import load_registry     # noqa: E402


async def main() -> int:
    s = get_settings()
    conn = load_registry().get(sys.argv[1] if len(sys.argv) > 1 else None)
    workspace = s.workspace / conn.alias

    async with session_scope() as sess:
        row = (await sess.execute(text(
            "SELECT id, org_id FROM runs WHERE org_alias = :a "
            "ORDER BY created_at DESC LIMIT 1"), {"a": conn.alias})).first()
    if not row:
        print("no run found - run scripts/setup_org.py first")
        return 1
    run_id = str(row.id)
    print(f"run       : {run_id}")
    print(f"workspace : {workspace}\n")

    stats = await index_workspace(run_id, workspace)

    print("=== index summary ===")
    print(f"  artifacts indexed : {stats.artifacts}")
    print(f"  tokens            : {stats.tokens:,}")
    print(f"  string literals   : {stats.literals:,}")
    print(f"  reference edges   : {stats.edges:,}")

    print("\n  artifacts by metadata type:")
    for t, n in sorted(stats.by_type.items(), key=lambda kv: -kv[1])[:14]:
        print(f"    {t:26} {n:>5}")

    if stats.dynamic_files:
        print(f"\n  DYNAMIC APEX detected in {len(stats.dynamic_files)} file(s).")
        print("  Every object these touch gets capped at NEEDS_REVIEW - a name")
        print("  built at runtime cannot be resolved, and guessing deletes fields.")
        for f in stats.dynamic_files[:8]:
            print(f"    - {f}")

    if stats.unmapped_folders:
        print(f"\n  unmapped folders (still indexed, type unknown): "
              f"{', '.join(sorted(stats.unmapped_folders))}")
    if stats.unreadable:
        print(f"\n  unreadable files: {len(stats.unreadable)}")

    async with session_scope() as sess:
        print("\n=== reference edges by tier ===")
        for r in (await sess.execute(text("""
            SELECT tier::text, match_kind, count(*) n FROM reference_edges
            WHERE run_id = :r GROUP BY 1,2 ORDER BY 1, 3 DESC
        """), {"r": run_id})).all():
            print(f"  Tier {r.tier}  {r.match_kind:16} {r.n:>6}")

        print("\n=== most-referenced components (evidence of USE) ===")
        for r in (await sess.execute(text("""
            SELECT c.ctype::text, c.api_name, count(*) n,
                   count(DISTINCT e.from_artifact_id) srcs
            FROM reference_edges e JOIN components c ON c.id = e.to_component_id
            WHERE e.run_id = :r AND e.tier = 'A' AND c.in_scope
            GROUP BY 1,2 ORDER BY 3 DESC LIMIT 10
        """), {"r": run_id})).all():
            print(f"  {r.ctype:14} {r.api_name:44} {r.n:>4} refs / {r.srcs} sources")

        print("\n=== in-scope components with ZERO references found so far ===")
        print("  (NOT a verdict - only one collector has run. Data population,")
        print("   dependency edges and delete rehearsal have not been checked.)")
        rows = (await sess.execute(text("""
            SELECT c.ctype::text, c.api_name
            FROM components c
            WHERE c.run_id = :r AND c.in_scope
              AND NOT EXISTS (SELECT 1 FROM reference_edges e
                              WHERE e.to_component_id = c.id AND e.tier = 'A')
            ORDER BY c.ctype, c.api_name
        """), {"r": run_id})).all()
        by_type: dict[str, list[str]] = {}
        for r in rows:
            by_type.setdefault(r.ctype, []).append(r.api_name)
        for t, names in sorted(by_type.items()):
            print(f"\n  {t} ({len(names)}):")
            for n in names[:12]:
                print(f"    - {n}")
            if len(names) > 12:
                print(f"    ... and {len(names) - 12} more")

    await engine.dispose()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
