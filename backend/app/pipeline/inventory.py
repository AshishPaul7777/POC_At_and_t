"""Stage S10 - component inventory.

Establishes *what exists and is in scope* before any evidence is gathered. Get
the scope wrong and everything downstream is wrong: a component we never
inventoried is a component we never check, and one we wrongly include produces a
verdict nobody can act on.

The query strategy here is shaped entirely by measured Salesforce behaviour, not
by what the documentation implies:

  * ``CustomField`` (Tooling) is the authoritative field source. It returns every
    custom field in one query, keyed by ``TableEnumOrId``.

  * ``TableEnumOrId`` is polymorphic: an **object API name** for standard objects
    (``Account``) and an **18-character DurableId** for custom ones
    (``01IgL000007SEbtUAG``). Both forms must be handled.

  * ``EntityDefinition`` cannot be enumerated. It rejects ``queryMore()``
    outright, and ``LIMIT 2000`` returns 2,000 rows containing *zero* custom
    objects. It also silently drops unsupported WHERE predicates such as
    ``LIKE '%__c'``, returning non-matching rows with no error.

    But ``WHERE DurableId IN (...)`` **is** honoured - verified. So objects are
    resolved by targeted lookup from the ids the field query already gave us,
    never by enumeration.

  * A third of this org's custom fields (26 of 82) live on **standard** objects.
    Those fields are deletable and therefore in scope, even though their parent
    object is not.
"""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from xml.etree import ElementTree as ET

import structlog
from sqlalchemy import text

from app.db.session import session_scope
from app.pipeline import aliases as al
from app.salesforce.client import SalesforceClient

log = structlog.get_logger()

# Fields Salesforce reports as custom but which are not independently deletable.
_SKIP_FIELD_SUFFIXES = ("__Share", "__History", "__Feed", "__Tag")


async def abandon_stale_runs(org_id: str, *, older_than_minutes: int = 0) -> list[str]:
    """Mark orphaned active runs as FAILED so a new run can start.

    ``one_active_run_per_org`` deliberately blocks concurrent runs, but a process
    that crashes leaves its row in RUNNING forever and locks the org out. The
    constraint is right; it just needs a reaper.

    This is the honest-but-crude version: it assumes any active run is dead,
    which is safe only because a single worker holds a Postgres advisory lock.
    A multi-worker deployment needs a heartbeat column and a lease timeout, so
    that a genuinely-live run is never reaped out from under itself.
    """
    async with session_scope() as s:
        rows = (await s.execute(text("""
            UPDATE runs SET state = 'FAILED', finished_at = now(),
                   error = CAST(:err AS jsonb)
             WHERE org_id = :org
               AND state IN ('QUEUED','RUNNING','PAUSED','AWAITING_BUDGET')
               -- COALESCE matters: a run that died before setting started_at
               -- leaves it NULL, and `NULL < now()` is NULL rather than true,
               -- so a bare comparison silently reaps nothing and the org stays
               -- locked out with no error to explain why.
               AND COALESCE(started_at, created_at)
                   <= now() - make_interval(mins => :mins)
            RETURNING id
        """), {"org": org_id, "mins": older_than_minutes,
               "err": _json({"reason": "abandoned: no live worker",
                             "reaped_at": "now"})})).all()
    ids = [str(r.id) for r in rows]
    if ids:
        log.warning("stale_runs_abandoned", org_id=org_id, count=len(ids), ids=ids)
    return ids


async def create_run(
    sf: SalesforceClient, *, alias: str | None = None, config: dict | None = None
) -> str:
    """Open a run row and record the provenance needed to reproduce it.

    Org identity comes from the org itself, never from a constant: the id is read
    from ``/userinfo`` and the alias from the caller's connection, so the same
    code analyses any org it is pointed at.
    """
    me = await sf.identity()
    remaining, maximum = await sf.daily_api_remaining()
    org_id = me.get("organization_id", "unknown")
    async with session_scope() as s:
        row = (await s.execute(text("""
            INSERT INTO runs (org_id, org_alias, api_version, state,
                              run_as_username, config, tool_versions, started_at)
            VALUES (:org, :alias, :ver, 'RUNNING', :user,
                    CAST(:cfg AS jsonb), CAST(:tools AS jsonb), now())
            RETURNING id
        """), {
            "org": org_id,
            # Falls back to the org id rather than any developer-specific name.
            "alias": alias or org_id,
            "ver": sf._s.sf_api_version,
            # Recorded because the analysis can only see what this user sees. A
            # restricted user narrows coverage in a way indistinguishable from
            # components genuinely being unused.
            "user": me.get("preferred_username"),
            "cfg": _json(config or {}),
            "tools": _json({"api_version": sf._s.sf_api_version,
                            "api_remaining_at_start": remaining,
                            "api_max": maximum}),
        })).one()
    log.info("run_created", run_id=str(row.id), run_as=me.get("preferred_username"),
             api_remaining=remaining)
    return str(row.id)


