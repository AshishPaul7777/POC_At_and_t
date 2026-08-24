"""Dependency graph and reachability analysis.

A better model than counting references per component. Counting answers "how many
things mention this?", which is not the question. The question is:

    Can this component be reached from anything that actually RUNS or that a
    person actually SEES?

If yes, some business process depends on it. If no, nothing does - regardless of
how many files happen to mention its name.

This also makes every verdict explainable as a PATH rather than a number:

    Order_Total__c
      <- OrderFulfillmentService (ApexClass)
      <- CustomerOrderTrigger (ApexTrigger)  [ENTRY POINT: trigger fires on DML]

"5 binding references" tells a reviewer nothing. That chain tells them exactly
why the field is alive, and what would have to change for it not to be.

ENTRY POINTS (graph roots)
--------------------------
Things that execute or are seen WITHOUT being called by something else:

    triggers, active Flows, workflow rules, validation rules, scheduled jobs,
    reports, dashboards, list views, quick actions, email templates,
    LWC / Aura / Visualforce, and Apex exposed via @AuraEnabled, @InvocableMethod,
    @RestResource, global, webservice, Schedulable, Batchable, Queueable.

NOT entry points - placement, not execution:

    page layouts, FlexiPages, compact layouts   (presentation)
    profiles, permission sets                   (who could see it)
    an object's own definition file             (declaration)
    tabs, applications                          (navigation)

That list is the same distinction the classifier uses, applied structurally
instead of per-component.
"""

from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass, field

import structlog
from sqlalchemy import text

from app.db.session import session_scope

log = structlog.get_logger()

#: Artifact types that run or are seen on their own initiative.
ENTRY_POINT_TYPES: frozenset[str] = frozenset({
    "ApexTrigger", "Flow", "FlowDefinition", "Workflow", "ValidationRule",
    "ApprovalProcess", "AssignmentRules", "EscalationRules", "AutoResponseRules",
    "DuplicateRule", "MatchingRule",
    "Report", "Dashboard", "ReportType", "AnalyticSnapshot",
    "EmailTemplate", "QuickAction", "WebLink", "PathAssistant",
    "LightningComponentBundle", "AuraDefinitionBundle", "ApexPage",
    "ApexComponent", "CustomNotificationType", "PlatformEventChannelMember",
})

#: Types that place or permit a component without exercising it. Reaching a
#: component only through one of these does NOT make it used.
NON_ENTRY_TYPES: frozenset[str] = frozenset({
    "Layout", "FlexiPage", "CompactLayout", "Profile", "PermissionSet",
    "PermissionSetGroup", "CustomApplication", "CustomTab", "SharingRules",
    "CustomObject", "package.xml", "GlobalValueSet", "StandardValueSet",
})


@dataclass
class Node:
    key: str                    # "component:123" | "artifact:456"
    kind: str                   # component | artifact
    label: str
    ctype: str                  # CustomField | ApexClass | Layout | Flow ...
    component_id: int | None = None
    artifact_id: int | None = None
    is_entry_point: bool = False
    entry_reason: str | None = None
    in_scope: bool = True
    reachable: bool = False
    distance: int | None = None
    via: str | None = None      # key of the neighbour that reached it


@dataclass
class Graph:
    nodes: dict[str, Node] = field(default_factory=dict)
    out: dict[str, set[str]] = field(default_factory=lambda: defaultdict(set))
    inc: dict[str, set[str]] = field(default_factory=lambda: defaultdict(set))
    edge_meta: dict[tuple[str, str], dict] = field(default_factory=dict)

    def add_edge(self, src: str, dst: str, **meta) -> None:
        if src == dst:
            return
        self.out[src].add(dst)
        self.inc[dst].add(src)
        self.edge_meta[(src, dst)] = meta

    def path_to_root(self, key: str, max_len: int = 12) -> list[dict]:
        """Walk back along the discovery edges to the entry point that reached it."""
        path, seen = [], set()
        cur = key
        while cur and cur not in seen and len(path) < max_len:
            seen.add(cur)
            n = self.nodes.get(cur)
            if not n:
                break
            path.append({
                "key": n.key, "label": n.label, "type": n.ctype,
                "entry_point": n.is_entry_point,
                "entry_reason": n.entry_reason,
            })
            if n.is_entry_point:
                break
            cur = n.via
        return path


