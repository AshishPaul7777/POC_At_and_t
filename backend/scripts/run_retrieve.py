"""Stage S11 - retrieve the org's metadata to disk.

    .venv\\Scripts\\python.exe scripts\\run_retrieve.py [alias]
"""

from __future__ import annotations

import asyncio
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import get_settings                      # noqa: E402
from app.pipeline.retrieve import retrieve_all           # noqa: E402
from app.salesforce.auth import build_token_provider     # noqa: E402
from app.salesforce.connections import load_registry     # noqa: E402


async def main() -> int:
    s = get_settings()
    registry = load_registry()
    conn = registry.get(sys.argv[1] if len(sys.argv) > 1 else None)
    workspace = s.workspace / conn.alias

    print(f"org       : {conn.alias}  ({conn.instance_url})")
    print(f"workspace : {workspace}\n")

    result = await retrieve_all(conn, build_token_provider(s), workspace)

    print("=== retrieve summary ===")
    print(f"  manifest chunks : {len(result.manifests)}")
    print(f"  files retrieved : {result.files_retrieved}")
    print(f"  bytes           : {result.bytes_retrieved:,}")

    enumerated = {k: v for k, v in result.types_requested.items() if v >= 0}
    if enumerated:
        print("\n  enumerated (folder-scoped, cannot be wildcarded):")
        for k, v in sorted(enumerated.items()):
            print(f"    {k:18} {v:>5}")

    if result.warnings:
        print("\n  warnings:")
        for w in result.warnings:
            print(f"    - {w}")

    if result.failed_types:
        print(f"\n  FAILED types ({len(result.failed_types)}) -- these become coverage gaps,")
        print("  not silent absences:")
        for t, why in list(result.failed_types.items())[:12]:
            print(f"    {t:22} {why[:90]}")

    root = workspace / "mdapi"
    if root.exists():
        by_ext = Counter(p.suffix.lower() for p in root.rglob("*") if p.is_file())
        print("\n  retrieved file types:")
        for ext, n in by_ext.most_common(12):
            print(f"    {ext or '(none)':18} {n:>6}")

        by_dir = Counter(
            p.relative_to(root).parts[1] if len(p.relative_to(root).parts) > 1 else "?"
            for p in root.rglob("*") if p.is_file()
        )
        print("\n  top metadata folders:")
        for d, n in by_dir.most_common(15):
            print(f"    {d:26} {n:>6}")

    print(f"\n  degraded: {result.degraded}")
    return 1 if result.files_retrieved == 0 else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
