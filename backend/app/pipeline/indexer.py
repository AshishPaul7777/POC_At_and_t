"""Stage S20 - index retrieved metadata and resolve references.

Turns the retrieved file tree into searchable rows, then matches those rows
against the alias table to produce reference edges. This is where evidence
starts existing, and it costs zero API calls.

Three extraction layers run over every file and their results are UNIONED. A
parse failure in one must never delete a reference another found:

  L1  structural   parse the format properly (XML elements, Apex constructs).
                   Precise locators. Tier A.
  L2  literals     every string literal and text node, matched against aliases.
                   This is the highest-value guard against false UNUSED: it
                   catches dynamic access like sObj.get('Legacy_Code__c'),
                   Database.query('SELECT ...') and Aura's component.get("c.x")
                   with no semantic understanding at all. Tier A on exact match.
  L3  tokens       word-boundary tokens over the whole file. Broad recall.
                   Tier A for exact alias hits, Tier C for weak/label matches.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path

import structlog
from sqlalchemy import text

from app.pipeline.aliases import is_sweepable

from app.db.session import session_scope
from app.pipeline.source_text import DYNAMIC_MARKERS, extract  # noqa: F401

log = structlog.get_logger()

#: Retrieved folder name -> metadata type. Anything unmapped is still indexed
#: (under the folder name) rather than skipped: an unindexed file is a blind
#: spot, and blind spots become false UNUSED verdicts.
FOLDER_TO_TYPE: dict[str, str] = {
    "classes": "ApexClass", "triggers": "ApexTrigger", "pages": "ApexPage",
    "components": "ApexComponent", "lwc": "LightningComponentBundle",
    "aura": "AuraDefinitionBundle", "staticresources": "StaticResource",
    "objects": "CustomObject", "fields": "CustomField",
    "layouts": "Layout", "flexipages": "FlexiPage", "quickActions": "QuickAction",
    "tabs": "CustomTab", "applications": "CustomApplication",
    "homePageLayouts": "HomePageLayout", "labels": "CustomLabels",
    "flows": "Flow", "flowDefinitions": "FlowDefinition",
    "workflows": "Workflow", "approvalProcesses": "ApprovalProcess",
    "assignmentRules": "AssignmentRules", "escalationRules": "EscalationRules",
    "autoResponseRules": "AutoResponseRules", "sharingRules": "SharingRules",
    "duplicateRules": "DuplicateRule", "matchingRules": "MatchingRule",
    "reports": "Report", "dashboards": "Dashboard", "reportTypes": "ReportType",
    "email": "EmailTemplate", "letterhead": "Letterhead",
    "profiles": "Profile", "permissionsets": "PermissionSet",
    "permissionsetgroups": "PermissionSetGroup",
    "customPermissions": "CustomPermission",
    "notificationtypes": "CustomNotificationType",
    "remoteSiteSettings": "RemoteSiteSetting",
    "namedCredentials": "NamedCredential", "pathAssistants": "PathAssistant",
    "customMetadata": "CustomMetadata", "globalValueSets": "GlobalValueSet",
}

#: Files whose content is meaningful to search. Binary/static assets are
#: recorded as artifacts but not tokenised.
TEXTUAL_SUFFIXES = {
    ".xml", ".cls", ".trigger", ".page", ".component", ".cmp", ".app", ".evt",
    ".js", ".html", ".css",
    ".object", ".layout", ".flow", ".workflow", ".report", ".dashboard",
    ".reporttype", ".email", ".profile", ".permissionset", ".permissionsetgroup",
    ".app", ".flexipage", ".quickaction", ".tab", ".labels", ".sharingrules",
    ".assignmentrules", ".escalationrules", ".autoresponserules",
    ".duplicaterule", ".matchingrule", ".approvalprocess", ".weblink",
    ".customPermission", ".notiftype", ".remoteSite", ".pathAssistant", ".md",
}

#: Files whose tokens are program identifiers rather than English.
#:
#: The distinction drives where the bare-word guard applies. `start` in an Apex
#: class is the Batchable method; `Active` inside <status>Active</status> in a
#: Flow is an enum value that happens to spell a field name. Both are single
#: bare words, and only one is a reference -- so the guard is applied by
#: context, not by the shape of the alias alone.
#: Deliberately excludes .html and .css. An LWC template's `<strong>Active:</strong>`
#: is a display label and `.size-btn-active` is a CSS class -- both matched
#: Account.Active__c as a bare token. A template that really does reference the
#: field writes `{record.Active__c}`, which is suffixed and unaffected.
#: Alias kinds that are not API names, and so can never be a static reference.
#:
#: Salesforce API names are exact. A reference to a custom field is written
#: `Active__c`, `Account.Active__c` or `Account$Active__c` -- never `Active`,
#: and never the field's display label. Matching those looser forms produced
#: pure noise measured against a real org: `Store_Location__c.State__c` was
#: credited with `state` in a CartCheckoutSession layout, `Opportunity
#: .OrderNumber__c` with `ordernumber` in an Order layout, and
#: `Account.Active__c` with `<active>true</active>` in an assignment rule --
#: every one a different object's field that happens to share a word.
#:
#: They stay in the alias table: a reviewer asking "did you consider the label?"
#: deserves to see that the answer is yes, and that it was deliberately not
#: treated as evidence.
NON_REFERENCE_KINDS = frozenset({"stripped_suffix", "label"})

CODE_SUFFIXES = {".cls", ".trigger", ".js", ".ts", ".page", ".component", ".cmp", ".app"}

# Identifiers, including the __c / __r / __mdt suffixes that matter here.
_TOKEN_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*(?:__[cerbx]|__mdt)?", re.ASCII)
# Apex/JS string literals.
_LITERAL_RE = re.compile(r"'([^'\n\\]{2,120})'|\"([^\"\n\\]{2,120})\"")
# Visualforce / email-template merge fields: {!Account.Legacy_Code__c}
_MERGE_RE = re.compile(r"\{!\s*([^}]{2,200})\}")
# Report and Custom Report Type column refs: Customer_Order__c$Legacy_Code__c
_REPORT_COL_RE = re.compile(r"([A-Za-z0-9_]+\$[A-Za-z0-9_]+)")

# UI cross-references that the plain tokeniser cannot keep together.
_LWC_IMPORT_RE = re.compile(
    r"""from\s+['"]c/([A-Za-z]\w*)['"]""", re.I)
