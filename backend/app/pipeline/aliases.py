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

    bare = _bare(api_name)
    if bare.lower() != api_name.lower():
        add(bare, "bare_name", "field.bare_name")

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

    bare = _bare(api_name)
    if bare.lower() != api_name.lower():
        add(bare, "bare_name", "object.bare_name")

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
