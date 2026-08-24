"""Collectors that read DATA rather than metadata.

C80 exists because of the single most dangerous blind spot in this whole design.

Config-driven orgs store API names **as data**. A Custom Metadata record like

    Field_Mapping__mdt: { Source_Field__c: 'Legacy_Code__c',
                          Target_Object__c: 'Customer_Order__c' }

is load-bearing at runtime — some Apex reads that row and acts on it — but *no
metadata parser will ever find it*. The field is referenced by a string sitting
in a table. Every static analyser, including everything else in this codebase,
would report it as completely unreferenced and recommend deletion.

C90 asks Salesforce itself whether a delete would succeed. That is the only
signal here which is server-validated rather than inferred, so it outranks
everything else: a refusal names the exact blocker, and it catches references our
parsers missed for reasons we have not thought of.
"""

from __future__ import annotations

import json
from pathlib import Path

import structlog
from sqlalchemy import text
from xml.sax.saxutils import escape

from app.config import get_settings
from app.db.session import session_scope
from app.pipeline.collectors import CollectorOutcome
from app.salesforce.client import SalesforceClient

log = structlog.get_logger()

#: Text-ish types worth sweeping for API names. Numbers and dates cannot hold one.
_TEXTY = ("string", "textarea", "picklist", "url", "email", "phone", "id",
          "reference", "multipicklist")


async def collect_config_data(run_id: str, sf: SalesforceClient) -> CollectorOutcome:
    """Sweep Custom Metadata Type and list Custom Setting records for API names."""
    out = CollectorOutcome(
        "C80_config_data", "static_reference",
        method="enumerate CustomMetadata types and list Custom Settings, query "
               "every row, token-sweep all text values against the alias table "
               "(API names stored as DATA are invisible to metadata parsers)",
    )

    # Find the config-holding objects. EntityDefinition cannot be enumerated, so
    # this filters on the suffix pattern via a supported predicate.
    try:
        ents = await sf.query(
            "SELECT QualifiedApiName, Label, IsCustomSetting FROM EntityDefinition "
            "WHERE IsCustomSetting = true LIMIT 200", tooling=True)
    except Exception as e:
        ents = []
        out.status = "PARTIAL"
        out.error = f"custom settings lookup failed: {str(e)[:160]}"

    settings = [e["QualifiedApiName"] for e in ents
                if str(e.get("QualifiedApiName", "")).endswith("__c")]

    mdts: list[str] = []
    try:
        rows = await sf.query(
            "SELECT DeveloperName, NamespacePrefix FROM CustomObject "
            "WHERE NamespacePrefix = null", tooling=True)
        mdts = [f"{r['DeveloperName']}__mdt" for r in rows
                if r.get("DeveloperName")]
    except Exception:
        # CustomObject does not reliably list __mdt types; fall back to the
        # retrieved source tree, which does.
        mdts = _mdt_types_from_workspace(run_id)

    targets = sorted(set(settings + mdts))
    out.artifacts = len(targets)
    if not targets:
        out.status = "OK" if out.status == "OK" else out.status
        out.unavailable_reason = (
            "no Custom Metadata Types or list Custom Settings found. If the org "
            "gains any, API names stored in their records would be invisible to "
            "static analysis.")
        return out

    aliases = await _load_alias_index(run_id)
    matched: dict[int, list[dict]] = {}
    scanned = 0

    for obj in targets:
        try:
            fields = await sf.query(
                "SELECT QualifiedApiName, DataType FROM FieldDefinition "
                f"WHERE EntityDefinition.QualifiedApiName = '{obj}'", tooling=True)
        except Exception:
            continue
        texty = [f["QualifiedApiName"] for f in fields
                 if any(t in str(f.get("DataType", "")).lower() for t in _TEXTY)]
        if not texty:
            continue
        cols = ", ".join(texty[:40])
        try:
            rows = await sf.query(f"SELECT {cols} FROM {obj} LIMIT 2000")
        except Exception as e:
            log.info("config_scan_skipped", obj=obj, error=str(e)[:120])
            continue
        scanned += len(rows)
        for row in rows:
            for key, val in row.items():
                if key == "attributes" or not isinstance(val, str):
                    continue
                for hit in aliases.get(val.strip().lower(), ()):
                    matched.setdefault(hit[0], []).append(
                        {"object": obj, "field": key, "value": val[:80]})

    out.hits = len(matched)
    for component_id, refs in matched.items():
        out.evidence.append({
            "component_id": component_id, "result": "EVIDENCE_OF_USE",
            "tier": "A", "weight": 1.0,
            "payload": {
                "config_references": len(refs), "found_in": refs[:5],
                "note": "this API name is stored as DATA in configuration. "
                        "Runtime code reads that row, so the component is "
                        "load-bearing even though no metadata references it.",
            },
        })

    # Everything scanned but unmatched: a genuine negative, recorded as such.
    async with session_scope() as s:
        all_ids = [r.id for r in (await s.execute(text(
            "SELECT id FROM components WHERE run_id = :r AND in_scope"
        ), {"r": run_id})).all()]
    for cid in all_ids:
        if cid not in matched:
            out.evidence.append({
                "component_id": cid, "result": "NO_EVIDENCE_FOUND",
                "tier": None, "weight": 0.0,
                "payload": {"config_objects_scanned": len(targets),
                            "rows_scanned": scanned, "config_references": 0},
            })

    log.info("config_data_scanned", objects=len(targets), rows=scanned,
             matched=len(matched))
    return out


