"""Read-only tools over the analysis database.

These answer "what did the pipeline find, and why". Every one is a SELECT; none
can alter a verdict. Where the pipeline already materialises an answer -- the
``component_summary`` view, ``collector_run``, ``rule_trace`` -- these read it
rather than recompute it, so the agent and the UI can never disagree about what
a run concluded.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import text

from app.agent.tools.registry import obj, required, tool
from app.db.session import session_scope

TERMINAL = ("SUCCEEDED", "DEGRADED", "FAILED", "CANCELLED")


async def _rows(sql: str, params: dict) -> list[dict]:
    async with session_scope() as s:
        return [dict(r) for r in (await s.execute(text(sql), params)).mappings().all()]


async def _one(sql: str, params: dict) -> dict | None:
    rows = await _rows(sql, params)
    return rows[0] if rows else None


async def _latest_run_id() -> str | None:
    r = await _one(
        "SELECT id::text AS id FROM runs WHERE state = ANY(:st) "
        "ORDER BY created_at DESC LIMIT 1", {"st": list(TERMINAL)})
    return r["id"] if r else None


async def _resolve_run(run_id: str | None) -> str:
    rid = run_id or await _latest_run_id()
    if not rid:
        raise ValueError("no completed run exists yet")
    return rid


# -- runs ---------------------------------------------------------------------

@tool("list_runs",
      "List analysis runs, newest first, with verdict counts. Use this to find "
      "a run id, or to see what has changed between runs.",
      obj(limit={"type": "integer", "description": "default 10, max 50"}),
      tags=("run",))
async def list_runs(limit: int = 10) -> list[dict]:
    return await _rows("""
        SELECT r.id::text AS run_id, r.state::text AS state, r.org_alias,
               r.started_at, r.finished_at,
               count(*) FILTER (WHERE cl.label = 'UNUSED')       AS unused,
               count(*) FILTER (WHERE cl.label = 'USED')         AS used,
               count(*) FILTER (WHERE cl.label = 'NEEDS_REVIEW') AS needs_review,
               count(*) FILTER (WHERE cl.label = 'OUT_OF_SCOPE') AS out_of_scope
          FROM runs r LEFT JOIN classifications cl ON cl.run_id = r.id
         WHERE r.started_at IS NOT NULL
         GROUP BY r.id ORDER BY r.created_at DESC LIMIT :lim
    """, {"lim": max(1, min(limit, 50))})


@tool("get_run_summary",
      "Verdict counts, per-collector outcomes and coverage caveats for one run. "
      "Start here to understand what a run could and could not prove. Omit "
      "run_id for the most recent completed run.",
      obj(run_id={"type": "string"}),
      tags=("run",))
async def get_run_summary(run_id: str | None = None) -> dict[str, Any]:
    rid = await _resolve_run(run_id)
    verdicts = await _rows(
        "SELECT label::text AS label, count(*) AS n FROM classifications "
        "WHERE run_id = :r GROUP BY 1 ORDER BY 2 DESC", {"r": rid})
    collectors = await _rows("""
        SELECT collector_id, status, artifacts_searched, hits,
               unavailable_reason
          FROM collector_run WHERE run_id = :r ORDER BY collector_id
    """, {"r": rid})
    caveats = await _rows("""
        SELECT DISTINCT payload->>'text' AS text FROM events
         WHERE run_id = :r AND etype = 'coverage.caveat'
    """, {"r": rid})
    gaps = await _rows("""
        SELECT g.reason, g.detail, c.api_name
          FROM evidence_gaps g LEFT JOIN components c ON c.id = g.component_id
         WHERE g.run_id = :r
    """, {"r": rid})
    return {
        "run_id": rid,
        "verdicts": {v["label"]: v["n"] for v in verdicts},
        "collectors": collectors,
        "coverage_caveats": [c["text"] for c in caveats if c["text"]],
        "evidence_gaps": gaps,
    }


# -- components ---------------------------------------------------------------

@tool("query_components",
      "Search components in a run. Filter by verdict, type, or a substring of "
      "the API name. Returns a compact list -- use get_component for the full "
      "evidence trail of any one of them.",
      obj(run_id={"type": "string"},
          verdict={"type": "string",
                   "enum": ["UNUSED", "USED", "NEEDS_REVIEW", "OUT_OF_SCOPE"]},
          ctype={"type": "string",
                 "description": "CustomField, CustomObject, ApexClass, ApexTrigger, ApexMethod, StandardObject"},
          name_contains={"type": "string"},
          limit={"type": "integer", "description": "default 50, max 200"}),
      tags=("component",))
async def query_components(run_id: str | None = None, verdict: str | None = None,
                           ctype: str | None = None,
                           name_contains: str | None = None,
                           limit: int = 50) -> dict[str, Any]:
    rid = await _resolve_run(run_id)
    sql = ["SELECT api_name, ctype::text AS ctype, verdict::text AS verdict,",
           "       confidence, had_gaps, reason_codes, parent_object",
           "  FROM component_summary WHERE run_id = :r"]
    params: dict[str, Any] = {"r": rid, "lim": max(1, min(limit, 200))}
    if verdict:
        sql.append("AND verdict = :v"); params["v"] = verdict
    if ctype:
        sql.append("AND ctype::text = :t"); params["t"] = ctype
    if name_contains:
        sql.append("AND api_name ILIKE :n"); params["n"] = f"%{name_contains}%"
    sql.append("ORDER BY verdict, confidence DESC, api_name LIMIT :lim")
    rows = await _rows(" ".join(sql), params)
    return {"run_id": rid, "count": len(rows), "components": rows}


@tool("get_component",
      "Everything known about one component: verdict, the rule that decided it, "
      "and the full evidence trail INCLUDING collectors that searched and found "
      "nothing. Negative evidence is the claim a deletion rests on, so it is "
      "returned with the same weight as a hit.",
      required(obj(api_name={"type": "string",
                             "description": "e.g. Inventory_Item__c.Unit_Cost__c"},
                   run_id={"type": "string"}),
               "api_name"),
      tags=("component",))
async def get_component(api_name: str, run_id: str | None = None) -> dict[str, Any]:
    rid = await _resolve_run(run_id)
    comp = await _one("""
        SELECT c.id, c.api_name, c.ctype::text AS ctype, c.label, c.parent_object,
               c.in_scope, c.out_of_scope_reason, c.namespace,
               c.last_modified_date, c.attrs,
               cl.label::text AS verdict, cl.confidence, cl.reason_codes,
               cl.rule_trace, cl.had_gaps, cl.platform_blocked,
               cl.removal_prerequisites
          FROM components c
          LEFT JOIN classifications cl
            ON cl.component_id = c.id AND cl.run_id = c.run_id
         WHERE c.run_id = :r AND lower(c.api_name) = lower(:n)
    """, {"r": rid, "n": api_name})
    if not comp:
        near = await _rows(
            "SELECT api_name FROM components WHERE run_id = :r "
            "AND api_name ILIKE :n ORDER BY api_name LIMIT 10",
            {"r": rid, "n": f"%{api_name}%"})
        return {"error": f"no component named {api_name!r} in run {rid}",
                "did_you_mean": [x["api_name"] for x in near]}

    cid = comp.pop("id")
    evidence = await _rows("""
        SELECT collector_id, result::text AS result, tier, weight, payload
          FROM evidence WHERE run_id = :r AND component_id = :c
         ORDER BY collector_id
    """, {"r": rid, "c": cid})
    flags = await _rows("""
        SELECT code, severity, detail FROM uncertainty_flags
         WHERE run_id = :r AND component_id = :c
    """, {"r": rid, "c": cid})
    gaps = await _rows("""
        SELECT collector_id, reason, detail, is_decisive FROM evidence_gaps
         WHERE run_id = :r AND component_id = :c
    """, {"r": rid, "c": cid})
    refs = await _rows("""
        SELECT a.metadata_type, a.member_name, e.tier, e.match_kind, e.locator,
               left(e.snippet, 240) AS snippet
          FROM reference_edges e JOIN artifact a ON a.id = e.from_artifact_id
         WHERE e.run_id = :r AND e.to_component_id = :c
         ORDER BY e.tier, a.metadata_type LIMIT 60
    """, {"r": rid, "c": cid})
    return {"run_id": rid, "component": comp, "evidence": evidence,
            "uncertainty_flags": flags, "coverage_gaps": gaps,
            "referenced_by": refs}


@tool("get_source",
      "The Apex source of a class or trigger, as stored during inventory. "
      "Methods inherit their class body. Secrets are redacted.",
      required(obj(api_name={"type": "string"}, run_id={"type": "string"}),
               "api_name"),
      tags=("component", "code"))
async def get_source(api_name: str, run_id: str | None = None) -> dict[str, Any]:
    from app.pipeline.narrate import _redact

    rid = await _resolve_run(run_id)
    row = await _one("""
        SELECT c.api_name, c.ctype::text AS ctype, cs.body, cs.line_count
          FROM components c JOIN component_source cs ON cs.component_id = c.id
         WHERE c.run_id = :r AND lower(c.api_name) = lower(:n)
    """, {"r": rid, "n": api_name})
    if not row:
        # A method has no body of its own; its class does.
        row = await _one("""
            SELECT p.api_name, p.ctype::text AS ctype, cs.body, cs.line_count
              FROM components m
              JOIN components p ON p.run_id = m.run_id
                               AND p.api_name = m.parent_object
                               AND p.ctype = 'ApexClass'
              JOIN component_source cs ON cs.component_id = p.id
             WHERE m.run_id = :r AND lower(m.api_name) = lower(:n)
        """, {"r": rid, "n": api_name})
    if not row:
        return {"error": f"no stored source for {api_name!r}. Only ApexClass and "
                         "ApexTrigger bodies are captured."}
    return {**row, "body": _redact(row["body"] or "")}


# -- graph --------------------------------------------------------------------

@tool("graph_neighbours",
      "What reaches a component, and what it reaches. Use this to judge whether "
      "something is genuinely isolated or merely one hop from a live entry point.",
      required(obj(api_name={"type": "string"}, run_id={"type": "string"}),
               "api_name"),
      tags=("graph",))
async def graph_neighbours(api_name: str, run_id: str | None = None) -> dict[str, Any]:
    rid = await _resolve_run(run_id)
    comp = await _one(
        "SELECT id FROM components WHERE run_id = :r AND lower(api_name) = lower(:n)",
        {"r": rid, "n": api_name})
    if not comp:
        return {"error": f"no component named {api_name!r}"}
    inbound = await _rows("""
        SELECT a.metadata_type, a.member_name, e.tier, e.match_kind
          FROM reference_edges e JOIN artifact a ON a.id = e.from_artifact_id
         WHERE e.run_id = :r AND e.to_component_id = :c
         ORDER BY e.tier LIMIT 80
    """, {"r": rid, "c": comp["id"]})
    return {"api_name": api_name, "inbound_count": len(inbound),
            "inbound": inbound}


# -- cross-run ----------------------------------------------------------------

@tool("compare_runs",
      "Diff two runs by component verdict. The important case is a REGRESSION: "
      "something previously UNUSED that is now USED, which means an earlier "
      "deletion recommendation was wrong.",
      required(obj(base_run_id={"type": "string"}, head_run_id={"type": "string"}),
               "base_run_id", "head_run_id"),
      tags=("run",))
async def compare_runs(base_run_id: str, head_run_id: str) -> dict[str, Any]:
    rows = await _rows("""
        SELECT COALESCE(b.api_name, h.api_name) AS api_name,
               b.verdict::text AS before, h.verdict::text AS after
          FROM (SELECT api_name, verdict FROM component_summary WHERE run_id = :b) b
     FULL OUTER JOIN
               (SELECT api_name, verdict FROM component_summary WHERE run_id = :h) h
            ON b.api_name = h.api_name
         WHERE b.verdict IS DISTINCT FROM h.verdict
         ORDER BY 1
    """, {"b": base_run_id, "h": head_run_id})
    regressions = [r for r in rows
                   if r["before"] == "UNUSED" and r["after"] == "USED"]
    return {"changed": len(rows), "regressions": regressions, "changes": rows[:200]}
