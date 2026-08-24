"""Run Stage S10 (inventory) against the live org and report what landed.

Usage:  .venv\\Scripts\\python.exe scripts\\run_inventory.py
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import text                              # noqa: E402

from app.db.session import engine, session_scope         # noqa: E402
from app.pipeline.inventory import (                     # noqa: E402
    abandon_stale_runs,
    create_run,
    inventory,
)
from app.salesforce.client import SalesforceClient       # noqa: E402
from app.salesforce.connections import load_registry     # noqa: E402


async def main() -> int:
    async with SalesforceClient() as sf:
        me = await sf.identity()
        # A crashed run leaves its row RUNNING and locks the org out, so reap
        # before starting. Safe here because one worker holds the advisory lock.
        if (stale := await abandon_stale_runs(me["organization_id"])):
            print(f"reaped {len(stale)} abandoned run(s)\n")
        conn = load_registry().get()
        run_id = await create_run(sf, alias=conn.alias, config={"scope": ["CustomObject", "CustomField", "Apex"]})
        print(f"run: {run_id}\n")
        counts = await inventory(run_id, sf)
        spent = sf.governor.snapshot().consumed_by_run
        remaining = sf.governor.snapshot().remaining

    print("=== inventory counts ===")
    for k, v in counts.items():
        print(f"  {k:38} {v:>5}")
    print(f"\n  API calls used: {spent}   remaining: {remaining}")

    async with session_scope() as s:
        print("\n=== in-scope components by type ===")
        for r in (await s.execute(text("""
            SELECT ctype::text, in_scope, count(*) n FROM components
            WHERE run_id = :r GROUP BY 1,2 ORDER BY 1,2 DESC
        """), {"r": run_id})).all():
            tag = "in scope" if r.in_scope else "out of scope"
            print(f"  {r.ctype:16} {tag:14} {r.n:>5}")

        print("\n=== alias coverage (what we can match against) ===")
        for r in (await s.execute(text("""
            SELECT alias_kind, count(*) n FROM alias
            WHERE run_id = :r GROUP BY 1 ORDER BY 2 DESC
        """), {"r": run_id})).all():
            print(f"  {r.alias_kind:22} {r.n:>5}")
        total = await s.scalar(text("SELECT count(*) FROM alias WHERE run_id = :r"),
                               {"r": run_id})
        comps = await s.scalar(text("SELECT count(*) FROM components WHERE run_id = :r"),
                              {"r": run_id})
        print(f"  {'TOTAL':22} {total:>5}   ({total / max(comps,1):.1f} per component)")

        print("\n=== custom fields on STANDARD objects (in scope, easy to miss) ===")
        for r in (await s.execute(text("""
            SELECT parent_object, count(*) n FROM components
            WHERE run_id = :r AND ctype = 'CustomField'
              AND (attrs->>'parent_in_scope') = 'false'
            GROUP BY 1 ORDER BY 2 DESC
        """), {"r": run_id})).all():
            print(f"  {r.parent_object:22} {r.n:>5} custom field(s)")

        print("\n=== Apex methods: entry points vs internal ===")
        for r in (await s.execute(text("""
            SELECT (attrs->>'is_entry_point')::bool AS entry,
                   (attrs->>'is_test')::bool AS is_test, count(*) n
            FROM components WHERE run_id = :r AND ctype = 'ApexMethod'
            GROUP BY 1,2 ORDER BY 1 DESC, 2
        """), {"r": run_id})).all():
            kind = "ENTRY POINT" if r.entry else "internal"
            t = " (test)" if r.is_test else ""
            print(f"  {kind + t:26} {r.n:>5}")
        print("  note: entry points are externally invocable, so zero Apex callers")
        print("        can never make them UNUSED - the caller is outside the org.")

        print("\n=== collector runs recorded (negative evidence machinery) ===")
        for r in (await s.execute(text("""
            SELECT collector_id, status::text, artifacts_searched, hits
            FROM collector_run WHERE run_id = :r ORDER BY collector_id
        """), {"r": run_id})).all():
            print(f"  {r.collector_id:32} {r.status:9} searched={r.artifacts_searched:<5} hits={r.hits}")

        await s.execute(text(
            "UPDATE runs SET state='SUCCEEDED', finished_at=now() WHERE id=:r"
        ), {"r": run_id})

    await engine.dispose()
    print(f"\nrun {run_id} marked SUCCEEDED")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