def _mdt_types_from_workspace(run_id: str) -> list[str]:
    """Discover __mdt types from the retrieved source, which lists them reliably."""
    s = get_settings()
    found: set[str] = set()
    root = s.workspace
    if not root.exists():
        return []
    for p in root.rglob("*.object"):
        if p.stem.endswith("__mdt"):
            found.add(p.stem)
    for p in root.rglob("*.object-meta.xml"):
        stem = p.name.replace(".object-meta.xml", "")
        if stem.endswith("__mdt"):
            found.add(stem)
    return sorted(found)


async def _load_alias_index(run_id: str) -> dict[str, list[tuple[int, str]]]:
    from collections import defaultdict
    async with session_scope() as s:
        rows = (await s.execute(text(
            "SELECT component_id, alias_lc, alias_kind FROM alias "
            "WHERE run_id = :r AND alias_kind IN "
            "('api_name','qualified','bare_name','sf_id_15','sf_id_18')"
        ), {"r": run_id})).all()
    out: dict[str, list[tuple[int, str]]] = defaultdict(list)
    for r in rows:
        out[r.alias_lc].append((r.component_id, r.alias_kind))
    return out


# ---------------------------------------------------------------------------
# C90 — delete rehearsal
# ---------------------------------------------------------------------------

async def collect_delete_rehearsal(
    run_id: str, sf: SalesforceClient, cli_factory, *, batch_size: int = 25,
) -> CollectorOutcome:
    """Ask Salesforce whether the UNUSED set could actually be deleted.

    A validate-only destructive deploy. **Nothing is deleted** — Salesforce
    checks the request and reports whether it would succeed, naming whatever
    still references the component if not.

    This is the only server-validated signal in the system, so it outranks every
    inference: it can promote a component back to USED, never the reverse. It is
    also the honest accuracy metric — anything we called UNUSED that fails here
    is a confirmed false positive.

    Batched deliberately: one blocker fails its whole batch, so small batches
    keep a single stubborn component from masking the rest.
    """
    out = CollectorOutcome(
        "C90_delete_rehearsal", "delete_rehearsal",
        method="generate destructiveChanges.xml and run "
               "`sf project deploy start --dry-run` (validate-only; deletes "
               "nothing). Salesforce's own dependency checker names any blocker.",
    )

    async with session_scope() as s:
        rows = (await s.execute(text("""
            SELECT c.id, c.ctype::text AS ctype, c.api_name
              FROM classifications cl JOIN components c ON c.id = cl.component_id
             WHERE cl.run_id = :r AND cl.label = 'UNUSED' AND c.in_scope
             ORDER BY c.ctype, c.api_name
        """), {"r": run_id})).all()

    if not rows:
        out.status = "SKIPPED"
        out.unavailable_reason = "no UNUSED components to rehearse"
        return out

    # A method is not a deployable metadata type. You remove one by editing its
    # class, not by a destructive change, so a rehearsal for it can only ever
    # fail — and recording that failure as a coverage gap would wrongly suppress
    # verdicts for every dead method in the org.
    #
    # NOT_APPLICABLE is the honest result: this check does not apply here, which
    # is a different claim from "the check failed". A method's deletability
    # follows its class, and the class IS rehearsed.
    methods = [r for r in rows if r.ctype == "ApexMethod"]
    rows = [r for r in rows if r.ctype != "ApexMethod"]
    for r in methods:
        out.evidence.append({
            "component_id": r.id, "result": "NOT_APPLICABLE",
            "tier": None, "weight": 0.0,
            "payload": {
                "rehearsal": "NOT_APPLICABLE",
                "note": "Apex methods are not independently deployable. Removing "
                        "one means editing its class, so Salesforce cannot "
                        "validate a method-level delete. The parent class is "
                        "rehearsed instead.",
            }})
    if methods:
        log.info("rehearsal_methods_skipped", count=len(methods))
    if not rows:
        out.status = "OK"
        out.artifacts = len(methods)
        return out

    out.artifacts = len(rows)
    cli = cli_factory()
    ws = get_settings().workspace / "rehearsal"
    ws.mkdir(parents=True, exist_ok=True)
    unresolved: list = []
    still_failing: list = []

    for start in range(0, len(rows), batch_size):
        batch = rows[start : start + batch_size]
        manifest_dir = ws / f"batch-{start // batch_size:02d}"
        manifest_dir.mkdir(parents=True, exist_ok=True)

        # An empty package.xml plus destructiveChanges is the standard shape for
        # a delete-only deployment.
        (manifest_dir / "package.xml").write_text(
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            '<Package xmlns="http://soap.sforce.com/2006/04/metadata">\n'
            f"    <version>{cli._conn.api_version}</version>\n"
            "</Package>\n", encoding="utf-8")

        by_type: dict[str, list[str]] = {}
        for r in batch:
            by_type.setdefault(_destructive_type(r.ctype), []).append(r.api_name)
        lines = ['<?xml version="1.0" encoding="UTF-8"?>',
                 '<Package xmlns="http://soap.sforce.com/2006/04/metadata">']
        for t, members in sorted(by_type.items()):
            lines.append("    <types>")
            for m in sorted(members):
                lines.append(f"        <members>{escape(m)}</members>")
            lines.append(f"        <name>{escape(t)}</name>")
            lines.append("    </types>")
        lines.append(f"    <version>{cli._conn.api_version}</version>")
        lines.append("</Package>")
        (manifest_dir / "destructiveChangesPost.xml").write_text(
            "\n".join(lines) + "\n", encoding="utf-8")

        ok, detail = await cli.deploy_dry_run(manifest_dir)
        blockers = _parse_blockers(detail)

        for r in batch:
            blocker = blockers.get(r.api_name.lower())
            if ok and not blocker:
                out.hits += 1
                out.evidence.append({
                    "component_id": r.id, "result": "NO_EVIDENCE_FOUND",
                    "tier": None, "weight": 0.0,
                    "payload": {"rehearsal": "PASSED", "server_validated": True,
                                "note": "Salesforce accepted the delete in "
                                        "validate-only mode; no metadata "
                                        "dependency blocks removal."},
                })
            elif blocker:
                # Conclusive. Salesforce says something still needs this.
                out.evidence.append({
                    "component_id": r.id, "result": "EVIDENCE_OF_USE",
                    "tier": "A", "weight": 1.0,
                    "payload": {"rehearsal": "BLOCKED", "server_validated": True,
                                "blocker": blocker,
                                "note": "Salesforce refused the delete. This is "
                                        "conclusive: something still depends on "
                                        "it, and our analysis missed it."},
                })
            else:
                unresolved.append(r)
        if not ok:
            out.status = "PARTIAL"

    # RETRY INDIVIDUALLY.
    #
    # A batch fails as a unit, so one stubborn component drags every other member
    # down with it — they inherit a gap they did nothing to earn, and a gap
    # blocks an UNUSED verdict. Measured on a real run: a single batch failure
    # left every candidate unvalidated and three fields wrongly parked at
    # INSUFFICIENT_EVIDENCE. Retrying alone tells us which component is actually
    # the problem.
    if unresolved:
        log.info("rehearsal_retrying_individually", count=len(unresolved))
        for r in unresolved:
            d = ws / f"solo-{r.id}"
            d.mkdir(parents=True, exist_ok=True)
            _write_manifest(d, {_destructive_type(r.ctype): [r.api_name]},
                            cli._conn.api_version)
            ok1, detail1 = await cli.deploy_dry_run(d)
            blocker = _parse_blockers(detail1).get(r.api_name.lower())
            if ok1 and not blocker:
                out.hits += 1
                out.evidence.append({
                    "component_id": r.id, "result": "NO_EVIDENCE_FOUND",
                    "tier": None, "weight": 0.0,
                    "payload": {"rehearsal": "PASSED", "server_validated": True,
                                "retried_alone": True,
                                "note": "Salesforce accepted the delete when "
                                        "validated on its own; the earlier batch "
                                        "failure was caused by another member."}})
            elif blocker:
                out.evidence.append({
                    "component_id": r.id, "result": "EVIDENCE_OF_USE",
                    "tier": "A", "weight": 1.0,
                    "payload": {"rehearsal": "BLOCKED", "server_validated": True,
                                "blocker": blocker, "retried_alone": True}})
            else:
                unaddressable = _not_metadata_addressable(detail1, r.api_name)
                if unaddressable:
                    out.evidence.append({
                        "component_id": r.id, "result": "NOT_APPLICABLE",
                        "tier": None, "weight": 0.0,
                        "payload": {
                            "rehearsal": "NOT_APPLICABLE",
                            "server_validated": True,
                            "salesforce_said": unaddressable,
                            "note": "Salesforce reports no deployable component "
                                    "of this type with this name. It exists in "
                                    "the describe but is not addressable through "
                                    "the Metadata API, so it cannot be deleted "
                                    "this way and a rehearsal cannot apply.",
                        }})
                    out.artifacts += 1
                    await _mark_not_addressable(run_id, r.id, unaddressable)
                    log.info("rehearsal_not_addressable", component=r.api_name)
                else:
                    still_failing.append(r)

    # RETRY AS CLUSTERS.
    #
    # Some components genuinely cannot be deleted alone: a set of Apex classes
    # that reference each other fails individually because removing one breaks
    # the others, and Salesforce is right to refuse. Rehearsing them one at a
    # time therefore proves nothing, and leaving them as coverage gaps
    # understates what is actually safe.
    #
    # The whole cluster is the unit of deletion, so it must also be the unit of
    # validation. If the group deploys cleanly together, every member is
    # confirmed — which matches the prerequisite the classifier already writes.
    if still_failing:
        clusters = await _cluster(run_id, still_failing)
        log.info("rehearsal_retrying_clusters", groups=len(clusters))
        for gi, group in enumerate(clusters):
            if len(group) == 1:
                out.gaps.append({
                    "component_id": group[0].id, "reason": "REHEARSAL_INCONCLUSIVE",
                    "detail": "failed alone and belongs to no cluster"})
                continue
            d = ws / f"cluster-{gi:02d}"
            by_type: dict[str, list[str]] = {}
            for r in group:
                by_type.setdefault(_destructive_type(r.ctype), []).append(r.api_name)
            _write_manifest(d, by_type, cli._conn.api_version)
            okc, detailc = await cli.deploy_dry_run(d)
            blockers = _parse_blockers(detailc)
            names = ", ".join(sorted(r.api_name for r in group)[:4])
            for r in group:
                blocker = blockers.get(r.api_name.lower())
                if okc and not blocker:
                    out.hits += 1
                    out.evidence.append({
                        "component_id": r.id, "result": "NO_EVIDENCE_FOUND",
                        "tier": None, "weight": 0.0,
                        "payload": {
                            "rehearsal": "PASSED_AS_CLUSTER",
                            "server_validated": True,
                            "cluster_size": len(group),
                            "cluster_members": sorted(x.api_name for x in group),
                            "note": "Salesforce accepted the delete when the whole "
                                    "group was validated together. It fails "
                                    "individually because these depend on each "
                                    "other - they must be deleted as one change.",
                        }})
                elif blocker:
                    out.evidence.append({
                        "component_id": r.id, "result": "EVIDENCE_OF_USE",
                        "tier": "A", "weight": 1.0,
                        "payload": {"rehearsal": "BLOCKED", "server_validated": True,
                                    "blocker": blocker, "in_cluster_of": len(group)}})
                else:
                    out.gaps.append({
                        "component_id": r.id, "reason": "REHEARSAL_INCONCLUSIVE",
                        "detail": f"cluster [{names}] failed: {detailc[:160]}"})

    log.info("rehearsal_complete", candidates=len(rows), passed=out.hits,
             blocked=sum(1 for e in out.evidence
                         if e["payload"].get("rehearsal") == "BLOCKED"))
    return out



