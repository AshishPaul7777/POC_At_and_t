"""Event Monitoring evidence for C40 (runtime telemetry).

Loads optional local CSVs under ``backend/event_data`` (or ``EVENT_LOG_DIR``)
and downloads org ``EventLogFile`` types that map to **judged** in-scope
components:

  ApexClass / Method / Trigger  — ApexExecution, ApexTrigger, ApexCallout,
                                   ApexRestApi
  Lightning / Aura bundles     — LightningInteraction (``c:Name``)
  CustomObject                 — LightningPageView / Interaction entity,
                                   RestApi ENTITY_NAME, UniqueQuery FROM,
                                   DatabaseSave KEY_PREFIX
  CustomField                  — RestApi / UniqueQuery SOQL ``*__c`` tokens

Types that do not name those components (Login, URI, FlowExecution, bare API,
AuraRequest platform noise, etc.) are intentionally skipped.
"""

from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import unquote

import structlog

from app.config import REPO_ROOT, get_settings

if TYPE_CHECKING:
    from app.salesforce.client import SalesforceClient

log = structlog.get_logger()

DEFAULT_EVENT_LOG_DIR = REPO_ROOT / "backend" / "event_data"

# Only types that can attach to judged inventory keys.
ORG_EVENT_TYPES = (
    "ApexExecution",
    "ApexTrigger",
    "ApexCallout",
    "ApexRestApi",
    "LightningInteraction",
    "LightningPageView",
    "RestApi",
    "UniqueQuery",
    "DatabaseSave",
)

_CUSTOM_FIELD_RE = re.compile(r"\b([A-Za-z][A-Za-z0-9_]*(?:__[cC]))\b")
_FROM_OBJECT_RE = re.compile(
    r"\bFROM\s+([A-Za-z][A-Za-z0-9_]*(?:__[cC])?)\b", re.IGNORECASE)
_UI_COMPONENT_RE = re.compile(
    r"(?:^|[;,\s])(?:c:|c/|aura:|markup://c:|markup://aura:)([A-Za-z][A-Za-z0-9_]*)",
    re.IGNORECASE)
_REST_SUFFIX_RE = re.compile(
    r"(restservice|restresource|resource|controller|service)$", re.IGNORECASE)


@dataclass
class EventLogHit:
    """Aggregated event-log activity for one component key."""
    executions: int = 0
    triggers: int = 0
    callouts: int = 0
    ui_views: int = 0
    object_accesses: int = 0
    field_refs: int = 0
    rest_hits: int = 0
    last_seen: str | None = None
    samples: list[str] = field(default_factory=list)

    @property
    def total(self) -> int:
        return (
            self.executions + self.triggers + self.callouts
            + self.ui_views + self.object_accesses + self.field_refs
            + self.rest_hits
        )

    def note(self, kind: str, when: str | None, sample: str) -> None:
        if kind == "execution":
            self.executions += 1
        elif kind == "trigger":
            self.triggers += 1
        elif kind == "callout":
            self.callouts += 1
        elif kind == "ui":
            self.ui_views += 1
        elif kind == "object":
            self.object_accesses += 1
        elif kind == "field":
            self.field_refs += 1
        elif kind == "rest":
            self.rest_hits += 1
        if when and (not self.last_seen or when > self.last_seen):
            self.last_seen = when
        if sample and len(self.samples) < 3 and sample not in self.samples:
            self.samples.append(sample)

    def merge(self, other: EventLogHit) -> None:
        self.executions += other.executions
        self.triggers += other.triggers
        self.callouts += other.callouts
        self.ui_views += other.ui_views
        self.object_accesses += other.object_accesses
        self.field_refs += other.field_refs
        self.rest_hits += other.rest_hits
        if other.last_seen and (not self.last_seen or other.last_seen > self.last_seen):
            self.last_seen = other.last_seen
        for s in other.samples:
            if len(self.samples) >= 3:
                break
            if s not in self.samples:
                self.samples.append(s)


