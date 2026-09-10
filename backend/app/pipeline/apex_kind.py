"""Derive Apex class role(s) for Overview bucketing and Explorer tags.

Roles are based on inventory attrs already stored on the class and its methods
(interfaces, annotations, is_test). A class can carry several surfaces at once
(e.g. Batchable + Schedulable); Overview still uses one primary role.
"""

from __future__ import annotations

from typing import Any

# Short interface names (Salesforce often prefixes System. / Database. /
# Messaging., and Batchable may carry a type parameter).
_ENTRY_IFACE_SHORT = {
    "batchable",
    "schedulable",
    "queueable",
    "inboundemailhandler",
    "callable",
    "comparable",
    "allowscallouts",
    "triggerhandler",
}

#: Display / priority order — first match is the Overview primary.
KIND_ORDER = (
    "rest",
    "scheduled",
    "batch",
    "queueable",
    "invocable",
    "aura",
    "email",
    "webservice",
    "entry",
    "test",
    "regular",
)


def _norm_iface(raw: str) -> str:
    base = (raw or "").lower().split("<", 1)[0].strip()
    return base.rsplit(".", 1)[-1] if base else ""


def iface_is_entry(raw: str) -> bool:
    return _norm_iface(raw) in _ENTRY_IFACE_SHORT


def _lower_list(v: Any) -> list[str]:
    if not isinstance(v, list):
        return []
    return [str(x).lower() for x in v if x is not None and str(x)]


def _parse_attrs(raw: Any) -> dict[str, Any]:
    if isinstance(raw, str):
        import json
        try:
            raw = json.loads(raw)
        except Exception:
            return {}
    return dict(raw or {}) if isinstance(raw, dict) else {}


def list_apex_class_kinds(
    class_attrs: dict[str, Any] | None,
    method_attrs_list: list[dict[str, Any] | None] | None = None,
) -> list[str]:
    """All roles that apply, in KIND_ORDER. Test is exclusive."""
    attrs = class_attrs or {}
    if attrs.get("is_test") is True:
        return ["test"]

    ifaces = {_norm_iface(i) for i in (attrs.get("interfaces") or [])}
    class_anns = _lower_list(attrs.get("annotations"))
    method_anns: list[str] = []
    method_mods: list[str] = []
    for ma in method_attrs_list or []:
        if not ma:
            continue
        method_anns.extend(_lower_list(ma.get("annotations")))
        method_mods.extend(_lower_list(ma.get("modifiers")))
    anns = set(class_anns) | set(method_anns)

    found: list[str] = []
    if "restresource" in anns or any(a.startswith("http") for a in anns):
        found.append("rest")
    if "schedulable" in ifaces:
        found.append("scheduled")
    if "batchable" in ifaces:
        found.append("batch")
    if "queueable" in ifaces:
        found.append("queueable")
    if "inboundemailhandler" in ifaces:
        found.append("email")
    if "invocablemethod" in anns:
        found.append("invocable")
    if "auraenabled" in anns:
        found.append("aura")
    if "webservice" in method_mods or "webservice" in anns:
        found.append("webservice")

    if found:
        # Keep KIND_ORDER, drop generic entry when a specific surface exists.
        order = {k: i for i, k in enumerate(KIND_ORDER)}
        return sorted(found, key=lambda k: order.get(k, 99))

    if attrs.get("is_entry_point") is True:
        return ["entry"]
    return ["regular"]


def classify_apex_class_kind(
    class_attrs: dict[str, Any] | None,
    method_attrs_list: list[dict[str, Any] | None] | None = None,
) -> str:
    """Primary role (first of ``list_apex_class_kinds``)."""
    return list_apex_class_kinds(class_attrs, method_attrs_list)[0]


def enrich_component_rows_with_apex_kind(rows: list[dict]) -> list[dict]:
    """Stamp attrs.apex_kind / apex_kinds on ApexClass rows."""
    by_parent: dict[int, list[dict]] = {}
    for r in rows:
        if r.get("ctype") == "ApexMethod" and r.get("parent_id") is not None:
            by_parent.setdefault(int(r["parent_id"]), []).append(r)

    for r in rows:
        if r.get("ctype") != "ApexClass":
            continue
        attrs = _parse_attrs(r.get("attrs"))
        kids = by_parent.get(int(r["id"]), [])
        kinds = list_apex_class_kinds(attrs, [k.get("attrs") for k in kids])
        attrs["apex_kinds"] = kinds
        attrs["apex_kind"] = kinds[0]
        r["attrs"] = attrs
    return rows


def apex_kinds_for_class_row(
    class_attrs: Any,
    method_attrs_list: list[Any] | None = None,
) -> list[str]:
    """Convenience for explorer: accept raw attrs blobs."""
    return list_apex_class_kinds(
        _parse_attrs(class_attrs),
        [_parse_attrs(m) for m in (method_attrs_list or [])],
    )