_LWC_NS_IMPORT_RE = re.compile(
    r"""from\s+['"]c/([\w]+)/([A-Za-z]\w*)['"]""", re.I)
_AURA_TAG_RE = re.compile(r"<(?:c|[\w]+):([A-Za-z]\w*)\b")
_AURA_DEP_RE = re.compile(
    r"""resource\s*=\s*['"](?:markup://)?(?:c|[\w]+):([A-Za-z]\w*)['"]""",
    re.I)
_COMPONENT_NAME_RE = re.compile(
    r"<componentName>\s*((?:[\w]+[:/])?[A-Za-z]\w*)\s*</componentName>",
    re.I)
_LWC_ELEMENT_RE = re.compile(r"<(c-[a-z][a-z0-9]*(?:-[a-z0-9]+)*)\b")
_TAB_LWC_RE = re.compile(
    r"<lwcComponent>\s*([A-Za-z]\w*)\s*</lwcComponent>", re.I)
_TAB_AURA_RE = re.compile(
    r"<auraComponent>\s*((?:[\w]+[:/])?[A-Za-z]\w*)\s*</auraComponent>",
    re.I)

#: Target component types for which App/Tab placement is Tier-A use (not weak).
UI_COMPONENT_TYPES = frozenset({
    "LightningComponentBundle", "AuraDefinitionBundle",
})

#: App/Tab are weak for fields (navigation), but placement for UI bundles.
UI_PLACEMENT_CONSUMER_TYPES = frozenset({
    "CustomApplication", "CustomTab",
})

# DYNAMIC_MARKERS now lives in source_text, where it is applied to code with
# comments already stripped — a commented-out Database.query() should not taint
# a whole object's fields.


