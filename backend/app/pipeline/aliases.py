"""Alias generation.

One component appears in the org's metadata under many different textual forms.
Get this table wrong and every downstream verdict is wrong, because a collector
that searches for the wrong string finds nothing and "found nothing" is one step
away from "delete it".

A single custom field can legitimately appear as:

    Legacy_Code__c                       Apex, SOQL, most XML
    Customer_Order__c.Legacy_Code__c     layouts, formulas, LWC schema imports
    Legacy_Code__r                       ONLY if it is a lookup/master-detail
    Legacy_Code                          report XML, some Flow refs, URL params
    Customer_Order__c$Legacy_Code__c     report/CRT column references
    00N...                               hardcoded button URLs, retURL params
    "Legacy Code"                        report display columns (weak, Tier C)

Salesforce API names are case-insensitive, so everything is normalised to
lowercase before comparison.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Alias:
    alias_lc: str
    kind: str
    generated_by: str


def _id15(sf_id: str | None) -> str | None:
    """18-character IDs truncate cleanly to 15; the reverse needs a checksum."""
    if not sf_id:
        return None
    return sf_id[:15] if len(sf_id) == 18 else None


def _bare(api_name: str) -> str:
    """Strip the custom suffix: Legacy_Code__c -> Legacy_Code."""
    for suffix in ("__c", "__mdt", "__e", "__b", "__x"):
        if api_name.lower().endswith(suffix):
            return api_name[: -len(suffix)]
    return api_name


def field_aliases(
    *,
    api_name: str,
    parent_object: str,
    label: str | None = None,
    sf_id: str | None = None,
    relationship_name: str | None = None,
) -> list[Alias]:
    """``api_name`` is the unqualified field name, e.g. ``Legacy_Code__c``."""
    out: list[Alias] = []
    add = lambda v, k, g: out.append(Alias(v.lower(), k, g))  # noqa: E731

    add(api_name, "api_name", "field.api_name")
    add(f"{parent_object}.{api_name}", "qualified", "field.qualified")
    # Report and Custom Report Type XML join object and field with '$'.
    add(f"{parent_object}${api_name}", "report_qualified", "field.report_qualified")

    # NOT a name. `Active__c` with the suffix removed is `Active`, which
    # Salesforce never uses to mean this field -- every real reference writes
    # Active__c, Account.Active__c or Account$Active__c. Kept in the table for
    # provenance and refused at match time; see NON_REFERENCE_KINDS.
    bare = _bare(api_name)
    if bare.lower() != api_name.lower():
        add(bare, "stripped_suffix", "field.stripped_suffix")

    # Relationship names are read from FieldDefinition, never derived by string
    # substitution: they are freely renameable and guessing produces both false
    # positives and false negatives.
    if relationship_name:
        add(relationship_name, "relationship_name", "field.relationship_from_metadata")
        add(f"{parent_object}.{relationship_name}", "qualified",
            "field.relationship_qualified")

    if sf_id:
        add(sf_id, "sf_id_18", "field.sf_id")
        if (short := _id15(sf_id)):
            add(short, "sf_id_15", "field.sf_id_15")

    # Labels are Tier C only: they appear in report column headers, but they are
    # not unique and not stable, so a label match can never prove use.
    if label and label.lower() != api_name.lower():
        add(label, "label", "field.label")

    return _dedupe(out)


def object_aliases(
    *,
    api_name: str,
    label: str | None = None,
    sf_id: str | None = None,
    key_prefix: str | None = None,
    child_relationships: list[str] | None = None,
) -> list[Alias]:
    out: list[Alias] = []
    add = lambda v, k, g: out.append(Alias(v.lower(), k, g))  # noqa: E731

    add(api_name, "api_name", "object.api_name")

    # Same reasoning as fields: `Retail_Product__c` without its suffix is not a
    # name the platform recognises. The __r relationship form below IS one.
    bare = _bare(api_name)
    if bare.lower() != api_name.lower():
        add(bare, "stripped_suffix", "object.stripped_suffix")

    # The relationship form used when traversing TO this object.
    if api_name.lower().endswith("__c"):
        add(api_name[:-1] + "r", "relationship_name", "object.relationship_suffix")

    # Child relationship names, harvested from the lookups pointing at this
    # object. Read from metadata rather than pluralised by guesswork.
    for rel in child_relationships or []:
        add(rel, "plural_relationship", "object.child_relationship_from_metadata")

    # Key prefix appears in hardcoded URLs inside custom buttons, Visualforce and
    # LWC navigation - a genuine reference that no API-name search would find.
    if key_prefix:
        add(key_prefix, "key_prefix", "object.key_prefix")

    if sf_id:
        add(sf_id, "sf_id_18", "object.sf_id")
        if (short := _id15(sf_id)):
            add(short, "sf_id_15", "object.sf_id_15")

    if label and label.lower() != api_name.lower():
        add(label, "label", "object.label")

    return _dedupe(out)


def apex_aliases(
    *, api_name: str, sf_id: str | None = None, is_trigger: bool = False
) -> list[Alias]:
    out: list[Alias] = []
    add = lambda v, k, g: out.append(Alias(v.lower(), k, g))  # noqa: E731

    add(api_name, "api_name", "apex.api_name")
    if not is_trigger:
        # Qualified call form, and the Flow/LWC import form.
        add(f"{api_name}.", "qualified", "apex.dotted")
    if sf_id:
        add(sf_id, "sf_id_18", "apex.sf_id")
        if (short := _id15(sf_id)):
            add(short, "sf_id_15", "apex.sf_id_15")
    return _dedupe(out)


def method_aliases(*, class_name: str, method_name: str) -> list[Alias]:
    """Method-level forms, including the declarative binding shapes."""
    out: list[Alias] = []
    add = lambda v, k, g: out.append(Alias(v.lower(), k, g))  # noqa: E731

    add(method_name, "api_name", "method.name")
    add(f"{class_name}.{method_name}", "qualified", "method.qualified")
    # LWC:   import x from '@salesforce/apex/Class.method'
    add(f"@salesforce/apex/{class_name}.{method_name}", "qualified", "method.lwc_import")
    # Aura:  component.get("c.method")   -- a string literal, so only a literal
    #        harvest will ever find it.
    add(f"c.{method_name}", "bare_name", "method.aura_binding")
    # Visualforce getter binding: {!propertyName} maps to getPropertyName().
    if method_name.lower().startswith("get") and len(method_name) > 3:
        prop = method_name[3:]
        add(prop, "bare_name", "method.vf_getter_property")
    return _dedupe(out)


def _dedupe(aliases: list[Alias]) -> list[Alias]:
    """Keep the first occurrence of each (alias, kind) pair."""
    seen: set[tuple[str, str]] = set()
    out: list[Alias] = []
    for a in aliases:
        # Single-character or empty aliases would match almost everything.
        if len(a.alias_lc) < 2:
            continue
        key = (a.alias_lc, a.kind)
        if key not in seen:
            seen.add(key)
            out.append(a)
    return out


# ---------------------------------------------------------------------------
# Distinctiveness: which aliases may be matched by a broad text sweep
# ---------------------------------------------------------------------------
#
# Stripping the custom suffix from `Active__c` yields the alias `active`, and
# its label is `Active` too. Swept as a bare token across every retrieved file,
# that matched the English word "active" in a doc-comment and the literal
# `<status>Active</status>` in an Apex class's meta file -- and the field
# collected a dozen Apex classes as "references" that mention it nowhere.
#
# Tier C is not harmless. It suppresses UNUSED through rule R7, so noise sends
# genuinely dead metadata to a review queue that nobody can trust, and it fills
# an evidence trail whose entire value is that a reviewer can believe it.
#
# The rule: a broad sweep may only match an alias that ordinary prose cannot
# accidentally produce. A structural marker -- an underscore, a dot, a dollar,
# a custom suffix -- is such a signal. A single lowercase word is not.
#
# This costs nothing real. A genuine reference to a custom field is written
# `Active__c`, `Account.Active__c` or `{!Account.Active__c}`; none is a bare
# word. Anything that reaches a field by its stripped name alone is
# indistinguishable from prose, and could not have been trusted regardless.

#: Words that are ordinary English, ordinary Apex, or ordinary metadata XML.
#: A bare alias equal to one of these is never swept for.
#:
#: Not an attempt to enumerate English -- just the intersection of "plausible
#: Salesforce API name with the suffix stripped" and "appears constantly in code
#: and prose". Extend it when a false positive proves a word belongs here.
AMBIGUOUS_BARE_WORDS: frozenset[str] = frozenset({
    # Field names that are also plain English
    "active", "address", "amount", "balance", "brand", "category", "city",
    "code", "color", "comment", "comments", "company", "contact", "count",
    "country", "currency", "customer", "date", "day", "default", "description",
    "detail", "details", "discount", "email", "end", "error", "event", "file",
    "first", "flag", "group", "hour", "icon", "image", "index", "info", "item",
    "items", "key", "label", "language", "languages", "last", "level", "limit",
    "line", "link", "location", "manager", "message", "method", "minute",
    "mobile", "month", "name", "note", "notes", "number", "order", "owner",
    "page", "parent", "path", "phone", "picture", "price", "priority",
    "product", "quantity", "rate", "reason", "record", "region", "result",
    "role", "source", "start", "state", "status", "step", "street", "subject",
    "summary", "tag", "target", "tax", "team", "time", "title", "total",
    "type", "unit", "units", "url", "user", "value", "version", "week", "year",
    "zip",
    # Apex / JS keywords and near-keywords
    "abstract", "boolean", "break", "case", "catch", "class", "const",
    "continue", "date", "datetime", "decimal", "delete", "double", "else",
    "enum", "extends", "false", "final", "finally", "for", "get", "global",
    "id", "if", "implements", "import", "insert", "instanceof", "integer",
    "interface", "list", "long", "map", "merge", "new", "null", "object",
    "override", "private", "protected", "public", "return", "select", "set",
    "static", "string", "super", "switch", "test", "this", "throw", "transient",
    "true", "try", "undelete", "update", "upsert", "virtual", "void", "while",
    "with", "without", "sharing",
    # Metadata XML vocabulary. `<status>Active</status>` in every .cls-meta.xml
    # is the reason this section exists.
    "apiversion", "available", "content", "criteria", "deleted", "draft",
    "enabled", "field", "fields", "fullname", "inactive", "layout", "obsolete",
    "readonly", "required", "section", "settings", "true", "visible",
})

#: Below this length a bare word is too collision-prone to sweep for, whatever
#: it says. `sla` and `ext` will match something in any org.
MIN_BARE_ALIAS_LENGTH = 6


def is_sweepable(alias_lc: str) -> bool:
    """May this alias be matched by a broad token / literal / comment sweep?

    Structural extraction and exact API-name matching are unaffected: those work
    from the qualified and suffixed forms, which always pass this test.
    """
    if not alias_lc:
        return False

    # A structural marker no English sentence produces by accident.
    if any(ch in alias_lc for ch in ("_", ".", "$", "/")):
        return True

    # A Salesforce record id: 15 or 18 alphanumerics, and always mixed-case in
    # origin, so length plus the digit is signal enough.
    if len(alias_lc) in (15, 18) and alias_lc.isalnum() and any(c.isdigit() for c in alias_lc):
        return True

    if alias_lc in AMBIGUOUS_BARE_WORDS:
        return False
    return len(alias_lc) >= MIN_BARE_ALIAS_LENGTH
