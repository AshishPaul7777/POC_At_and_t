"""Classification — deterministic rules over collected evidence.

No LLM, no weighted sum, no machine learning. The verdict is decided by rules
evaluated in order, first match wins, and the trace of which rule fired is stored
so any verdict can be explained exactly.

    R0   managed package / namespaced            -> OUT_OF_SCOPE
    R1   standard object or standard field       -> OUT_OF_SCOPE
    R2   completeness gate failed                -> NEEDS_REVIEW
    R3a  layout-only Tier-A + no data            -> UNUSED (or R6 if flagged)
    R3   any other Tier-A evidence of use        -> USED
    R4   no Tier A but Tier B (runtime/data)     -> USED
    R5   Apex entry point / exposed UI / test, no caller -> NEEDS_REVIEW
    R6   Tier-D review flag (not RECENTLY_CHANGED) -> NEEDS_REVIEW
    R5b  Apex/private UI unreachable from any entry -> UNUSED
    R7a  presence-only weak refs (FLS/layout/…) + no data -> UNUSED
    R7   other weak/inconclusive evidence only   -> NEEDS_REVIEW
    R9   nothing anywhere, coverage complete     -> UNUSED

Post-passes (after every component has a provisional verdict):
    R_PARENT  USED method forces parent class USED
    R8 / R8c  referenced by a live component -> NEEDS_REVIEW;
              closed unused cluster stays UNUSED with a delete-together prereq

Confidence is a 0-100 number for SORTING THE REVIEW QUEUE. It never decides a
verdict. Ranking a queue wrongly wastes someone's morning; deciding a verdict
wrongly deletes production metadata.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import structlog
from sqlalchemy import text

from app.db.session import session_scope

log = structlog.get_logger()

#: Collectors that must have completed for an UNUSED verdict to be permitted.
#: A missing or failed one fails the completeness gate (R2) — absence of
#: evidence is never evidence of absence.
#:
#: C70 is required for Apex: without a reachability result, R5b cannot prove
#: "nothing can reach this", and R9 must not fire on that absence.
REQUIRED_COLLECTORS = {
    "CustomField": {"C10_static_index", "C20_data_population"},
    "CustomObject": {"C10_static_index", "C20_data_population"},
    "ApexClass": {"C10_static_index", "C40_runtime", "C70_reachability"},
    "ApexTrigger": {"C10_static_index", "C40_runtime", "C70_reachability"},
    # C90 is deliberately absent for methods: they are not deployable on their
    # own, so a delete rehearsal cannot apply and must not be required.
    "ApexMethod": {"C10_static_index", "C70_reachability"},
    # UI: placement/imports are C10; C70 proves private bundles are unreachable.
    # C40 Event Monitoring (LightningInteraction) is optional Tier B, not required.
    "LightningComponentBundle": {"C10_static_index", "C70_reachability"},
    "AuraDefinitionBundle": {"C10_static_index", "C70_reachability"},
}


#: Uncertainty flag codes that are informational only — visible in the UI and
#: check-flow, but must not fire R6 / suppress UNUSED.
INFORMATIONAL_FLAG_CODES = frozenset({
    "RECENTLY_CHANGED",
    # AI confirmed use from listed evidence — display only; Tier-A S40 evidence
    # is what moves the verdict to USED.
    "LLM_CONFIRMS_USE",
})


def _review_flags(flags: list) -> list:
    """Flags that suppress UNUSED. Recent-change flags are display-only."""
    out = []
    for f in flags:
        code = getattr(f, "code", None)
        if code is None and isinstance(f, dict):
            code = f.get("code")
        if not code or code in INFORMATIONAL_FLAG_CODES:
            continue
        out.append(f)
    return out


def attrs_entry_point(comp: dict) -> bool:
    """Is this component itself externally invocable?

    An entry point's caller lives outside the org, so zero internal callers can
    never make it dead — the check has to happen before any unreachable rule.
    """
    return bool((comp.get("attrs") or {}).get("is_entry_point"))


def _is_layout_only_evidence(e: dict) -> bool:
    """True when this Tier-A hit is only a layout/FlexiPage placement from C10."""
    return (
        e.get("collector_id") == "C10_static_index"
        and bool((e.get("payload") or {}).get("layout_only"))
    )


#: Consumer types that only prove *presence* (FLS, nav, layout), not business use.
PRESENCE_ONLY_SOURCE_TYPES = frozenset({
    "Profile", "PermissionSet", "PermissionSetGroup",
    "CustomApplication", "CustomTab", "SharingRules",
    "Layout", "FlexiPage", "CompactLayout",
})


def _weak_source_types(weak_rows: list[dict]) -> list[str]:
    out: set[str] = set()
    for e in weak_rows:
        for s in (e.get("payload") or {}).get("weak_source_types") or []:
            if s:
                out.add(str(s))
    return sorted(out)


def _is_presence_only_weak(sources: list[str]) -> bool:
    if not sources:
        return True
    return all(s in PRESENCE_ONLY_SOURCE_TYPES for s in sources)


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
                   c.in_scope, c.out_of_scope_reason, c.attrs, c.parent_id
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

    comps_list = [dict(c) for c in comps]
    verdicts = [
        _decide(c, evidence.get(c["id"], []), gaps.get(c["id"], []),
                flags.get(c["id"], []), ran)
        for c in comps_list
    ]

    # A USED method means its parent ApexClass is in use — you cannot delete the
    # class while keeping a live method. The reverse is not true: unused
    # methods may sit inside a used class.
    verdicts = _promote_classes_with_used_methods(comps_list, verdicts)

    # R8 needs every other verdict first: a component referenced by anything that
    # is not itself UNUSED cannot be deleted yet. Delete clusters, never leaves.
    # Uses in-memory labels — never the classifications table — so a first
    # classify pass sees USED referrers before anything is persisted.
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
    # Only fires when EVERY Tier-A hit is layout-only C10. A Dependency API or
    # config-data Tier-A hit must fall through to R3 USED instead.
    # Completeness must PASS: gaps must not be papered over by layout placement.
    static = next((e for e in ev if e["collector_id"] == "C10_static_index"), None)
    payload = (static or {}).get("payload", {}) or {}
    layout_only = bool(payload.get("layout_only"))
    zero_data = any(
        e["collector_id"] == "C20_data_population"
        and e["result"] == "NO_EVIDENCE_FOUND"
        for e in ev
    )
    only_layout_tier_a = bool(tier_a) and all(_is_layout_only_evidence(e) for e in tier_a)
    if only_layout_tier_a and zero_data and completeness["gate"] == "PASS":
        names = list(payload.get("layout_names") or [])
        # NOT blocking — Salesforce strips deleted fields from layouts
        # automatically (confirmed by validate-only rehearsal). Doing it first
        # is still safer: the layout change is visible and reversible alone.
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
        if flags:
            # Flags suppress UNUSED (R6). Layout-only must not bypass that.
            # RECENTLY_CHANGED is informational only and does not suppress.
            review = _review_flags(flags)
            if review:
                codes = ", ".join(sorted({f.code for f in review}))
                return fire("R6", "NEEDS_REVIEW", codes, min(conf, 60))
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

    # R5 — externally invocable Apex, or an exposed UI bundle with no placement.
    # Callers / Experience wiring may live outside retrieved metadata.
    attrs = comp.get("attrs") or {}
    if ctype in ("ApexMethod", "ApexClass") and attrs.get("is_entry_point"):
        return fire("R5", "NEEDS_REVIEW", "UNCALLED_ENTRY_POINT", min(conf, 60))
    if ctype in ("LightningComponentBundle", "AuraDefinitionBundle") and (
            attrs.get("is_exposed") or attrs.get("is_entry_point")):
        return fire("R5", "NEEDS_REVIEW",
                    "EXPOSED_UI_NO_PLACEMENT (isExposed/global surface with no "
                    "observed FlexiPage, tab, app, or import reference)",
                    min(conf, 60))
    # A test class is never UNUSED on its own: it must be deleted together with
    # whatever it tests, and deleting it alone silently drops coverage.
    if attrs.get("is_test"):
        return fire("R5", "NEEDS_REVIEW",
                    "TEST_ONLY (delete with the code it covers, never alone)",
                    min(conf, 60))

    # R6 — Tier-D review flags suppress UNUSED.
    #
    # Must run BEFORE R5b / R9. RECENTLY_CHANGED is excluded: it stays visible
    # as a flag on the check-flow but must not force Needs review by itself.
    review = _review_flags(flags)
    if review:
        codes = ", ".join(sorted({
            (getattr(f, "code", None)
             or (f.get("code") if isinstance(f, dict) else None)
             or "")
            for f in review
        } - {""}))
        return fire("R6", "NEEDS_REVIEW", codes, min(conf, 60))

    # R5b — code / private UI nothing can reach.
    #
    # Placed AFTER the entry-point, test-class, and flag checks, deliberately.
    # Apex is the clean case for this rule: unlike a field, a class has no
    # presentation layer to be merely "placed" on. If nothing that runs can
    # reach it and it exposes no entry point of its own, nothing can execute it.
    # Private (non-exposed) LWC/Aura with no imports follow the same logic.
    if unreachable and ctype in (
            "ApexClass", "ApexTrigger", "ApexMethod",
            "LightningComponentBundle", "AuraDefinitionBundle") \
            and not attrs_entry_point(comp) and not tier_b:
        return fire("R5b", "UNUSED",
                    "UNREACHABLE (no trigger, flow, UI component or external "
                    "entry point can reach this, and it exposes none itself)",
                    max(min(conf, 85), 70))

    # R7a / R7 — weak evidence only (permission sets, FLS, inactive leftovers).
    #
    # Presence on a profile or permission set is created with every field; it
    # does not prove business use. Same for layout-adjacent weak signals when
    # they never rose to Tier A. With no data and a clean gate → Unused (R7a).
    # Other weak sources (ambiguous) still need a person (R7).
    weak = [
        e for e in by_result.get("INCONCLUSIVE", [])
        if (e["payload"] or {}).get("weak_references")
    ]
    if weak and not used:
        sources = _weak_source_types(weak)
        detail = f" ({', '.join(sources[:3])} only)" if sources else ""
        needs_data = ctype in ("CustomField", "CustomObject")
        data_ok = (not needs_data) or zero_data
        if (
            _is_presence_only_weak(sources)
            and data_ok
            and completeness["gate"] == "PASS"
        ):
            review = _review_flags(flags)
            if review:
                codes = ", ".join(sorted({f.code for f in review}))
                return fire("R6", "NEEDS_REVIEW", codes, min(conf, 60))
            return fire(
                "R7a", "UNUSED",
                f"PRESENCE_ONLY_NO_DATA{detail}",
                min(conf, 70),
            )
        return fire("R7", "NEEDS_REVIEW", f"WEAK_SIGNAL_ONLY{detail}", min(conf, 65))

    # R9 — every required collector ran cleanly and none found anything.
    if completeness["gate"] == "PASS":
        return fire("R9", "UNUSED", "NO_EVIDENCE_FROM_ANY_COLLECTOR", max(conf, 70))

    return fire("R2", "NEEDS_REVIEW", "INSUFFICIENT_EVIDENCE", min(conf, 55))


def _promote_classes_with_used_methods(
    comps: list[dict], verdicts: list[Verdict]
) -> list[Verdict]:
    """If any ApexMethod is USED, its parent ApexClass must also be USED."""
    by_id = {v.component_id: v for v in verdicts}
    parent_of = {
        c["id"]: c.get("parent_id")
        for c in comps
        if c.get("ctype") == "ApexMethod" and c.get("parent_id")
    }
    promoted = 0
    for method_id, parent_id in parent_of.items():
        mv = by_id.get(method_id)
        pv = by_id.get(parent_id)
        if not mv or not pv or mv.label != "USED":
            continue
        if pv.label == "USED":
            continue
        pv.label = "USED"
        pv.reason_codes = ["HAS_USED_METHOD"]
        pv.confidence = max(pv.confidence, 80.0)
        pv.rule_trace.append({
            "rule": "R_PARENT",
            "fired": True,
            "why": "parent class of a USED method cannot be unused or review-only",
        })
        promoted += 1
    if promoted:
        log.info("promoted_classes_with_used_methods", count=promoted)
    return verdicts


async def _apply_transitive_guard(run_id: str, verdicts: list[Verdict]) -> list[Verdict]:
    """Nothing is UNUSED while a live component still references it.

    A field may itself be unreferenced while a formula field that IS used points
    at it. Deleting the leaf breaks the live consumer, so the cluster has to go
    together or not at all.

    Referrer liveness is read from the in-memory ``verdicts`` list — never from
    the classifications table — because this runs before ``_persist``. Joining
    classifications on a first pass left ``alive_refs`` empty and wrongly kept
    live-referenced leaves as UNUSED (R8c).
    """
    by_id = {v.component_id: v for v in verdicts}
    label_by_id = {v.component_id: v.label for v in verdicts}
    unused = {v.component_id for v in verdicts if v.label == "UNUSED"}
    if not unused:
        return verdicts

    async with session_scope() as s:
        rows = (await s.execute(text("""
            SELECT DISTINCT e.to_component_id AS target,
                            a.member_name AS src,
                            a.metadata_type AS src_type,
                            src.id AS src_id
              FROM reference_edges e
              JOIN artifact a ON a.id = e.from_artifact_id
              JOIN components src ON src.run_id = e.run_id
                                 AND lower(src.api_name) = lower(a.member_name)
             WHERE e.run_id = :r AND e.to_component_id = ANY(:ids)
               AND e.tier = 'A'
               -- Presentation metadata is excluded here on purpose. A layout
               -- referencing a field is a REMOVAL PREREQUISITE, already recorded
               -- as such, not a live consumer that keeps the field alive.
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
    # reaching any of them, is one orphaned unit — keep UNUSED with a
    # delete-together prerequisite rather than N separate review items.
    blocked = clustered = 0
    seen_targets: set[int] = set()
    for r in rows:
        if r.target in seen_targets:
            continue
        v = by_id.get(r.target)
        if not v or v.label != "UNUSED":
            continue
        seen_targets.add(r.target)
        src_rows = [x for x in rows if x.target == r.target]
        live = [
            x for x in src_rows
            if label_by_id.get(x.src_id) not in (None, "UNUSED")
        ]
        if live:
            src = live[0]
            v.label = "NEEDS_REVIEW"
            v.reason_codes = ["BLOCKED_BY_DEPENDENT"]
            v.rule_trace.append({
                "rule": "R8", "fired": True,
                "why": f"still referenced by {src.src}, which is not itself unused"})
            v.confidence = min(v.confidence, 60)
            blocked += 1
        else:
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
