"""Live Salesforce reads, through the same governed client the pipeline uses.

The agent deliberately gets no private path to the org. Every call here goes
through ``SalesforceClient``, which means it inherits the API safety floor, the
15-second timeout that keeps requests out of the long-running concurrency class,
and ``assert_routable`` -- the check that stops a query being sent to the wrong
endpoint and returning INVALID_TYPE, which reads downstream exactly like "no
records found".

Read-only is enforced by parsing the statement, not by asking the model nicely.
"""

from __future__ import annotations

import re
from typing import Any

import structlog

from app.agent.tools.registry import obj, required, tool

log = structlog.get_logger()

#: Anything that is not a bare SELECT is refused. Salesforce SOQL has no DML,
#: but the client also speaks to Tooling endpoints where a crafted string could
#: do more, and a deletion tool has no business accepting write syntax at all.
_SELECT_ONLY = re.compile(r"^\s*SELECT\s", re.IGNORECASE)
_FORBIDDEN = re.compile(
    r"\b(INSERT|UPDATE|DELETE|UPSERT|MERGE|UNDELETE|CREATE|ALTER|DROP)\b",
    re.IGNORECASE)

MAX_ROWS = 200


@tool("soql_query",
      "Run a read-only SOQL query against the live org. Use for questions the "
      "stored analysis cannot answer -- current record counts, whether a field "
      "holds data today. Costs API budget, so prefer grep_workspace and the "
      "stored evidence when they suffice. Set tooling=true for metadata objects "
      "such as ApexClass or CustomField.",
      required(obj(soql={"type": "string", "description": "must start with SELECT"},
                   tooling={"type": "boolean",
                            "description": "true for the Tooling API; default false"}),
               "soql"),
      tags=("org",))
async def soql_query(soql: str, tooling: bool = False) -> dict[str, Any]:
    from app.salesforce.client import SalesforceClient
    from app.salesforce.routing import RoutingError

    if not _SELECT_ONLY.match(soql):
        return {"error": "only SELECT statements are permitted"}
    if _FORBIDDEN.search(soql):
        return {"error": "statement contains a write keyword and was refused"}

    try:
        async with SalesforceClient() as sf:
            rows = await sf.query(soql, tooling=tooling)
            spent = sf.governor.snapshot().consumed_by_run
    except RoutingError as e:
        # Recoverable and worth explaining: the model can flip `tooling` and
        # retry, which is exactly what the message tells it to do.
        return {"error": f"routing: {e}",
                "hint": "retry with the other value of `tooling`"}
    except Exception as e:
        return {"error": f"{type(e).__name__}: {str(e)[:300]}"}

    truncated = len(rows) > MAX_ROWS
    return {
        "row_count": len(rows),
        "truncated": truncated,
        "rows": rows[:MAX_ROWS],
        "api_calls_used": spent,
        "note": "Query returned no rows." if not rows else None,
    }


@tool("describe_object",
      "Field-level describe for one Salesforce object: every field's API name, "
      "type and whether it is custom. Use it to confirm a field still exists, or "
      "to find fields the analysis may not have inventoried.",
      required(obj(sobject={"type": "string", "description": "e.g. Account"}),
               "sobject"),
      tags=("org",))
async def describe_object(sobject: str) -> dict[str, Any]:
    from app.salesforce.client import SalesforceClient

    if not re.fullmatch(r"[A-Za-z0-9_]+", sobject or ""):
        return {"error": f"invalid object name: {sobject!r}"}
    try:
        async with SalesforceClient() as sf:
            data = await sf._request(
                "GET",
                f"/services/data/v{sf._s.sf_api_version}/sobjects/{sobject}/describe")
    except Exception as e:
        return {"error": f"{type(e).__name__}: {str(e)[:300]}"}

    fields = [
        {"name": f.get("name"), "type": f.get("type"), "label": f.get("label"),
         "custom": f.get("custom"), "nillable": f.get("nillable")}
        for f in (data.get("fields") or [])
    ]
    return {"sobject": sobject, "label": data.get("label"),
            "custom": data.get("custom"), "queryable": data.get("queryable"),
            "field_count": len(fields), "fields": fields}
