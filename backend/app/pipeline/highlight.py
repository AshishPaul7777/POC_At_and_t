"""Where, in a file, the references we already recorded actually are.

The static indexer resolves *which* component a file references but throws the
positions away: `reference_edges.locator` and `.snippet` are declared in the
schema and never written, and `_resolve()` works from `{token: count}` maps that
have no offsets in them by the time an edge is built. So a code view cannot ask
the database "which line is this on" -- there is no such column.

This module answers that question at read time instead, and it answers it by
*locating edges that already exist* rather than by matching afresh:

    1. load the edges recorded from this one artifact
    2. restrict the alias table to just those components
    3. find the positions of those aliases in the file
    4. keep a position only if an edge with that (component, match_kind) exists

Step 4 is the whole design. Every guard that makes the analysis trustworthy --
the distinctiveness guard that stopped `Active__c` matching the English word
"active", the ambiguity guard for same-named fields on different objects, the
tier downgrades for tests and inactive consumers -- lives in `indexer._resolve`.
Re-deriving them here would mean two implementations of the rule that decides
whether a component is dead, drifting apart silently. Inheriting them means a
suppressed alias produced no edge, so it can produce no highlight.

A highlighted line reads as more authoritative than a table row, so anything
this module cannot place is reported as unplaced rather than dropped.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.pipeline.aliases import is_sweepable
from app.pipeline.indexer import (
    CODE_SUFFIXES,
    NON_REFERENCE_KINDS,
    _MERGE_RE,
    _REPORT_COL_RE,
    _TOKEN_RE,
)
from app.pipeline.source_text import extract

#: Metadata namespace on every MDAPI file.
_NS = "{http://soap.sforce.com/2006/04/metadata}"

#: Child elements of an object file that carry their own `fullName`. Only
#: `fields` maps to a component we classify; the rest are located so the UI can
#: mark them "not analysed" rather than leaving them looking unjudged-but-clean.
_OBJECT_MEMBERS = ("fields", "validationRules", "listViews", "recordTypes",
                   "webLinks", "compactLayouts", "fieldSets")


@dataclass
class Hit:
    """One placed reference, as a character range over the file as served."""
    start: int
    end: int
    component_id: int
    match_kind: str
    alias_kind: str
    #: code | markup | comment | literal, or `definition` once reclassified.
    region: str
    #: Other components whose alias matched the same characters.
    also: list[int] = field(default_factory=list)


def build_allowed(
    alias_rows: list[tuple[int, str, str]],
) -> dict[str, list[tuple[int, str]]]:
    """Alias rows -> lookup table, with the non-reference kinds removed.

    Lives here rather than in the SQL so the exclusion cannot be forgotten by a
    new caller. `stripped_suffix` and `label` are forms a reference is never
    written in: `Active__c` with its suffix removed is `Active`, which Salesforce
    never uses to mean that field, and matching it lit up every English "active"
    in the org.
    """
    out: dict[str, list[tuple[int, str]]] = {}
    for component_id, alias_lc, alias_kind in alias_rows:
        if alias_kind in NON_REFERENCE_KINDS:
            continue
        out.setdefault(alias_lc, []).append((component_id, alias_kind))
    return out


def _tokens_in(src: str, lo: int, hi: int):
    """Yield (start, end, lowercased) for every identifier in a range.

    Mirrors `indexer._tokenise`, including the report-column form: report XML
    joins object and field with `$`, and the plain tokeniser splits that into
    two names, losing the qualified one.
    """
    window = src[lo:hi]
    for m in _TOKEN_RE.finditer(window):
        yield lo + m.start(), lo + m.end(), m.group(0).lower()
    for m in _REPORT_COL_RE.finditer(window):
        yield lo + m.start(1), lo + m.end(1), m.group(1).lower()


def locate(
    src: str,
    suffix: str,
    allowed: dict[str, list[tuple[int, str]]],
    edge_kinds: set[tuple[int, str]],
) -> list[Hit]:
    """Find the recorded references inside `src`.

    `src` must be the exact string the client will render -- redacted, newline
    normalised, BOM stripped -- because every offset returned indexes into it.

    `allowed` maps a lowercased alias to (component_id, alias_kind), already
    restricted to components with an edge from this artifact and already free of
    `NON_REFERENCE_KINDS`. That exclusion is load-bearing rather than defensive:
    an edge records only the one alias_kind that won on tier, so a component
    whose edge was won by its api_name would otherwise also light up wherever
    its *label* appears -- which is the bug the exact-match rule exists to stop.
    """
    ex = extract(suffix, src)
    is_code = suffix in CODE_SUFFIXES
    hits: list[Hit] = []

    def take(start: int, end: int, alias: str, match_kind: str, region: str) -> None:
        for component_id, alias_kind in allowed.get(alias, ()):
            if (component_id, match_kind) not in edge_kinds:
                continue
            hits.append(Hit(start, end, component_id, match_kind, alias_kind, region))

    for lo, hi, kind in ex.spans:
        if kind in ("code", "markup"):
            for s, e, tok in _tokens_in(src, lo, hi):
                # The distinctiveness guard, applied exactly where the indexer
                # applies it: a bare word is refused for a token sweep over
                # markup, where element names and enum values are ordinary
                # English, and left alone in code.
                if not is_code and not is_sweepable(tok):
                    continue
                take(s, e, tok, "token_sweep", kind)

            # Merge fields are read off the code stream by the indexer, so they
            # are searched here in code and markup but never inside literals.
            for m in _MERGE_RE.finditer(src[lo:hi]):
                expr = m.group(1).strip()
                take(lo + m.start(), lo + m.end(), expr.lower(), "merge_field", kind)
                for seg in re.split(r"[.\s(),]+", expr):
                    if len(seg) > 1:
                        take(lo + m.start(), lo + m.end(), seg.lower(),
                             "merge_field", kind)

        elif kind == "literal":
            # The span carries its quotes; the recorded literal does not.
            inner_lo, inner_hi = lo + 1, max(lo + 1, hi - 1)
            value = src[inner_lo:inner_hi]
            take(inner_lo, inner_hi, value.strip().lower(), "string_literal", kind)
            # A dynamic SOQL string is itself a container of references, so the
            # indexer tokenises inside it. Do the same, so the field name inside
            # 'SELECT Legacy_Code__c FROM ...' is highlighted, not the whole query.
            if " " in value:
                for s, e, tok in _tokens_in(src, inner_lo, inner_hi):
                    take(s, e, tok, "string_literal", kind)

        elif kind == "comment":
            for s, e, tok in _tokens_in(src, lo, hi):
                # Guarded unconditionally, in every file type. "Retrieves active
                # promoted records" in a docblock is prose, not a reference.
                if not is_sweepable(tok):
                    continue
                take(s, e, tok, "comment_mention", kind)

    return _dedupe(hits)


def _dedupe(hits: list[Hit]) -> list[Hit]:
    """Collapse to non-overlapping ranges, keeping the losers as `also`.

    `Customer_Order__c$Legacy_Code__c` from the report-column form overlaps two
    plain token matches for two different components. Emitting all three would
    force the renderer to nest spans inside spans, which it would get wrong;
    emitting only the longest would silently lose a component from the tooltip.
    """
    if not hits:
        return []
    # Longest wins at a given start, so the qualified form beats its own parts.
    hits.sort(key=lambda h: (h.start, -(h.end - h.start), h.component_id))
    out: list[Hit] = []
    for h in hits:
        if out and h.start < out[-1].end:
            prev = out[-1]
            if h.component_id != prev.component_id and h.component_id not in prev.also:
                prev.also.append(h.component_id)
            continue
        out.append(h)
    return out


def line_index(src: str) -> list[int]:
    """Start offset of every line. One pass, reused for every hit."""
    starts = [0]
    for i, ch in enumerate(src):
        if ch == "\n":
            starts.append(i + 1)
    return starts


def _line_of(starts: list[int], pos: int) -> int:
    lo, hi = 0, len(starts) - 1
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if starts[mid] <= pos:
            lo = mid
        else:
            hi = mid - 1
    return lo


def to_segments(src: str, starts: list[int], hits: list[Hit]) -> list[dict]:
    """Per-line segments, so the client never reasons about character offsets.

    A merge field can span lines -- `_MERGE_RE`'s `[^}]` matches newlines -- and
    so can an Apex string literal. Splitting here means the renderer only ever
    sees "on line N, from column A to column B".
    """
    out: list[dict] = []
    for h in hits:
        first, last = _line_of(starts, h.start), _line_of(starts, max(h.start, h.end - 1))
        for ln in range(first, last + 1):
            seg_lo = max(h.start, starts[ln])
            seg_hi = min(h.end, starts[ln + 1] if ln + 1 < len(starts) else len(src))
            if seg_hi <= seg_lo:
                continue
            out.append({
                "line": ln,
                "col": seg_lo - starts[ln],
                "end_col": seg_hi - starts[ln],
                "text": src[seg_lo:seg_hi],
                "component_id": h.component_id,
                "match_kind": h.match_kind,
                "alias_kind": h.alias_kind,
                "region": h.region,
                "also": h.also,
            })
    return out


def object_member_spans(src: str) -> tuple[dict[str, dict], bool]:
    """Line spans of every named member inside an MDAPI `.object` file.

    One `.object` holds every field of the sObject inline, so "which lines are
    this field" is a real question with a real answer -- and regexing it would
    be wrong, because a picklist value inside `<valueSet>` also has a
    `<fullName>`. `lxml` is already a declared dependency and its elements carry
    `.sourceline`, so the containing element is asked directly.

    Returns `(members, complete)`. `complete` is False when the file could not be
    parsed at all; a caller must not synthesise spans for what is missing.
    """
    try:
        from lxml import etree
    except ImportError:  # pragma: no cover - lxml is a declared dependency
        return {}, False

    try:
        root = etree.fromstring(src.encode("utf-8"),
                                etree.XMLParser(recover=True, huge_tree=True))
    except Exception:
        return {}, False
    if root is None:
        return {}, False

    lines = src.split("\n")
    members: dict[str, dict] = {}
    for tag in _OBJECT_MEMBERS:
        for el in root.iter(_NS + tag):
            name = el.findtext(_NS + "fullName")
            if not name or el.sourceline is None:
                continue
            start = el.sourceline - 1                     # to 0-based
            end = max((d.sourceline or 0) for d in el.iter()) - 1
            # lxml reports where a start tag ends, never where an element does,
            # so walk forward to the closing tag rather than guessing.
            close = f"</{tag}>"
            while end < len(lines) and close not in lines[end]:
                end += 1
            members.setdefault(f"{tag}:{name}", {
                "member": tag, "name": name,
                "start_line": start, "end_line": min(end, len(lines) - 1),
            })
    return members, True