@dataclass
class EventLogIndex:
    """Lookup tables keyed by lowercased API name / prefix / path."""
    by_class: dict[str, EventLogHit] = field(default_factory=dict)
    by_method: dict[str, EventLogHit] = field(default_factory=dict)
    by_trigger: dict[str, EventLogHit] = field(default_factory=dict)
    by_ui: dict[str, EventLogHit] = field(default_factory=dict)
    by_object: dict[str, EventLogHit] = field(default_factory=dict)
    by_field: dict[str, EventLogHit] = field(default_factory=dict)
    by_key_prefix: dict[str, EventLogHit] = field(default_factory=dict)
    by_rest_path: dict[str, EventLogHit] = field(default_factory=dict)
    files_read: list[str] = field(default_factory=list)
    rows: int = 0
    source: str = "none"  # local_event_log | event_log_file | mixed

    def hit_for_class(self, api_name: str) -> EventLogHit | None:
        return self.by_class.get(api_name.lower())

    def hit_for_method(self, api_name: str) -> EventLogHit | None:
        return self.by_method.get(api_name.lower())

    def hit_for_trigger(self, api_name: str) -> EventLogHit | None:
        return self.by_trigger.get(api_name.lower())

    def hit_for_ui(self, api_name: str) -> EventLogHit | None:
        return self.by_ui.get(api_name.lower())

    def hit_for_object(self, api_name: str) -> EventLogHit | None:
        return self.by_object.get(api_name.lower())

    def hit_for_field(self, api_name: str, parent_object: str | None = None) -> EventLogHit | None:
        key = api_name.lower()
        hit = self.by_field.get(key)
        if hit:
            return hit
        if parent_object:
            return self.by_field.get(f"{parent_object.lower()}.{key}")
        return None

    def hit_for_key_prefix(self, prefix: str) -> EventLogHit | None:
        return self.by_key_prefix.get((prefix or "").lower())

    def hit_for_apex_class(self, api_name: str) -> EventLogHit | None:
        """Class execution/callout, or REST path derived from the class name."""
        hit = self.hit_for_class(api_name)
        if hit and hit.total:
            return hit
        for key in rest_path_keys_for_class(api_name):
            rh = self.by_rest_path.get(key)
            if rh and rh.total:
                return rh
        return hit

    def merge(self, other: EventLogIndex) -> None:
        for bucket_name in (
            "by_class", "by_method", "by_trigger", "by_ui", "by_object",
            "by_field", "by_key_prefix", "by_rest_path",
        ):
            src = getattr(other, bucket_name)
            dst = getattr(self, bucket_name)
            for key, hit in src.items():
                _ensure_hit(dst, key).merge(hit)
        self.rows += other.rows
        for name in other.files_read:
            if name not in self.files_read:
                self.files_read.append(name)
        if self.source == "none":
            self.source = other.source
        elif other.source != "none" and other.source != self.source:
            self.source = "mixed"


def _ensure_hit(bucket: dict[str, EventLogHit], key: str) -> EventLogHit:
    hit = bucket.get(key)
    if not hit:
        hit = EventLogHit()
        bucket[key] = hit
    return hit


def rest_path_keys_for_class(api_name: str) -> list[str]:
    """Map ``CustomerOrderRestService`` → ``customerorderrestservice``, ``customerorder``."""
    n = (api_name or "").lower().strip()
    if not n:
        return []
    keys = [n]
    stripped = _REST_SUFFIX_RE.sub("", n)
    if stripped and stripped != n:
        keys.append(stripped)
    return keys


def event_log_dir() -> Path:
    s = get_settings()
    raw = getattr(s, "event_log_dir", None)
    if raw:
        p = Path(raw)
        return p if p.is_absolute() else (REPO_ROOT / p)
    return DEFAULT_EVENT_LOG_DIR


def load_event_logs(directory: Path | None = None) -> EventLogIndex:
    """Parse every ``*.csv`` in the event-log directory into an index."""
    root = directory or event_log_dir()
    idx = EventLogIndex(source="local_event_log")
    if not root.is_dir():
        log.info("event_logs_absent", path=str(root))
        return EventLogIndex()

    for path in sorted(root.glob("*.csv")):
        try:
            text = path.read_text(encoding="utf-8-sig")
            _ingest_csv_text(text, idx, label=path.name)
            idx.files_read.append(path.name)
        except Exception as e:
            log.warning("event_log_read_failed", file=path.name, error=str(e)[:160])
    if not idx.files_read:
        return EventLogIndex()
    log.info("event_logs_loaded", files=len(idx.files_read), rows=idx.rows,
             classes=len(idx.by_class), methods=len(idx.by_method),
             triggers=len(idx.by_trigger), ui=len(idx.by_ui),
             objects=len(idx.by_object), fields=len(idx.by_field))
    return idx