@dataclass
class IndexStats:
    artifacts: int = 0
    tokens: int = 0
    literals: int = 0
    edges: int = 0
    dynamic_files: list[str] = field(default_factory=list)
    unmapped_folders: set[str] = field(default_factory=set)
    unreadable: list[str] = field(default_factory=list)
    by_type: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    #: Apex meta files whose content was deliberately not scanned.
    skipped_apex_meta: int = 0
    #: Bare-word alias hits refused by the distinctiveness guard, by alias.
    #: Counted rather than discarded silently: if this ever gets large for a
    #: name that turns out to be real, the stop list is what needs changing.
    suppressed_bare: dict[str, int] = field(default_factory=lambda: defaultdict(int))


def _metadata_type(rel: Path) -> str:
    for part in rel.parts:
        if part in FOLDER_TO_TYPE:
            return FOLDER_TO_TYPE[part]
    # Unmapped: keep the folder name so it is visible rather than silently lost.
    return rel.parts[0] if rel.parts else "Unknown"


def _is_test_artifact(path: Path, content: str) -> bool:
    """References from tests are Tier C, never Tier A.

    Without this discriminator every field a test class touches looks USED, and
    the whole point of a test-only field is that it has no live consumer.
    """
    if path.suffix.lower() != ".cls":
        return False
    stem = path.stem.lower()
    if stem.endswith("test") or stem.startswith("test"):
        return True
    return "@istest" in content[:4000].lower()


#: Custom Metadata / Custom Settings gating fields follow no platform schema —
#: teams name their own enable/active flag. A record whose only such field is
#: false is a disabled feature flag: it names a component without proving any
#: business process currently reaches it through that record.
_CMDT_GATE_FIELD_RE = re.compile(
    r"<field>(\w*(?:is_?enabled|is_?active|active|enabled)\w*)</field>\s*"
    r"<value[^>]*type=\"xsd:boolean\">false</value>",
    re.IGNORECASE,
)


def _is_active(mdtype: str, content: str, abs_path: Path | None = None) -> bool:
    """Is the consumer live?

    A reference from an inactive Flow, deactivated rule, or Inactive Apex class
    cannot prove current use. Such consumers are marked inactive so C10 only
    counts active artifacts.
    """
    low = content.lower()
    if mdtype in ("Flow", "FlowDefinition"):
        if "<status>obsolete</status>" in low or "<status>draft</status>" in low:
            return False
    if mdtype in ("Workflow", "ValidationRule", "ApprovalProcess", "DuplicateRule"):
        if "<active>false</active>" in low:
            return False
    if mdtype == "CustomMetadata" and _CMDT_GATE_FIELD_RE.search(content):
        return False
    if mdtype in ("ApexClass", "ApexTrigger") and abs_path is not None:
        meta = Path(str(abs_path) + "-meta.xml")
        if meta.is_file():
            try:
                meta_low = meta.read_text(encoding="utf-8", errors="replace").lower()
            except OSError:
                meta_low = ""
            if "<status>inactive</status>" in meta_low:
                return False
    return True


def _is_apex_meta(rel: Path) -> bool:
    n = rel.name.lower()
    return n.endswith(".cls-meta.xml") or n.endswith(".trigger-meta.xml")


def _member_name(rel: Path, mdtype: str) -> str:
    """Artifact member key. LWC/Aura folders share one member across files."""
    if mdtype in UI_COMPONENT_TYPES and len(rel.parts) >= 2:
        return rel.parts[1]
    return rel.stem


def _ui_reference_literals(content: str) -> dict[str, str]:
    """Exact UI reference forms the plain tokeniser would split apart."""
    out: dict[str, str] = {}
    for m in _LWC_IMPORT_RE.finditer(content):
        name = m.group(1)
        out.setdefault(f"c/{name}".lower(), "lwc_import")
        out.setdefault(name.lower(), "lwc_import")
    for m in _LWC_NS_IMPORT_RE.finditer(content):
        ns, name = m.group(1), m.group(2)
        out.setdefault(f"{ns}/{name}".lower(), "lwc_ns_import")
        out.setdefault(f"c/{name}".lower(), "lwc_ns_import")
        out.setdefault(name.lower(), "lwc_ns_import")
    for m in _AURA_TAG_RE.finditer(content):
        name = m.group(1)
        out.setdefault(f"c:{name}".lower(), "aura_tag")
        out.setdefault(name.lower(), "aura_tag")
    for m in _AURA_DEP_RE.finditer(content):
        name = m.group(1)
        out.setdefault(f"c:{name}".lower(), "aura_dependency")
        out.setdefault(name.lower(), "aura_dependency")
    for m in _COMPONENT_NAME_RE.finditer(content):
        raw = m.group(1).strip()
        out.setdefault(raw.lower(), "flexipage_component")
        if ":" in raw:
            out.setdefault(raw.split(":", 1)[-1].lower(), "flexipage_component")
        if "/" in raw:
            out.setdefault(raw.split("/", 1)[-1].lower(), "flexipage_component")
    for m in _LWC_ELEMENT_RE.finditer(content):
        out.setdefault(m.group(1).lower(), "lwc_element")
    for m in _TAB_LWC_RE.finditer(content):
        name = m.group(1)
        out.setdefault(name.lower(), "tab_lwc")
        out.setdefault(f"c/{name}".lower(), "tab_lwc")
    for m in _TAB_AURA_RE.finditer(content):
        raw = m.group(1).strip()
        out.setdefault(raw.lower(), "tab_aura")
        if ":" in raw:
            out.setdefault(raw.split(":", 1)[-1].lower(), "tab_aura")
    return out


