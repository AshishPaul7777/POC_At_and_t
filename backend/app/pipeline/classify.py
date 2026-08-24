"""Classification — deterministic rules over collected evidence.

No LLM, no weighted sum, no machine learning. The verdict is decided by rules
evaluated in order, first match wins, and the trace of which rule fired is stored
so any verdict can be explained exactly.

    R0  managed package / namespaced           -> OUT_OF_SCOPE
    R1  standard object or standard field      -> OUT_OF_SCOPE
    R2  completeness gate failed               -> NEEDS_REVIEW
    R3  any Tier-A evidence of use             -> USED
    R4  no Tier A but Tier B (runtime/data)    -> USED
    R5  Apex entry point, no observed caller   -> NEEDS_REVIEW
    R6  any Tier-D uncertainty flag            -> NEEDS_REVIEW
    R7  weak/inconclusive evidence only        -> NEEDS_REVIEW
    R8  referenced by something not-UNUSED     -> NEEDS_REVIEW
    R9  nothing anywhere, coverage complete    -> UNUSED

Confidence is a 0-100 number for SORTING THE REVIEW QUEUE. It never decides a
verdict. Ranking a queue wrongly wastes someone's morning; deciding a verdict
wrongly deletes production metadata.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import structlog
from sqlalchemy import text

from app.config import get_settings
from app.db.session import session_scope

log = structlog.get_logger()

#: Collectors that must have completed for an UNUSED verdict to be permitted.
#: A missing or failed one fails the completeness gate (R2) — absence of
#: evidence is never evidence of absence.
REQUIRED_COLLECTORS = {
    "CustomField": {"C10_static_index", "C20_data_population"},
    "CustomObject": {"C10_static_index", "C20_data_population"},
    "ApexClass": {"C10_static_index", "C40_runtime"},
    "ApexTrigger": {"C10_static_index", "C40_runtime"},
    # C90 is deliberately absent for methods: they are not deployable on their
    # own, so a delete rehearsal cannot apply and must not be required.
    "ApexMethod": {"C10_static_index"},
}


def attrs_entry_point(comp: dict) -> bool:
    """Is this component itself externally invocable?

    An entry point's caller lives outside the org, so zero internal callers can
    never make it dead — the check has to happen before any unreachable rule.
    """
    return bool((comp.get("attrs") or {}).get("is_entry_point"))


@dataclass
class Verdict:
    component_id: int
    label: str
    confidence: float
    reason_codes: list[str] = field(default_factory=list)
    rule_trace: list[dict] = field(default_factory=list)
    score_breakdown: list[dict] = field(default_factory=list)
    completeness: dict = field(default_factory=dict)
    had_gaps: bool = False
    #: Ordered steps required before deletion. An UNUSED verdict with a blocking
    #: prerequisite is NOT one-click safe, and the report must say so.
    removal_prerequisites: list[dict] = field(default_factory=list)


async def classify_run(run_id: str) -> dict[str, int]:
    async with session_scope() as s:
        comps = (await s.execute(text("""
            SELECT c.id, c.ctype::text AS ctype, c.api_name, c.namespace,
                   c.in_scope, c.out_of_scope_reason, c.attrs
              FROM components c WHERE c.run_id = :r
        """), {"r": run_id})).mappings().all()

        ev_rows = (await s.execute(text("""
            SELECT component_id, collector_id, result::text, tier::text, weight, payload
              FROM evidence WHERE run_id = :r
        """), {"r": run_id})).mappings().all()

        gap_rows = (await s.execute(text("""
            SELECT component_id, collector_id, reason FROM evidence_gaps
             WHERE run_id = :r
        """), {"r": run_id})).all()

        flag_rows = (await s.execute(text("""
            SELECT component_id, code, severity, detail FROM uncertainty_flags
             WHERE run_id = :r
        """), {"r": run_id})).all()

        ran = {r[0] for r in (await s.execute(text("""
            SELECT collector_id FROM collector_run
             WHERE run_id = :r AND status IN ('OK','PARTIAL')
        """), {"r": run_id})).all()}

    evidence: dict[int, list[dict]] = {}
    for e in ev_rows:
        evidence.setdefault(e["component_id"], []).append(dict(e))
    gaps: dict[int, list] = {}
    for g in gap_rows:
        gaps.setdefault(g.component_id, []).append(g)
    flags: dict[int, list] = {}
    for f in flag_rows:
        flags.setdefault(f.component_id, []).append(f)

    verdicts = [
        _decide(dict(c), evidence.get(c["id"], []), gaps.get(c["id"], []),
                flags.get(c["id"], []), ran)
        for c in comps
    ]

    # R8 needs every other verdict first: a component referenced by anything that
    # is not itself UNUSED cannot be deleted yet. Delete clusters, never leaves.
    verdicts = await _apply_transitive_guard(run_id, verdicts)

    await _persist(run_id, verdicts)

    counts: dict[str, int] = {}
    for v in verdicts:
        counts[v.label] = counts.get(v.label, 0) + 1
    log.info("classification_complete", run_id=run_id, **counts)
    return counts


def _decide(comp: dict, ev: list[dict], gaps: list, flags: list,
            ran: set[str]) -> Verdict:
    cid, ctype = comp["id"], comp["ctype"]
    trace: list[dict] = []
    score: list[dict] = []
    prereqs: list[dict] = []

    def fire(rule: str, label: str, why: str, conf: float) -> Verdict:
        trace.append({"rule": rule, "fired": True, "why": why})
        return Verdict(cid, label, max(0.0, min(100.0, conf)),
                       reason_codes=[why], rule_trace=trace,
                       score_breakdown=score,
                       completeness=completeness, had_gaps=bool(gaps),
                       removal_prerequisites=prereqs)

    # R0 / R1 — out of scope
    if comp.get("namespace"):
        completeness = {"evaluated": False, "reason": "managed package"}
        return fire("R0", "OUT_OF_SCOPE", "MANAGED_PACKAGE", 100)
    if not comp.get("in_scope"):
        completeness = {"evaluated": False,
                        "reason": comp.get("out_of_scope_reason") or "not in scope"}
        return fire("R1", "OUT_OF_SCOPE",
                    comp.get("out_of_scope_reason") or "OUT_OF_SCOPE", 100)

    required = REQUIRED_COLLECTORS.get(ctype, {"C10_static_index"})
    missing = sorted(required - ran)
    gap_collectors = sorted({g.collector_id for g in gaps})
    completeness = {
        "required": sorted(required),
        "ran": sorted(required & ran),
        "missing": missing,
        "gaps": gap_collectors,
        "gate": "PASS" if not missing and not gap_collectors else "FAIL",
    }

    by_result: dict[str, list[dict]] = {}
    for e in ev:
        by_result.setdefault(e["result"], []).append(e)
        if e["result"] == "EVIDENCE_OF_USE":
            score.append({"collector": e["collector_id"],
                          "contribution": +int(30 * (e["weight"] or 1)),
                          "why": f"tier {e['tier']} evidence of use"})
        elif e["result"] == "NO_EVIDENCE_FOUND":
            score.append({"collector": e["collector_id"], "contribution": +15,
                          "why": "searched, nothing found"})
        elif e["result"] == "INCONCLUSIVE":
            score.append({"collector": e["collector_id"], "contribution": -10,
                          "why": "inconclusive"})
    for f in flags:
        score.append({"collector": f.code, "contribution": -30,
                      "why": f.detail or f.code})

    used = by_result.get("EVIDENCE_OF_USE", [])
    tier_a = [e for e in used if e["tier"] == "A"]
    tier_b = [e for e in used if e["tier"] == "B"]
    conf = 50 + sum(s["contribution"] for s in score)

    # Reachability is the sharpest signal available, because it asks the right
    # question: can anything that RUNS or is SEEN get here? Counting references
    # cannot distinguish "called by a live trigger" from "mentioned in a file
    # nothing executes".
    reach = next((e for e in ev if e["collector_id"] == "C70_reachability"), None)
    reachable = bool((reach or {}).get("payload", {}).get("reachable"))
    unreachable = reach is not None and not reachable

    # R2 — completeness gate. Checked BEFORE any UNUSED path can be reached.
    if completeness["gate"] == "FAIL" and not used:
        return fire("R2", "NEEDS_REVIEW",
                    f"INSUFFICIENT_EVIDENCE (missing: {', '.join(missing + gap_collectors)})",
                    min(conf, 55))

    # R3a — layout-only, with no data. UNUSED, with a removal prerequisite.
    #
    # Every custom field lands on a layout the moment it is created, so layout
    # presence proves a deploy would break - not that anyone uses the field. A
    # field that ONLY layouts reference, holding no data in any record, is
    # unused in the only sense anyone cares about.
    #
    # Calling it NEEDS_REVIEW (the earlier behaviour) buried the finding and made
    # a reader re-derive the same reasoning for every field. Calling it UNUSED
    # with an explicit prerequisite states both facts plainly: nothing uses it,
    # and it must come off these layouts before it can go.
    static = next((e for e in ev if e["collector_id"] == "C10_static_index"), None)
    payload = (static or {}).get("payload", {}) or {}
    layout_only = bool(payload.get("layout_only"))
    zero_data = any(
        e["collector_id"] == "C20_data_population"
        and e["result"] == "NO_EVIDENCE_FOUND"
        for e in ev
    )
    if tier_a and layout_only and zero_data:
        names = list(payload.get("layout_names") or [])
        # NOT blocking, and this was corrected by the delete rehearsal rather
        # than reasoned out. An earlier version asserted that layout presence
        # would fail a destructive deploy; Salesforce accepted all six such
        # fields with zero component failures, because it strips a deleted field
        # from its layouts automatically.
        #
        # The step is kept because doing it first is still the safer order — it
        # makes the change visible to admins before the field disappears — but
        # calling it blocking would have been a confident, wrong claim.
        prereqs.append({
            "step": "Remove the field from its page layouts",
            "reason": "Salesforce strips a deleted field from layouts "
                      "automatically, so this does not block the deploy "
                      "(confirmed by validate-only rehearsal). Doing it first is "
                      "still safer: the layout change is visible and reversible "
                      "on its own.",
            "targets": names,
            "blocking": False,
        })
        prereqs.append({
            "step": "Hide via field-level security and observe",
            "reason": "Revoke read access on every profile and permission set, "
                      "then wait one full business cycle. A hidden dependency "
                      "then fails loudly instead of silently reading null. This "
                      "is the step that catches what static analysis cannot: "
                      "integrations, dynamic Apex and ad-hoc reports.",
            "targets": [],
            "blocking": False,
        })
        prereqs.append({
            "step": "Export the field's data before deleting",
            "reason": "Deleted custom fields are restorable from the recycle bin "
                      "for 15 days; after that the data is gone. A CSV export is "
                      "the only durable rollback.",
            "targets": [],
            "blocking": False,
        })
        return fire("R3a", "UNUSED",
                    f"LAYOUT_ONLY_NO_DATA (no functional reference, no data; "
                    f"remove from {len(names)} layout(s) first)",
                    min(conf, 70))

    # R3 — a single Tier-A hit outranks every 'found nothing'. Finding usage is
    # proof; not finding it is only absence.
    if tier_a:
        codes = "BINDING_REFERENCE"
        if layout_only:
            codes = "BINDING_REFERENCE (layout-only, but data exists)"
        if reachable:
            codes = "REACHABLE_FROM_ENTRY_POINT"
            path = (reach or {}).get("payload", {}).get("path") or []
            if path:
                codes += f" ({' -> '.join(path[:4])})"
        return fire("R3", "USED", f"{codes} ({len(tier_a)} collector(s))",
                    max(conf, 80))

    # R4 — runtime or data evidence with no static reference. Real, but it means
    # something writes this without any metadata naming it.
    if tier_b:
        return fire("R4", "USED", "RUNTIME_OR_DATA_EVIDENCE", max(min(conf, 75), 60))

    # R5 — externally invocable Apex. Its caller lives outside the org, so zero
    # observed callers can never make it unused.
    attrs = comp.get("attrs") or {}
    if ctype == "ApexMethod" and attrs.get("is_entry_point"):
        return fire("R5", "NEEDS_REVIEW", "UNCALLED_ENTRY_POINT", min(conf, 60))
    # A test class is never UNUSED on its own: it must be deleted together with
    # whatever it tests, and deleting it alone silently drops coverage.
    if attrs.get("is_test"):
        return fire("R5", "NEEDS_REVIEW",
                    "TEST_ONLY (delete with the code it covers, never alone)",
                    min(conf, 60))

    # R5b — code nothing can reach.
    #
    # Placed AFTER the entry-point and test-class checks, deliberately. An
    # earlier version ran it first and marked a test class plus its @TestSetup
    # method UNUSED with no prerequisites — which would invite someone to delete
    # a test and silently lose the coverage it provides.
    #
    # Apex is the clean case for this rule: unlike a field, a class has no
    # presentation layer to be merely "placed" on. If nothing that runs can
    # reach it and it exposes no entry point of its own, nothing can execute it.
    if unreachable and ctype in ("ApexClass", "ApexTrigger", "ApexMethod") \
            and not attrs_entry_point(comp) and not tier_b:
        return fire("R5b", "UNUSED",
                    "UNREACHABLE (no trigger, flow, UI component or external "
                    "entry point can reach this, and it exposes none itself)",
                    max(min(conf, 85), 70))

    # R6 — any uncertainty flag suppresses UNUSED outright.
    if flags:
        codes = ", ".join(sorted({f.code for f in flags}))
        return fire("R6", "NEEDS_REVIEW", codes, min(conf, 60))

    # R7 — genuinely weak evidence: something references it, but only in a way
    # that cannot prove use.
    #
    # The distinction that matters here, and which an earlier version got wrong:
    # a collector reporting INCONCLUSIVE is NOT the same as weak evidence. The
    # Dependency API returns INCONCLUSIVE for every component it has no edge
    # for — deliberately, because its coverage is partial and absence there
    # proves nothing. Treating that as "weak signal suggesting use" made R7 fire
    # for practically everything, which blocked R9 and buried real findings in
    # the review queue.
    #
    # So only count inconclusive results that carry an actual positive trace.
    weak = [
        e for e in by_result.get("INCONCLUSIVE", [])
        if (e["payload"] or {}).get("weak_references")
    ]
    if weak:
        sources = sorted({
            s for e in weak
            for s in (e["payload"] or {}).get("weak_source_types", [])
        })
        detail = f" ({', '.join(sources[:3])} only)" if sources else ""
        return fire("R7", "NEEDS_REVIEW", f"WEAK_SIGNAL_ONLY{detail}", min(conf, 65))

    # R9 — every required collector ran cleanly and none found anything.
    if completeness["gate"] == "PASS":
        return fire("R9", "UNUSED", "NO_EVIDENCE_FROM_ANY_COLLECTOR", max(conf, 70))

    return fire("R2", "NEEDS_REVIEW", "INSUFFICIENT_EVIDENCE", min(conf, 55))


async def _apply_transitive_guard(run_id: str, verdicts: list[Verdict]) -> list[Verdict]:
    """Nothing is UNUSED while a live component still references it.

    A field may itself be unreferenced while a formula field that IS used points
    at it. Deleting the leaf breaks the live consumer, so the cluster has to go
    together or not at all.
    """
    by_id = {v.component_id: v for v in verdicts}
    unused = {v.component_id for v in verdicts if v.label == "UNUSED"}
    if not unused:
        return verdicts

    async with session_scope() as s:
        rows = (await s.execute(text("""
            SELECT DISTINCT e.to_component_id AS target, a.member_name AS src,
                            a.metadata_type AS src_type
              FROM reference_edges e
              JOIN artifact a ON a.id = e.from_artifact_id
             WHERE e.run_id = :r AND e.to_component_id = ANY(:ids)
               AND e.tier = 'A'
               -- Presentation metadata is excluded here on purpose. A layout
               -- referencing a field is a REMOVAL PREREQUISITE, already recorded
               -- as such, not a live consumer that keeps the field alive.
               -- Without this exclusion the guard would immediately undo every
               -- layout-only UNUSED verdict and the tool would find nothing.
               AND a.metadata_type NOT IN ('Layout','FlexiPage','CompactLayout')
               -- A component's own object file declares it; that is definition,
               -- not use.
               AND NOT (a.metadata_type = 'CustomObject' AND EXISTS (
                     SELECT 1 FROM components c2
                      WHERE c2.id = e.to_component_id
                        AND (c2.parent_object = a.member_name
                             OR c2.api_name = a.member_name)))
        """), {"r": run_id, "ids": list(unused)})).all()

    # CLOSED CLUSTER DETECTION.
    #
    # A set of components that reference only each other, with nothing live
    # reaching any of them, is not "blocked" in any meaningful sense — it is one
    # orphaned unit. Treating each member as an independent blocked item turned
    # a single decision ("delete this dead service layer, or keep it") into 18
    # separate review items on a real run, which is how a review queue becomes
    # unusable.
    #
    # The distinction that matters: is the referencing component ITSELF unused?
    # If yes, the reference proves nothing — they are dead together.
    async with session_scope() as s:
        alive_refs = {
            r.target for r in (await s.execute(text("""
                SELECT DISTINCT e.to_component_id AS target
                  FROM reference_edges e
                  JOIN artifact a ON a.id = e.from_artifact_id
                  JOIN components src ON src.run_id = e.run_id
                                     AND lower(src.api_name) = lower(a.member_name)
                  JOIN classifications scl ON scl.component_id = src.id
                 WHERE e.run_id = :r AND e.to_component_id = ANY(:ids)
                   AND e.tier = 'A'
                   AND a.metadata_type NOT IN ('Layout','FlexiPage','CompactLayout')
                   -- Only a referrer that is NOT itself unused keeps something
                   -- alive. A dead class pointing at a dead field proves nothing.
                   AND scl.label <> 'UNUSED'
            """), {"r": run_id, "ids": list(unused)})).all()
        }

    blocked = clustered = 0
    for r in rows:
        v = by_id.get(r.target)
        if not v or v.label != "UNUSED":
            continue
        if r.target in alive_refs:
            v.label = "NEEDS_REVIEW"
            v.reason_codes = ["BLOCKED_BY_DEPENDENT"]
            v.rule_trace.append({
                "rule": "R8", "fired": True,
                "why": f"still referenced by {r.src}, which is not itself unused"})
            v.confidence = min(v.confidence, 60)
            blocked += 1
        else:
            # Everything pointing at it is also unused: keep the UNUSED verdict
            # and mark it as part of a cluster so the report can present the
            # group as one decision rather than N.
            v.reason_codes = [*v.reason_codes, "PART_OF_ORPHANED_CLUSTER"]
            v.removal_prerequisites.append({
                "step": "Delete the whole cluster together",
                "reason": "This references, or is referenced by, other components "
                          "that are also unused. Nothing live reaches any of them, "
                          "so they can go together — but deleting one alone would "
                          "break the others.",
                "targets": [], "blocking": True,
            })
            v.rule_trace.append({
                "rule": "R8c", "fired": True,
                "why": "closed cluster: every referrer is itself unused"})
            clustered += 1
    if clustered:
        log.info("orphaned_clusters_kept_unused", count=clustered)
    if blocked:
        log.info("transitive_guard_applied", blocked=blocked)
    return verdicts


async def _persist(run_id: str, verdicts: list[Verdict]) -> None:
    import json

    async with session_scope() as s:
        await s.execute(text("""
            INSERT INTO classifications (run_id, component_id, label, confidence,
                reason_codes, rule_trace, score_breakdown, completeness, had_gaps,
                removal_prerequisites)
            VALUES (:r, :c, CAST(:l AS verdict), :conf, :codes,
                    CAST(:trace AS jsonb), CAST(:score AS jsonb),
                    CAST(:comp AS jsonb), :gaps, CAST(:prereq AS jsonb))
            ON CONFLICT (run_id, component_id) DO UPDATE SET
                label = EXCLUDED.label, confidence = EXCLUDED.confidence,
                reason_codes = EXCLUDED.reason_codes,
                rule_trace = EXCLUDED.rule_trace,
                score_breakdown = EXCLUDED.score_breakdown,
                completeness = EXCLUDED.completeness, had_gaps = EXCLUDED.had_gaps,
                removal_prerequisites = EXCLUDED.removal_prerequisites
        """), [{"r": run_id, "c": v.component_id, "l": v.label,
                "conf": v.confidence, "codes": v.reason_codes,
                "trace": json.dumps(v.rule_trace, default=str),
                "score": json.dumps(v.score_breakdown, default=str),
                "comp": json.dumps(v.completeness, default=str),
                "gaps": v.had_gaps,
                "prereq": json.dumps(v.removal_prerequisites, default=str)}
               for v in verdicts])