async def inventory(run_id: str, sf: SalesforceClient) -> dict[str, int]:
    """Populate ``components`` and ``alias``. Returns counts by type."""
    counts: dict[str, int] = {}

    fields_raw = await _collect(
        sf, run_id, "C00_inventory_fields", "structural",
        "SELECT Id, DeveloperName, TableEnumOrId, NamespacePrefix FROM CustomField",
        tooling=True,
    )
    # Namespaced components belong to installed packages: they cannot be deleted,
    # so a verdict on them is noise. They stay indexed as a *reference source*.
    local_fields = [f for f in fields_raw if not f.get("NamespacePrefix")]
    packaged = len(fields_raw) - len(local_fields)

    parents = {f["TableEnumOrId"] for f in local_fields}
    obj_ids = {p for p in parents if _looks_like_id(p)}
    std_names = parents - obj_ids

    id_to_object = await _resolve_objects_by_id(sf, run_id, obj_ids)
    std_meta = await _resolve_objects_by_name(sf, run_id, std_names)

    async with session_scope() as s:
        # --- custom objects (in scope: deletable) ---------------------------
        n = 0
        for durable, meta in id_to_object.items():
            api = meta["QualifiedApiName"]
            cid = await _insert_component(
                s, run_id, "CustomObject", api,
                label=meta.get("Label"), sf_id=durable,
                attrs={"key_prefix": meta.get("KeyPrefix"),
                       "durable_id": durable,
                       "source": "EntityDefinition via DurableId IN"},
            )
            await _insert_aliases(s, run_id, cid, al.object_aliases(
                api_name=api, label=meta.get("Label"), sf_id=durable,
                key_prefix=meta.get("KeyPrefix")))
            n += 1
        counts["CustomObject"] = n

        # --- standard objects (OUT_OF_SCOPE, but indexed) --------------------
        # They cannot be deleted, yet they must exist as parents for their custom
        # fields and as reference sources. Typed as StandardObject rather than
        # reusing CustomObject: labelling Account a custom object makes the data
        # state something false, and a reader should not need a footnote.
        n = 0
        for name in sorted(std_names):
            meta = std_meta.get(name, {})
            cid = await _insert_component(
                s, run_id, "StandardObject", name,
                label=meta.get("Label") or name, sf_id=meta.get("DurableId"),
                in_scope=False, out_of_scope_reason="STANDARD_OBJECT",
                attrs={"key_prefix": meta.get("KeyPrefix"), "standard": True},
            )
            await _insert_aliases(s, run_id, cid, al.object_aliases(
                api_name=name, label=meta.get("Label"),
                key_prefix=meta.get("KeyPrefix")))
            n += 1
        counts["StandardObject(out of scope)"] = n

        # --- custom fields ---------------------------------------------------
        parent_ids = await _parent_map(s, run_id)
        field_meta = await _field_details(sf, run_id, id_to_object, std_names)

        n = skipped = 0
        for f in local_fields:
            api = f["DeveloperName"] + "__c"
            if any(api.endswith(x) for x in _SKIP_FIELD_SUFFIXES):
                skipped += 1
                continue
            parent = id_to_object.get(f["TableEnumOrId"], {}).get(
                "QualifiedApiName", f["TableEnumOrId"])
            detail = field_meta.get(f"{parent}.{api}", {})
            qualified = f"{parent}.{api}"
            cid = await _insert_component(
                s, run_id, "CustomField", qualified,
                label=detail.get("Label"), sf_id=f["Id"],
                parent_id=parent_ids.get(parent.lower()),
                parent_object=parent,
                attrs={
                    "data_type": detail.get("DataType"),
                    "relationship_name": detail.get("RelationshipName"),
                    # An explicit human governance declaration
                    # (Active/DeprecateCandidate/Deprecated) that most cleanup
                    # tools ignore entirely.
                    "business_status": detail.get("BusinessStatus"),
                    "compliance_group": detail.get("ComplianceGroup"),
                    "is_calculated": detail.get("IsCalculated"),
                    "is_indexed": detail.get("IsIndexed"),
                    "parent_in_scope": parent in
                        {m["QualifiedApiName"] for m in id_to_object.values()},
                },
            )
            await _insert_aliases(s, run_id, cid, al.field_aliases(
                api_name=api, parent_object=parent, label=detail.get("Label"),
                sf_id=f["Id"], relationship_name=detail.get("RelationshipName")))
            n += 1
        counts["CustomField"] = n
        counts["CustomField(system, skipped)"] = skipped
        counts["CustomField(packaged, excluded)"] = packaged

        # --- Apex ------------------------------------------------------------
        counts.update(await _inventory_apex(s, run_id, sf))

    log.info("inventory_complete", run_id=run_id, **counts)
    return counts


