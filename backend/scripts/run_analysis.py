"""Run all collectors, then classify. Produces the first real verdicts.

    .venv\\Scripts\\python.exe scripts\\run_analysis.py [alias]
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import text                              # noqa: E402

from app.config import get_settings                      # noqa: E402
from app.db.session import engine, session_scope         # noqa: E402
from app.pipeline import collectors as col               # noqa: E402
from app.pipeline.classify import classify_run           # noqa: E402
from app.pipeline.collectors_data import collect_config_data  # noqa: E402
from app.pipeline.indexer import DYNAMIC_MARKERS         # noqa: E402
from app.salesforce import capabilities as cap           # noqa: E402
from app.salesforce.client import SalesforceClient       # noqa: E402
from app.salesforce.connections import load_registry     # noqa: E402


async def main() -> int:
    s_cfg = get_settings()
    conn = load_registry().get(sys.argv[1] if len(sys.argv) > 1 else None)

    async with session_scope() as s:
        row = (await s.execute(text(
            "SELECT id, org_id FROM runs WHERE org_alias = :a "
            "ORDER BY created_at DESC LIMIT 1"), {"a": conn.alias})).first()
    if not row:
        print("no run found — run scripts/setup_org.py first")
        return 1
    run_id, org_id = str(row.id), row.org_id
    print(f"run: {run_id}\n")

    caps = await cap.load(org_id)
    if not caps:
        print("no cached capabilities — run scripts/setup_org.py first")
        return 1

    # Files flagged as dynamic during indexing.
    async with session_scope() as s:
        dyn = [r.file_path for r in (await s.execute(text("""
            SELECT DISTINCT a.file_path FROM artifact a
              JOIN artifact_literal l ON l.artifact_id = a.id
             WHERE a.run_id = :r AND a.metadata_type = 'ApexClass'
        """), {"r": run_id})).all()]
    dynamic_files = []
    ws = s_cfg.workspace / conn.alias
    for f in dyn:
        for p in ws.rglob(Path(f).name):
            try:
                if any(m in p.read_text(encoding="utf-8", errors="replace").lower()
                       for m in DYNAMIC_MARKERS):
                    dynamic_files.append(f)
            except OSError:
                pass
            break

    print("=== running collectors ===")
    async with SalesforceClient() as sf:
        sf.governor.apply_capabilities(
            compression=caps.composite_compression,
            api_remaining=caps.api_limit_remaining,
            api_max=caps.api_limit_max)

        for name, coro in [
            ("C10 static references", col.collect_static_references(run_id)),
            ("C20 data population", col.collect_data_population(run_id, sf)),
            ("C30 dependency API", col.collect_dependency_api(run_id, sf, caps)),
            ("C40 runtime telemetry", col.collect_runtime(run_id, sf, caps)),
            ("C50 temporal", col.collect_temporal(run_id)),
            ("C60 dynamic Apex taint", col.collect_dynamic_taint(run_id, dynamic_files)),
            # API names stored as DATA in config records — invisible to every
            # metadata parser, and the most dangerous blind spot in the design.
            ("C80 config data", collect_config_data(run_id, sf)),
        ]:
            outcome = await coro
            await col.persist(run_id, outcome)
            extra = f"  ({outcome.unavailable_reason})" if outcome.unavailable_reason else ""
            print(f"  {name:26} {outcome.status:9} hits={outcome.hits:<5} "
                  f"evidence={len(outcome.evidence):<5} flags={len(outcome.flags)}{extra}")
        spent = sf.governor.snapshot().consumed_by_run

    # Reachability runs last: it needs the full edge set, and it answers the
    # question the other collectors only approximate — is this reachable from
    # anything that actually runs or is seen?
    from app.pipeline.graph import (  # noqa: PLC0415
        build_graph,
        compute_reachability,
        persist_reachability,
    )
    g = await build_graph(run_id)
    gstats = compute_reachability(g)
    await persist_reachability(run_id, g)
    print(f"  {'C70 reachability':26} {'OK':9} "
          f"entry_points={gstats['entry_points']:<4} "
          f"reachable={gstats['reachable']:<5} "
          f"unreachable={gstats['unreachable_components']}")

    print(f"\n  API calls used: {spent}  (reachability costs none — pure graph)")

    print("\n=== classifying ===")
    counts = await classify_run(run_id)

    # Delete rehearsal runs AFTER a provisional classification, because it needs
    # to know which components to ask about. Salesforce's answer then feeds a
    # second classification pass — it is the only server-validated signal here,
    # so it outranks every inference we made.
    if s_cfg.analysis_enable_delete_rehearsal and counts.get("UNUSED"):
        print(f"\n=== delete rehearsal ({counts['UNUSED']} candidates) ===")
        print("  validate-only; deletes nothing. Asking Salesforce whether each")
        print("  candidate COULD be removed, and what blocks it if not.")
        from app.pipeline.collectors_data import collect_delete_rehearsal
        from app.pipeline.retrieve import SalesforceCli
        from app.salesforce.auth import build_token_provider

        def cli_factory():
            return SalesforceCli(conn, s_cfg.workspace / conn.alias,
                                 build_token_provider(s_cfg))

        async with SalesforceClient() as sf2:
            outcome = await collect_delete_rehearsal(
                run_id, sf2, cli_factory,
                batch_size=s_cfg.analysis_rehearsal_batch_size)
        await col.persist(run_id, outcome)
        blocked = [e for e in outcome.evidence
                   if e["payload"].get("rehearsal") == "BLOCKED"]
        print(f"  status={outcome.status}  passed={outcome.hits}  "
              f"blocked={len(blocked)}  inconclusive={len(outcome.gaps)}")
        for e in blocked:
            print(f"    BLOCKED: {e['payload'].get('blocker', '')[:110]}")
        if blocked:
            print("  These are CONFIRMED FALSE POSITIVES from our analysis —")
            print("  the honest accuracy measure for this run.")

        print("\n=== reclassifying with the platform's answer ===")
        counts = await classify_run(run_id)

    # Narration runs LAST, on frozen verdicts. The model explains a decision the
    # rules already made; it never makes one. Its only influence is toward
    # caution — a flagged concern downgrades UNUSED to NEEDS_REVIEW.
    if s_cfg.llm_enabled:
        print("\n=== AI narration ===")
        from app.pipeline.narrate import narrate_run
        n = await narrate_run(run_id)
        if n["status"] == "UNAVAILABLE":
            print(f"  SKIPPED — {n.get('detail', '')[:150]}")
            print("  The analysis is complete and correct without it; narration")
            print("  is enrichment, not evidence.")
        elif n["status"] == "DISABLED":
            print("  disabled (LLM_ENABLED=false)")
        else:
            print(f"  provider={n.get('provider')} model={n.get('model')}")
            print(f"  base_url={n.get('base_url')}")
            print(f"  generated={n['generated']} of {n['candidates']} candidates"
                  f"  refused={n.get('refused', 0)}  failed={n.get('failed', 0)}")
            if n.get("flagged_for_review"):
                print(f"  {n['flagged_for_review']} component(s) flagged by the AI as")
                print("  possibly still mattering -> downgraded to NEEDS_REVIEW.")
                print("\n=== reclassifying after AI-raised concerns ===")
                counts = await classify_run(run_id)

    for label in ("USED", "UNUSED", "NEEDS_REVIEW", "OUT_OF_SCOPE"):
        print(f"  {label:16} {counts.get(label, 0):>5}")

    async with session_scope() as s:
        print("\n=== verdicts by component type ===")
        for r in (await s.execute(text("""
            SELECT c.ctype::text AS ctype, cl.label::text AS label, count(*) n
              FROM classifications cl JOIN components c ON c.id = cl.component_id
             WHERE cl.run_id = :r AND c.in_scope
             GROUP BY 1,2 ORDER BY 1,2
        """), {"r": run_id})).all():
            print(f"  {r.ctype:14} {r.label:14} {r.n:>4}")

        print("\n=== UNUSED candidates, with what each needs before deletion ===")
        rows = (await s.execute(text("""
            SELECT c.ctype::text AS ctype, c.api_name, cl.confidence,
                   cl.reason_codes, cl.removal_prerequisites
              FROM classifications cl JOIN components c ON c.id = cl.component_id
             WHERE cl.run_id = :r AND cl.label = 'UNUSED'
             ORDER BY jsonb_array_length(cl.removal_prerequisites),
                      cl.confidence DESC, c.api_name
        """), {"r": run_id})).all()
        if not rows:
            print("  none — every component has some evidence or an open question")
        for r in rows:
            print(f"\n  [{r.confidence:5.1f}] {r.ctype:12} {r.api_name}")
            prereqs = r.removal_prerequisites or []
            if not prereqs:
                print("           ready to delete: no prerequisites found")
            for i, p in enumerate(prereqs, 1):
                flag = "BLOCKING" if p.get("blocking") else "advised "
                print(f"           {i}. [{flag}] {p['step']}")
                if p.get("targets"):
                    tgts = p["targets"]
                    print(f"              -> {', '.join(tgts[:3])}"
                          + (f" (+{len(tgts) - 3} more)" if len(tgts) > 3 else ""))
                print(f"              {p['reason']}")

        blocking = await s.scalar(text("""
            SELECT count(*) FROM classifications
             WHERE run_id = :r AND label = 'UNUSED'
               AND jsonb_path_exists(removal_prerequisites, '$[*] ? (@.blocking == true)')
        """), {"r": run_id})
        if blocking:
            print(f"\n  {blocking} of these have a BLOCKING prerequisite: a destructive")
            print("  deploy will fail until it is cleared. None is one-click safe.")

        print("\n=== why NEEDS_REVIEW (top reasons) ===")
        for r in (await s.execute(text("""
            SELECT unnest(reason_codes) AS reason, count(*) n
              FROM classifications
             WHERE run_id = :r AND label = 'NEEDS_REVIEW'
             GROUP BY 1 ORDER BY 2 DESC LIMIT 10
        """), {"r": run_id})).all():
            print(f"  {r.n:>4}  {r.reason}")

        print("\n=== worked example: full evidence trail ===")
        ex = (await s.execute(text("""
            SELECT c.id, c.api_name, cl.label::text AS label, cl.confidence
              FROM classifications cl JOIN components c ON c.id = cl.component_id
             WHERE cl.run_id = :r AND c.in_scope AND c.ctype = 'CustomField'
             ORDER BY (cl.label = 'UNUSED') DESC, cl.confidence DESC LIMIT 1
        """), {"r": run_id})).first()
        if ex:
            print(f"  {ex.api_name}  ->  {ex.label} ({ex.confidence:.0f})")
            for e in (await s.execute(text("""
                SELECT collector_id, result::text AS result, tier::text AS tier, payload
                  FROM evidence WHERE component_id = :c ORDER BY collector_id
            """), {"c": ex.id})).all():
                tier = f" tier {e.tier}" if e.tier else ""
                print(f"    {e.collector_id:22} {e.result:18}{tier}")
                print(f"        {str(e.payload)[:120]}")

    await engine.dispose()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
