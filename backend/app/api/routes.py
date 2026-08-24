"""HTTP API for the frontend.

Read-only. Nothing here mutates an org or starts work — running the pipeline is
still a deliberate CLI action, so a stray browser request cannot spend an org's
API budget.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import text

from app.db.session import session_scope
from app.pipeline.graph import build_graph, compute_reachability, to_cytoscape

router = APIRouter(prefix="/api", tags=["analysis"])

#: Graph construction reads the whole edge set, so it is cached per run. Runs are
#: immutable once finished, which makes this safe.
_graph_cache: dict[str, Any] = {}


@router.get("/runs")
async def list_runs() -> list[dict]:
    """Run history, newest first, with enough detail to pick one from a menu."""
    async with session_scope() as s:
        rows = (await s.execute(text("""
            SELECT r.id::text, r.org_id, r.org_alias, r.state::text, r.api_version,
                   r.run_as_username, r.started_at, r.finished_at, r.last_seq,
                   (SELECT count(*) FROM components c
                     WHERE c.run_id = r.id AND c.in_scope) AS components,
                   (SELECT count(*) FROM classifications cl
                     WHERE cl.run_id = r.id AND cl.label = 'UNUSED') AS unused,
                   (SELECT count(*) FROM classifications cl
                     WHERE cl.run_id = r.id AND cl.label = 'NEEDS_REVIEW') AS needs_review,
                   EXTRACT(EPOCH FROM (COALESCE(r.finished_at, now()) - r.started_at))
                     AS duration_s
              FROM runs r ORDER BY r.created_at DESC LIMIT 50
        """))).mappings().all()
    return [dict(r) for r in rows]


@router.get("/runs/{run_id}/summary")
async def summary(run_id: str) -> dict:
    async with session_scope() as s:
        verdicts = (await s.execute(text("""
            SELECT cl.label::text AS label, c.ctype::text AS ctype, count(*) AS n
              FROM classifications cl JOIN components c ON c.id = cl.component_id
             WHERE cl.run_id = :r GROUP BY 1,2
        """), {"r": run_id})).mappings().all()
        if not verdicts:
            raise HTTPException(404, "no classifications for this run")

        collectors = (await s.execute(text("""
            SELECT collector_id, collector_family, status::text AS status, method,
                   artifacts_searched, hits, unavailable_reason
              FROM collector_run WHERE run_id = :r ORDER BY collector_id
        """), {"r": run_id})).mappings().all()

        run = (await s.execute(text("""
            SELECT org_alias, org_id, state::text AS state, api_version,
                   run_as_username, started_at, finished_at
              FROM runs WHERE id = :r
        """), {"r": run_id})).mappings().first()

        caps = (await s.execute(text("""
            SELECT edition, org_name, api_limit_max, api_limit_remaining,
                   has_event_monitoring, has_dependency_api, has_field_history,
                   has_code_coverage, unavailable_objects
              FROM org_capabilities WHERE org_id = :o
        """), {"o": (run or {}).get("org_id")})).mappings().first()

    totals: dict[str, int] = {}
    by_type: dict[str, dict[str, int]] = {}
    for v in verdicts:
        totals[v["label"]] = totals.get(v["label"], 0) + v["n"]
        by_type.setdefault(v["ctype"], {})[v["label"]] = v["n"]

    # Surfaced explicitly so the UI can state what this run could NOT prove,
    # rather than implying the absence of a finding means absence of risk.
    caveats: list[str] = []
    for c in collectors:
        if c["status"] in ("UNAVAILABLE", "PARTIAL") and c["unavailable_reason"]:
            caveats.append(f"{c['collector_id']}: {c['unavailable_reason']}")

    return {
        "run": dict(run) if run else None,
        "org": dict(caps) if caps else None,
        "totals": totals,
        "by_type": by_type,
        "collectors": [dict(c) for c in collectors],
        "coverage_caveats": caveats,
    }


@router.get("/runs/{run_id}/components")
async def components(
    run_id: str,
    verdict: str | None = None,
    ctype: str | None = None,
    q: str | None = None,
    limit: int = Query(500, le=5000),
) -> list[dict]:
    sql = """
        SELECT c.id, c.ctype::text AS ctype, c.api_name, c.label,
               c.parent_object, c.in_scope, c.last_modified_date,
               cl.label::text AS verdict, cl.confidence, cl.reason_codes,
               cl.had_gaps, cl.removal_prerequisites,
               (SELECT count(*) FROM evidence e
                 WHERE e.component_id = c.id AND e.result = 'EVIDENCE_OF_USE') AS hits,
               (SELECT count(*) FROM evidence e
                 WHERE e.component_id = c.id
                   AND e.result = 'NO_EVIDENCE_FOUND') AS clean,
               (SELECT count(*) FROM evidence e
                 WHERE e.component_id = c.id
                   AND e.result IN ('INCONCLUSIVE','NOT_APPLICABLE')) AS unclear,
               (SELECT count(*) FROM evidence_gaps g
                 WHERE g.component_id = c.id) AS gaps,
               (SELECT count(*) FROM uncertainty_flags u
                 WHERE u.component_id = c.id) AS flags
          FROM components c
          LEFT JOIN classifications cl ON cl.component_id = c.id
         WHERE c.run_id = :r
    """
    params: dict[str, Any] = {"r": run_id, "lim": limit}
    if verdict:
        sql += " AND cl.label = CAST(:v AS verdict)"
        params["v"] = verdict
    if ctype:
        sql += " AND c.ctype = CAST(:t AS component_type)"
        params["t"] = ctype
    if q:
        sql += " AND c.api_name ILIKE :q"
        params["q"] = f"%{q}%"
    sql += " ORDER BY cl.label, cl.confidence DESC, c.api_name LIMIT :lim"

    async with session_scope() as s:
        rows = (await s.execute(text(sql), params)).mappings().all()
    return [dict(r) for r in rows]


@router.get("/runs/{run_id}/components/{component_id}")
async def component_detail(run_id: str, component_id: int) -> dict:
    async with session_scope() as s:
        comp = (await s.execute(text("""
            SELECT c.id, c.ctype::text AS ctype, c.api_name, c.label, c.namespace,
                   c.parent_object, c.in_scope, c.out_of_scope_reason, c.attrs,
                   c.created_date, c.last_modified_date, c.sf_id
              FROM components c WHERE c.id = :c AND c.run_id = :r
        """), {"c": component_id, "r": run_id})).mappings().first()
        if not comp:
            raise HTTPException(404, "component not found")

        cls = (await s.execute(text("""
            SELECT label::text AS label, confidence, reason_codes, rule_trace,
                   score_breakdown, completeness, had_gaps, removal_prerequisites
              FROM classifications WHERE component_id = :c
        """), {"c": component_id})).mappings().first()

        # Positive AND negative evidence, joined to the collector so the UI can
        # show the literal query used. "We searched here and found nothing" is
        # only credible if you can see what was searched.
        ev = (await s.execute(text("""
            SELECT e.collector_id, e.result::text AS result, e.tier::text AS tier,
                   e.weight, e.payload, cr.method, cr.status::text AS collector_status,
                   cr.artifacts_searched, cr.unavailable_reason
              FROM evidence e
              LEFT JOIN collector_run cr
                     ON cr.run_id = e.run_id AND cr.collector_id = e.collector_id
             WHERE e.component_id = :c ORDER BY e.collector_id
        """), {"c": component_id})).mappings().all()

        gaps = (await s.execute(text("""
            SELECT collector_id, reason, detail, stage_key FROM evidence_gaps
             WHERE component_id = :c
        """), {"c": component_id})).mappings().all()

        flags = (await s.execute(text("""
            SELECT code, severity, detail FROM uncertainty_flags
             WHERE component_id = :c
        """), {"c": component_id})).mappings().all()

        refs = (await s.execute(text("""
            SELECT a.metadata_type, a.member_name, a.is_active, a.is_test,
                   e.tier::text AS tier, e.match_kind, e.tags
              FROM reference_edges e JOIN artifact a ON a.id = e.from_artifact_id
             WHERE e.to_component_id = :c
             ORDER BY e.tier, a.metadata_type, a.member_name LIMIT 200
        """), {"c": component_id})).mappings().all()

        # AI narration is returned in its own key, never merged into evidence.
        # The UI must be able to render it as unmistakably generated: a reader
        # who cannot tell prose from verified fact will act on the wrong one.
        llm = (await s.execute(text("""
            SELECT provider, model, business_purpose, evidence_recap, risk_note,
                   unused_rationale, status, generated_at, prompt_sha256,
                   request, input_tokens, output_tokens
              FROM llm_artifacts WHERE component_id = :c
        """), {"c": component_id})).mappings().first()

    return {
        "component": dict(comp),
        "classification": dict(cls) if cls else None,
        "evidence": [dict(e) for e in ev],
        "gaps": [dict(g) for g in gaps],
        "flags": [dict(f) for f in flags],
        "references": [dict(r) for r in refs],
        "llm": dict(llm) if llm else None,
    }


@router.get("/runs/{run_id}/graph")
async def graph(
    run_id: str,
    max_nodes: int = Query(400, le=2000),
    only_scope: bool = True,
) -> dict:
    key = f"{run_id}:{max_nodes}:{only_scope}"
    if key in _graph_cache:
        return _graph_cache[key]
    g = await build_graph(run_id)
    stats = compute_reachability(g)
    async with session_scope() as s:
        # Carried into the node payload so a hover can explain the verdict
        # without a request per node — the point of a tooltip is that it appears
        # instantly, and a round trip defeats that.
        verdicts = {
            r.component_id: {
                "label": r.label,
                "confidence": round(r.confidence or 0),
                "reasons": list(r.reason_codes or []),
                "prereqs": len(r.removal_prerequisites or []),
                "gaps": r.had_gaps,
            }
            for r in (await s.execute(text(
                "SELECT component_id, label::text AS label, confidence, "
                "reason_codes, removal_prerequisites, had_gaps "
                "FROM classifications WHERE run_id = :r"), {"r": run_id})).all()
        }
    payload = to_cytoscape(g, only_scope=only_scope, max_nodes=max_nodes,
                           verdicts=verdicts)
    payload["stats"] = stats
    _graph_cache[key] = payload
    return payload


@router.post("/runs/{run_id}/report")
async def build_report(run_id: str) -> dict:
    """Generate the report artifacts for a run.

    Server-side by necessity: the evidence set is far larger than a browser
    should hold, and a report is a run artifact any user can re-download rather
    than a per-browser accident.
    """
    from app.report.builder import build_all

    files = await build_all(run_id)
    return {
        "run_id": run_id,
        "files": {k: Path(v).name for k, v in files.items()},
        "paths": files,
        "download": {k: f"/api/runs/{run_id}/report/{k}" for k in files},
    }


@router.get("/runs/{run_id}/report/{kind}")
async def download_report(run_id: str, kind: str):
    """Stream a previously-generated artifact."""
    from fastapi.responses import FileResponse

    from app.report.builder import build_all

    files = await build_all(run_id)   # idempotent; regenerates from current data
    if kind not in files:
        raise HTTPException(404, f"unknown artifact {kind!r}; have {list(files)}")
    p = Path(files[kind])
    if not p.exists():
        raise HTTPException(404, "artifact missing on disk")
    media = {
        "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "markdown": "text/markdown",
        "json": "application/json",
        "destructive_changes": "application/xml",
    }.get(kind, "application/octet-stream")
    return FileResponse(p, media_type=media, filename=p.name)


@router.get("/runs/{run_id}/graph/path/{component_id}")
async def reachability_path(run_id: str, component_id: int) -> dict:
    """The chain from a component back to the entry point that keeps it alive.

    This is the answer to "why is this used?" in the form a reviewer can act on.
    """
    g = await build_graph(run_id)
    compute_reachability(g)
    node = g.nodes.get(f"component:{component_id}")
    if not node:
        raise HTTPException(404, "component not in graph")
    return {
        "component": node.label,
        "reachable": node.reachable,
        "hops": node.distance,
        "path": list(reversed(g.path_to_root(node.key))),
        "inbound_edges": len(g.inc.get(node.key, ())),
    }