# ---------------------------------------------------------------------------
# LWC / Aura (retrieve-backed — isExposed/targets live in meta XML)
# ---------------------------------------------------------------------------

_XML_NS_STRIP = re.compile(r"\{[^}]+\}")


def _local(tag: str) -> str:
    return _XML_NS_STRIP.sub("", tag)


async def inventory_ui_from_workspace(run_id: str, workspace: Path) -> dict[str, int]:
    """Inventory unmanaged LWC and Aura bundles from the retrieved workspace.

    Prefer retrieve-backed inventory over Tooling: ``isExposed``, targets and
    Aura ``access`` / ``implements`` live in the meta/cmp files, not in the
    Tooling row. Called after retrieve, before index, so aliases exist when
    the indexer resolves FlexiPage and cross-bundle references.
    """
    counts = {"LightningComponentBundle": 0, "AuraDefinitionBundle": 0,
              "LightningComponentBundle(packaged, excluded)": 0,
              "AuraDefinitionBundle(packaged, excluded)": 0}
    async with session_scope() as s:
        for abs_path, rel in _ui_bundle_roots(workspace):
            folder = rel.parts[0].lower()
            bundle = rel.parts[1] if len(rel.parts) > 1 else abs_path.name
            if folder == "lwc":
                meta = abs_path / f"{bundle}.js-meta.xml"
                attrs, namespaced = _parse_lwc_meta(meta, bundle)
                ctype = "LightningComponentBundle"
            else:
                meta = _aura_cmp_path(abs_path, bundle)
                attrs, namespaced = _parse_aura_cmp(meta, bundle)
                ctype = "AuraDefinitionBundle"
            if namespaced:
                counts[f"{ctype}(packaged, excluded)"] += 1
                continue
            cid = await _insert_component(
                s, run_id, ctype, bundle,
                label=attrs.get("master_label") or bundle,
                attrs=attrs,
            )
            await _insert_aliases(
                s, run_id, cid,
                al.ui_bundle_aliases(api_name=bundle),
            )
            counts[ctype] += 1

    await _record_collector(
        run_id, "C00_inventory_ui_bundles", "structural", "*",
        status="OK",
        method="scan retrieved lwc/ and aura/ folders; parse js-meta.xml / .cmp",
        artifacts=counts["LightningComponentBundle"] + counts["AuraDefinitionBundle"],
        hits=counts["LightningComponentBundle"] + counts["AuraDefinitionBundle"],
    )
    log.info("inventory_ui_complete", run_id=run_id, **counts)
    return counts


def _ui_bundle_roots(workspace: Path) -> list[tuple[Path, Path]]:
    """Yield (absolute bundle dir, relative path under unpackaged)."""
    out: list[tuple[Path, Path]] = []
    seen: set[str] = set()
    for base in workspace.rglob("unpackaged"):
        if not base.is_dir():
            continue
        for folder in ("lwc", "aura"):
            root = base / folder
            if not root.is_dir():
                continue
            for child in root.iterdir():
                if not child.is_dir() or child.name.startswith("."):
                    continue
                key = f"{folder}/{child.name}".lower()
                if key in seen:
                    continue
                seen.add(key)
                out.append((child, Path(folder) / child.name))
    return out


