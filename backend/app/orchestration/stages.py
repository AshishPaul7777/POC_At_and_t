"""Stage implementations.

Thin wrappers over the pipeline modules. Each stage's job is to run the real work
and translate it into progress events and a result summary — the analysis logic
stays where it belongs, so the CLI scripts and the UI execute exactly the same
code paths and cannot drift apart.
"""

from __future__ import annotations

import structlog

from app.config import get_settings
from app.orchestration.runner import Orchestrator
from app.salesforce.client import SalesforceClient

log = structlog.get_logger()


async def s_connect(o: Orchestrator) -> dict:
    from app.salesforce import capabilities as cap

    conn = o.ctx["conn"]
    async with SalesforceClient() as sf:
        o.bus.emit("stage.note", "org.connect", text="authenticating")
        me = await sf.identity()
        o.bus.emit("stage.note", "org.connect",
                   text=f"running as {me.get('preferred_username')}")

        # Reuse a cached probe when present: it costs ~46 calls and an org's
        # capabilities do not change between runs minutes apart.
        caps = await cap.load(me["organization_id"])
        if caps is None:
            o.bus.emit("stage.note", "org.connect",
                       text="probing capabilities (first run for this org)")
            caps = await cap.probe(sf, conn.alias)
            await cap.save(caps)
        else:
            o.bus.emit("stage.note", "org.connect", text="using cached capabilities")
            caps.api_limit_remaining, caps.api_limit_max = \
                await sf.daily_api_remaining()
            # Event Monitoring can flip from empty → licensed (or retention
            # refill) without other caps changing. Re-check cheaply when the
            # cache said unavailable, so C40 does not inherit a stale blind spot.
            if caps.has_event_monitoring is False:
                refreshed = await cap.refresh_event_monitoring(sf, caps)
                if refreshed:
                    o.bus.emit("stage.note", "org.connect",
                               text="Event Monitoring now visible — cache updated")
            await cap.save(caps)

        o.ctx["caps"] = caps
        o.bus.emit("budget.tick", "org.connect",
                   remaining=caps.api_limit_remaining, limit=caps.api_limit_max)
        for c in caps.coverage_caveats():
            # Surfaced as events so the UI can show limitations as they are
            # discovered, not only in the final report.
            o.bus.emit("coverage.caveat", "org.connect", text=c)
        spent = sf.governor.snapshot().consumed_by_run

    return {"edition": caps.edition, "org": caps.org_name,
            "api_remaining": caps.api_limit_remaining,
            "event_monitoring": caps.has_event_monitoring,
            "dependency_api": caps.has_dependency_api,
            "caveats": len(caps.coverage_caveats()), "api_calls": spent}


async def s_inventory(o: Orchestrator) -> dict:
    from app.pipeline.inventory import inventory

    async with SalesforceClient() as sf:
        counts = await inventory(o.run_id, sf)
        spent = sf.governor.snapshot().consumed_by_run
    in_scope = sum(v for k, v in counts.items()
                   if not any(x in k for x in ("packaged", "skipped", "out of scope")))
    o.bus.emit("stage.note", "inventory",
               text=f"{in_scope} in-scope components")
    return {"api_calls": spent, "in_scope": in_scope, **counts}


async def s_retrieve(o: Orchestrator) -> dict:
    from app.pipeline.retrieve import retrieve_all
    from app.salesforce.auth import build_token_provider

    s_cfg = get_settings()
    conn = o.ctx["conn"]
    ws = s_cfg.workspace / conn.alias
    o.bus.emit("stage.note", "source.retrieve", text=f"workspace {ws}")

    def progress(label: str, done: int, total: int) -> None:
        o.bus.emit("stage.note", "source.retrieve", text=label)
        o.bus.progress("source.retrieve", done, total)

    result = await retrieve_all(conn, build_token_provider(s_cfg), ws,
                                on_progress=progress)
    o.ctx["retrieve"] = result

    for w in result.warnings:
        o.bus.emit("coverage.caveat", "source.retrieve", text=w)
    out = {"files": result.files_retrieved, "bytes": result.bytes_retrieved,
           "chunks": len(result.manifests),
           "failed_types": len(result.failed_types)}
    if result.degraded:
        out["_state"] = "DEGRADED"
        out["_reason"] = f"{len(result.failed_types)} metadata type(s) failed"
    if result.files_retrieved == 0:
        raise RuntimeError("no metadata retrieved; check SF_CLI_PATH")
    return out


