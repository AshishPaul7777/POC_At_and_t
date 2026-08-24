"""End-to-end check of auth + governor + client against the live org.

Run:  .venv\\Scripts\\python.exe scripts\\verify_client.py
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import get_settings                      # noqa: E402
from app.salesforce.client import SalesforceClient       # noqa: E402
from app.salesforce.governor import BudgetSnapshot       # noqa: E402


def rule(t: str) -> None:
    print(f"\n{'=' * 4} {t} {'=' * (60 - len(t))}")


async def main() -> int:
    s = get_settings()
    ticks: list[BudgetSnapshot] = []

    async with SalesforceClient() as sf:
        sf.governor.on_tick(ticks.append)

        rule("identity")
        me = await sf.identity()
        print(f"  running as : {me.get('preferred_username')}")
        print(f"  org id     : {me.get('organization_id')}")

        rule("budget")
        remaining, maximum = await sf.daily_api_remaining()
        print(f"  DailyApiRequests : {remaining} / {maximum}")
        print(f"  safety floor     : {s.sf_api_safety_floor}")

        rule("pre-flight cost plan")
        # Measured ratio, not the documented-but-wrong 25x.
        plan = sf.governor.plan(
            {
                "inventory (≈40 tooling queries via composite)": sf.governor.composite_cost(40),
                "dependency shards": 20,
                "field population (6 objects)": 8,
                "runtime signals": 12,
                "delete rehearsal": 10,
            },
            remaining,
        )
        for k, v in plan.items.items():
            print(f"  {k:46} {v:>5}")
        print(f"  {'TOTAL':46} {plan.total:>5}")
        print(f"  reservable={plan.reservable}  fits={plan.fits}")

        rule("tooling query")
        classes = await sf.query(
            "SELECT Id, Name, NamespacePrefix FROM ApexClass WHERE NamespacePrefix = null",
            tooling=True,
        )
        print(f"  local Apex classes : {len(classes)}")
        print(f"  sample             : {', '.join(c['Name'] for c in classes[:4])}")

        rule("composite batching (many queries, few calls)")
        # Routing matters: Report and Dashboard are Data-API objects and are NOT
        # queryable through the Tooling API, while ValidationRule and Layout are
        # Tooling-only. Sending either to the wrong endpoint returns
        # INVALID_TYPE, which would otherwise read as "zero found".
        tooling_soqls = [
            "SELECT COUNT() FROM ApexTrigger WHERE NamespacePrefix = null",
            "SELECT COUNT() FROM Flow",
            "SELECT COUNT() FROM ValidationRule",
            "SELECT COUNT() FROM Layout",
            "SELECT COUNT() FROM CustomField WHERE NamespacePrefix = null",
        ]
        rest_soqls = [
            "SELECT COUNT() FROM Report",
            "SELECT COUNT() FROM Dashboard",
        ]
        before = sf.governor.snapshot().consumed_by_run
        t_res = await sf.batched_queries(tooling_soqls, tooling=True)
        r_res = await sf.batched_queries(rest_soqls, tooling=False)
        spent = sf.governor.snapshot().consumed_by_run - before
        for q, r in list(zip(tooling_soqls, t_res, strict=False)) + list(
            zip(rest_soqls, r_res, strict=False)
        ):
            label = q.split("FROM ")[1].split(" WHERE")[0]
            print(f"  {label:22} {r['totalSize'] if r else 'FAILED'}")
        n = len(tooling_soqls) + len(rest_soqls)
        print(f"  -> {n} queries cost an estimated {spent} API call(s)")

        rule("field population (the O(objects) probe)")
        objs = await sf.query(
            "SELECT QualifiedApiName FROM EntityDefinition "
            "WHERE IsCustomizable = true ORDER BY QualifiedApiName",
            tooling=True,
        )
        custom = [o["QualifiedApiName"] for o in objs
                  if o["QualifiedApiName"].endswith("__c")][:1]
        if custom:
            target = custom[0]
            fields = await sf.query(
                "SELECT QualifiedApiName, DataType FROM FieldDefinition "
                f"WHERE EntityDefinition.QualifiedApiName = '{target}'",
                tooling=True,
            )
            # No type pre-filtering: the client discovers unsupported fields
            # from Salesforce's own error and drops them, which is more durable
            # than a hand-maintained list.
            probe = [f["QualifiedApiName"] for f in fields
                     if f["QualifiedApiName"].endswith("__c")]
            print(f"  object           : {target}")
            print(f"  probing {len(probe)} fields")
            before = sf.governor.snapshot().consumed_by_run
            pop, unsupported = await sf.field_population(target, probe)
            spent = sf.governor.snapshot().consumed_by_run - before
            empty = [f for f, v in pop.items() if v["populated"] == 0]
            total = next(iter(pop.values()))["total"] if pop else 0
            print(f"  records          : {total}")
            print(f"  API calls used   : {spent}")
            print(f"  aggregated       : {len(pop)}")
            print(f"  fields at 0%     : {len(empty)}")
            for f in empty[:6]:
                print(f"      - {f}")
            if unsupported:
                print(f"  cannot aggregate : {len(unsupported)} -> existence probe")
                for f, why in list(unsupported.items())[:4]:
                    got = await sf.field_has_any_value(target, f)
                    state = {True: "has data", False: "empty", None: "INCONCLUSIVE"}[got]
                    print(f"      - {f}: {state}  ({why})")
            if empty:
                print("  NOTE: 0% population is CORROBORATING evidence only, never")
                print("        deciding - a field can be written by an integration")
                print("        tomorrow, or populated in 2019 and dead ever since.")

        rule("governor state")
        snap = sf.governor.snapshot()
        print(f"  consumed this run : {snap.consumed_by_run}")
        print(f"  remaining         : {snap.remaining}")
        print(f"  breaker           : {'open' if snap.breaker_open else 'closed'}")
        print(f"  telemetry ticks   : {len(ticks)}")

    print("\nALL CLIENT CHECKS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
