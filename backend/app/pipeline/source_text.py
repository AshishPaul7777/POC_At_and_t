"""Source-aware text extraction.

The naive approach — tokenise the whole file — treats a name in a comment as a
reference. That is not merely imprecise, it is wrong in the dangerous direction:
a commented-out block or a stale doc-comment produces a Tier-A binding edge and
keeps a genuinely dead field alive forever.

    /** Legacy_Code__c was removed in 2019 */     <- NOT a reference
    // sObj.put('Legacy_Code__c', v);              <- NOT a reference
    obj.Legacy_Code__c = v;                        <- a reference

So code is split into three streams before matching, and each carries a
different evidentiary weight:

    code       -> Tier A. Executable references.
    literals   -> Tier A. Catches dynamic access that no parser can resolve.
    comments   -> Tier C. Someone once cared about this name; that is a reason to
                  look, never proof of use.

This is not a parser. A real ANTLR parse would also resolve types and call
targets, and remains the right answer for method-level analysis. But stripping
comments and isolating literals removes the largest class of false positives for
a fraction of the cost.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# XML comments have no nesting and no string-literal complication.
_XML_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)
# CDATA holds markup or code that IS meaningful, so keep the contents.
_CDATA = re.compile(r"<!\[CDATA\[(.*?)\]\]>", re.DOTALL)


@dataclass
class Extracted:
    code: str = ""
    comments: str = ""
    literals: list[str] = field(default_factory=list)
    #: True when a construct was found that makes static analysis undecidable.
    dynamic: bool = False
    dynamic_hits: list[str] = field(default_factory=list)


DYNAMIC_MARKERS = (
    "getglobaldescribe", "database.query", "database.querywithbinds",
    "database.countquery", "search.query", "type.forname",
    "getpopulatedfieldsasmap", "json.deserializeuntyped", "getsobjecttype",
    "fieldset.getfields", "sobjecttype.getdescribe", "schema.describesobjects",
    ".getdescribe(", "callable", "metadata.operations",
)


def extract_apex(src: str) -> Extracted:
    """Split Apex/JS into code, comments and string literals.

    Hand-written scanner rather than regexes, because the three constructs are
    mutually recursive in a way regexes get wrong: a `//` inside a string is not
    a comment, and a quote inside a comment does not open a string. Getting that
    backwards is exactly how commented-out code starts counting as live.
    """
    out = Extracted()
    code: list[str] = []
    comments: list[str] = []
    lit: list[str] = []

    i, n = 0, len(src)
    state = "code"  # code | line_comment | block_comment | s_quote | d_quote
    buf: list[str] = []

    while i < n:
        c = src[i]
        nxt = src[i + 1] if i + 1 < n else ""

        if state == "code":
            if c == "/" and nxt == "/":
                state, i = "line_comment", i + 2
                continue
            if c == "/" and nxt == "*":
                state, i = "block_comment", i + 2
                continue
            if c == "'":
                state, buf, i = "s_quote", [], i + 1
                continue
            if c == '"':
                state, buf, i = "d_quote", [], i + 1
                continue
            code.append(c)
            i += 1
            continue

        if state == "line_comment":
            if c == "\n":
                comments.append("\n")
                state = "code"
            else:
                comments.append(c)
            i += 1
            continue

        if state == "block_comment":
            if c == "*" and nxt == "/":
                comments.append(" ")
                state, i = "code", i + 2
                continue
            comments.append(c)
            i += 1
            continue

        # inside a string literal
        quote = "'" if state == "s_quote" else '"'
        if c == "\\" and nxt:
            buf.append(nxt)
            i += 2
            continue
        if c == quote:
            value = "".join(buf)
            if len(value) >= 2:
                lit.append(value)
            # Replace the literal with a space so adjacent identifiers do not
            # accidentally fuse into a new token.
            code.append(" ")
            state, i = "code", i + 1
            continue
        buf.append(c)
        i += 1

    out.code = "".join(code)
    out.comments = "".join(comments)
    out.literals = lit

    low = out.code.lower()
    out.dynamic_hits = [m for m in DYNAMIC_MARKERS if m in low]
    out.dynamic = bool(out.dynamic_hits)
    return out


def extract_xml(src: str) -> Extracted:
    """Strip XML comments; keep CDATA contents, which are real content."""
    out = Extracted()
    comments = " ".join(m.group(0) for m in _XML_COMMENT.finditer(src))
    body = _XML_COMMENT.sub(" ", src)
    body = _CDATA.sub(lambda m: " " + m.group(1) + " ", body)
    out.code = body
    out.comments = comments
    low = body.lower()
    out.dynamic_hits = [m for m in DYNAMIC_MARKERS if m in low]
    out.dynamic = bool(out.dynamic_hits)
    return out


def extract(path_suffix: str, src: str) -> Extracted:
    if path_suffix in (".cls", ".trigger", ".js", ".ts"):
        return extract_apex(src)
    return extract_xml(src)