def _parse_lwc_meta(meta: Path, bundle: str) -> tuple[dict, bool]:
    """Return (attrs, is_namespaced). Missing meta => private unmanaged defaults."""
    attrs: dict = {
        "is_exposed": False,
        "targets": [],
        "master_label": bundle,
        "is_entry_point": False,
    }
    if not meta.is_file():
        return attrs, False
    try:
        text_body = meta.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return attrs, False
    # Managed package marker in path/name is rare; namespace in meta is uncommon
    # for LWC — namespaced bundles usually live under a namespace folder. Treat
    # explicit <namespace> if present.
    try:
        root = ET.fromstring(text_body)
    except ET.ParseError:
        return attrs, False

    targets: list[str] = []
    exposed = False
    label = bundle
    namespace = None
    for el in root.iter():
        name = _local(el.tag).lower()
        if name == "isexposed" and (el.text or "").strip().lower() == "true":
            exposed = True
        elif name == "masterlabel" and el.text:
            label = el.text.strip()
        elif name == "target" and el.text:
            targets.append(el.text.strip())
        elif name == "namespace" and el.text:
            namespace = el.text.strip()
    attrs = {
        "is_exposed": exposed,
        "targets": targets,
        "master_label": label,
        # Exposed LWC can still be wired by an admin without any static ref —
        # same R5 posture as externally invocable Apex.
        "is_entry_point": exposed,
    }
    return attrs, bool(namespace)


def _aura_cmp_path(bundle_dir: Path, bundle: str) -> Path | None:
    for cand in (bundle_dir / f"{bundle}.cmp", bundle_dir / f"{bundle}.app"):
        if cand.is_file():
            return cand
    # Fall back to any .cmp in the folder.
    for cand in bundle_dir.glob("*.cmp"):
        return cand
    return None


def _parse_aura_cmp(meta: Path | None, bundle: str) -> tuple[dict, bool]:
    attrs: dict = {
        "access": None,
        "interfaces": [],
        "is_exposed": False,
        "is_entry_point": False,
        "master_label": bundle,
    }
    if meta is None or not meta.is_file():
        return attrs, False
    try:
        text_body = meta.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return attrs, False

    # Namespace form: <namespace:Bundle — packaged if not c:
    namespaced = bool(re.search(
        r"<(?!aura:|c:)([A-Za-z]\w*):" + re.escape(bundle), text_body))

    access_m = re.search(
        r"""\baccess\s*=\s*["'](global|public|private)["']""",
        text_body, re.I)
    access = (access_m.group(1).lower() if access_m else "public")
    interfaces = re.findall(
        r"""\bimplements\s*=\s*["']([^"']+)["']""", text_body, re.I)
    iface_list: list[str] = []
    for blob in interfaces:
        iface_list.extend(p.strip() for p in blob.split(",") if p.strip())

    # global/public surfaces can still be composed in Experience Builder etc.
    exposed = access in ("global", "public")
    attrs = {
        "access": access,
        "interfaces": iface_list,
        "is_exposed": exposed,
        "is_entry_point": exposed,
        "master_label": bundle,
    }
    return attrs, namespaced


# ---------------------------------------------------------------------------
# Apex
# ---------------------------------------------------------------------------

