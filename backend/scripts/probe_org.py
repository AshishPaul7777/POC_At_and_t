"""Discover a target org's capabilities and cache them.

Run this once per org before analysing it. Everything it reports was previously
a hardcoded constant measured against a single Developer Edition org; probing
turns those assumptions into facts about the org actually being analysed.

    .venv\\Scripts\\python.exe scripts\\probe_org.py            # the only org
    .venv\\Scripts\\python.exe scripts\\probe_org.py acme-prod  # a named org
    .venv\\Scripts\\python.exe scripts\\probe_org.py --all      # every org
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db.session import engine                       # noqa: E402
from app.salesforce import capabilities as cap          # noqa: E402
from app.salesforce.client import SalesforceClient      # noqa: E402
from app.salesforce.connections import load_registry    # noqa: E402


def yn(v: bool | None) -> str:
    return {True: "yes", False: "NO", None: "unknown"}[v]


async def probe_one(conn) -> None:
    print(f"\n{'=' * 70}\n{conn.alias}  ->  {conn.instance_url}\n{'=' * 70}")
    async with SalesforceClient() as sf:
        caps = await cap.probe(sf, conn.alias)
        await cap.save(caps)

    print(f"  org id           : {caps.org_id}")
    print(f"  org name         : {caps.org_name}")
    print(f"  edition          : {caps.edition}")
    print(f"  sandbox          : {yn(caps.is_sandbox)}")
    print(f"  api version      : {caps.api_version}")
    print(f"  running as       : {caps.run_as_username}"
          f"   (admin: {yn(caps.run_as_is_admin)})")
    print()
    print(f"  API allowance    : {caps.api_limit_remaining} of {caps.api_limit_max} remaining")
    print(f"  composite ratio  : {caps.composite_compression}x")
    note = caps.probe_notes.get("composite")
    if isinstance(note, dict):
        print(f"                     samples: {note.get('per_subrequest_samples')}"
              f"  used: {note.get('used')} (worst case)")
    elif note:
        print(f"                     {note}")
    print()
    print("  feature availability")
    print(f"    event monitoring : {yn(caps.has_event_monitoring)}")
    print(f"    dependency API   : {yn(caps.has_dependency_api)}")
    if caps.dependency_api_types:
        print(f"                       covers: {', '.join(caps.dependency_api_types[:8])}")
        dep = caps.probe_notes.get("dependency_api") or {}
        if isinstance(dep, dict) and dep.get("truncation_suspected"):
            print("                       WARNING: hit the 2,000-row cap; data is partial")
    print(f"    field history    : {yn(caps.has_field_history)}")
    print(f"    code coverage    : {yn(caps.has_code_coverage)}")

    if caps.routing_overrides:
        print(f"\n  routing corrections for this org ({len(caps.routing_overrides)}):")
        for k, v in sorted(caps.routing_overrides.items()):
            print(f"    {k:28} -> {v}")
    else:
        print("\n  routing            : registry matches this org, no corrections")

    if caps.unavailable_objects:
        print(f"\n  not queryable here ({len(caps.unavailable_objects)}):")
        print("    " + ", ".join(sorted(caps.unavailable_objects)))

    caveats = caps.coverage_caveats()
    if caveats:
        print("\n  COVERAGE LIMITATIONS (these go verbatim into the report):")
        for c in caveats:
            print(f"    - {c}")
    else:
        print("\n  no coverage limitations detected")

    print(f"\n  probe cost       : {caps.probe_api_cost} API calls")


async def main() -> int:
    args = [a for a in sys.argv[1:]]
    registry = load_registry()
    print(f"configured orgs: {', '.join(registry.aliases)}")

    if "--all" in args:
        targets = registry.all()
    elif args:
        targets = [registry.get(args[0])]
    else:
        targets = [registry.get()]

    for conn in targets:
        await probe_one(conn)

    await engine.dispose()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