async def fetch_event_logs_from_org(
    sf: SalesforceClient,
    *,
    lookback_days: int | None = None,
    max_files_per_type: int | None = None,
    event_types: tuple[str, ...] = ORG_EVENT_TYPES,
) -> EventLogIndex:
    """Download recent in-scope EventLogFile CSVs and index runtime evidence."""
    s = get_settings()
    days = lookback_days if lookback_days is not None else s.event_log_lookback_days
    per_type = (
        max_files_per_type if max_files_per_type is not None
        else s.event_log_max_files_per_type
    )
    idx = EventLogIndex(source="event_log_file")
    since = (datetime.now(UTC) - timedelta(days=max(1, days))).strftime("%Y-%m-%dT%H:%M:%SZ")

    for etype in event_types:
        files = await _query_event_files(sf, etype, since=since, limit=per_type)
        for row in files:
            eid = row.get("Id") or ""
            label = f"EventLogFile:{etype}:{eid}"
            try:
                text = await sf.get_event_log_file(eid)
                _ingest_csv_text(text, idx, label=label, event_hint=etype)
                idx.files_read.append(label)
            except Exception as e:
                log.warning("event_log_file_download_failed", id=eid, event=etype,
                            error=str(e)[:160])

    log.info("event_logs_from_org", files=len(idx.files_read), rows=idx.rows,
             classes=len(idx.by_class), methods=len(idx.by_method),
             triggers=len(idx.by_trigger), ui=len(idx.by_ui),
             objects=len(idx.by_object), fields=len(idx.by_field),
             rest_paths=len(idx.by_rest_path), prefixes=len(idx.by_key_prefix))
    return idx if idx.files_read else EventLogIndex()


async def _query_event_files(
    sf: SalesforceClient, etype: str, *, since: str, limit: int
) -> list[dict]:
    lim = max(1, limit)
    soql = (
        "SELECT Id, EventType, LogDate, LogFileLength "
        f"FROM EventLogFile WHERE EventType = '{etype}' "
        f"AND LogDate >= {since} "
        "ORDER BY LogDate DESC "
        f"LIMIT {lim}"
    )
    try:
        files = await sf.query(soql)
    except Exception as e:
        log.warning("event_log_file_query_failed", event=etype, error=str(e)[:200])
        return []
    if files:
        return files
    try:
        return await sf.query(
            "SELECT Id, EventType, LogDate, LogFileLength "
            f"FROM EventLogFile WHERE EventType = '{etype}' "
            "ORDER BY LogDate DESC "
            f"LIMIT {min(lim, 3)}"
        )
    except Exception as e:
        log.warning("event_log_file_query_failed", event=etype, error=str(e)[:200])
        return []


def _field_map(fieldnames: list[str]) -> dict[str, str]:
    return {h.strip().upper(): h for h in fieldnames}


def _get(row: dict, fields: dict[str, str], *candidates: str) -> str:
    for col in candidates:
        src = fields.get(col)
        if src:
            val = (row.get(src) or "").strip()
            if val:
                return val
    return ""


