"""Run the whole pipeline from the CLI, exactly as the UI does.

`run_analysis.py` reuses the newest existing run row and executes only the
collectors and the classifier. That is the right tool for iterating on scoring,
and the wrong one after a change to the *indexer*: reference edges are written
by the `index` stage, so re-scoring alone leaves the previous run's edges --
including any the current matching rules would now reject -- in place.

This calls `start_run`, which is the same entry point `POST /api/runs` uses, so
all ten stages execute against a fresh run row.

    .venv\\Scripts\\python.exe scripts\\run_full.py [alias]
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import text                          # noqa: E402

from app.db.session import engine, session_scope     # noqa: E402
from app.orchestration.runner import start_run       # noqa: E402


async def main() -> int:
    alias = sys.argv[1] if len(sys.argv) > 1 else None
    run_id = await start_run(alias)
    print(f"run: {run_id}", flush=True)

    # start_run kicks the work off as a background task and returns. Await that
    # task rather than the coroutine, or the loop closes under the pipeline.
    tasks = [t for t in asyncio.all_tasks() if t.get_name() == f"run:{run_id}"]
    await asyncio.gather(*tasks)

    # The table is `stages`, keyed on `key`, and it stores timestamps rather
    # than a duration. Counts come from classifications: `runs` carries no
    # verdict columns, the API derives those per request.
    async with session_scope() as s:
        row = (await s.execute(text("""
            SELECT state, started_at, finished_at
              FROM runs WHERE id = CAST(:r AS uuid)
        """), {"r": run_id})).mappings().first()
        stages = (await s.execute(text("""
            SELECT key, state, started_at, finished_at, skip_reason
              FROM stages WHERE run_id = CAST(:r AS uuid) ORDER BY ordinal
        """), {"r": run_id})).mappings().all()
        verdicts = (await s.execute(text("""
            SELECT label::text AS label, count(*) AS n
              FROM classifications WHERE run_id = CAST(:r AS uuid)
             GROUP BY label
        """), {"r": run_id})).mappings().all()

    print("\nstages:")
    for st in stages:
        secs = ((st["finished_at"] - st["started_at"]).total_seconds()
                if st["started_at"] and st["finished_at"] else 0)
        note = f"  {st['skip_reason']}" if st["skip_reason"] else ""
        print(f"  {st['key']:<18} {st['state']:<10} {secs:6.1f}s{note}")

    total = sum(int(v["n"]) for v in verdicts)
    elapsed = ((row["finished_at"] - row["started_at"]).total_seconds()
               if row["started_at"] and row["finished_at"] else 0)
    print(f"\nrun {row['state']}: {total} components in {elapsed:.0f}s")
    for v in sorted(verdicts, key=lambda v: -int(v["n"])):
        print(f"  {v['label']:<16} {v['n']}")

    await engine.dispose()
    return 0 if row["state"] in ("SUCCEEDED", "DEGRADED") else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