async def build_graph(run_id: str) -> Graph:
    g = Graph()

    async with session_scope() as s:
        comps = (await s.execute(text("""
            SELECT id, ctype::text AS ctype, api_name, in_scope, attrs, parent_object
              FROM components WHERE run_id = :r
        """), {"r": run_id})).mappings().all()

        arts = (await s.execute(text("""
            SELECT id, metadata_type, member_name, is_active, is_test
              FROM artifact WHERE run_id = :r
        """), {"r": run_id})).mappings().all()

        edges = (await s.execute(text("""
            SELECT e.from_artifact_id, e.to_component_id, e.tier::text AS tier,
                   e.match_kind
              FROM reference_edges e
             WHERE e.run_id = :r AND e.tier IN ('A','B')
        """), {"r": run_id})).mappings().all()

    # --- component nodes -----------------------------------------------------
    for c in comps:
        attrs = c["attrs"] or {}
        entry = False
        reason = None
        # Apex whose caller lives outside the org is a root in its own right:
        # zero internal callers can never make it unreachable.
        if c["ctype"] == "ApexMethod" and attrs.get("is_entry_point"):
            entry, reason = True, "externally invocable Apex method"
        g.nodes[f"component:{c['id']}"] = Node(
            key=f"component:{c['id']}", kind="component", label=c["api_name"],
            ctype=c["ctype"], component_id=c["id"], in_scope=c["in_scope"],
            is_entry_point=entry, entry_reason=reason,
        )

    # --- artifact nodes ------------------------------------------------------
    for a in arts:
        mt = a["metadata_type"]
        entry = mt in ENTRY_POINT_TYPES
        reason = None
        if entry:
            reason = {
                "ApexTrigger": "trigger fires on record DML",
                "Flow": "flow runs on its own trigger or schedule",
                "Workflow": "workflow rule evaluates on record change",
                "ValidationRule": "validation runs on save",
                "Report": "a person runs or views this report",
                "Dashboard": "a person views this dashboard",
                "EmailTemplate": "sent to a recipient",
                "LightningComponentBundle": "rendered in the UI",
                "AuraDefinitionBundle": "rendered in the UI",
                "ApexPage": "rendered in the UI",
                "QuickAction": "invoked by a user",
            }.get(mt, f"{mt} executes or is user-facing")
        # An inactive consumer cannot start a business process. It still blocks
        # deletion, which the prerequisites capture, but it is not a live root.
        if entry and not a["is_active"]:
            entry, reason = False, f"{mt} is inactive"
        # Test code exercises components without any business process needing
        # them; treating tests as roots would mark test-only fields used.
        if entry and a["is_test"]:
            entry, reason = False, f"{mt} is test code"

        g.nodes[f"artifact:{a['id']}"] = Node(
            key=f"artifact:{a['id']}", kind="artifact",
            label=a["member_name"], ctype=mt, artifact_id=a["id"],
            is_entry_point=entry, entry_reason=reason,
        )

    # --- edges: artifact -> component ---------------------------------------
    for e in edges:
        src = f"artifact:{e['from_artifact_id']}"
        dst = f"component:{e['to_component_id']}"
        if src in g.nodes and dst in g.nodes:
            g.add_edge(src, dst, tier=e["tier"], match_kind=e["match_kind"])

    # --- edges: component -> its own artifact -------------------------------
    # An Apex class is both a component (something we may delete) and an
    # artifact (something that references other components). Linking the two is
    # what lets reachability flow THROUGH code: trigger -> class -> field.
    by_member: dict[tuple[str, str], int] = {}
    for a in arts:
        by_member[(a["metadata_type"], a["member_name"].lower())] = a["id"]
    for c in comps:
        if c["ctype"] in ("ApexClass", "ApexTrigger"):
            aid = by_member.get((c["ctype"], c["api_name"].lower()))
            if aid:
                g.add_edge(f"component:{c['id']}", f"artifact:{aid}",
                           kind="implements")
        elif c["ctype"] == "ApexMethod":
            # A method belongs to its class: reaching the class reaches its
            # methods, and an entry-point method makes its class reachable.
            cls = (c["parent_object"] or "").lower()
            aid = by_member.get(("ApexClass", cls))
            if aid:
                g.add_edge(f"artifact:{aid}", f"component:{c['id']}",
                           kind="declares")

    log.info("graph_built", nodes=len(g.nodes),
             edges=sum(len(v) for v in g.out.values()),
             entry_points=sum(1 for n in g.nodes.values() if n.is_entry_point))
    return g


