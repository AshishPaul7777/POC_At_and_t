"""The org as a file tree, with the analysis painted onto it.

Read-only, like the rest of `api/`. Two things here are worth knowing before
changing anything:

**The tree comes from the database, the bytes come from disk.** `artifact` rows
are run-scoped and immutable, so the shape of the tree is always the shape that
run saw. The workspace on disk is *not* run-scoped -- `stages.py` keys it by org
alias, so a later run of the same org overwrites it -- which is why every
response says whether the bytes on disk still belong to the run being viewed.

**A file's path is a whitelist, not a hint.** Nothing is served because it
resolves inside a directory; it is served because this run recorded an artifact
with that exact path. The workspace also holds `.sf/` CLI state and the
`probe/` and `rehearsal/` destructive-change packages, none of which should ever
reach a browser, and a containment check alone would happily serve them.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import text

from app.config import get_settings
from app.db.session import session_scope
from app.pipeline.apex_kind import apex_kinds_for_class_row
from app.pipeline.highlight import (
    build_allowed,
    line_index,
    locate,
    object_member_spans,
    to_segments,
)
from app.pipeline.narrate import _redact

router = APIRouter(prefix="/api", tags=["explorer"])

#: Serving a whole file is the point, but an unbounded read is still a foot-gun.
MAX_BYTES = 2_000_000
#: Above this, locating references costs more than the view is worth.
MAX_LOCATE_BYTES = 4_000_000

#: Finished runs are immutable, so a built tree stays valid. In-progress runs
#: are not -- caching mid-index produced an empty tree that stuck forever once
#: the run completed. Bounded, unlike the graph cache in routes.py.
_TREE_CACHE: dict[str, tuple[str, Any]] = {}
_TREE_CACHE_MAX = 8
_TERMINAL_STATES = frozenset({"SUCCEEDED", "DEGRADED", "FAILED", "CANCELLED"})

#: Which metadata type holds the definition of each component type, and which
#: column carries the file's member name. `parent_object` means something
#: different per ctype: for a field it is the object, for a method the class,
#: but for a TRIGGER it is TableEnumOrId -- the object the trigger fires on,
#: which is not the file the trigger lives in.
_COMP_KEY = """
    SELECT c.id, c.ctype::text AS ctype, c.api_name, c.parent_object,
           CASE c.ctype::text
             WHEN 'ApexClass'   THEN 'ApexClass'
             WHEN 'ApexTrigger' THEN 'ApexTrigger'
             WHEN 'ApexMethod'  THEN 'ApexClass'
             ELSE 'CustomObject'
           END AS md_type,
           lower(CASE WHEN c.ctype::text IN ('CustomField', 'ApexMethod')
                      THEN c.parent_object ELSE c.api_name END) AS member_lc,
           cl.label::text AS verdict, cl.confidence, cl.reason_codes,
           c.attrs
      FROM components c
      LEFT JOIN classifications cl ON cl.component_id = c.id
     WHERE c.run_id = :r