def scan_workspace(root: Path) -> list[tuple[Path, Path]]:
    """Yield (absolute, relative-to-unpackaged) for every retrieved file."""
    out: list[tuple[Path, Path]] = []
    for base in root.rglob("unpackaged"):
        if not base.is_dir():
            continue
        for p in base.rglob("*"):
            if p.is_file():
                out.append((p, p.relative_to(base)))
    return out


async def index_workspace(run_id: str, workspace: Path) -> IndexStats:
    stats = IndexStats()
    files = scan_workspace(workspace)
    if not files:
        log.warning("index_no_files", workspace=str(workspace))
        return stats

    aliases = await _load_aliases(run_id)
    parents = await _load_parents(run_id)
    ctypes = await _load_component_types(run_id)
    ambiguous = sum(1 for v in aliases.values() if len({c for c, _ in v}) > 1)
    log.info("index_start", files=len(files), aliases=len(aliases),
             ambiguous_aliases=ambiguous)

    async with session_scope() as s:
        for abs_path, rel in files:
            mdtype = _metadata_type(rel)
            if mdtype not in FOLDER_TO_TYPE.values():
                stats.unmapped_folders.add(rel.parts[0] if rel.parts else "?")

            content = ""
            if abs_path.suffix.lower() in TEXTUAL_SUFFIXES:
                try:
                    content = abs_path.read_text(encoding="utf-8", errors="replace")
                except OSError as e:
                    stats.unreadable.append(f"{rel}: {e}")

            is_test = _is_test_artifact(abs_path, content)
            active = _is_active(mdtype, content, abs_path)
            member = _member_name(rel, mdtype)

            artifact_id = await _insert_artifact(
                s, run_id, mdtype, member, str(rel), is_test=is_test, active=active,
                size=abs_path.stat().st_size)
            stats.artifacts += 1
            stats.by_type[mdtype] += 1

            if not content:
                continue

            # Inactive Apex / Flow / rules are inventoried but not scanned for
            # outbound references — static-ref evidence only comes from active
            # consumers.
            if not active and mdtype in (
                "ApexClass", "ApexTrigger", "Flow", "FlowDefinition",
                "Workflow", "ValidationRule", "ApprovalProcess", "DuplicateRule",
            ):
                continue

            # Comments are separated BEFORE tokenising. Counting a name in a
            # comment as a reference keeps genuinely dead components alive
            # forever — a stale doc-comment or a commented-out block is not use.
            # An Apex class or trigger meta file holds apiVersion and status
            # and nothing else -- `<status>Active</status>` is the entire
            # payload. It cannot carry a reference, so scanning it can only
            # produce false ones. LWC and email meta files are NOT skipped:
            # those carry targets, targetConfigs and object bindings that are
            # genuine references.
            if _is_apex_meta(rel):
                stats.skipped_apex_meta += 1
                continue

            ex = extract(abs_path.suffix.lower(), content)
            if ex.dynamic:
                stats.dynamic_files.append(str(rel))

            tokens = _tokenise(ex.code)
            comment_tokens = _tokenise(ex.comments) if ex.comments else {}
            if tokens:
                await _insert_tokens(s, run_id, artifact_id, tokens)
                stats.tokens += len(tokens)

            literals = _literals_from(ex)
            literals.update(_ui_reference_literals(ex.code))
            if literals:
                await _insert_literals(s, run_id, artifact_id, literals)
                stats.literals += len(literals)

            edges, suppressed = _resolve(aliases, tokens, literals, mdtype,
                                         is_test, active, parents,
                                         comment_tokens,
                                         abs_path.suffix.lower(),
                                         ctypes)
            for alias_lc, n in suppressed.items():
                stats.suppressed_bare[alias_lc] += n
            if edges:
                await _insert_edges(s, run_id, artifact_id, edges,
                                    consumer_active=active, consumer_is_test=is_test)
                stats.edges += len(edges)

    # The most-suppressed names, so a wrong stop-list entry is visible rather
    # than being a silent loss of recall.
    top_suppressed = sorted(stats.suppressed_bare.items(),
                            key=lambda kv: -kv[1])[:10]
    log.info("index_complete", artifacts=stats.artifacts, tokens=stats.tokens,
             literals=stats.literals, edges=stats.edges,
             dynamic_files=len(stats.dynamic_files),
             skipped_apex_meta=stats.skipped_apex_meta,
             suppressed_bare_hits=sum(stats.suppressed_bare.values()),
             top_suppressed=top_suppressed)
    return stats