def compute_reachability(g: Graph) -> dict[str, int]:
    """BFS from every entry point, recording how each node was reached."""
    q: deque[str] = deque()
    for key, n in g.nodes.items():
        if n.is_entry_point:
            n.reachable, n.distance, n.via = True, 0, None
            q.append(key)

    while q:
        cur = q.popleft()
        d = g.nodes[cur].distance or 0
        for nxt in g.out.get(cur, ()):
            node = g.nodes.get(nxt)
            if node and not node.reachable:
                node.reachable, node.distance, node.via = True, d + 1, cur
                q.append(nxt)

    stats = {
        "entry_points": sum(1 for n in g.nodes.values() if n.is_entry_point),
        "reachable": sum(1 for n in g.nodes.values() if n.reachable),
        "unreachable_components": sum(
            1 for n in g.nodes.values()
            if n.kind == "component" and n.in_scope and not n.reachable),
    }
    log.info("reachability_computed", **stats)
    return stats


async def persist_reachability(run_id: str, g: Graph) -> None:
    """Store reachability as evidence, with the path that justifies it."""
    import json

    evidence, gaps = [], []
    for n in g.nodes.values():
        if n.kind != "component" or not n.in_scope:
            continue
        if n.reachable:
            path = g.path_to_root(n.key)
            root = path[-1] if path else None
            evidence.append({
                "component_id": n.component_id,
                "result": "EVIDENCE_OF_USE", "tier": "A", "weight": 1.0,
                "payload": {
                    "reachable": True,
                    "hops_from_entry_point": n.distance,
                    "entry_point": (root or {}).get("label"),
                    "entry_point_type": (root or {}).get("type"),
                    "entry_reason": (root or {}).get("entry_reason"),
                    "path": [p["label"] for p in reversed(path)],
                },
            })
        else:
            evidence.append({
                "component_id": n.component_id,
                "result": "NO_EVIDENCE_FOUND", "tier": None, "weight": 0.0,
                "payload": {
                    "reachable": False,
                    "note": "not reachable from any trigger, flow, report, "
                            "UI component or externally-invocable Apex. Nothing "
                            "that runs or is seen depends on this.",
                    "inbound_edges": len(g.inc.get(n.key, ())),
                },
            })

    async with session_scope() as s:
        await s.execute(text("""
            INSERT INTO collector_run (run_id, collector_id, collector_family,
                scope_key, status, method, query_text, artifacts_searched, hits)
            VALUES (:r, 'C70_reachability', 'graph', '*', 'OK', :m, :m, :n, :h)
            ON CONFLICT (run_id, collector_id, scope_key) DO UPDATE SET
                hits = EXCLUDED.hits, artifacts_searched = EXCLUDED.artifacts_searched
        """), {"r": run_id,
               "m": "BFS from every entry point (triggers, active flows, reports, "
                    "UI components, externally-invocable Apex) across the "
                    "reference graph; presentation and permission metadata are "
                    "not roots",
               "n": len(g.nodes),
               "h": sum(1 for e in evidence if e["result"] == "EVIDENCE_OF_USE")})

        if evidence:
            await s.execute(text("""
                INSERT INTO evidence (run_id, component_id, collector_id, result,
                                      tier, weight, payload)
                VALUES (:r, :c, 'C70_reachability', CAST(:res AS collector_result),
                        CAST(:tier AS evidence_tier), :w, CAST(:p AS jsonb))
                ON CONFLICT (component_id, collector_id) DO UPDATE SET
                    result = EXCLUDED.result, tier = EXCLUDED.tier,
                    weight = EXCLUDED.weight, payload = EXCLUDED.payload
            """), [{"r": run_id, "c": e["component_id"], "res": e["result"],
                    "tier": e.get("tier"), "w": e["weight"],
                    "p": json.dumps(e["payload"], default=str)} for e in evidence])