async def s_index(o: Orchestrator) -> dict:
    from app.pipeline.indexer import index_workspace
    from app.pipeline.inventory import inventory_ui_from_workspace

    s_cfg = get_settings()
    ws = s_cfg.workspace / o.ctx["conn"].alias
    # LWC/Aura attrs live in retrieved meta — inventory them before the index
    # so FlexiPage and cross-bundle alias matches have component rows to hit.
    ui_counts = await inventory_ui_from_workspace(o.run_id, ws)
    o.bus.emit("stage.note", "index",
               text=f"UI inventory: "
                    f"{ui_counts.get('LightningComponentBundle', 0)} LWC, "
                    f"{ui_counts.get('AuraDefinitionBundle', 0)} Aura")
    stats = await index_workspace(o.run_id, ws)
    o.ctx["dynamic_files"] = stats.dynamic_files
    if stats.dynamic_files:
        o.bus.emit("coverage.caveat", "index",
                   text=f"dynamic Apex in {len(stats.dynamic_files)} file(s): "
                        "every object they touch is held at NEEDS_REVIEW, because "
                        "a name built at runtime cannot be resolved statically")
    return {"artifacts": stats.artifacts, "tokens": stats.tokens,
            "literals": stats.literals, "edges": stats.edges,
            "dynamic_files": len(stats.dynamic_files),
            **{f"ui_{k}": v for k, v in ui_counts.items()
               if not k.endswith("excluded)")}}


async def s_collect(o: Orchestrator) -> dict:
    from app.pipeline import collectors as col
    from app.pipeline.collectors_data import collect_config_data

    caps = o.ctx["caps"]
    summary: dict = {}
    degraded = False

    async with SalesforceClient() as sf:
        sf.governor.apply_capabilities(
            compression=caps.composite_compression,
            api_remaining=caps.api_limit_remaining,
            api_max=caps.api_limit_max)
        sf.governor.on_tick(lambda snap: o.bus.emit(
            "budget.tick", "collect", **snap.as_event()))

        jobs = [
            ("C10_static_index", col.collect_static_references(o.run_id)),
            ("C20_data_population", col.collect_data_population(o.run_id, sf)),
            ("C30_dependency_api", col.collect_dependency_api(o.run_id, sf, caps)),
            ("C40_runtime", col.collect_runtime(o.run_id, sf, caps)),
            ("C50_temporal", col.collect_temporal(o.run_id)),
            ("C60_dynamic_apex", col.collect_dynamic_taint(
                o.run_id, o.ctx.get("dynamic_files", []))),
            ("C80_config_data", collect_config_data(o.run_id, sf)),
        ]
        for i, (name, coro) in enumerate(jobs, 1):
            o.bus.emit("collector.started", "collect", collector=name)
            outcome = await coro
            await col.persist(o.run_id, outcome)
            if outcome.status not in ("OK", "SKIPPED"):
                degraded = True
                if outcome.unavailable_reason:
                    o.bus.emit("coverage.caveat", "collect",
                               text=f"{name}: {outcome.unavailable_reason}")
            o.bus.emit("collector.finished", "collect", collector=name,
                       status=outcome.status, hits=outcome.hits,
                       evidence=len(outcome.evidence), flags=len(outcome.flags),
                       gaps=len(outcome.gaps))
            o.bus.progress("collect", i, len(jobs), collector=name)
            summary[name] = outcome.status
        spent = sf.governor.snapshot().consumed_by_run

    out = {"collectors": len(jobs), "api_calls": spent, **summary}
    if degraded:
        out["_state"] = "DEGRADED"
        out["_reason"] = "one or more collectors could not complete"
    return out


async def s_graph(o: Orchestrator) -> dict:
    from app.pipeline.graph import (
        build_graph,
        compute_reachability,
        persist_reachability,
    )

    g = await build_graph(o.run_id)
    stats = compute_reachability(g)
    await persist_reachability(o.run_id, g)
    o.bus.emit("stage.note", "graph",
               text=f"{stats['entry_points']} entry points; "
                    f"{stats['unreachable_components']} in-scope components "
                    "unreachable from anything that runs or is seen")
    return {"nodes": len(g.nodes),
            "edges": sum(len(v) for v in g.out.values()), **stats}


async def s_classify(o: Orchestrator) -> dict:
    from app.pipeline.classify import classify_run

    counts = await classify_run(o.run_id)
    o.bus.emit("verdicts", "classify", **counts)
    return dict(counts)