async def _cluster(run_id: str, rows: list) -> list[list]:
    """Group components that reference each other into connected sets.

    Union-find over the reference edges restricted to this set. Only edges
    *within* the group matter: an edge to something outside it is what makes a
    component blocked rather than clustered, and that case was already decided
    before we got here.
    """
    by_name = {r.api_name.lower(): r for r in rows}
    parent = {r.id: r.id for r in rows}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    async with session_scope() as s:
        edges = (await s.execute(text("""
            SELECT a.member_name AS src, e.to_component_id AS dst
              FROM reference_edges e
              JOIN artifact a ON a.id = e.from_artifact_id
             WHERE e.run_id = :r AND e.tier = 'A'
               AND e.to_component_id = ANY(:ids)
        """), {"r": run_id, "ids": [r.id for r in rows]})).all()

    ids = {r.id for r in rows}
    for e in edges:
        src = by_name.get((e.src or "").lower())
        if src and e.dst in ids:
            union(src.id, e.dst)

    groups: dict[int, list] = {}
    for r in rows:
        groups.setdefault(find(r.id), []).append(r)
    return list(groups.values())


def _write_manifest(d, by_type: dict[str, list[str]], api: str) -> None:
    """Empty package.xml plus destructiveChanges - the delete-only shape."""
    d.mkdir(parents=True, exist_ok=True)
    header = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<Package xmlns="http://soap.sforce.com/2006/04/metadata">\n'
    )
    (d / "package.xml").write_text(
        f"{header}    <version>{api}</version>\n</Package>\n", encoding="utf-8")
    lines = ['<?xml version="1.0" encoding="UTF-8"?>',
             '<Package xmlns="http://soap.sforce.com/2006/04/metadata">']
    for t, members in sorted(by_type.items()):
        lines.append("    <types>")
        for m in sorted(members):
            lines.append(f"        <members>{escape(m)}</members>")
        lines.append(f"        <name>{escape(t)}</name>")
        lines.append("    </types>")
    lines.append(f"    <version>{api}</version>")
    lines.append("</Package>")
    (d / "destructiveChangesPost.xml").write_text(
        "\n".join(lines) + "\n", encoding="utf-8")