def _tokenise(content: str) -> dict[str, int]:
    counts: dict[str, int] = defaultdict(int)
    for m in _TOKEN_RE.finditer(content):
        counts[m.group(0).lower()] += 1
    # Report/CRT columns join object and field with '$'; the plain tokeniser
    # would split them and lose the qualified form.
    for m in _REPORT_COL_RE.finditer(content):
        counts[m.group(1).lower()] += 1
    return counts


_SOQL_SHAPE_RE = re.compile(r"\bselect\b.*\bfrom\b", re.IGNORECASE | re.DOTALL)


def _literals_from(ex) -> dict[str, str]:
    """Literal -> origin. Uses the properly-scanned literals, not a regex.

    A regex cannot tell a quote inside a comment from one that opens a string,
    so it both invents literals and misses real ones.
    """
    out: dict[str, str] = {}
    for val in ex.literals:
        v = val.strip()
        if len(v) < 2:
            continue
        out.setdefault(v.lower(), "string_literal")
        # A dynamic SOQL string is itself a container of references, so tokenise
        # inside it too: 'SELECT Legacy_Code__c FROM Order__c' yields both names.
        #
        # Restricted to SOQL-shaped literals, not any literal with a space —
        # otherwise a free-text audit-log message that happens to name a
        # component in prose ("auraCard ran its gated task") would count as a
        # dynamic reference, which is exactly the false positive this file
        # exists to avoid.
        if _SOQL_SHAPE_RE.search(v):
            for t in _TOKEN_RE.finditer(v):
                out.setdefault(t.group(0).lower(), "string_literal")
    for m in _MERGE_RE.finditer(ex.code):
        expr = m.group(1).strip()
        out.setdefault(expr.lower(), "merge_field")
        for seg in re.split(r"[.\s(),]+", expr):
            if len(seg) > 1:
                out.setdefault(seg.lower(), "merge_field")
    return out


#: Consumers whose references can never exceed Tier C, however they were found.
#:
#: Field-level security is the important case. Every field gets FLS entries when
#: it is created, so a profile naming a field says only that someone *could* see
#: it - not that anything uses it. Counting FLS as proof of use would mark
#: essentially every field USED and make the whole analysis worthless.
#:
#: Layouts are deliberately NOT in this list: layout presence is genuinely
#: binding (deleting the field breaks the deploy), even though it is weak
#: evidence of business use. That nuance is handled later by the LAYOUT_ONLY tag.
WEAK_CONSUMER_TYPES = {
    "Profile", "PermissionSet", "PermissionSetGroup", "CustomApplication",
    "CustomTab", "SharingRules",
}


