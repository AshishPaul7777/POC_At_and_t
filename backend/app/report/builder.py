"""Report generation.

The deliverable. Everything upstream exists so this document can be trusted, so
it is built to be *falsifiable* rather than persuasive:

  * Coverage limitations are a mandatory, prominent section — not an appendix
    footnote. A reader must be able to see what this run could NOT prove.
  * Negative evidence ships alongside positive, in long form, one row per
    component per collector. "We searched here and found nothing" is the
    load-bearing claim behind every deletion candidate.
  * AI-written prose is confined to clearly-labelled columns and never mixed
    into a cell beside verified data.
  * Nothing is presented as one-click safe. Every candidate carries its removal
    prerequisites and the staged procedure.

Server-side by necessity: the evidence set is far larger than a browser should
hold, and a report is a run artifact any user can re-download rather than a
per-browser accident.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from xml.sax.saxutils import escape

import structlog
from sqlalchemy import text

from app.config import get_settings
from app.db.session import session_scope

log = structlog.get_logger()

COLLECTOR_LABEL = {
    "C10_static_index": "Static references",
    "C20_data_population": "Record data",
    "C30_dependency_api": "Salesforce dependency API",
    "C40_runtime": "Runtime execution",
    "C50_temporal": "Recent changes",
    "C60_dynamic_apex": "Dynamic Apex",
    "C70_reachability": "Reachability from entry points",
    "C80_config_data": "Config data (CMDT / custom settings)",
    "C90_delete_rehearsal": "Delete rehearsal (server-validated)",
    "S40_narration": "AI narration",
}

RESULT_LABEL = {
    "EVIDENCE_OF_USE": "FOUND",
    "NO_EVIDENCE_FOUND": "SEARCHED — NOTHING FOUND",
    "INCONCLUSIVE": "INCONCLUSIVE",
    "NOT_APPLICABLE": "UNAVAILABLE HERE",
    "FAILED": "CHECK FAILED",
}


@dataclass
class ReportData:
    run: dict
    org: dict | None
    totals: dict[str, int]
    by_type: list[dict]
    components: list[dict]
    evidence: list[dict]
    collectors: list[dict]
    caveats: list[str]
    generated_at: str


async def gather(run_id: str) -> ReportData:
    async with session_scope() as s:
        run = (await s.execute(text("""
            SELECT id::text, org_id, org_alias, api_version, state::text AS state,
                   run_as_username, started_at, finished_at, tool_versions
              FROM runs WHERE id = :r
        """), {"r": run_id})).mappings().first()
        if not run:
            raise ValueError(f"no such run: {run_id}")

        org = (await s.execute(text("""
            SELECT * FROM org_capabilities WHERE org_id = :o
        """), {"o": run["org_id"]})).mappings().first()

        totals_rows = (await s.execute(text("""
            SELECT cl.label::text AS label, count(*) n
              FROM classifications cl JOIN components c ON c.id = cl.component_id
             WHERE cl.run_id = :r GROUP BY 1
        """), {"r": run_id})).all()

        by_type = (await s.execute(text("""
            SELECT c.ctype::text AS ctype, cl.label::text AS label, count(*) n
              FROM classifications cl JOIN components c ON c.id = cl.component_id
             WHERE cl.run_id = :r AND c.in_scope GROUP BY 1,2 ORDER BY 1,2
        """), {"r": run_id})).mappings().all()

        components = (await s.execute(text("""
            SELECT c.id, c.ctype::text AS ctype, c.api_name, c.label AS field_label,
                   c.parent_object, c.namespace, c.in_scope, c.created_date,
                   c.last_modified_date, c.attrs,
                   cl.label::text AS verdict, cl.confidence, cl.reason_codes,
                   cl.removal_prerequisites, cl.had_gaps, cl.completeness,
                   cl.rule_trace,
                   l.business_purpose, l.evidence_recap, l.risk_note,
                   l.status AS llm_status, l.model AS llm_model
              FROM components c
              LEFT JOIN classifications cl ON cl.component_id = c.id
              LEFT JOIN llm_artifacts l ON l.component_id = c.id
             WHERE c.run_id = :r
             ORDER BY (cl.label = 'UNUSED') DESC, c.ctype, c.api_name
        """), {"r": run_id})).mappings().all()

        evidence = (await s.execute(text("""
            SELECT c.api_name, c.ctype::text AS ctype, e.collector_id,
                   e.result::text AS result, e.tier::text AS tier, e.payload,
                   cr.method, cr.status::text AS collector_status,
                   cr.artifacts_searched, cr.unavailable_reason
              FROM evidence e
              JOIN components c ON c.id = e.component_id
              LEFT JOIN collector_run cr
                     ON cr.run_id = e.run_id AND cr.collector_id = e.collector_id
             WHERE e.run_id = :r AND c.in_scope
             ORDER BY c.ctype, c.api_name, e.collector_id
        """), {"r": run_id})).mappings().all()

        collectors = (await s.execute(text("""
            SELECT collector_id, collector_family, status::text AS status, method,
                   artifacts_searched, hits, unavailable_reason, error_text
              FROM collector_run WHERE run_id = :r ORDER BY collector_id
        """), {"r": run_id})).mappings().all()

    caveats: list[str] = []
    for c in collectors:
        if c["status"] in ("UNAVAILABLE", "PARTIAL", "ERROR") and (
                c["unavailable_reason"] or c["error_text"]):
            caveats.append(
                f"{COLLECTOR_LABEL.get(c['collector_id'], c['collector_id'])} "
                f"({c['status']}): {c['unavailable_reason'] or c['error_text']}")

    return ReportData(
        run=dict(run), org=dict(org) if org else None,
        totals={r.label: r.n for r in totals_rows},
        by_type=[dict(r) for r in by_type],
        components=[dict(r) for r in components],
        evidence=[dict(r) for r in evidence],
        collectors=[dict(r) for r in collectors],
        caveats=caveats,
        generated_at=datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC"),
    )


# ---------------------------------------------------------------------------
# XLSX — the working artifact someone plans a cleanup sprint in
# ---------------------------------------------------------------------------

def write_xlsx(d: ReportData, path: Path) -> Path:
    import xlsxwriter

    path.parent.mkdir(parents=True, exist_ok=True)
    wb = xlsxwriter.Workbook(str(path), {"constant_memory": True})

    f = {
        "h1": wb.add_format({"bold": True, "font_size": 15}),
        "h2": wb.add_format({"bold": True, "font_size": 12, "bottom": 1}),
        "hdr": wb.add_format({"bold": True, "bg_color": "#11151D",
                              "font_color": "#E6E9EF", "border": 1}),
        "body": wb.add_format({"valign": "top", "text_wrap": True}),
        "mono": wb.add_format({"font_name": "Consolas", "valign": "top"}),
        "wrap": wb.add_format({"text_wrap": True, "valign": "top"}),
        # Verdict colours mirror the UI exactly, so the two never disagree.
        "USED": wb.add_format({"font_color": "#1E7B5C", "bold": True}),
        "UNUSED": wb.add_format({"font_color": "#9A6209", "bold": True}),
        "NEEDS_REVIEW": wb.add_format({"font_color": "#33499C", "bold": True}),
        "OUT_OF_SCOPE": wb.add_format({"font_color": "#6B7383"}),
        # AI prose is tinted and italic so it cannot be mistaken for a finding,
        # and the distinction survives a greyscale print.
        "ai": wb.add_format({"italic": True, "font_color": "#5B44B8",
                             "text_wrap": True, "valign": "top"}),
        "warn": wb.add_format({"font_color": "#9A6209", "text_wrap": True,
                               "valign": "top"}),
    }

    # -- Summary --------------------------------------------------------------
    ws = wb.add_worksheet("Summary")
    ws.set_column(0, 0, 34)
    ws.set_column(1, 1, 74)
    r = 0
    ws.write(r, 0, "Salesforce Org Cleanup — Analysis Report", f["h1"]); r += 2
    for k, v in [
        ("Org", f"{d.run['org_alias']} ({d.run['org_id']})"),
        ("Org name", (d.org or {}).get("org_name", "—")),
        ("Edition", (d.org or {}).get("edition", "—")),
        ("API version", d.run["api_version"]),
        ("Analysis ran as", d.run["run_as_username"] or "—"),
        ("Run id", d.run["id"]),
        ("Generated", d.generated_at),
    ]:
        ws.write(r, 0, k, f["hdr"]); ws.write(r, 1, str(v), f["body"]); r += 1
    r += 1

    ws.write(r, 0, "Verdicts", f["h2"]); r += 1
    for label in ("USED", "UNUSED", "NEEDS_REVIEW", "OUT_OF_SCOPE"):
        ws.write(r, 0, label.replace("_", " "), f.get(label, f["body"]))
        ws.write_number(r, 1, d.totals.get(label, 0))
        r += 1
    r += 1

    ws.write(r, 0, "What the verdicts mean", f["h2"]); r += 1
    for k, v in [
        ("USED", "Something that runs or is seen depends on this: Apex, a "
                 "trigger, a flow, a report, a UI component, or real data."),
        ("UNUSED", "No business process references it and no record holds a "
                   "value. NOT the same as safe to delete in one step — see the "
                   "prerequisites on each candidate."),
        ("NEEDS REVIEW", "The evidence is ambiguous, or a check could not run. "
                         "A human decides. This is the deliberate default "
                         "whenever anything is uncertain."),
        ("OUT OF SCOPE", "Managed-package or standard components. They cannot "
                         "be deleted, so no verdict is offered."),
    ]:
        ws.write(r, 0, k, f["hdr"]); ws.write(r, 1, v, f["wrap"]); r += 1
    r += 2

    ws.write(r, 0, "IMPORTANT", f["h2"]); r += 1
    ws.write(r, 0, "This report is not an authorisation to delete.", f["warn"])
    ws.write(r, 1,
             "Every candidate must go through the staged procedure on the "
             "'Remediation' sheet. Deleted custom fields are recoverable from "
             "the recycle bin for 15 days; deleted Apex is not recoverable at "
             "all without a source snapshot.", f["warn"])

    # -- Coverage & limitations (mandatory, early on purpose) -----------------
    ws = wb.add_worksheet("Coverage & Limits")
    ws.set_column(0, 0, 40); ws.set_column(1, 1, 96)
    r = 0
    ws.write(r, 0, "Coverage and limitations", f["h1"]); r += 1
    ws.write(r, 0, "What this run could NOT prove. These are not errors — they "
                   "are the honest bounds of what is knowable in this org.",
             f["wrap"]); r += 2
    if d.caveats:
        for c in d.caveats:
            ws.write(r, 0, "LIMITATION", f["warn"])
            ws.write(r, 1, c, f["warn"]); r += 1
    else:
        ws.write(r, 0, "None detected", f["body"]); r += 1
    r += 1

    ws.write(r, 0, "Known blind spots (structural)", f["h2"]); r += 1
    for b in [
        "API names assembled at runtime from data we cannot see.",
        "External integrations (ETL, middleware, partner apps) whose field "
        "mappings live outside the org entirely.",
        "Ad-hoc reports built in the report builder and never saved.",
        "Anonymous Apex, which is not persisted and cannot be inspected.",
        "Managed-package code reading your fields via dynamic describe.",
        "Anything created between this analysis and the eventual deletion.",
    ]:
        ws.write(r, 1, b, f["wrap"]); r += 1
    r += 1

    ws.write(r, 0, "Collectors", f["h2"]); r += 1
    for col, name in enumerate(["Collector", "Status", "Searched", "Hits", "Method"]):
        ws.write(r, col, name, f["hdr"])
    r += 1
    ws.set_column(4, 4, 90)
    for c in d.collectors:
        ws.write(r, 0, COLLECTOR_LABEL.get(c["collector_id"], c["collector_id"]), f["body"])
        ws.write(r, 1, c["status"], f["warn"] if c["status"] != "OK" else f["body"])
        ws.write_number(r, 2, c["artifacts_searched"] or 0)
        ws.write_number(r, 3, c["hits"] or 0)
        ws.write(r, 4, c["method"] or "", f["wrap"])
        r += 1

    # -- Deletion candidates --------------------------------------------------
    ws = wb.add_worksheet("Deletion Candidates")
    cands = [c for c in d.components if c["verdict"] == "UNUSED"]
    headers = ["Component", "Type", "Object", "Confidence", "Why",
               "Prerequisites before deleting", "AI summary (unverified)",
               "AI: argument for keeping"]
    widths = [46, 14, 22, 11, 52, 62, 58, 44]
    for col, (h, w) in enumerate(zip(headers, widths, strict=False)):
        ws.write(0, col, h, f["hdr"]); ws.set_column(col, col, w)
    ws.freeze_panes(1, 0)
    ws.autofilter(0, 0, max(len(cands), 1), len(headers) - 1)
    for i, c in enumerate(cands, start=1):
        prereqs = c["removal_prerequisites"] or []
        steps = "\n".join(
            f"{n}. {p['step']}"
            + (f" [{', '.join(p['targets'][:3])}]" if p.get("targets") else "")
            + ("  (BLOCKING)" if p.get("blocking") else "")
            for n, p in enumerate(prereqs, 1))
        ws.write(i, 0, c["api_name"], f["mono"])
        ws.write(i, 1, c["ctype"], f["body"])
        ws.write(i, 2, c["parent_object"] or "", f["body"])
        ws.write_number(i, 3, round(c["confidence"] or 0))
        ws.write(i, 4, "; ".join(c["reason_codes"] or []), f["wrap"])
        ws.write(i, 5, steps, f["wrap"])
        # AI columns are visually distinct AND labelled in the header.
        ws.write(i, 6, c["business_purpose"] or "", f["ai"])
        ws.write(i, 7, c["risk_note"] or "", f["ai"])

    # -- Needs review ---------------------------------------------------------
    ws = wb.add_worksheet("Needs Review")
    review = [c for c in d.components if c["verdict"] == "NEEDS_REVIEW"]
    headers = ["Component", "Type", "Object", "Why it is uncertain",
               "Question a human must answer", "AI summary (unverified)"]
    widths = [46, 14, 22, 54, 54, 58]
    for col, (h, w) in enumerate(zip(headers, widths, strict=False)):
        ws.write(0, col, h, f["hdr"]); ws.set_column(col, col, w)
    ws.freeze_panes(1, 0)
    ws.autofilter(0, 0, max(len(review), 1), len(headers) - 1)
    for i, c in enumerate(review, start=1):
        ws.write(i, 0, c["api_name"], f["mono"])
        ws.write(i, 1, c["ctype"], f["body"])
        ws.write(i, 2, c["parent_object"] or "", f["body"])
        ws.write(i, 3, "; ".join(c["reason_codes"] or []), f["wrap"])
        ws.write(i, 4, _question_for(c), f["wrap"])
        ws.write(i, 5, c["business_purpose"] or "", f["ai"])

    # -- All components -------------------------------------------------------
    ws = wb.add_worksheet("All Components")
    headers = ["Component", "Type", "Object", "Verdict", "Confidence",
               "Reasons", "In scope", "Gaps", "Last modified"]
    widths = [46, 14, 22, 15, 11, 46, 10, 8, 20]
    for col, (h, w) in enumerate(zip(headers, widths, strict=False)):
        ws.write(0, col, h, f["hdr"]); ws.set_column(col, col, w)
    ws.freeze_panes(1, 0)
    ws.autofilter(0, 0, max(len(d.components), 1), len(headers) - 1)
    for i, c in enumerate(d.components, start=1):
        ws.write(i, 0, c["api_name"], f["mono"])
        ws.write(i, 1, c["ctype"], f["body"])
        ws.write(i, 2, c["parent_object"] or "", f["body"])
        ws.write(i, 3, (c["verdict"] or "").replace("_", " "),
                 f.get(c["verdict"] or "", f["body"]))
        ws.write(i, 4, round(c["confidence"] or 0) if c["confidence"] else "", f["body"])
        ws.write(i, 5, "; ".join(c["reason_codes"] or []), f["wrap"])
        ws.write(i, 6, "yes" if c["in_scope"] else "no", f["body"])
        ws.write(i, 7, "yes" if c["had_gaps"] else "", f["body"])
        ws.write(i, 8, str(c["last_modified_date"] or "")[:19], f["body"])

    # -- Evidence detail (long format) ---------------------------------------
    # One row per component per collector, INCLUDING the misses. This is what
    # makes the report auditable rather than merely assertive.
    ws = wb.add_worksheet("Evidence Detail")
    headers = ["Component", "Type", "Collector", "Result", "Tier",
               "What was searched", "Findings"]
    widths = [44, 13, 30, 26, 7, 74, 68]
    for col, (h, w) in enumerate(zip(headers, widths, strict=False)):
        ws.write(0, col, h, f["hdr"]); ws.set_column(col, col, w)
    ws.freeze_panes(1, 0)
    ws.autofilter(0, 0, max(len(d.evidence), 1), len(headers) - 1)
    for i, e in enumerate(d.evidence, start=1):
        ws.write(i, 0, e["api_name"], f["mono"])
        ws.write(i, 1, e["ctype"], f["body"])
        ws.write(i, 2, COLLECTOR_LABEL.get(e["collector_id"], e["collector_id"]), f["body"])
        ws.write(i, 3, RESULT_LABEL.get(e["result"], e["result"]),
                 f["body"] if e["result"] == "EVIDENCE_OF_USE" else f["wrap"])
        ws.write(i, 4, e["tier"] or "", f["body"])
        ws.write(i, 5, e["method"] or "", f["wrap"])
        ws.write(i, 6, json.dumps(e["payload"], default=str)[:600], f["wrap"])

    # -- Remediation ----------------------------------------------------------
    ws = wb.add_worksheet("Remediation")
    ws.set_column(0, 0, 8); ws.set_column(1, 1, 34); ws.set_column(2, 2, 96)
    r = 0
    ws.write(r, 0, "Staged deletion procedure", f["h1"]); r += 1
    ws.write(r, 0, "", f["body"])
    ws.write(r, 2, "Do not skip the observation window. It is the step that "
                   "catches what static analysis structurally cannot: "
                   "integrations, dynamic Apex, and ad-hoc reports.", f["warn"])
    r += 2
    for n, (title, detail) in enumerate([
        ("Snapshot", "Retrieve the metadata and export the affected data to CSV. "
                     "Commit both. This is the only durable rollback: deleted "
                     "fields are recoverable for 15 days, deleted Apex never."),
        ("Rehearse", "Run a validate-only destructive deploy in a sandbox, then "
                     "against production. Salesforce names anything that still "
                     "depends on the component. This report already did this "
                     "once — re-run it, because the org may have changed."),
        ("Deprecate in place", "Rename the LABEL (never the API name) to "
                              "ZZ_DEPRECATED_*, remove from layouts and list "
                              "views, and revoke field-level security from every "
                              "profile and permission set."),
        ("Observe", "Wait at least 60 days, spanning a month-end, a quarter-end "
                    "and any known annual job. Revoked FLS is what makes this "
                    "informative: a hidden dependency now fails loudly instead "
                    "of silently reading null."),
        ("Delete", "In a change window, in batches of 25 or fewer, grouped by "
                   "feature so a rollback is coherent. Never one mass delete."),
        ("Verify", "Watch Apex exception emails, AsyncApexJob failures and "
                   "integration error queues for one further cycle."),
    ], start=1):
        ws.write(r, 0, n, f["hdr"]); ws.write(r, 1, title, f["h2"])
        ws.write(r, 2, detail, f["wrap"]); r += 1

    wb.close()
    log.info("xlsx_written", path=str(path), candidates=len(cands))
    return path


def _question_for(c: dict) -> str:
    """The specific question a reviewer must answer, per reason code."""
    codes = " ".join(c["reason_codes"] or [])
    if "DYNAMIC_APEX" in codes:
        return ("Apex in this org builds field or object names at runtime, so "
                "static analysis cannot see whether this is referenced. Does any "
                "dynamic code path touch it?")
    if "UNCALLED_ENTRY_POINT" in codes:
        return ("This is externally invocable (@AuraEnabled, @RestResource, "
                "global or similar). Its caller lives outside the org. Is any "
                "external system or UI still calling it?")
    if "TEST_ONLY" in codes:
        return ("Only test code references this. Is the code it covers still "
                "needed? If not, delete both together.")
    if "BLOCKED_BY_DEPENDENT" in codes:
        return ("Another component that is not itself unused still references "
                "this. Can the whole cluster go together?")
    if "WEAK_SIGNAL" in codes:
        return ("The only references are from permissions, labels, comments or "
                "inactive automation. Is any of that consumer about to be "
                "reactivated?")
    if "INSUFFICIENT_EVIDENCE" in codes:
        return ("A required check could not run, so absence of evidence proves "
                "nothing here. Re-run once the gap on the Coverage sheet is "
                "resolved.")
    if "RECENTLY_CHANGED" in codes:
        return ("Changed recently — this may be an in-progress feature rather "
                "than an abandoned one. Is anyone still building on it?")
    if "LLM_FLAGGED" in codes:
        return ("The AI raised a concern about this (see its summary). Treat as "
                "a prompt to look, not as a finding.")
    return "Review the evidence trail and decide."


# ---------------------------------------------------------------------------
# Markdown / JSON / destructive changes
# ---------------------------------------------------------------------------

def write_markdown(d: ReportData, path: Path) -> Path:
    cands = [c for c in d.components if c["verdict"] == "UNUSED"]
    review = [c for c in d.components if c["verdict"] == "NEEDS_REVIEW"]
    L: list[str] = []
    a = L.append

    a("# Salesforce Org Cleanup — Analysis Report")
    a("")
    a(f"**Org** `{d.run['org_alias']}` ({d.run['org_id']}) · "
      f"{(d.org or {}).get('edition', '—')} · API v{d.run['api_version']}  ")
    a(f"**Ran as** `{d.run['run_as_username']}` · **Generated** {d.generated_at}  ")
    a(f"**Run** `{d.run['id']}`")
    a("")
    a("| Verdict | Count |")
    a("|---|---:|")
    for k in ("USED", "UNUSED", "NEEDS_REVIEW", "OUT_OF_SCOPE"):
        a(f"| {k.replace('_', ' ')} | {d.totals.get(k, 0)} |")
    a("")
    a("> This report is **not** an authorisation to delete. Every candidate must "
      "go through the staged procedure below. Deleted custom fields are "
      "recoverable for 15 days; deleted Apex is not recoverable without a "
      "source snapshot.")
    a("")

    a("## Coverage and limitations")
    a("")
    a("What this run could not prove. Not errors — the honest bounds of what is "
      "knowable in this org.")
    a("")
    if d.caveats:
        for c in d.caveats:
            a(f"- {c}")
    else:
        a("- None detected.")
    a("")

    a("## Deletion candidates")
    a("")
    if not cands:
        a("_None. Every component has some evidence of use, or an open question._")
    for c in cands:
        a(f"### `{c['api_name']}`")
        a("")
        a(f"- **Type** {c['ctype']}"
          + (f" on `{c['parent_object']}`" if c["parent_object"] else ""))
        a(f"- **Confidence** {round(c['confidence'] or 0)}")
        a(f"- **Why** {'; '.join(c['reason_codes'] or [])}")
        prereqs = c["removal_prerequisites"] or []
        if prereqs:
            a("- **Before deleting:**")
            for n, p in enumerate(prereqs, 1):
                tag = " **(BLOCKING)**" if p.get("blocking") else ""
                tgt = f" — `{', '.join(p['targets'][:3])}`" if p.get("targets") else ""
                a(f"  {n}. {p['step']}{tag}{tgt}")
                a(f"     {p['reason']}")
        if c.get("business_purpose"):
            a("")
            a(f"> **AI-generated summary — not independently verified** "
              f"({c.get('llm_model', 'unknown model')})  ")
            a(f"> {c['business_purpose']}")
            if c.get("risk_note"):
                a(f">  ")
                a(f"> _Argument for keeping it:_ {c['risk_note']}")
        a("")

    a("## Needs review")
    a("")
    if not review:
        a("_None._")
    else:
        a("| Component | Type | Why | Question to answer |")
        a("|---|---|---|---|")
        for c in review:
            a(f"| `{c['api_name']}` | {c['ctype']} | "
              f"{'; '.join(c['reason_codes'] or [])} | {_question_for(c)} |")
    a("")

    a("## Method")
    a("")
    a("Every component was checked by each collector independently. Each records "
      "its result **whether or not it found anything** — \"searched and found "
      "nothing\" is a different claim from \"never checked\", and only the first "
      "supports a deletion.")
    a("")
    a("| Collector | Status | Searched | Hits |")
    a("|---|---|---:|---:|")
    for c in d.collectors:
        a(f"| {COLLECTOR_LABEL.get(c['collector_id'], c['collector_id'])} "
          f"| {c['status']} | {c['artifacts_searched'] or 0} | {c['hits'] or 0} |")
    a("")
    a("**The verdict is decided by deterministic rules, never by a model.** The "
      "AI writes summaries of decisions already made, and may only raise a "
      "concern that moves a component from UNUSED to NEEDS REVIEW.")
    a("")

    a("## Staged deletion procedure")
    a("")
    for n, s in enumerate([
        "**Snapshot** — retrieve metadata and export affected data to CSV; commit both.",
        "**Rehearse** — validate-only destructive deploy in a sandbox, then production.",
        "**Deprecate in place** — rename the label to `ZZ_DEPRECATED_*`, remove "
        "from layouts, revoke FLS everywhere.",
        "**Observe** — at least 60 days spanning a month-end, quarter-end and any "
        "annual job. This is the step that catches integrations and dynamic Apex.",
        "**Delete** — in a change window, batches of ≤25, grouped by feature.",
        "**Verify** — watch Apex exception emails and integration error queues.",
    ], start=1):
        a(f"{n}. {s}")

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(L) + "\n", encoding="utf-8")
    log.info("markdown_written", path=str(path))
    return path


def write_json(d: ReportData, path: Path) -> Path:
    payload = {
        "schema_version": 1,
        "generated_at": d.generated_at,
        "run": d.run,
        "org": d.org,
        "totals": d.totals,
        "by_type": d.by_type,
        "coverage_caveats": d.caveats,
        "collectors": d.collectors,
        "components": d.components,
        "evidence": d.evidence,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    log.info("json_written", path=str(path))
    return path


def write_destructive_changes(d: ReportData, out_dir: Path) -> list[Path]:
    """Emit a deployable delete package for the UNUSED set.

    This is what turns the report from a document into an executable outcome.
    Deliberately NOT deployed by this application: generating it is safe, running
    it is a human decision made in a change window.
    """
    cands = [c for c in d.components if c["verdict"] == "UNUSED"]
    if not cands:
        return []
    out_dir.mkdir(parents=True, exist_ok=True)
    api = d.run["api_version"]

    (out_dir / "package.xml").write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<Package xmlns="http://soap.sforce.com/2006/04/metadata">\n'
        f"    <version>{api}</version>\n</Package>\n", encoding="utf-8")

    by_type: dict[str, list[str]] = {}
    for c in cands:
        by_type.setdefault(c["ctype"], []).append(c["api_name"])
    lines = ['<?xml version="1.0" encoding="UTF-8"?>',
             "<!-- REVIEW BEFORE DEPLOYING. Generated from run "
             f"{d.run['id']} on {d.generated_at}.",
             "     Deleted custom fields are recoverable for 15 days; deleted",
             "     Apex is not recoverable without a source snapshot. -->",
             '<Package xmlns="http://soap.sforce.com/2006/04/metadata">']
    for t, members in sorted(by_type.items()):
        lines.append("    <types>")
        for m in sorted(members):
            lines.append(f"        <members>{escape(m)}</members>")
        lines.append(f"        <name>{escape(t)}</name>")
        lines.append("    </types>")
    lines.append(f"    <version>{api}</version>")
    lines.append("</Package>")
    (out_dir / "destructiveChangesPost.xml").write_text(
        "\n".join(lines) + "\n", encoding="utf-8")

    (out_dir / "README.txt").write_text(
        "Delete package for the UNUSED components in run "
        f"{d.run['id']}.\n\n"
        "Validate first (deletes nothing):\n"
        "  sf project deploy start --manifest package.xml \\\n"
        "    --post-destructive-changes destructiveChangesPost.xml \\\n"
        "    --dry-run --target-org <alias>\n\n"
        "Only remove --dry-run after completing the staged procedure in the\n"
        "report: snapshot, rehearse, deprecate in place, observe for a full\n"
        "business cycle, then delete in batches.\n",
        encoding="utf-8")

    log.info("destructive_changes_written", dir=str(out_dir), members=len(cands))
    return [out_dir / "package.xml", out_dir / "destructiveChangesPost.xml",
            out_dir / "README.txt"]


async def build_all(run_id: str, out_dir: Path | None = None) -> dict[str, str]:
    s = get_settings()
    out = out_dir or (s.workspace.parent / "reports" / run_id[:8])
    d = await gather(run_id)
    written = {
        "xlsx": str(write_xlsx(d, out / "cleanup-report.xlsx")),
        "markdown": str(write_markdown(d, out / "cleanup-report.md")),
        "json": str(write_json(d, out / "cleanup-report.json")),
    }
    dc = write_destructive_changes(d, out / "destructive")
    if dc:
        written["destructive_changes"] = str(dc[1])
    return written
