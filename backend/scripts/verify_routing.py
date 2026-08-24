"""Probe every routing-registry entry against the real org.

The registry in app/salesforce/routing.py is a claim about which endpoint serves
which object. A wrong entry is not a cosmetic bug: a mis-routed query returns
INVALID_TYPE, which reads downstream as "no references found" and can turn a
live component into a deletion candidate.

So the claim gets tested rather than trusted. Each object is queried on BOTH
endpoints and the registry is compared against what the org actually does.

Cost: 2 cheap calls per object, batched 25 at a time.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.salesforce.client import SalesforceClient          # noqa: E402
from app.salesforce.routing import (                        # noqa: E402
    DATA_ONLY,
    PROBE_OVERRIDES,
    TOOLING_ONLY,
    Endpoint,
    endpoint_for,
)


async def probe(sf: SalesforceClient, names: list[str], tooling: bool) -> dict[str, bool]:
    """True where the object is queryable on this endpoint.

    Objects that reject an unfiltered ``SELECT Id`` get a purpose-built query
    from PROBE_OVERRIDES. Without that, a probe failure would be misread as "the
    object does not exist here" -- the same conflation of error with emptiness
    that this module exists to prevent.
    """
    out: dict[str, bool] = {}
    size = 25
    prefix = f"v{sf._s.sf_api_version}/{'tooling/' if tooling else ''}query"
    from urllib.parse import quote

    for i in range(0, len(names), size):
        chunk = names[i : i + size]
        subs = [
            {
                "method": "GET",
                "url": f"{prefix}?q="
                + quote(PROBE_OVERRIDES.get(n, f"SELECT Id FROM {n} LIMIT 1")),
            }
            for n in chunk
        ]
        results = await sf.composite_batch(subs)
        for n, r in zip(chunk, results, strict=False):
            out[n] = r.get("statusCode", 500) < 300
    return out


async def main() -> int:
    names = sorted(TOOLING_ONLY | DATA_ONLY)
    print(f"probing {len(names)} objects on both endpoints "
          f"({(len(names) + 24) // 25 * 2} composite calls)\n")

    async with SalesforceClient() as sf:
        remaining_before, _ = await sf.daily_api_remaining()
        on_tooling = await probe(sf, names, tooling=True)
        on_data = await probe(sf, names, tooling=False)
        spent = sf.governor.snapshot().consumed_by_run

    wrong: list[str] = []
    unknown: list[str] = []

    print(f"{'object':38} {'tooling':>8} {'data':>6}   {'registry':>9}  verdict")
    print("-" * 84)
    for n in names:
        t, d = on_tooling[n], on_data[n]
        claimed = endpoint_for(n)
        if t and d:
            actual = Endpoint.BOTH
        elif t:
            actual = Endpoint.TOOLING
        elif d:
            actual = Endpoint.DATA
        else:
            actual = None

        if actual is None:
            verdict = "NOT PRESENT in this org"
            unknown.append(n)
        elif actual is Endpoint.BOTH:
            # Registry naming one endpoint is fine when both work: queries still
            # succeed. Worth knowing, not worth failing over.
            verdict = "ok (both work)"
        elif actual is claimed:
            verdict = "ok"
        else:
            verdict = f"WRONG -> should be {actual.value}"
            wrong.append(f"{n}: registry says {claimed.value}, org says {actual.value}")

        print(f"{n:38} {str(t):>8} {str(d):>6}   {claimed.value:>9}  {verdict}")

    print("\n" + "=" * 84)
    print(f"API calls used: {spent}   (remaining before run: {remaining_before})")
    if unknown:
        print(f"\n{len(unknown)} object(s) not present in this org (cannot verify here):")
        print("  " + ", ".join(unknown))
    if wrong:
        print(f"\n{len(wrong)} INCORRECT registry entries - fix routing.py:")
        for w in wrong:
            print(f"  - {w}")
        return 1
    print("\nRegistry matches the org for every verifiable object.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