"""

_VERDICTS = ("USED", "UNUSED", "NEEDS_REVIEW", "OUT_OF_SCOPE", "UNCLASSIFIED")


def _norm(p: str | None) -> str:
    """MDAPI paths are stored with the OS separator. The API speaks one form."""
    return (p or "").replace("\\", "/").strip("/")


async def _run_context(run_id: str) -> dict:
    """Org alias for the run, and whether the workspace still belongs to it."""
    async with session_scope() as s:
        row = (await s.execute(text(
            "SELECT org_alias, started_at FROM runs WHERE id = CAST(:r AS uuid)"
        ), {"r": run_id})).mappings().first()
        if not row:
            raise HTTPException(404, "no such run")
        # Byte-size comparison would miss a rename of equal length. Whether a
        # LATER run of this alias exists is the signal that actually decides it.
        newer = (await s.execute(text("""
            SELECT count(*) FROM runs
             WHERE org_alias = :a AND started_at > :t
        """), {"a": row["org_alias"], "t": row["started_at"]})).scalar_one()

    alias = row["org_alias"]
    root = get_settings().workspace / str(alias) / "mdapi"
    return {
        "org_alias": alias,
        "root": root,
        "available": root.is_dir(),
        "matches_run": not newer,
        "reason": None if not newer else (
            f"{newer} later run(s) of {alias} have overwritten the retrieved "
            "files on disk, so file contents may not match this run's findings"),
    }


def _resolve_on_disk(root: Path, rel: str) -> Path | None:
    """Find `rel` under any retrieve chunk, deterministically."""
    if not root.is_dir():
        return None
    matches = sorted(root.glob(f"chunk-*/extracted/unpackaged/{rel}"))
    for p in matches:
        # Belt and braces: the whitelist has already vetted `rel`, but a
        # resolve() check costs nothing and catches a malformed stored path.
        try:
            if p.is_file() and p.resolve().is_relative_to(root.resolve()):
                return p
        except OSError:
            continue
    return None


@router.get("/runs/{run_id}/tree")
async def tree(run_id: str) -> dict:
    """Every retrieved file, with what the analysis concluded about it."""
    async with session_scope() as s:
        run_row = (await s.execute(text("""
            SELECT state::text AS state, finished_at
              FROM runs WHERE id = CAST(:r AS uuid)
        """), {"r": run_id})).mappings().first()
    if not run_row:
        raise HTTPException(404, "no such run")

    finished = (
        run_row["finished_at"] is not None
        or run_row["state"] in _TERMINAL_STATES
    )
    # Keyed by finished_at so a cache filled before classify cannot survive.
    cache_token = (
        run_row["finished_at"].isoformat()
        if run_row["finished_at"] is not None
        else ""
    )
    cached = _TREE_CACHE.get(run_id)
    if finished and cached and cached[0] == cache_token:
        return cached[1]

    ctx = await _run_context(run_id)

    async with session_scope() as s:
        arts = (await s.execute(text("""
            SELECT a.id, a.metadata_type, a.member_name, a.file_path,
                   a.is_active, a.is_test,
                   (a.attrs->>'bytes')::bigint AS bytes
              FROM artifact a
             WHERE a.run_id = :r AND a.file_path IS NOT NULL
        """), {"r": run_id})).mappings().all()

        mapped = (await s.execute(text(f"""
            WITH k AS ({_COMP_KEY})
            SELECT a.id AS artifact_id,
                   COALESCE(k.verdict, 'UNCLASSIFIED') AS verdict,
                   count(*) AS n
              FROM k
              JOIN artifact a
                ON a.run_id = :r AND a.metadata_type = k.md_type
               AND lower(a.member_name) = k.member_lc
             GROUP BY a.id, COALESCE(k.verdict, 'UNCLASSIFIED')
        """), {"r": run_id})).mappings().all()

        # The anti-join. A wildcard CustomObject retrieve returns custom objects
        # only, so Account, Case, Contact and Lead have no file -- and a third of
        # this org's custom fields live on them. A file-first view that simply
        # omitted them would read as "the org is clean" for exactly the
        # components most likely to be deletable.
        unmapped = (await s.execute(text(f"""
            WITH k AS ({_COMP_KEY})
            SELECT COALESCE(k.parent_object, k.ctype) AS grp, k.ctype,
                   COALESCE(k.verdict, 'UNCLASSIFIED') AS verdict, count(*) AS n
              FROM k
             WHERE NOT EXISTS (
                     SELECT 1 FROM artifact a
                      WHERE a.run_id = :r AND a.metadata_type = k.md_type
                        AND lower(a.member_name) = k.member_lc)
             GROUP BY 1, 2, 3
        """), {"r": run_id})).mappings().all()

        # Apex class roles for .cls file tags. Methods feed the classifier.
        apex_comp_rows = (await s.execute(text(f"""
            WITH k AS ({_COMP_KEY})
            SELECT a.id AS artifact_id, k.id, k.ctype, k.api_name, k.attrs
              FROM k
              JOIN artifact a
                ON a.run_id = :r
               AND a.metadata_type = 'ApexClass'
               AND lower(a.member_name) = k.member_lc
             WHERE k.ctype IN ('ApexClass', 'ApexMethod')
        """), {"r": run_id})).mappings().all()

    counts: dict[int, dict[str, int]] = {}
    for m in mapped:
        counts.setdefault(m["artifact_id"], {})[m["verdict"]] = int(m["n"])

    # artifact_id → apex_kinds for class files
    methods_by_art: dict[int, list[Any]] = {}
    class_by_art: dict[int, Any] = {}
    for row in apex_comp_rows:
        aid = int(row["artifact_id"])
        if row["ctype"] == "ApexClass":
            class_by_art[aid] = row["attrs"]
        else:
            methods_by_art.setdefault(aid, []).append(row["attrs"])
    apex_kinds_by_art = {
        aid: apex_kinds_for_class_row(attrs, methods_by_art.get(aid, []))
        for aid, attrs in class_by_art.items()
    }

    nodes: dict[str, dict] = {}

    def folder(path: str) -> dict:
        node = nodes.get(path)
        if node is None:
            parent = path.rsplit("/", 1)[0] if "/" in path else None
            node = nodes[path] = {
                "path": path, "name": path.rsplit("/", 1)[-1], "parent": parent,
                "kind": "folder", "metadata_type": None, "artifact_id": None,
                "bytes": None, "counts": {}, "components": 0,
                "is_active": True, "is_test": False, "apex_kinds": None,
            }
            if parent is not None:
                folder(parent)
        return node

    for a in arts:
        path = _norm(a["file_path"])
        if not path:
            continue
        parent = path.rsplit("/", 1)[0] if "/" in path else None
        if parent:
            folder(parent)
        c = counts.get(a["id"], {})
        nodes[path] = {
            "path": path, "name": path.rsplit("/", 1)[-1], "parent": parent,
            "kind": "file", "metadata_type": a["metadata_type"],
            "artifact_id": a["id"], "bytes": a["bytes"],
            "counts": c, "components": sum(c.values()),
            "is_active": a["is_active"], "is_test": a["is_test"],
            "apex_kinds": apex_kinds_by_art.get(a["id"]) or None,
        }

    # Roll counts up into folders. Depth is three, so a Python walk beats a
    # recursive CTE for both speed and readability.
    for node in list(nodes.values()):
        if node["kind"] != "file" or not node["counts"]:
            continue
        parent = node["parent"]
        while parent:
            p = nodes.get(parent)
            if not p:
                break
            for verdict, n in node["counts"].items():
                p["counts"][verdict] = p["counts"].get(verdict, 0) + n
            p["components"] += node["components"]
            parent = p["parent"]

    for node in nodes.values():
        node["dominant"] = _dominant(node["counts"])

    groups: dict[str, dict] = {}
    for u in unmapped:
        g = groups.setdefault(f"{u['grp']}", {
            "group": u["grp"], "ctype": u["ctype"], "components": 0, "counts": {}})
        g["counts"][u["verdict"]] = g["counts"].get(u["verdict"], 0) + int(u["n"])
        g["components"] += int(u["n"])

    payload = {
        "run_id": run_id,
        "org_alias": ctx["org_alias"],
        "workspace": {"available": ctx["available"],
                      "matches_run": ctx["matches_run"],
                      "reason": ctx["reason"]},
        "totals": {
            "files": sum(1 for n in nodes.values() if n["kind"] == "file"),
            "folders": sum(1 for n in nodes.values() if n["kind"] == "folder"),
            "unmapped_components": sum(g["components"] for g in groups.values()),
        },
        "nodes": sorted(nodes.values(), key=lambda n: (n["kind"] != "folder", n["path"])),
        "unmapped": sorted(groups.values(), key=lambda g: -g["components"]),
    }

    # Only cache completed runs. A tree built mid-retrieve / mid-index is a
    # partial snapshot; serving it after the run finishes is how the explorer
    # showed zero files against a run that had hundreds of artifacts.
    if finished and cache_token:
        if len(_TREE_CACHE) >= _TREE_CACHE_MAX:
            _TREE_CACHE.pop(next(iter(_TREE_CACHE)))
        _TREE_CACHE[run_id] = (cache_token, payload)
    else:
        _TREE_CACHE.pop(run_id, None)
    return payload


def _dominant(counts: dict[str, int]) -> str | None:
    """The verdict to colour a node by: the most numerous, ties broken by need.

    Deliberately not "the worst verdict present". One dead field would paint its
    object folder, which would paint the root, and every folder in the tree ends
    up the same colour at precisely the zoom level where it should carry the
    most information.
    """
    if not counts:
        return None
    return max(counts.items(), key=lambda kv: (kv[1], -_VERDICTS.index(kv[0])))[0]


@router.get("/runs/{run_id}/file")
async def file(run_id: str, path: str = Query(..., max_length=400)) -> dict:
    """One file's contents, with every recorded reference placed on a line.

    `path` is a query parameter rather than part of the route: a path parameter
    containing slashes needs the `:path` converter, and proxies normalise `%2F`
    before FastAPI ever sees it.
    """
    rel = _norm(path)
    if not rel or ".." in rel.split("/"):
        raise HTTPException(400, "bad path")

    ctx = await _run_context(run_id)

    async with session_scope() as s:
        art = (await s.execute(text("""
            SELECT id, metadata_type, member_name, is_active, is_test,
                   (attrs->>'bytes')::bigint AS bytes
              FROM artifact
             WHERE run_id = :r AND replace(file_path, '\\', '/') = :p
        """), {"r": run_id, "p": rel})).mappings().first()
    # The whitelist: this run recorded this exact path, or it is not served.
    if not art:
        raise HTTPException(404, "no such file in this run")

    disk = _resolve_on_disk(ctx["root"], rel)
    if disk is None:
        raise HTTPException(
            404, "the retrieved copy of this file is no longer on disk")

    raw = disk.read_bytes()[:MAX_BYTES + 1]
    truncated = len(raw) > MAX_BYTES
    # utf-8-sig, or a BOM shifts column 0 of line 1 by one character. Strip \r
    # once here so the server's line index and the browser's split('\n') agree.
    src = raw[:MAX_BYTES].decode("utf-8-sig", errors="replace").replace("\r\n", "\n")
    # Redact BEFORE locating: substitution changes length, so locating first
    # would return offsets into a string nobody is going to render.
    src = _redact(src)

    notes: list[str] = []
    if truncated:
        notes.append(f"shown up to {MAX_BYTES:,} bytes; the file is longer")
    if not ctx["matches_run"]:
        notes.append(ctx["reason"])

    suffix = Path(rel).suffix.lower()
    defines, spans_complete = await _defines(run_id, art, src, suffix)

    # Checked before locating, not after -- doing the work and then discarding
    # it would cost exactly as much as not having the guard.
    if len(src) > MAX_LOCATE_BYTES:
        refs, unlocated = [], []
        notes.append("file too large to place references on lines")
    else:
        refs, unlocated = await _references(run_id, art["id"], src, suffix, defines)
        if unlocated:
            placed = len({r["component_id"] for r in refs})
            notes.append(f"{len(unlocated)} of {placed + len(unlocated)} recorded"
                         " references could not be placed on a line")

    return {
        "path": rel,
        "artifact_id": art["id"],
        "metadata_type": art["metadata_type"],
        "source": "workspace",
        "stale": not ctx["matches_run"],
        "stale_reason": ctx["reason"],
        "bytes_indexed": art["bytes"],
        "bytes_now": disk.stat().st_size,
        "line_count": src.count("\n") + 1,
        "truncated": truncated,
        "spans_complete": spans_complete,
        "content": src,
        "defines": defines,
        "references": refs,
        "unlocated": unlocated,
        "notes": notes,
    }


async def _defines(run_id: str, art, src: str, suffix: str) -> tuple[list[dict], bool]:
    """The components this file defines, with line spans where we can prove them."""
    async with session_scope() as s:
        rows = (await s.execute(text(f"""
            WITH k AS ({_COMP_KEY})
            SELECT k.* FROM k
             WHERE k.md_type = :mt AND k.member_lc = lower(:mn)
        """), {"r": run_id, "mt": art["metadata_type"],
               "mn": art["member_name"]})).mappings().all()

    members, complete = ({}, True)
    if suffix == ".object":
        members, complete = object_member_spans(src)

    out: list[dict] = []
    class_attrs = None
    method_attrs: list[Any] = []
    for r in rows:
        start = end = None
        source = None
        if r["ctype"] == "CustomField":
            short = r["api_name"].split(".")[-1]
            span = members.get(f"fields:{short}")
            if span:
                start, end, source = span["start_line"], span["end_line"], "xml_element"
        elif r["ctype"] == "ApexMethod":
            # The SymbolTable gives a declaration line and no end, so highlight
            # the declaration rather than inventing a body extent.
            line = (r["attrs"] or {}).get("line")
            if isinstance(line, int):
                start, end, source = line - 1, line - 1, "symbol_table"
            method_attrs.append(r["attrs"])
        elif r["ctype"] in ("ApexClass", "ApexTrigger",
                            "CustomObject", "StandardObject"):
            # An .object file IS the object's definition, the same way a .cls
            # file is the class's. Leaving it unspanned made the object the one
            # component in its own file with no region.
            start, end, source = 0, src.count("\n"), "whole_file"
            if r["ctype"] == "ApexClass":
                class_attrs = r["attrs"]

        out.append({
            "component_id": r["id"], "ctype": r["ctype"], "api_name": r["api_name"],
            "verdict": r["verdict"], "confidence": r["confidence"],
            "reason_codes": r["reason_codes"],
            "start_line": start, "end_line": end, "span_source": source,
            "apex_kinds": None,
        })

    if class_attrs is not None:
        kinds = apex_kinds_for_class_row(class_attrs, method_attrs)
        for d in out:
            if d["ctype"] == "ApexClass":
                d["apex_kinds"] = kinds

    # Members of an .object that are not component types at all. Naming them
    # keeps a file that is 80% action overrides from reading as 80% unjudged.
    for key, span in members.items():
        if span["member"] == "fields":
            continue
        out.append({
            "component_id": None, "ctype": span["member"], "api_name": span["name"],
            "verdict": None, "confidence": None, "reason_codes": None,
            "start_line": span["start_line"], "end_line": span["end_line"],
            "span_source": "xml_element",
        })
    return out, complete


async def _references(run_id: str, artifact_id: int, src: str, suffix: str,
                      defines: list[dict]) -> tuple[list[dict], list[dict]]:
    """Place this artifact's recorded edges onto lines."""
    async with session_scope() as s:
        edges = (await s.execute(text("""
            SELECT DISTINCT to_component_id, match_kind, tier
              FROM reference_edges WHERE from_artifact_id = :a
        """), {"a": artifact_id})).mappings().all()
        if not edges:
            return [], []

        ids = sorted({int(e["to_component_id"]) for e in edges})
        alias_rows = (await s.execute(text("""
            SELECT component_id, alias_lc, alias_kind FROM alias
             WHERE run_id = :r AND component_id = ANY(:ids)
        """), {"r": run_id, "ids": ids})).all()
        meta = (await s.execute(text("""
            SELECT c.id, c.ctype::text AS ctype, c.api_name,
                   cl.label::text AS verdict, cl.confidence, cl.reason_codes
              FROM components c
              LEFT JOIN classifications cl ON cl.component_id = c.id
             WHERE c.id = ANY(:ids)
        """), {"ids": ids})).mappings().all()

    by_id = {int(m["id"]): dict(m) for m in meta}
    tier_of = {(int(e["to_component_id"]), e["match_kind"]): e["tier"] for e in edges}
    allowed = build_allowed([(int(c), a, k) for c, a, k in alias_rows])
    edge_kinds = set(tier_of)

    hits = locate(src, suffix, allowed, edge_kinds)
    segments = to_segments(src, line_index(src), hits)

    # A component's own declaration is not a reference to itself. Without this
    # every field's <fullName> line in an .object renders as "referenced here".
    own = {d["component_id"]: (d["start_line"], d["end_line"])
           for d in defines
           if d["component_id"] is not None and d["start_line"] is not None}

    for seg in segments:
        cid = seg["component_id"]
        span = own.get(cid)
        if span and span[0] <= seg["line"] <= (span[1] if span[1] is not None else span[0]):
            seg["region"] = "definition"
        info = by_id.get(cid, {})
        seg["api_name"] = info.get("api_name")
        seg["ctype"] = info.get("ctype")
        seg["verdict"] = info.get("verdict")
        seg["confidence"] = info.get("confidence")
        seg["reason_codes"] = info.get("reason_codes")
        seg["tier"] = tier_of.get((cid, seg["match_kind"]))

    # A component can be placed as the SECONDARY match on a shared position:
    # `Store__c` exists on two objects in this org, so the ambiguity guard puts
    # one of them in `also`. Counting only primaries reported those as unplaced,
    # which would show a "could not place" warning for something plainly on
    # screen -- the exact kind of false alarm that makes a caveat get ignored.
    placed = {s["component_id"] for s in segments}
    placed |= {cid for s in segments for cid in s["also"]}
    unlocated = [{
        "component_id": cid,
        "api_name": by_id.get(cid, {}).get("api_name"),
        "verdict": by_id.get(cid, {}).get("verdict"),
        "why": "recorded as referenced from this file, but the text could not be "
               "found in the bytes being shown",
    } for cid in ids if cid not in placed]

    return segments, unlocated