async def _inventory_apex(s, run_id: str, sf: SalesforceClient) -> dict[str, int]:
    classes = await _collect(
        sf, run_id, "C00_inventory_apex_classes", "structural",
        # Body is fetched here so the narration stage can actually read the code
        # it is summarising. Without it the model infers purpose from the class
        # NAME alone, which produces confident-sounding guesswork.
        "SELECT Id, Name, NamespacePrefix, ApiVersion, Status, "
        "LengthWithoutComments, CreatedDate, LastModifiedDate, SymbolTable, Body "
        "FROM ApexClass",
        tooling=True,
    )
    triggers = await _collect(
        sf, run_id, "C00_inventory_apex_triggers", "structural",
        "SELECT Id, Name, NamespacePrefix, TableEnumOrId, Status, ApiVersion, "
        "UsageBeforeInsert, UsageAfterInsert, UsageBeforeUpdate, UsageAfterUpdate, "
        "UsageBeforeDelete, UsageAfterDelete, UsageAfterUndelete, "
        # Body for the same reason as ApexClass above: a trigger's
        # purpose lives in its handler calls, not in its name.
        "CreatedDate, LastModifiedDate, Body FROM ApexTrigger",
        tooling=True,
    )

    out = {"ApexClass": 0, "ApexClass(packaged, excluded)": 0,
           "ApexTrigger": 0, "ApexMethod": 0}

    for c in classes:
        if c.get("NamespacePrefix"):
            out["ApexClass(packaged, excluded)"] += 1
            continue
        st = c.get("SymbolTable") or {}
        is_test = _is_test_class(c["Name"], st)
        methods = st.get("methods") or []
        class_annotations = [
            a.get("name") for a in (st.get("annotations") or [])
        ]
        from app.pipeline.apex_kind import classify_apex_class_kind, list_apex_class_kinds

        method_attr_snapshots: list[dict] = []
        for mth in methods:
            mods = mth.get("modifiers") or []
            annotations = [a.get("name") for a in (mth.get("annotations") or [])]
            method_attr_snapshots.append({
                "modifiers": mods,
                "annotations": annotations,
            })
        is_entry = _is_class_entry_point(st, class_annotations, methods)
        kind_input = {
            "is_test": is_test,
            "interfaces": st.get("interfaces") or [],
            "annotations": class_annotations,
            "is_entry_point": is_entry,
        }
        kinds = list_apex_class_kinds(kind_input, method_attr_snapshots)
        class_attrs = {
            "api_version": c.get("ApiVersion"),
            "status": c.get("Status"),
            "loc": c.get("LengthWithoutComments"),
            # References from test code are Tier C, never Tier A. Without
            # this discriminator every field a test touches looks USED.
            "is_test": is_test,
            "interfaces": st.get("interfaces") or [],
            "parent_class": st.get("parentClass"),
            "method_count": len(methods),
            "annotations": class_annotations,
            # Class-level @RestResource / entry methods / entry interfaces:
            # callers may live outside the org, so zero internal refs must
            # never yield UNUSED on the class alone.
            "is_entry_point": is_entry,
            "apex_kind": kinds[0],
            "apex_kinds": kinds,
        }
        cid = await _insert_component(
            s, run_id, "ApexClass", c["Name"], label=c["Name"], sf_id=c["Id"],
            attrs=class_attrs,
            created=c.get("CreatedDate"), modified=c.get("LastModifiedDate"),
        )
        await _insert_aliases(s, run_id, cid,
                              al.apex_aliases(api_name=c["Name"], sf_id=c["Id"]))
        await _store_source(s, cid, c.get("Body"))
        out["ApexClass"] += 1

        # Methods come from SymbolTable, which is a reliable *inventory*. Its
        # `references` array is always empty (verified across all 28 methods in
        # this org) because it is compilation-unit scoped, so it is never a call
        # graph - that comes from offline AST parsing later.
        for mth in methods:
            mname = mth.get("name")
            if not mname:
                continue
            mods = mth.get("modifiers") or []
            annotations = [a.get("name") for a in (mth.get("annotations") or [])]
            mid = await _insert_component(
                s, run_id, "ApexMethod", f"{c['Name']}.{mname}",
                label=mname, parent_id=cid, parent_object=c["Name"],
                attrs={
                    "modifiers": mods,
                    "annotations": annotations,
                    "return_type": (mth.get("returnType") or None),
                    "arity": len(mth.get("parameters") or []),
                    "line": (mth.get("location") or {}).get("line"),
                    "is_test": is_test,
                    # Externally invocable => never UNUSED on the basis of zero
                    # Apex callers, because the caller lives outside the org.
                    "is_entry_point": _is_entry_point(mods, annotations, st, mname),
                },
            )
            await _insert_aliases(s, run_id, mid, al.method_aliases(
                class_name=c["Name"], method_name=mname))
            out["ApexMethod"] += 1

    for t in triggers:
        if t.get("NamespacePrefix"):
            continue
        events = [k[5:] for k in t if k.startswith("Usage") and t.get(k) is True]
        cid = await _insert_component(
            s, run_id, "ApexTrigger", t["Name"], label=t["Name"], sf_id=t["Id"],
            parent_object=t.get("TableEnumOrId"),
            attrs={"api_version": t.get("ApiVersion"), "status": t.get("Status"),
                   "on_object": t.get("TableEnumOrId"), "events": events},
            created=t.get("CreatedDate"), modified=t.get("LastModifiedDate"),
        )
        await _insert_aliases(s, run_id, cid, al.apex_aliases(
            api_name=t["Name"], sf_id=t["Id"], is_trigger=True))
        await _store_source(s, cid, t.get("Body"))
        out["ApexTrigger"] += 1

    return out


def _is_test_class(name: str, symbol_table: dict) -> bool:
    if name.lower().endswith("test") or name.lower().startswith("test"):
        return True
    for m in symbol_table.get("methods") or []:
        for a in m.get("annotations") or []:
            if (a.get("name") or "").lower() in ("istest", "testsetup"):
                return True
    return False


