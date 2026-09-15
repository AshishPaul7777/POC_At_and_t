"""Evidence collectors.

WHAT "USED" MEANS HERE
----------------------
Used means the component participates in an actual business process: something
in the org reads it, writes it, decides on it, or shows it to someone as part of
doing work. Concretely - Apex, triggers, Flows and Workflow rules, validation
rules, formulas, reports and dashboards, list views, LWC/Aura/Visualforce,
email templates, or real data in real records.

Used does NOT mean merely *present*. These say nothing about business use:

  * sitting on a page layout      - presentation placement, and every custom
                                    field gets one the moment it is created
  * field-level security grants   - every field gets FLS entries on creation;
                                    it means someone COULD see it, not that
                                    anyone does
  * its own object definition     - a declaration, not a use
  * tab or app membership         - navigation structure

That distinction is the whole product. Counting mere presence as use marks
essentially everything USED and finds nothing, which is worse than useless
because it looks authoritative.

Presence still matters for a different question - it decides what must happen
before deletion - and that is recorded as a removal prerequisite instead.

HOW COLLECTORS REPORT
---------------------

Each collector answers ONE question about a component, independently of the
others, and writes its own row — hit or miss, every time. They are deliberately
blind to each other's conclusions, so their agreement carries real information.

None of them returns "used" or "unused". A single collector is not entitled to a
verdict; it reports only what it saw:

    EVIDENCE_OF_USE    found something that uses this
    NO_EVIDENCE_FOUND  looked properly, found nothing
    INCONCLUSIVE       looked, but the answer cannot be trusted
    NOT_APPLICABLE     this check does not apply / is unavailable here
    FAILED             the check itself broke

The last three are what keep the system honest. Collapsing them into
NO_EVIDENCE_FOUND would turn "we could not check" into "there is nothing here",
which is exactly how a live component becomes a deletion candidate.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

import structlog
from sqlalchemy import text

from app.config import get_settings
from app.db.session import session_scope
from app.salesforce.capabilities import OrgCapabilities
from app.salesforce.client import SalesforceClient

log = structlog.get_logger()

# Types that cannot be aggregated are discovered from Salesforce's own error
# (see SalesforceClient.field_population), so no hand-maintained list here.


#: Metadata that PLACES a component without USING it.
#:
#: A reference from one of these is binding for deployment - a destructive
#: deploy fails while the reference exists - but it is not evidence that any
#: business process touches the component. So it produces a removal
#: prerequisite, not a USED verdict.
PRESENTATION_ONLY_TYPES = ("Layout", "FlexiPage", "CompactLayout")

#: What a weak reference from each source actually tells a reviewer. Vague
#: wording here turns into a review queue nobody can work through, because
#: "weak signal" gives no clue what question to ask.
_WEAK_NOTES = {
    "PermissionSet": "only a permission set grants access - that means someone "
                     "COULD see it, not that anything uses it",
    "Profile": "only a profile grants access - visibility, not use",
    "PermissionSetGroup": "only a permission set group grants access",
    "CustomApplication": "only named by an app definition (navigation, not use)",
    "CustomTab": "only named by a tab definition (navigation, not use)",
    "SharingRules": "only named by a sharing rule",
}


def _weak_note(sources: list[str]) -> str:
    notes = [_WEAK_NOTES[s] for s in sources if s in _WEAK_NOTES]
    if notes:
        return notes[0] if len(notes) == 1 else "; ".join(notes[:2])
    if sources:
        return f"only referenced from {', '.join(sources[:3])}, which cannot prove use"
    return "weak references only (permissions, labels, comments or test code)"


@dataclass
class CollectorOutcome:
    collector_id: str
    family: str
    status: str = "OK"
    method: str = ""
    artifacts: int = 0
    hits: int = 0
    unavailable_reason: str | None = None
    error: str | None = None
    evidence: list[dict] = field(default_factory=list)
    gaps: list[dict] = field(default_factory=list)
    flags: list[dict] = field(default_factory=list)


# ---------------------------------------------------------------------------
# C10 — static references (folds the indexer's edges into evidence)
# ---------------------------------------------------------------------------

async def collect_static_references(run_id: str) -> CollectorOutcome:
    out = CollectorOutcome(
        "C10_static_index", "static_reference",
        method="token sweep + string literals + merge fields over active "
               "retrieved metadata, matched against the alias table",
    )
    async with session_scope() as s:
        rows = (await s.execute(text("""
            SELECT c.id,
                   -- SELF-REFERENCE EXCLUSION, applied to every type.
                   -- A file naturally contains its own name: OrderFulfillment-
                   -- Service.cls says "OrderFulfillmentService", and a field is
                   -- declared inside its object's metadata. Neither is usage,
                   -- and counting them makes every component look referenced by
                   -- itself — which silently defeats the whole analysis.
                   count(*) FILTER (
                     WHERE e.tier = 'A' AND NOT _self.is_self
                       AND COALESCE(a.is_active, TRUE)
                       AND COALESCE(e.consumer_active, TRUE)
                   ) AS tier_a,
                   count(*) FILTER (
                     WHERE e.tier = 'C'
                       AND COALESCE(a.is_active, TRUE)
                       AND COALESCE(e.consumer_active, TRUE)
                   ) AS tier_c,
                   -- Naming the weak sources turns "weak signal" into an
                   -- answerable question: "only a permission set grants access"
                   -- is something a reviewer can act on; "weak signal" is not.
                   array_agg(DISTINCT a.metadata_type) FILTER (
                     WHERE e.tier = 'C' AND NOT _self.is_self
                       AND COALESCE(a.is_active, TRUE)
                       AND COALESCE(e.consumer_active, TRUE)
                   ) AS weak_sources,
                   count(DISTINCT e.from_artifact_id) FILTER (
                     WHERE NOT _self.is_self
                       AND COALESCE(a.is_active, TRUE)
                       AND COALESCE(e.consumer_active, TRUE)
                   )          AS sources,
                   -- Which consumer types produced the BINDING references?
                   -- A field known only to layouts is a different proposition
                   -- from one referenced by Apex or a Flow.
                   count(*) FILTER (
                     WHERE e.tier = 'A' AND a.metadata_type = 'Layout'
                       AND COALESCE(a.is_active, TRUE)
                       AND COALESCE(e.consumer_active, TRUE)
                   ) AS layout_refs,
                   -- SELF-DEFINITION EXCLUSION. A field is declared inside its
                   -- own object's metadata file, so a naive count treats every
                   -- field as referenced by its own definition. That is not
                   -- usage, and left in it makes layout-only detection
                   -- impossible: every field looks functionally referenced.
                   count(*) FILTER (
                     WHERE e.tier = 'A' AND NOT _self.is_self
                       AND COALESCE(a.is_active, TRUE)
                       AND COALESCE(e.consumer_active, TRUE)
                       AND a.metadata_type NOT IN ('Layout','FlexiPage','CompactLayout')
                   ) AS functional_refs,
                   array_agg(DISTINCT a.metadata_type) FILTER (
                     WHERE e.tier = 'A' AND NOT _self.is_self
                       AND COALESCE(a.is_active, TRUE)
                       AND COALESCE(e.consumer_active, TRUE)
                   ) AS binding_sources,
                   -- Named layouts, so the report can say exactly which ones to
                   -- edit rather than leaving the reader to go find them.
                   array_agg(DISTINCT a.member_name) FILTER (
                     WHERE e.tier = 'A'
                       AND COALESCE(a.is_active, TRUE)
                       AND COALESCE(e.consumer_active, TRUE)
                       AND a.metadata_type IN ('Layout','FlexiPage','CompactLayout')
                   ) AS layout_names
              FROM components c
              LEFT JOIN reference_edges e ON e.to_component_id = c.id
              LEFT JOIN artifact a ON a.id = e.from_artifact_id
                   LEFT JOIN LATERAL (SELECT
                    a.member_name IS NOT NULL AND (
                      -- a field declared in its own object's file
                      (a.metadata_type = 'CustomObject'
                       AND a.member_name IN (c.parent_object, c.api_name))
                      -- Apex naming itself
                      OR (a.metadata_type IN ('ApexClass','ApexTrigger')
                          AND lower(a.member_name) IN (
                                lower(c.api_name), lower(coalesce(c.parent_object,''))))
                      -- LWC / Aura bundle naming itself
                      OR (a.metadata_type IN (
                                'LightningComponentBundle','AuraDefinitionBundle')
                          AND lower(a.member_name) = lower(c.api_name))
                    ) AS is_self) _self ON TRUE
             WHERE c.run_id = :r AND c.in_scope
             GROUP BY c.id
        """), {"r": run_id})).all()
        total_artifacts = await s.scalar(
            text("SELECT count(*) FROM artifact WHERE run_id = :r AND is_active"),
            {"r": run_id}) or 0

    out.artifacts = total_artifacts
    for r in rows:
        if r.tier_a:
            out.hits += 1
            # LAYOUT_ONLY is the important distinction here. Layout presence is
            # genuinely binding - deleting the field breaks the deploy - but it
            # is weak evidence of *business* use, because every custom field
            # lands on a layout the moment it is created. A field known only to
            # layouts, holding no data, is a prime cleanup candidate even though
            # a naive reference count calls it used.
            layout_only = bool(r.layout_refs) and not r.functional_refs
            out.evidence.append({
                "component_id": r.id, "result": "EVIDENCE_OF_USE", "tier": "A",
                "weight": 0.5 if layout_only else 1.0,
                "payload": {
                    "binding_references": r.tier_a,
                    "sources": r.sources,
                    "layout_references": r.layout_refs,
                    "functional_references": r.functional_refs,
                    "binding_source_types": list(r.binding_sources or []),
                    "layout_names": list(r.layout_names or []),
                    "layout_only": layout_only,
                    **({"note": "only layouts reference this; binding for deploy "
                                "purposes but not evidence of business use"}
                       if layout_only else {}),
                },
            })
        elif r.tier_c:
            # Weak references only: consistent with use, nowhere near proof.
            srcs = list(r.weak_sources or [])
            out.evidence.append({
                "component_id": r.id, "result": "INCONCLUSIVE", "tier": "C",
                "weight": 0.3,
                "payload": {
                    "weak_references": r.tier_c, "sources": r.sources,
                    "weak_source_types": srcs,
                    "note": _weak_note(srcs),
                },
            })
        else:
            out.evidence.append({
                "component_id": r.id, "result": "NO_EVIDENCE_FOUND", "tier": None,
                "weight": 0.0,
                "payload": {"artifacts_searched": total_artifacts,
                            "binding_references": 0},
            })
    return out


# ---------------------------------------------------------------------------
# C20 — data population
# ---------------------------------------------------------------------------

async def collect_data_population(
    run_id: str, sf: SalesforceClient
) -> CollectorOutcome:
    """Does real data exist in each field, and does the object hold records?

    Population is CORROBORATING evidence only, never deciding. A field can be
    100% populated by a 2019 migration and dead ever since, or 0% populated and
    written by an integration next Tuesday.
    """
    out = CollectorOutcome(
        "C20_data_population", "data_population",
        method="multi-aggregate SOQL: SELECT COUNT(Id), COUNT(f1), COUNT(f2)... "
               "(O(objects), not O(fields)); existence probe for types that "
               "reject COUNT()",
    )
    async with session_scope() as s:
        fields = (await s.execute(text("""
            SELECT id, api_name, parent_object FROM components
             WHERE run_id = :r AND ctype = 'CustomField' AND in_scope
             ORDER BY parent_object, api_name
        """), {"r": run_id})).all()
        objects = (await s.execute(text("""
            SELECT id, api_name FROM components
             WHERE run_id = :r AND ctype = 'CustomObject' AND in_scope
        """), {"r": run_id})).all()

    by_object: dict[str, list] = {}
    for f in fields:
        by_object.setdefault(f.parent_object, []).append(f)
    out.artifacts = len(by_object)

    for obj, flds in by_object.items():
        names = [f.api_name.split(".", 1)[-1] for f in flds]
        try:
            pop, unsupported = await sf.field_population(obj, names)
        except Exception as e:
            out.status = "PARTIAL"
            for f in flds:
                out.gaps.append({
                    "component_id": f.id, "reason": "FAILED",
                    "detail": f"population query failed for {obj}: {str(e)[:160]}"})
            continue

        for f in flds:
            short = f.api_name.split(".", 1)[-1]
            if short in pop:
                v = pop[short]
                if v["populated"] > 0:
                    out.hits += 1
                    out.evidence.append({
                        "component_id": f.id, "result": "EVIDENCE_OF_USE",
                        "tier": "B", "weight": 0.7, "payload": {
                            **v, "pct": round(100 * v["populated"] / max(v["total"], 1), 2)}})
                else:
                    out.evidence.append({
                        "component_id": f.id, "result": "NO_EVIDENCE_FOUND",
                        "tier": None, "weight": 0.0, "payload": {
                            **v, "pct": 0.0,
                            "note": "no record holds a value; corroborating only"}})
            elif short in unsupported:
                got = await sf.field_has_any_value(obj, short)
                if got is True:
                    out.hits += 1
                    out.evidence.append({
                        "component_id": f.id, "result": "EVIDENCE_OF_USE",
                        "tier": "B", "weight": 0.7,
                        "payload": {"probe": "existence", "has_data": True}})
                elif got is False:
                    out.evidence.append({
                        "component_id": f.id, "result": "NO_EVIDENCE_FOUND",
                        "tier": None, "weight": 0.0,
                        "payload": {"probe": "existence", "has_data": False,
                                    "reason": unsupported[short]}})
                else:
                    # Probe failed: NOT the same as "no data".
                    out.evidence.append({
                        "component_id": f.id, "result": "INCONCLUSIVE",
                        "tier": None, "weight": 0.0,
                        "payload": {"probe": "existence", "error": True}})

    # Object-level record counts.
    for o in objects:
        try:
            n = await sf.query_count(f"SELECT COUNT() FROM {o.api_name}")
        except Exception as e:
            out.gaps.append({"component_id": o.id, "reason": "FAILED",
                             "detail": str(e)[:160]})
            continue
        if n > 0:
            out.hits += 1
            out.evidence.append({
                "component_id": o.id, "result": "EVIDENCE_OF_USE", "tier": "B",
                "weight": 0.7, "payload": {"record_count": n}})
        else:
            out.evidence.append({
                "component_id": o.id, "result": "NO_EVIDENCE_FOUND", "tier": None,
                "weight": 0.0, "payload": {"record_count": 0}})
    return out


# ---------------------------------------------------------------------------
# C30 — Dependency API
# ---------------------------------------------------------------------------

async def collect_dependency_api(
    run_id: str, sf: SalesforceClient, caps: OrgCapabilities
) -> CollectorOutcome:
    """Salesforce's own record of what references what (MetadataComponentDependency).

    Component-level only (ApexClass, LWC, Flow, …) — not ApexMethod. A miss is
    reported as NO_EVIDENCE_FOUND so the UI shows Nothing found.
    """
    out = CollectorOutcome(
        "C30_dependency_api", "dependency_graph",
        method="MetadataComponentDependency (Tooling; component-level edges)",
    )
    if caps.has_dependency_api is False:
        out.status = "UNAVAILABLE"
        out.unavailable_reason = "Dependency API did not respond in this org"
        return out

    try:
        rows = await sf.query(
            "SELECT MetadataComponentId, MetadataComponentName, "
            "MetadataComponentType, RefMetadataComponentId, "
            "RefMetadataComponentName, RefMetadataComponentType "
            "FROM MetadataComponentDependency LIMIT 2000",
            tooling=True,
        )
    except Exception as e:
        out.status = "ERROR"
        out.error = str(e)[:300]
        return out

    out.artifacts = len(rows)
    truncated = len(rows) == 2000

    referenced: dict[str, list[dict]] = {}
    for r in rows:
        for key in (r.get("RefMetadataComponentName"), r.get("RefMetadataComponentId")):
            if key:
                referenced.setdefault(str(key).lower(), []).append({
                    "by": r.get("MetadataComponentName"),
                    "by_type": r.get("MetadataComponentType")})

    async with session_scope() as s:
        comps = (await s.execute(text("""
            SELECT c.id, c.api_name, c.sf_id, c.ctype::text
              FROM components c
             WHERE c.run_id = :r AND c.in_scope AND c.ctype::text != 'ApexMethod'
        """), {"r": run_id})).all()

    for c in comps:
        keys = [c.api_name.lower(), c.api_name.split(".")[-1].lower()]
        if c.sf_id:
            keys += [c.sf_id.lower(), c.sf_id[:15].lower()]
        # The same edge is indexed under both name and Id, and a component is
        # looked up under several aliases — without dedupe the UI lists
        # "accountList (LWC)" three times for one real dependency.
        seen: set[tuple[str | None, str | None]] = set()
        found: list[dict] = []
        for k in dict.fromkeys(keys):  # preserve order, drop duplicate keys
            for d in referenced.get(k, []):
                sig = (d.get("by"), d.get("by_type"))
                if sig in seen:
                    continue
                seen.add(sig)
                found.append(d)
        if found:
            out.hits += 1
            out.evidence.append({
                "component_id": c.id, "result": "EVIDENCE_OF_USE", "tier": "A",
                "weight": 1.0,
                "payload": {"dependency_edges": len(found), "referenced_by": found[:6]}})
        else:
            out.evidence.append({
                "component_id": c.id, "result": "NO_EVIDENCE_FOUND", "tier": None,
                "weight": 0.0, "payload": {
                    "dependency_edges": 0,
                    "rows_sampled": len(rows),
                    "truncated": truncated,
                    "note": "not found",
                    "types_covered": caps.dependency_api_types[:12]}})

    if truncated:
        out.status = "PARTIAL"
        out.unavailable_reason = (
            "Dependency API returned the 2,000-row cap; some edges may be missing")
    return out


# ---------------------------------------------------------------------------
# C40 — runtime telemetry
# ---------------------------------------------------------------------------

async def collect_runtime(
    run_id: str, sf: SalesforceClient, caps: OrgCapabilities
) -> CollectorOutcome:
    """Runtime evidence for judged in-scope components.

    Apex: AsyncApexJob / CronTrigger / coverage plus Event Monitoring.
    LWC/Aura, CustomObject, CustomField: Event Monitoring only (optional
    Tier B — not required for completeness).
    """
    from app.pipeline.event_logs import (
        EventLogIndex,
        fetch_event_logs_from_org,
        load_event_logs,
        payload_from_hit,
    )

    out = CollectorOutcome(
        "C40_runtime", "runtime_telemetry",
        method="AsyncApexJob / CronTrigger / coverage; EventLogFile types that "
               "map to judged components (Apex*, LightningInteraction/PageView, "
               "RestApi, UniqueQuery, DatabaseSave, ApexRestApi); optional local "
               "CSVs under backend/event_data",
    )
    async with session_scope() as s:
        components = (await s.execute(text("""
            SELECT id, api_name, sf_id, ctype::text AS ctype,
                   parent_object, attrs
              FROM components
             WHERE run_id = :r
               AND ctype IN (
                   'ApexClass','ApexTrigger','ApexMethod',
                   'LightningComponentBundle','AuraDefinitionBundle',
                   'CustomObject','CustomField')
               AND in_scope
        """), {"r": run_id})).all()

    # Prefer live org logs; merge local CSVs (POC / demos) on top.
    event_idx = EventLogIndex()
    try:
        org_idx = await fetch_event_logs_from_org(sf)
        if org_idx.files_read:
            event_idx.merge(org_idx)
            caps.has_event_monitoring = True
            caps.probe_notes["event_monitoring"] = (
                f"downloaded {len(org_idx.files_read)} EventLogFile(s), "
                f"{org_idx.rows} rows"
            )
    except Exception as e:
        log.warning("event_log_org_fetch_failed", error=str(e)[:200])
        out.error = (out.error or "") + f" EventLogFile: {str(e)[:120]}"

    local_idx = load_event_logs()
    if local_idx.files_read:
        event_idx.merge(local_idx)

    if event_idx.files_read:
        out.artifacts += event_idx.rows

    apex_rows = [c for c in components if c.ctype in (
        "ApexClass", "ApexTrigger", "ApexMethod")]
    other_rows = [c for c in components if c.ctype not in (
        "ApexClass", "ApexTrigger", "ApexMethod")]

    ran: dict[str, dict] = {}
    try:
        jobs = await sf.query(
            "SELECT ApexClassId, JobType, Status, COUNT(Id) runs "
            "FROM AsyncApexJob GROUP BY ApexClassId, JobType, Status")
        for j in jobs:
            cid = (j.get("ApexClassId") or "")[:15].lower()
            if cid:
                ran.setdefault(cid, {"runs": 0})["runs"] += int(j.get("runs") or 0)
        out.artifacts += len(jobs)
    except Exception as e:
        out.status = "PARTIAL"
        out.error = f"AsyncApexJob: {str(e)[:160]}"

    scheduled: set[str] = set()
    try:
        crons = await sf.query(
            "SELECT CronJobDetail.Name, State, TimesTriggered, NextFireTime "
            "FROM CronTrigger")
        for c in crons:
            nm = ((c.get("CronJobDetail") or {}).get("Name") or "").lower()
            if nm:
                scheduled.add(nm)
        out.artifacts += len(crons)
    except Exception as e:
        out.error = (out.error or "") + f" CronTrigger: {str(e)[:120]}"

    coverage: dict[str, int] = {}
    if caps.has_code_coverage:
        try:
            cov = await sf.query(
                "SELECT ApexClassOrTriggerId, NumLinesCovered FROM "
                "ApexCodeCoverageAggregate", tooling=True)
            for c in cov:
                cid = (c.get("ApexClassOrTriggerId") or "")[:15].lower()
                if cid:
                    coverage[cid] = int(c.get("NumLinesCovered") or 0)
        except Exception:
            pass

    log_source = event_idx.source if event_idx.files_read else "none"

    def _attrs(row) -> dict:
        a = row.attrs
        if isinstance(a, dict):
            return a
        return {}

    def _ev_hit_for(row) -> object | None:
        name = row.api_name or ""
        ctype = row.ctype
        if ctype == "ApexClass":
            return event_idx.hit_for_apex_class(name)
        if ctype == "ApexTrigger":
            return event_idx.hit_for_trigger(name)
        if ctype == "ApexMethod":
            return event_idx.hit_for_method(name)
        if ctype in ("LightningComponentBundle", "AuraDefinitionBundle"):
            return event_idx.hit_for_ui(name)
        if ctype == "CustomObject":
            hit = event_idx.hit_for_object(name)
            if hit and hit.total:
                return hit
            prefix = (_attrs(row).get("key_prefix") or "")
            return event_idx.hit_for_key_prefix(str(prefix)) if prefix else None
        if ctype == "CustomField":
            return event_idx.hit_for_field(name, row.parent_object)
        return None

    for c in apex_rows:
        sid = (c.sf_id or "")[:15].lower()
        name = c.api_name or ""
        ctype = c.ctype
        job = ran.get(sid) if ctype != "ApexMethod" else None
        sched = name.lower() in scheduled if ctype == "ApexClass" else False
        cov = coverage.get(sid, 0) if ctype != "ApexMethod" else 0
        ev_hit = _ev_hit_for(c)
        org_hit = bool(job or sched)
        log_hit = bool(ev_hit and ev_hit.total > 0)

        if org_hit or log_hit:
            out.hits += 1
            payload: dict = {
                "entity_name": name,
                "async_runs": (job or {}).get("runs", 0),
                "scheduled": sched,
                "lines_covered": cov,
            }
            if log_hit and ev_hit:
                payload.update(payload_from_hit(ev_hit, source=log_source))
            out.evidence.append({
                "component_id": c.id, "result": "EVIDENCE_OF_USE", "tier": "B",
                "weight": 0.95 if log_hit else 0.9, "payload": payload})
        else:
            note = (
                "no async, scheduled, or event-log execution recorded. "
                "Synchronous Apex with no Event Monitoring leave no trace "
                "here, so this is weak evidence."
            )
            if event_idx.files_read:
                src_label = (
                    "org EventLogFile" if "event_log_file" in log_source
                    else "local event logs"
                )
                note = (
                    f"no async/scheduled job and no matching row in {src_label} "
                    f"({len(event_idx.files_read)} file(s))"
                )
            out.evidence.append({
                "component_id": c.id, "result": "NO_EVIDENCE_FOUND", "tier": None,
                "weight": 0.0, "payload": {
                    "entity_name": name,
                    "async_runs": 0, "scheduled": False, "lines_covered": cov,
                    "note": note}})

    for c in other_rows:
        name = c.api_name or ""
        ev_hit = _ev_hit_for(c)
        log_hit = bool(ev_hit and ev_hit.total > 0)
        if log_hit and ev_hit:
            out.hits += 1
            payload = {"entity_name": name}
            payload.update(payload_from_hit(ev_hit, source=log_source))
            out.evidence.append({
                "component_id": c.id, "result": "EVIDENCE_OF_USE", "tier": "B",
                "weight": 0.9, "payload": payload})
        else:
            note = (
                "no matching Event Monitoring row for this component"
                if event_idx.files_read else
                "Event Monitoring not available; UI/object/field runtime "
                "access is unobservable here"
            )
            out.evidence.append({
                "component_id": c.id, "result": "NO_EVIDENCE_FOUND", "tier": None,
                "weight": 0.0, "payload": {"entity_name": name, "note": note}})

    # Event Monitoring is the strongest runtime signal. Loud when missing.
    org_em = any(f.startswith("EventLogFile:") for f in event_idx.files_read)
    local_only = event_idx.files_read and not org_em
    if not event_idx.files_read and caps.has_event_monitoring is False:
        out.status = "PARTIAL" if out.status == "OK" else out.status
        out.unavailable_reason = (
            "Event Monitoring unavailable: external API traffic, page views and "
            "report exports are unobservable in this org")
    elif local_only and caps.has_event_monitoring is False:
        log.info("event_logs_substituted_for_event_monitoring",
                 files=event_idx.files_read)
    elif org_em:
        log.info("event_logs_from_event_log_file",
                 files=len(event_idx.files_read), rows=event_idx.rows)
    return out


# ---------------------------------------------------------------------------
# C50 — temporal
# ---------------------------------------------------------------------------

async def collect_temporal(run_id: str) -> CollectorOutcome:
    """Recently-created components look exactly like dead ones."""
    s_cfg = get_settings()
    days = await _run_recent_change_days(run_id, s_cfg.analysis_recent_change_days)
    cutoff = datetime.now(UTC) - timedelta(days=days)
    out = CollectorOutcome(
        "C50_temporal", "temporal",
        method=f"created/modified within {days} days "
               "-> RECENTLY_CHANGED (half-built features resemble dead ones)",
    )
    async with session_scope() as s:
        rows = (await s.execute(text("""
            SELECT id, api_name, created_date, last_modified_date
              FROM components
             WHERE run_id = :r AND in_scope AND ctype::text != 'ApexMethod'
        """), {"r": run_id})).all()
    out.artifacts = len(rows)
    for r in rows:
        newest = max([d for d in (r.created_date, r.last_modified_date) if d],
                     default=None)
        if newest and newest > cutoff:
            out.hits += 1
            out.flags.append({
                "component_id": r.id, "code": "RECENTLY_CHANGED",
                "severity": "medium",
                "detail": f"last changed {newest:%Y-%m-%d}; within the "
                          f"{days}-day recency window — may be an in-progress "
                          "feature rather than an abandoned one",
                "source_ref": {
                    "window_days": days,
                    "last_changed": f"{newest:%Y-%m-%d}",
                },
            })
    return out


async def _run_recent_change_days(run_id: str, default: int) -> int:
    """Per-run override from ``runs.config.recent_change_days``, else settings."""
    async with session_scope() as s:
        cfg = await s.scalar(text(
            "SELECT config FROM runs WHERE id = :r"), {"r": run_id})
    if isinstance(cfg, dict):
        raw = cfg.get("recent_change_days")
        try:
            n = int(raw)
            if 1 <= n <= 3650:
                return n
        except (TypeError, ValueError):
            pass
    return default


# ---------------------------------------------------------------------------
# C60 — dynamic Apex blast radius
# ---------------------------------------------------------------------------

async def collect_dynamic_taint(run_id: str, dynamic_files: list[str]) -> CollectorOutcome:
    """Quarantine everything a dynamic-Apex class could touch.

    No extractor can resolve a name assembled at runtime, so we do not try. We
    detect the capability and hold back every component the class could reach.

    Deliberately blunt: a generic `SObjectUtils.copyFields(a, b)` can touch
    anything, and pretending otherwise is how tools delete production fields.
    """
    out = CollectorOutcome(
        "C60_dynamic_apex", "static_reference",
        method="scan for getGlobalDescribe / Database.query / .get( / .put( / "
               "Type.forName etc; taint every object such a class touches",
    )
    if not dynamic_files:
        return out

    # Preserve path + class stem for clickable locations in the UI.
    file_by_class: dict[str, str] = {}
    for f in dynamic_files:
        stem = f.split("\\")[-1].split("/")[-1].removesuffix(".cls")
        if stem:
            file_by_class[stem] = f.replace("\\", "/")
    class_names = list(file_by_class.keys())

    async with session_scope() as s:
        rows = (await s.execute(text("""
            SELECT DISTINCT c.id, c.api_name, c.ctype::text
              FROM components c
              JOIN reference_edges e ON e.to_component_id = c.id
              JOIN artifact a ON a.id = e.from_artifact_id
             WHERE c.run_id = :r AND c.in_scope
               AND a.member_name = ANY(:names)
        """), {"r": run_id, "names": class_names})).all()

        # Standard objects count here too. Dynamic Apex reaching Account must
        # quarantine Account's CUSTOM fields — those are deletable and therefore
        # in scope, even though the object itself is not. Restricting this to
        # custom objects would leave a third of this org's fields unprotected.
        touched_objects = {r.api_name for r in rows
                           if r.ctype in ("CustomObject", "StandardObject")}
        extra = []
        if touched_objects:
            extra = (await s.execute(text("""
                SELECT id, api_name FROM components
                 WHERE run_id = :r AND ctype = 'CustomField' AND in_scope
                   AND parent_object = ANY(:objs)
            """), {"r": run_id, "objs": list(touched_objects)})).all()

        # Map dynamic class names → inventoried ApexClass rows for reveal links.
        class_comps = (await s.execute(text("""
            SELECT id, api_name FROM components
             WHERE run_id = :r AND ctype = 'ApexClass'
               AND api_name = ANY(:names)
        """), {"r": run_id, "names": class_names})).all()

    sources = [{
        "api_name": r.api_name,
        "component_id": int(r.id),
        "file_path": file_by_class.get(r.api_name),
    } for r in class_comps]
    # Classes found only as files (not inventoried) still appear as locations.
    known = {s["api_name"].lower() for s in sources}
    for name, path in file_by_class.items():
        if name.lower() not in known:
            sources.append({"api_name": name, "component_id": None,
                            "file_path": path})

    out.artifacts = len(class_names)
    seen: set[int] = set()
    names_preview = ", ".join(class_names[:3])
    for r in list(rows) + list(extra):
        if r.id in seen:
            continue
        seen.add(r.id)
        out.flags.append({
            "component_id": r.id, "code": "DYNAMIC_APEX_IN_SCOPE",
            "severity": "high",
            "detail": f"reachable from dynamic Apex in {names_preview}; "
                      "a runtime-built name cannot be resolved statically",
            "source_ref": {"sources": sources},
        })
    out.hits = len(seen)
    return out


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------

async def persist(run_id: str, outcome: CollectorOutcome) -> None:
    import json

    async with session_scope() as s:
        await s.execute(text("""
            INSERT INTO collector_run (run_id, collector_id, collector_family,
                scope_key, status, method, query_text, artifacts_searched, hits,
                unavailable_reason, error_text)
            VALUES (:r, :cid, :fam, '*', CAST(:st AS collector_status), :m, :m,
                    :arts, :hits, :why, :err)
            ON CONFLICT (run_id, collector_id, scope_key) DO UPDATE SET
                status = EXCLUDED.status, hits = EXCLUDED.hits,
                artifacts_searched = EXCLUDED.artifacts_searched,
                unavailable_reason = EXCLUDED.unavailable_reason,
                error_text = EXCLUDED.error_text
        """), {"r": run_id, "cid": outcome.collector_id, "fam": outcome.family,
               "st": outcome.status, "m": outcome.method[:400],
               "arts": outcome.artifacts, "hits": outcome.hits,
               "why": outcome.unavailable_reason, "err": outcome.error})

        if outcome.evidence:
            await s.execute(text("""
                INSERT INTO evidence (run_id, component_id, collector_id, result,
                                      tier, weight, payload)
                VALUES (:r, :c, :cid, CAST(:res AS collector_result),
                        CAST(:tier AS evidence_tier), :w, CAST(:p AS jsonb))
                ON CONFLICT (component_id, collector_id) DO UPDATE SET
                    result = EXCLUDED.result, tier = EXCLUDED.tier,
                    weight = EXCLUDED.weight, payload = EXCLUDED.payload
            """), [{"r": run_id, "c": e["component_id"],
                    "cid": outcome.collector_id, "res": e["result"],
                    "tier": e.get("tier"), "w": e.get("weight", 0.0),
                    "p": json.dumps(e.get("payload", {}), default=str)}
                   for e in outcome.evidence])

        if outcome.gaps:
            await s.execute(text("""
                INSERT INTO evidence_gaps (run_id, component_id, collector_id,
                                           stage_key, reason, detail)
                VALUES (:r, :c, :cid, 'collect', :why, :d)
                ON CONFLICT (component_id, collector_id) DO NOTHING
            """), [{"r": run_id, "c": g["component_id"],
                    "cid": outcome.collector_id, "why": g["reason"],
                    "d": g.get("detail")} for g in outcome.gaps])

        if outcome.flags:
            import json as _json
            await s.execute(text("""
                INSERT INTO uncertainty_flags (run_id, component_id, code,
                                               severity, detail, source_ref)
                VALUES (:r, :c, :code, :sev, :d, CAST(:src AS jsonb))
                ON CONFLICT (component_id, code) DO UPDATE SET
                    detail = EXCLUDED.detail,
                    source_ref = COALESCE(EXCLUDED.source_ref, uncertainty_flags.source_ref)
            """), [{"r": run_id, "c": f["component_id"], "code": f["code"],
                    "sev": f.get("severity", "medium"), "d": f.get("detail"),
                    "src": _json.dumps(f.get("source_ref")) if f.get("source_ref") is not None else None}
                   for f in outcome.flags])

    log.info("collector_persisted", collector=outcome.collector_id,
             status=outcome.status, hits=outcome.hits,
             evidence=len(outcome.evidence), gaps=len(outcome.gaps),
             flags=len(outcome.flags))