@router.get("/runs/{run_id}/components/{component_id}/location")
async def location(run_id: str, component_id: int) -> dict:
    """Where a component is defined, so other views can reveal it in the tree."""
    async with session_scope() as s:
        row = (await s.execute(text(f"""
            WITH k AS ({_COMP_KEY})
            SELECT k.id, k.ctype, k.api_name, k.attrs,
                   replace(a.file_path, '\\', '/') AS path
              FROM k
              JOIN artifact a
                ON a.run_id = :r AND a.metadata_type = k.md_type
               AND lower(a.member_name) = k.member_lc
             WHERE k.id = :c
        """), {"r": run_id, "c": component_id})).mappings().first()

    if not row:
        # Not an error: standard-object fields legitimately have no file.
        return {"path": None, "start_line": None, "end_line": None,
                "span_source": None,
                "why": "this component was not retrieved as a file -- standard "
                       "objects are not returned by a wildcard CustomObject "
                       "retrieve, so their fields have no file to open"}

    ctx = await _run_context(run_id)
    disk = _resolve_on_disk(ctx["root"], _norm(row["path"]))
    start = end = None
    source = None
    if disk is not None:
        src = disk.read_bytes()[:MAX_BYTES].decode("utf-8-sig", errors="replace")
        src = src.replace("\r\n", "\n")
        if row["ctype"] == "CustomField":
            members, _ = object_member_spans(src)
            span = members.get(f"fields:{row['api_name'].split('.')[-1]}")
            if span:
                start, end, source = span["start_line"], span["end_line"], "xml_element"
        elif row["ctype"] == "ApexMethod":
            line = (row["attrs"] or {}).get("line")
            if isinstance(line, int):
                start, end, source = line - 1, line - 1, "symbol_table"
        elif row["ctype"] in ("ApexClass", "ApexTrigger"):
            start, end, source = 0, src.count("\n"), "whole_file"

    return {"path": _norm(row["path"]), "start_line": start, "end_line": end,
            "span_source": source, "why": None}