def _ingest_csv_text(
    text: str,
    idx: EventLogIndex,
    *,
    label: str = "",
    event_hint: str = "",
) -> None:
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        return
    fields = _field_map(list(reader.fieldnames))
    hint = (event_hint or label).lower()

    for row in reader:
        event = (_get(row, fields, "EVENT_TYPE") or hint).lower()
        when = _get(row, fields, "TIMESTAMP_DERIVED", "TIMESTAMP", "LOGDATE") or None
        idx.rows += 1

        if "apexexecution" in event:
            _ingest_apex_execution(row, fields, idx, when)
        elif "apextrigger" in event and "apexexecution" not in event:
            trig = _get(row, fields, "TRIGGER_NAME")
            if not trig:
                continue
            op = _get(row, fields, "OPERATION_TYPE", "TRIGGER_TYPE")
            _ensure_hit(idx.by_trigger, trig.lower()).note(
                "trigger", when, f"{trig}:{op}" if op else trig)
        elif "apexcallout" in event:
            cls = _get(row, fields, "APEX_CLASS_NAME", "CLASS_NAME")
            meth = _get(row, fields, "APEX_METHOD_NAME", "METHOD_NAME")
            if not cls:
                continue
            uri = _get(row, fields, "URI", "URL")
            sample = f"{cls}.{meth}" if meth else cls
            if uri:
                sample = f"{sample} → {uri[:60]}"
            _ensure_hit(idx.by_class, cls.lower()).note("callout", when, sample)
            if meth:
                _ensure_hit(idx.by_method, f"{cls}.{meth}".lower()).note(
                    "callout", when, sample)
        elif "apexrestapi" in event:
            uri = _get(row, fields, "URI")
            path = _rest_path_token(uri)
            if path:
                _ensure_hit(idx.by_rest_path, path).note(
                    "rest", when, uri[:80] or path)
        elif "lightninginteraction" in event:
            _ingest_lightning_interaction(row, fields, idx, when)
        elif "lightningpageview" in event:
            entity = _get(row, fields, "PAGE_ENTITY_TYPE")
            if _is_custom_object(entity):
                _ensure_hit(idx.by_object, entity.lower()).note(
                    "object", when, f"page:{entity}")
        elif "restapi" in event and "apexrest" not in event:
            entity = _get(row, fields, "ENTITY_NAME")
            if _is_custom_object(entity):
                _ensure_hit(idx.by_object, entity.lower()).note(
                    "object", when, f"rest:{entity}")
            query = _get(row, fields, "QUERY")
            if query:
                _ingest_soql_tokens(query, idx, when, sample_prefix="restapi")
        elif "uniquequery" in event:
            query = _get(row, fields, "QUERY_IDENTIFIER", "QUERY")
            if query:
                _ingest_soql_tokens(query, idx, when, sample_prefix="uniquequery")
        elif "databasesave" in event:
            prefix = _get(row, fields, "KEY_PREFIX")
            if prefix:
                dml = _get(row, fields, "DML_TYPE")
                _ensure_hit(idx.by_key_prefix, prefix.lower()).note(
                    "object", when, f"dml:{dml or '?'}:{prefix}")
        else:
            # Local / unknown CSV: try classic Apex columns.
            cls = _get(row, fields, "APEX_CLASS_NAME", "CLASS_NAME")
            trig = _get(row, fields, "TRIGGER_NAME")
            if cls:
                _ensure_hit(idx.by_class, cls.lower()).note("execution", when, cls)
            if trig:
                _ensure_hit(idx.by_trigger, trig.lower()).note("trigger", when, trig)


def _ingest_apex_execution(
    row: dict, fields: dict[str, str], idx: EventLogIndex, when: str | None
) -> None:
    cls = _get(row, fields, "APEX_CLASS_NAME", "CLASS_NAME")
    meth = _get(row, fields, "APEX_METHOD_NAME", "METHOD_NAME")
    if not cls:
        entry = _get(row, fields, "ENTRY_POINT")
        kind, name, meth = _parse_entry_point(entry)
        if kind == "trigger" and name:
            op = meth
            _ensure_hit(idx.by_trigger, name.lower()).note(
                "trigger", when, f"{name}:{op}" if op else name)
            return
        if kind != "class" or not name:
            return
        cls, meth = name, meth
    _ensure_hit(idx.by_class, cls.lower()).note(
        "execution", when, f"{cls}.{meth}" if meth else cls)
    if meth:
        _ensure_hit(idx.by_method, f"{cls}.{meth}".lower()).note(
            "execution", when, f"{cls}.{meth}")