_ENTRY_ANNOTATIONS = {
    "auraenabled", "invocablemethod", "remoteaction", "future", "httpget",
    "httppost", "httpput", "httppatch", "httpdelete", "restresource",
    # Both are invoked by the test framework, never by application code, so
    # zero callers is their normal state rather than evidence of death.
    "istest", "testsetup",
}
#: Class-level annotations that expose the type itself (not a single method).
_CLASS_ENTRY_ANNOTATIONS = {
    "restresource",
}
_ENTRY_INTERFACES = {
    "database.batchable", "schedulable", "queueable",
    "messaging.inboundemailhandler", "callable", "comparable",
    "database.allowscallouts", "triggerhandler",
    # Salesforce SymbolTable usually prefixes these (System.Schedulable, …).
    "system.schedulable", "system.queueable", "system.callable",
    "system.comparable",
}
_ENTRY_METHOD_NAMES = {"execute", "start", "finish", "handleinboundemail", "call"}


def _iface_names(symbol_table: dict) -> set[str]:
    """Normalized interface names, with and without namespace / generics."""
    from app.pipeline.apex_kind import _norm_iface

    out: set[str] = set()
    for raw in symbol_table.get("interfaces") or []:
        full = (raw or "").lower().split("<", 1)[0].strip()
        if not full:
            continue
        out.add(full)
        out.add(_norm_iface(full))
    return out


def _ifaces_match_entry(symbol_table: dict) -> bool:
    ifaces = _iface_names(symbol_table)
    if ifaces & _ENTRY_INTERFACES:
        return True
    shorts = {i.rsplit(".", 1)[-1] for i in _ENTRY_INTERFACES}
    return bool(ifaces & shorts)


def _is_entry_point(
    modifiers: list[str], annotations: list[str | None], symbol_table: dict, name: str
) -> bool:
    """True when callers may live outside ordinary Apex call sites.

    Bare ``global`` is visibility, not an external surface — treating it as an
    entry point made every helper on a global class look like an API. Webservice
    and the invocable annotations remain entry signals.
    """
    mods = {m.lower() for m in modifiers}
    if "webservice" in mods:
        return True
    if any((a or "").lower() in _ENTRY_ANNOTATIONS for a in annotations):
        return True
    if _ifaces_match_entry(symbol_table) and name.lower() in _ENTRY_METHOD_NAMES:
        return True
    return False


def _is_class_entry_point(
    symbol_table: dict,
    class_annotations: list[str | None],
    methods: list[dict],
) -> bool:
    """True when the class (or any of its methods) is externally invocable.

    @RestResource lives on the class; HTTP verbs / AuraEnabled / etc. live on
    methods. Either one means callers may sit outside the org.
    """
    if any((a or "").lower() in _CLASS_ENTRY_ANNOTATIONS for a in class_annotations):
        return True
    if _ifaces_match_entry(symbol_table):
        return True
    for mth in methods:
        mname = mth.get("name")
        if not mname:
            continue
        mods = mth.get("modifiers") or []
        annotations = [a.get("name") for a in (mth.get("annotations") or [])]
        if _is_entry_point(mods, annotations, symbol_table, mname):
            return True
    return False


# ---------------------------------------------------------------------------
# Object resolution
# ---------------------------------------------------------------------------

def _looks_like_id(value: str) -> bool:
    """Custom-object parents arrive as 15/18-char ids; standard ones as names."""
    return (
        len(value) in (15, 18)
        and value[:3].isalnum()
        and not value.endswith("__c")
        and any(ch.isdigit() for ch in value)
        and "_" not in value
    )


async def _resolve_objects_by_id(
    sf: SalesforceClient, run_id: str, obj_ids: set[str]
) -> dict[str, dict]:
    """Resolve DurableIds to object metadata.

    ``WHERE DurableId IN (...)`` is honoured by EntityDefinition (verified),
    unlike ``LIKE``, and unlike enumeration which cannot reach custom objects at
    all. EntityDefinition's DurableId is 15 characters while CustomField returns
    18, so ids are truncated for the lookup and mapped back afterwards.
    """
    if not obj_ids:
        return {}
    short_to_full = {i[:15]: i for i in obj_ids}
    out: dict[str, dict] = {}
    shorts = sorted(short_to_full)
    for i in range(0, len(shorts), 50):
        chunk = shorts[i : i + 50]
        in_list = ", ".join(f"'{c}'" for c in chunk)
        rows = await _collect(
            sf, run_id, f"C00_resolve_objects_{i // 50}", "structural",
            "SELECT DurableId, QualifiedApiName, Label, KeyPrefix, "
            f"DeploymentStatus, LastModifiedDate FROM EntityDefinition "
            f"WHERE DurableId IN ({in_list})",
            tooling=True,
        )
        for r in rows:
            full = short_to_full.get(r["DurableId"], r["DurableId"])
            out[full] = r
    return out