def _resolve(
    aliases: dict[str, list[tuple[int, str]]],
    tokens: dict[str, int],
    literals: dict[str, str],
    mdtype: str,
    is_test: bool,
    active: bool,
    parents: dict[int, str] | None = None,
    comment_tokens: dict[str, int] | None = None,
    suffix: str = "",
    ctypes: dict[int, str] | None = None,
) -> tuple[list[dict], Counter[str]]:
    """Match extracted strings against the alias table.

    Tiering encodes how much a hit is worth. A layout naming a field is binding
    (Tier A). A label-only match is a coincidence risk (Tier C). References from
    tests, from inactive consumers, or from permission metadata are downgraded
    regardless of how they were found - none of them prove current use.

    Exception: CustomTab / CustomApplication naming an LWC or Aura bundle is
    deliberate placement — Tier A for those target types only.
    """
    seen: dict[tuple[int, str], dict] = {}
    suppressed: Counter[str] = Counter()
    is_code = suffix in CODE_SUFFIXES
    weak_consumer = mdtype in WEAK_CONSUMER_TYPES
    placement_for_ui = mdtype in UI_PLACEMENT_CONSUMER_TYPES
    parents = parents or {}
    ctypes = ctypes or {}

    def add(component_id: int, kind: str, match_kind: str, tier: str,
            ambiguous: bool = False) -> None:
        target_ctype = ctypes.get(component_id, "")
        downgrade = is_test or not active
        if weak_consumer:
            # App/Tab → UI bundle is placement (use). Same App/Tab → field stays weak.
            if not (placement_for_ui and target_ctype in UI_COMPONENT_TYPES):
                downgrade = True
        if downgrade:
            tier = "C"
        key = (component_id, match_kind)
        prev = seen.get(key)
        if prev is None or _tier_rank(tier) > _tier_rank(prev["tier"]):
            seen[key] = {"component_id": component_id, "tier": tier,
                         "match_kind": match_kind, "alias_kind": kind,
                         "ambiguous": ambiguous}

    def emit(alias_str: str, match_kind: str, base_tier_for: callable) -> None:
        # DISTINCTIVENESS GUARD -- applied by context, not to everything.
        #
        # `Active__c` reduces to the alias `active`, which matched the English
        # word in a doc-comment and `<status>Active</status>` in metadata XML,
        # collecting a dozen Apex classes that reference the field nowhere.
        #
        # But the same bare shape IS a reference in other places: `start` in an
        # Apex class is the Batchable method, `'SKU'` quoted in JavaScript is a
        # deliberate field name, and `{!Description}` is an explicit binding.
        # Measuring an earlier, blunter version of this guard against the real
        # workspace showed it deleting exactly those -- Tier A evidence, in the
        # direction that produces a false UNUSED.
        #
        # So a bare word is refused only for a token sweep over markup, where
        # element names and enum values are ordinary English. Code identifiers,
        # string literals and merge fields are left alone.
        if not is_code and match_kind == "token_sweep" and not is_sweepable(alias_str):
            suppressed[alias_str] += 1
            return
        matches = [(cid, k) for cid, k in aliases.get(alias_str, ())
                   if k not in NON_REFERENCE_KINDS]
        if not matches:
            return
        # AMBIGUITY GUARD. The same unqualified field name can exist on several
        # objects - `is_active__c` lives on two here - so a bare token match
        # would mark EVERY same-named field used, from a single mention. That
        # inflates USED and is the wrong direction to be wrong in.
        #
        # When ambiguous, keep Tier A only for the component whose parent object
        # this file also mentions; everything else drops to Tier C.
        ambiguous = len(matches) > 1
        for component_id, alias_kind in matches:
            tier = base_tier_for(alias_kind)
            if ambiguous and tier == "A":
                parent = (parents.get(component_id) or "").lower()
                if not parent or parent not in tokens:
                    tier = "C"
            add(component_id, alias_kind, match_kind, tier, ambiguous)

    for tok in tokens:
        emit(tok, "token_sweep",
             lambda k: "C" if k in ("label", "bare_name") else "A")

    for lit, origin in literals.items():
        # An exact alias match inside a string literal is a real reference: this
        # is how dynamic access gets caught at all.
        emit(lit, "merge_field" if origin == "merge_field" else "string_literal",
             lambda k: "C" if k == "label" else "A")

    # Comment mentions are Tier C and never more. Someone once cared about this
    # name — a reason to look, never proof that anything uses it. Recorded rather
    # than discarded so a reviewer can see the trail.
    for tok in (comment_tokens or {}):
        # Guarded unconditionally. This is the case that started it: "Retrieves
        # active promoted records" in a docblock is prose, not a reference to
        # Account.Active__c, in any file type.
        if not is_sweepable(tok):
            suppressed[tok] += 1
            continue
        for component_id, alias_kind in aliases.get(tok, ()):
            if alias_kind in NON_REFERENCE_KINDS:
                continue
            add(component_id, alias_kind, "comment_mention", "C")

    return list(seen.values()), suppressed


