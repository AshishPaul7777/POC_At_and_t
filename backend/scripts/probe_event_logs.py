"""Probe EventLogFile against the connected org."""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))
load_dotenv(ROOT / ".env")
load_dotenv(Path(__file__).resolve().parent / ".env")


async def main() -> None:
    from app.salesforce.client import SalesforceClient

    async with SalesforceClient() as sf:
        me = await sf.identity()
        print("org", me.get("organization_id"), "user", me.get("preferred_username"))

        for label, soql in [
            ("recent", "SELECT Id, EventType, LogDate, LogFileLength FROM EventLogFile ORDER BY LogDate DESC LIMIT 25"),
            ("apex_exec", "SELECT Id, EventType, LogDate FROM EventLogFile WHERE EventType = 'ApexExecution' ORDER BY LogDate DESC LIMIT 10"),
            ("apex_trig", "SELECT Id, EventType, LogDate FROM EventLogFile WHERE EventType = 'ApexTrigger' ORDER BY LogDate DESC LIMIT 5"),
            ("countish", "SELECT COUNT() FROM EventLogFile"),
        ]:
            try:
                if label == "countish":
                    # COUNT() returns totalSize via query helper differently
                    rows = await sf.query(soql)
                    print(label, "ok", rows)
                else:
                    rows = await sf.query(soql)
                    print(f"\n=== {label}: {len(rows)} rows ===")
                    for r in rows[:15]:
                        print(
                            " ",
                            r.get("EventType"),
                            r.get("LogDate"),
                            "bytes=",
                            r.get("LogFileLength"),
                            "id=",
                            r.get("Id"),
                        )
            except Exception as e:
                print(f"\n=== {label}: ERROR ===")
                print(" ", type(e).__name__, str(e)[:400])


if __name__ == "__main__":
    asyncio.run(main())