async def _resolve_objects_by_name(
    sf: SalesforceClient, run_id: str, names: set[str]
) -> dict[str, dict]:
    if not names:
        return {}
    out: dict[str, dict] = {}
    ordered = sorted(names)
    for i in range(0, len(ordered), 50):
        chunk = ordered[i : i + 50]
        in_list = ", ".join(f"'{c}'" for c in chunk)
        rows = await _collect(
            sf, run_id, f"C00_resolve_std_objects_{i // 50}", "structural",
            "SELECT DurableId, QualifiedApiName, Label, KeyPrefix "
            f"FROM EntityDefinition WHERE QualifiedApiName IN ({in_list})",
            tooling=True,
        )
        for r in rows:
            out[r["QualifiedApiName"]] = r
    return out


async def _field_details(
    sf: SalesforceClient, run_id: str, id_to_object: dict, std_names: set[str]
) -> dict[str, dict]:
    """Per-field metadata, keyed ``Object.Field__c``.

    FieldDefinition requires a filter on the parent entity, so this is one query
    per object rather than one global query - batched through composite so the
    cost stays proportional to object count, not field count.
    """
    objects = [m["QualifiedApiName"] for m in id_to_object.values()] + sorted(std_names)
    soqls = [
        "SELECT QualifiedApiName, Label, DataType, RelationshipName, "
        "BusinessStatus, ComplianceGroup, IsCalculated, IsIndexed, IsNillable "
        f"FROM FieldDefinition WHERE EntityDefinition.QualifiedApiName = '{o}'"
        for o in objects
    ]
    results = await sf.batched_queries(soqls, tooling=True)
    out: dict[str, dict] = {}
    ok = 0
    for obj, res in zip(objects, results, strict=False):
        if not res:
            continue
        ok += 1
        for f in res.get("records", []):
            out[f"{obj}.{f['QualifiedApiName']}"] = f
    await _record_collector(
        run_id, "C00_field_details", "structural", "*",
        status="OK" if ok == len(objects) else "PARTIAL",
        method="FieldDefinition per object, batched via composite/batch",
        artifacts=len(objects), hits=len(out),
        note=None if ok == len(objects) else f"{len(objects) - ok} object(s) returned no metadata",
    )
    return out


# ---------------------------------------------------------------------------
# Persistence helpers
# ---------------------------------------------------------------------------

async def _insert_component(
    s, run_id: str, ctype: str, api_name: str, *, label=None, sf_id=None,
    parent_id=None, parent_object=None, in_scope=True, out_of_scope_reason=None,
    attrs: dict | None = None, created=None, modified=None,
) -> int:
    row = (await s.execute(text("""
        INSERT INTO components (run_id, ctype, api_name, label, sf_id, parent_id,
                                parent_object, in_scope, out_of_scope_reason,
                                attrs, created_date, last_modified_date)
        VALUES (:run, CAST(:ctype AS component_type), :api, :label, :sfid, :pid,
                :pobj, :scope, :reason, CAST(:attrs AS jsonb), :created, :modified)
        ON CONFLICT (run_id, ctype, api_name) DO UPDATE SET attrs = EXCLUDED.attrs
        RETURNING id
    """), {
        "run": run_id, "ctype": ctype, "api": api_name, "label": label,
        "sfid": sf_id, "pid": parent_id, "pobj": parent_object,
        "scope": in_scope, "reason": out_of_scope_reason,
        "attrs": _json(attrs or {}), "created": _dt(created), "modified": _dt(modified),
    })).one()
    return int(row.id)