async def s_rehearse(o: Orchestrator) -> dict:
    from app.pipeline.classify import classify_run
    from app.pipeline.collectors import persist
    from app.pipeline.collectors_data import collect_delete_rehearsal
    from app.pipeline.retrieve import SalesforceCli
    from app.salesforce.auth import build_token_provider

    s_cfg = get_settings()
    if not s_cfg.analysis_enable_delete_rehearsal:
        return {"_state": "SKIPPED", "_reason": "disabled by config"}

    conn = o.ctx["conn"]

    def cli_factory():
        return SalesforceCli(conn, s_cfg.workspace / conn.alias,
                             build_token_provider(s_cfg))

    async with SalesforceClient() as sf:
        outcome = await collect_delete_rehearsal(
            o.run_id, sf, cli_factory,
            batch_size=s_cfg.analysis_rehearsal_batch_size)
    await persist(o.run_id, outcome)

    blocked = [e for e in outcome.evidence
               if e["payload"].get("rehearsal") == "BLOCKED"]
    for e in blocked:
        o.bus.emit("rehearsal.blocked", "rehearse",
                   blocker=str(e["payload"].get("blocker", ""))[:300])
    if outcome.status == "SKIPPED":
        return {"_state": "SKIPPED", "_reason": outcome.unavailable_reason}

    # Salesforce's answer outranks every inference we made, so re-classify.
    counts = await classify_run(o.run_id)
    o.bus.emit("verdicts", "rehearse", **counts)
    return {"candidates": outcome.artifacts, "passed": outcome.hits,
            "blocked": len(blocked), "inconclusive": len(outcome.gaps),
            "false_positives_found": len(blocked), **counts}


async def s_narrate(o: Orchestrator) -> dict:
    from app.pipeline.classify import classify_run
    from app.pipeline.narrate import narrate_run

    s_cfg = get_settings()
    if not s_cfg.llm_enabled:
        return {"_state": "SKIPPED", "_reason": "LLM_ENABLED=false"}

    # The client is passed in so narration can sample real field values; a
    # summary written from a field NAME alone is guesswork dressed as fact.
    async with SalesforceClient() as sf:
        n = await narrate_run(o.run_id, sf=sf)
    if n["status"] == "UNAVAILABLE":
        o.bus.emit("coverage.caveat", "narrate",
                   text=f"AI narration unavailable: {n.get('detail', '')[:160]}. "
                        "Verdicts are unaffected — narration is enrichment.")
        return {"_state": "DEGRADED", "_reason": "provider unreachable",
                "detail": str(n.get("detail", ""))[:200]}
    if n["status"] == "DISABLED":
        return {"_state": "SKIPPED", "_reason": "disabled"}

    if n.get("flagged_for_review") or n.get("promoted_to_used"):
        counts = await classify_run(o.run_id)
        o.bus.emit("verdicts", "narrate", **counts)
        n.update(counts)

    # Stage card: narration outcomes only — not provider diagnostics.
    out = {
        "generated": n.get("generated", 0),
        "failed": n.get("failed", 0),
        "refused": n.get("refused", 0),
        "flagged_for_review": n.get("flagged_for_review", 0),
        "promoted_to_used": n.get("promoted_to_used", 0),
        "candidates": n.get("candidates", 0),
        "model": n.get("model"),
    }
    if "USED" in n:
        out["USED"] = n["USED"]
        out["UNUSED"] = n.get("UNUSED", 0)
        out["NEEDS_REVIEW"] = n.get("NEEDS_REVIEW", 0)
    # Reporting SUCCEEDED when every call failed would be dishonest: the stage
    # produced nothing. It is DEGRADED, and the reason is surfaced as a caveat so
    # a reader knows the summaries are missing rather than empty by design.
    failed, generated = n.get("failed", 0), n.get("generated", 0)
    if failed and not generated:
        o.bus.emit("coverage.caveat", "narrate",
                   text=f"AI narration produced nothing: all {failed} attempts "
                        "failed (check the provider gateway and API key). "
                        "Verdicts are unaffected.")
        out["_state"] = "DEGRADED"
        out["_reason"] = f"all {failed} narration attempts failed"
    elif failed:
        out["_state"] = "DEGRADED"
        out["_reason"] = f"{failed} of {failed + generated} narrations failed"
    return out


async def s_report(o: Orchestrator) -> dict:
    from app.report.builder import build_all

    files = await build_all(o.run_id)
    o.bus.emit("report.ready", "report", files=list(files))
    return {"artifacts": len(files), **{k: v.split("\\")[-1].split("/")[-1]
                                       for k, v in files.items()}}


STAGE_FNS = {
    "org.connect": s_connect,
    "inventory": s_inventory,
    "source.retrieve": s_retrieve,
    "index": s_index,
    "collect": s_collect,
    "graph": s_graph,
    "classify": s_classify,
    "rehearse": s_rehearse,
    "narrate": s_narrate,
    "report": s_report,
}