def _destructive_type(ctype: str) -> str:
    return {
        "CustomField": "CustomField", "CustomObject": "CustomObject",
        "ApexClass": "ApexClass", "ApexTrigger": "ApexTrigger",
    }.get(ctype, ctype)


async def _mark_not_addressable(run_id: str, component_id: int, msg: str) -> None:
    """Drop the component out of scope so rule R1 reports it as OUT_OF_SCOPE.

    Reusing the existing scope mechanism rather than adding a rule: "the
    platform will not delete this" is exactly what OUT_OF_SCOPE already means
    for managed-package components.
    """
    async with session_scope() as s:
        await s.execute(text("""
            UPDATE components
               SET in_scope = FALSE,
                   out_of_scope_reason = :why
             WHERE run_id = :r AND id = :c
        """), {"r": run_id, "c": component_id,
               "why": "NOT_METADATA_ADDRESSABLE"})
    log.info("component_out_of_scope", component_id=component_id,
             reason="NOT_METADATA_ADDRESSABLE", detail=msg[:120])


def _not_metadata_addressable(detail: str, api_name: str) -> str | None:
    """Return Salesforce's message when it says the component simply is not there.

    Distinct from a blocker. A blocker means "something still references this";
    this means "no such deployable component", which is what the platform says
    about fields that exist in the describe but are not exposed as Metadata API
    CustomFields -- Salesforce-owned fields on standard objects, for instance.
    """
    try:
        payload = json.loads(detail)
    except Exception:
        return None
    result = payload.get("result") or payload.get("data") or {}
    details = result.get("details") or {}
    entries = []
    for key in ("componentFailures", "componentSuccesses"):
        node = details.get(key) or result.get(key) or []
        if isinstance(node, dict):
            node = [node]
        entries.extend(node)
    needle = f"named: {api_name} found".lower()
    for f in entries:
        msg = str(f.get("problem") or "")
        low = msg.lower()
        if low.startswith("no ") and needle in low:
            return msg[:300]
    return None


def _parse_blockers(detail: str) -> dict[str, str]:
    """Map component name -> the reason Salesforce refused to delete it."""
    blockers: dict[str, str] = {}
    try:
        payload = json.loads(detail)
    except Exception:
        return blockers
    result = payload.get("result") or payload.get("data") or {}
    failures = (result.get("details", {}) or {}).get("componentFailures") or []
    if isinstance(failures, dict):
        failures = [failures]
    for f in failures:
        name = str(f.get("fullName") or "").lower()
        msg = str(f.get("problem") or "")
        if name:
            blockers[name] = msg[:300]
    return blockers