async def _store_source(s, component_id: int, body: str | None) -> None:
    """Persist an Apex body so narration can read the code, not just its name."""
    if not body:
        return
    import hashlib
    await s.execute(text("""
        INSERT INTO component_source (component_id, body, body_sha256, line_count)
        VALUES (:c, :b, :h, :n)
        ON CONFLICT (component_id) DO UPDATE
          SET body = EXCLUDED.body, body_sha256 = EXCLUDED.body_sha256,
              line_count = EXCLUDED.line_count
    """), {"c": component_id, "b": body,
           "h": hashlib.sha256(body.encode()).hexdigest(),
           "n": body.count("\n") + 1})


async def _insert_aliases(s, run_id: str, component_id: int, items) -> None:
    if not items:
        return
    await s.execute(text("""
        INSERT INTO alias (run_id, component_id, alias_lc, alias_kind, generated_by)
        VALUES (:run, :cid, :a, :k, :g)
        ON CONFLICT (run_id, component_id, alias_lc, alias_kind) DO NOTHING
    """), [{"run": run_id, "cid": component_id, "a": a.alias_lc,
            "k": a.kind, "g": a.generated_by} for a in items])


async def _parent_map(s, run_id: str) -> dict[str, int]:
    """Object name -> component id, for linking fields to their parent.

    Must include StandardObject: a large share of custom fields live on standard
    objects (26 of 83 in the development org), and omitting them would leave
    those fields orphaned with no parent link.
    """
    rows = (await s.execute(text(
        "SELECT id, api_name FROM components "
        "WHERE run_id = :r AND ctype IN ('CustomObject','StandardObject')"
    ), {"r": run_id})).all()
    return {r.api_name.lower(): r.id for r in rows}


async def _collect(
    sf: SalesforceClient, run_id: str, collector_id: str, family: str,
    soql: str, *, tooling: bool = False,
) -> list[dict]:
    """Run a query and record a collector_run row whether it hits or misses.

    Every collector writes a row every time. That is what makes "we searched and
    found nothing" distinguishable from "we never checked" - and that distinction
    is the whole product.
    """
    try:
        rows = await sf.query(soql, tooling=tooling)
    except Exception as e:
        await _record_collector(run_id, collector_id, family, "*", status="ERROR",
                                method=soql[:400], artifacts=0, hits=0,
                                error=str(e)[:500])
        raise
    await _record_collector(run_id, collector_id, family, "*", status="OK",
                            method=soql[:400], artifacts=len(rows), hits=len(rows))
    return rows


async def _record_collector(
    run_id: str, collector_id: str, family: str, scope: str, *, status: str,
    method: str, artifacts: int, hits: int, error: str | None = None,
    note: str | None = None,
) -> None:
    async with session_scope() as s:
        await s.execute(text("""
            INSERT INTO collector_run (run_id, collector_id, collector_family,
                scope_key, status, method, query_text, artifacts_searched, hits,
                unavailable_reason, error_text)
            VALUES (:run, :cid, :fam, :scope, CAST(:st AS collector_status),
                    :method, :q, :arts, :hits, :note, :err)
            ON CONFLICT (run_id, collector_id, scope_key) DO UPDATE SET
                status = EXCLUDED.status, hits = EXCLUDED.hits,
                artifacts_searched = EXCLUDED.artifacts_searched
        """), {"run": run_id, "cid": collector_id, "fam": family, "scope": scope,
               "st": status, "method": method[:400], "q": method,
               "arts": artifacts, "hits": hits, "note": note, "err": error})


def _json(obj) -> str:
    import json
    return json.dumps(obj, default=str)


def _dt(value):
    """Parse a Salesforce datetime into a real ``datetime``.

    Salesforce emits ``2026-07-30T13:52:14.000+0000`` - a basic-format UTC offset
    without the colon that ``fromisoformat`` wants. asyncpg refuses strings for a
    timestamptz column, so this must be converted rather than passed through.

    Returns None on anything unparseable: a missing timestamp is a small loss of
    temporal evidence, whereas raising here would abort the whole inventory.
    """
    if value is None or isinstance(value, datetime):
        return value
    if not isinstance(value, str) or not value:
        return None
    text_val = value.strip()
    # +0000 -> +00:00 ; also tolerate a trailing Z.
    if text_val.endswith("Z"):
        text_val = text_val[:-1] + "+00:00"
    elif len(text_val) > 5 and text_val[-5] in "+-" and text_val[-3] != ":":
        text_val = text_val[:-2] + ":" + text_val[-2:]
    try:
        return datetime.fromisoformat(text_val)
    except ValueError:
        log.debug("unparseable_datetime", value=value)
        return None