def to_cytoscape(g: Graph, *, only_scope: bool = True, max_nodes: int = 600,
                 verdicts: dict[int, dict] | None = None) -> dict:
    """Serialise for the frontend.

    Capped deliberately: a full org graph is tens of thousands of nodes and a
    force layout of that is an unreadable hairball. Unreachable components and
    their neighbourhoods come first, since those are what the user is looking for.
    """
    # Ordering: unreachable components first (what the user is hunting), then
    # entry points, then everything else.
    #
    # Entry points are ranked high deliberately. Without them the view is a
    # field of disconnected dots: they are the anchors every path terminates at,
    # and a graph that shows consequences without causes is not worth drawing.
    verdicts = verdicts or {}
    ranked = sorted(
        g.nodes.values(),
        key=lambda n: (
            0 if (n.kind == "component" and n.in_scope and not n.reachable)
            else 1 if n.is_entry_point
            else 2,
            n.kind != "component",
            n.distance or 99,
        ),
    )
    keep: set[str] = set()
    for n in ranked:
        if only_scope and n.kind == "component" and not n.in_scope:
            continue
        keep.add(n.key)

        # Keep the DIRECT NEIGHBOURHOOD, not just the discovery path. An
        # unreachable node has no discovery path by definition, so following
        # `via` alone selected a set of isolated nodes with no edges between
        # them - a graph of 400 dots and nothing to look at. The neighbours are
        # the interesting part: they show what does still point at a component
        # that nothing live can reach.
        for nb in list(g.inc.get(n.key, ()))[:6]:
            keep.add(nb)
        for nb in list(g.out.get(n.key, ()))[:6]:
            keep.add(nb)

        # And the chain back to the entry point, so a path is never truncated.
        cur = n.via
        hops = 0
        while cur and hops < 6:
            keep.add(cur)
            cur = g.nodes[cur].via if cur in g.nodes else None
            hops += 1
        if len(keep) >= max_nodes:
            break

    nodes = [{
        "data": {
            "id": n.key, "label": n.label, "kind": n.kind, "type": n.ctype,
            "reachable": n.reachable, "entryPoint": n.is_entry_point,
            "entryReason": n.entry_reason, "distance": n.distance,
            "inScope": n.in_scope,
            # Carried so the graph can highlight by verdict, and explain one on
            # hover, without a second round trip per node.
            "verdict": (verdicts.get(n.component_id) or {}).get("label")
                       if n.component_id else None,
            "cls": verdicts.get(n.component_id) if n.component_id else None,
            "componentId": n.component_id,
        }
    } for k, n in g.nodes.items() if k in keep]

    edges = [{
        "data": {"id": f"{s}->{d}", "source": s, "target": d,
                 **g.edge_meta.get((s, d), {})}
    } for s, dsts in g.out.items() if s in keep
      for d in dsts if d in keep]

    return {"nodes": nodes, "edges": edges,
            "truncated": len(keep) < len(g.nodes),
            "total_nodes": len(g.nodes), "shown_nodes": len(nodes)}