def _ingest_lightning_interaction(
    row: dict, fields: dict[str, str], idx: EventLogIndex, when: str | None
) -> None:
    entity = _get(row, fields, "PAGE_ENTITY_TYPE")
    if _is_custom_object(entity):
        _ensure_hit(idx.by_object, entity.lower()).note(
            "object", when, f"interaction:{entity}")
    # COMPONENT_NAME may be "Loyalty_Membership__c" or "force:x;c:myBundle"
    for col in ("COMPONENT_NAME", "TARGET_UI_ELEMENT", "PARENT_UI_ELEMENT"):
        raw = _get(row, fields, col)
        if not raw:
            continue
        if _is_custom_object(raw) and ";" not in raw and ":" not in raw:
            _ensure_hit(idx.by_object, raw.lower()).note(
                "object", when, f"{col.lower()}:{raw}")
        for name in _extract_ui_components(raw):
            _ensure_hit(idx.by_ui, name.lower()).note(
                "ui", when, f"{col.lower()}:{name}")


def _ingest_soql_tokens(
    query: str, idx: EventLogIndex, when: str | None, *, sample_prefix: str
) -> None:
    text = unquote(query or "").strip().strip('"')
    if not text:
        return
    for m in _FROM_OBJECT_RE.finditer(text):
        obj = m.group(1)
        if _is_custom_object(obj) or obj.lower().endswith("__c"):
            _ensure_hit(idx.by_object, obj.lower()).note(
                "object", when, f"{sample_prefix}:FROM {obj}")
    for m in _CUSTOM_FIELD_RE.finditer(text):
        fld = m.group(1)
        # Skip relationship fields ending __r (regex already requires __c).
        _ensure_hit(idx.by_field, fld.lower()).note(
            "field", when, f"{sample_prefix}:{fld}")


def _is_custom_object(name: str) -> bool:
    n = (name or "").strip()
    if not n or n.lower() in {"contact", "account", "user", "userfavorite", "organization"}:
        return False
    return n.endswith("__c") or n.endswith("__C")


def _extract_ui_components(raw: str) -> list[str]:
    found: list[str] = []
    for m in _UI_COMPONENT_RE.finditer(raw or ""):
        name = m.group(1)
        if name and name.lower() not in found:
            found.append(name)
    return found


def _rest_path_token(uri: str) -> str:
    """``/CustomerOrder/`` → ``customerorder``; ignore /services/data paths."""
    u = unquote(uri or "").strip()
    if not u or "/services/" in u.lower():
        return ""
    parts = [p for p in u.split("/") if p and not p.startswith("{")]
    if not parts:
        return ""
    token = parts[0]
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", token):
        return ""
    return token.lower()


# Noise / non-component entry points in ApexExecution EVENT_TYPE rows.
_SKIP_ENTRY_POINTS = frozenset({
    "execute_anonymous_apex",
    "triggers",
    "anonymousapex",
})


def _parse_entry_point(entry: str) -> tuple[str, str, str]:
    """Classify an ApexExecution ENTRY_POINT.

    Returns ``(kind, name, detail)`` where kind is ``class``, ``trigger``, or
    ``skip``. For classes, ``detail`` is the method name when present.
    """
    raw = (entry or "").strip()
    if not raw:
        return "skip", "", ""
    low = raw.lower()
    if low in _SKIP_ENTRY_POINTS or low.startswith("vf-") or low.startswith("/apex/"):
        return "skip", "", ""

    if " trigger event " in low and " on " in low:
        name = raw.split(" on ", 1)[0].strip()
        detail = raw.lower().split(" trigger event ", 1)[-1].strip()
        return ("trigger", name, detail) if name else ("skip", "", "")

    cleaned = raw.rstrip("()")
    if "." in cleaned:
        left, right = cleaned.rsplit(".", 1)
        left, right = left.strip(), right.strip()
        if left and right and " " not in left and " " not in right:
            return "class", left, right

    if " " not in raw and "/" not in raw:
        return "class", raw, ""
    return "skip", "", ""


def payload_from_hit(hit: EventLogHit, *, source: str = "local_event_log") -> dict:
    return {
        "event_log_executions": hit.executions,
        "event_log_triggers": hit.triggers,
        "event_log_callouts": hit.callouts,
        "event_log_ui_views": hit.ui_views,
        "event_log_object_accesses": hit.object_accesses,
        "event_log_field_refs": hit.field_refs,
        "event_log_rest_hits": hit.rest_hits,
        "event_log_last_seen": hit.last_seen,
        "event_log_samples": list(hit.samples),
        "source": source,
    }