def _tier_rank(t: str) -> int:
    return {"A": 3, "B": 2, "C": 1, "D": 0}.get(t, 0)


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------

async def _load_aliases(run_id: str) -> dict[str, list[tuple[int, str]]]:
    async with session_scope() as s:
        rows = (await s.execute(text(
            "SELECT component_id, alias_lc, alias_kind FROM alias WHERE run_id = :r"
        ), {"r": run_id})).all()
    out: dict[str, list[tuple[int, str]]] = defaultdict(list)
    for r in rows:
        out[r.alias_lc].append((r.component_id, r.alias_kind))
    return out


async def _load_component_types(run_id: str) -> dict[int, str]:
    async with session_scope() as s:
        rows = (await s.execute(text(
            "SELECT id, ctype::text AS ctype FROM components WHERE run_id = :r"
        ), {"r": run_id})).all()
    return {r.id: r.ctype for r in rows}


async def _load_parents(run_id: str) -> dict[int, str]:
    """component_id -> parent object, for disambiguating same-named fields."""
    async with session_scope() as s:
        rows = (await s.execute(text(
            "SELECT id, parent_object FROM components "
            "WHERE run_id = :r AND parent_object IS NOT NULL"
        ), {"r": run_id})).all()
    return {r.id: r.parent_object for r in rows}


async def _insert_artifact(s, run_id, mdtype, member, path, *, is_test, active, size) -> int:
    row = (await s.execute(text("""
        INSERT INTO artifact (run_id, metadata_type, member_name, file_path,
                              parse_status, is_active, is_test, attrs)
        VALUES (:r, :t, :m, :p, 'OK', :a, :test, CAST(:attrs AS jsonb))
        ON CONFLICT (run_id, metadata_type, member_name)
        DO UPDATE SET file_path = EXCLUDED.file_path
        RETURNING id
    """), {"r": run_id, "t": mdtype, "m": member, "p": path,
           "a": active, "test": is_test,
           "attrs": f'{{"bytes": {size}}}'})).one()
    return int(row.id)


async def _insert_tokens(s, run_id, artifact_id, tokens: dict[str, int]) -> None:
    await s.execute(text("""
        INSERT INTO artifact_token (run_id, artifact_id, token_lc, occurrences)
        VALUES (:r, :a, :t, :n)
        ON CONFLICT (artifact_id, token_lc) DO UPDATE
          SET occurrences = artifact_token.occurrences + EXCLUDED.occurrences
    """), [{"r": run_id, "a": artifact_id, "t": t, "n": n}
           for t, n in tokens.items()])


async def _insert_literals(s, run_id, artifact_id, literals: dict[str, str]) -> None:
    await s.execute(text("""
        INSERT INTO artifact_literal (run_id, artifact_id, literal_lc, locator)
        VALUES (:r, :a, :l, CAST(:loc AS jsonb))
    """), [{"r": run_id, "a": artifact_id, "l": lit,
            "loc": f'{{"origin": "{origin}"}}'}
           for lit, origin in literals.items()])


async def _insert_edges(
    s, run_id, artifact_id, edges: list[dict],
    *, consumer_active: bool = True, consumer_is_test: bool = False,
) -> None:
    await s.execute(text("""
        INSERT INTO reference_edges (run_id, from_artifact_id, to_component_id,
            tier, match_kind, collector_id, consumer_active, consumer_is_test, tags)
        VALUES (:r, :a, :c, CAST(:tier AS evidence_tier), :mk,
                'C10_static_index', :active, :test, :tags)
    """), [{"r": run_id, "a": artifact_id, "c": e["component_id"],
            "tier": e["tier"], "mk": e["match_kind"],
            "active": consumer_active, "test": consumer_is_test,
            "tags": [e["alias_kind"]]} for e in edges])
