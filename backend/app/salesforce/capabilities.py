"""Per-org capability discovery.

Every value here was, at some point, a constant measured against a single
Developer Edition org. That was a latent correctness bug: the same code pointed
at an Enterprise org would have mis-sized its API budget by ~7x, and pointed at
an org with Event Monitoring licensed would have ignored the strongest usage
signal available.

So the rule is: **discover, do not assume.** And crucially, distinguish three
states rather than two -

    available        -> use it
    unavailable      -> collector reports NOT_APPLICABLE, lowering coverage
    not yet probed   -> unknown, which is NOT the same as unavailable

That third state matters. Treating "we never checked" as "there is nothing here"
is precisely how a live component becomes a deletion candidate.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field

import structlog
from sqlalchemy import text

from app.db.session import session_scope
from app.salesforce.client import SalesforceClient
from app.salesforce.routing import DATA_ONLY, TOOLING_ONLY, Endpoint, endpoint_for

log = structlog.get_logger()

#: Conservative fallback if compression cannot be measured. Chosen low on
#: purpose: underestimating compression overstates cost, which makes a run look
#: expensive. Overestimating it would exhaust the org's budget mid-run.
FALLBACK_COMPRESSION = 1.0


@dataclass
class OrgCapabilities:
    org_id: str
    alias: str
    instance_url: str
    api_version: str
    edition: str | None = None
    org_name: str | None = None
    is_sandbox: bool | None = None

    api_limit_max: int | None = None
    api_limit_remaining: int | None = None

    composite_compression: float | None = None
    composite_max_queries: int | None = None

    has_event_monitoring: bool | None = None
    has_dependency_api: bool | None = None
    dependency_api_types: list[str] = field(default_factory=list)
    has_field_history: bool | None = None
    has_code_coverage: bool | None = None

    routing_overrides: dict[str, str] = field(default_factory=dict)
    unavailable_objects: list[str] = field(default_factory=list)

    run_as_username: str | None = None
    run_as_is_admin: bool | None = None

    probe_api_cost: int = 0
    probe_notes: dict = field(default_factory=dict)

    # -- derived helpers -------------------------------------------------------

    def compression(self) -> float:
        return self.composite_compression or FALLBACK_COMPRESSION

    def endpoint_for(self, sobject: str) -> Endpoint:
        """Routing, with this org's discovered corrections applied."""
        if (override := self.routing_overrides.get(sobject)):
            return Endpoint(override)
        return endpoint_for(sobject)

    def is_available(self, sobject: str) -> bool:
        return sobject not in self.unavailable_objects

    def coverage_caveats(self) -> list[str]:
        """Plain-language limitations, for the report's Coverage section.

        Generated from what was actually observed so the report states its own
        blind spots instead of implying completeness.
        """
        out: list[str] = []
        local_event_logs = _local_event_log_files()
        if self.has_event_monitoring is False:
            if local_event_logs:
                out.append(
                    "Salesforce Event Monitoring returned no EventLogFile rows. "
                    "Local event-log CSVs ("
                    + ", ".join(local_event_logs[:5])
                    + (f" +{len(local_event_logs) - 5} more"
                       if len(local_event_logs) > 5 else "")
                    + ") are used for Apex execution, trigger, and callout "
                    "evidence. Other Event Monitoring types (API, URI, Report, "
                    "page views) remain unobservable."
                )
            else:
                out.append(
                    "Event Monitoring is unavailable in this org, so runtime access "
                    "(API reads, page views, report exports) is unobservable. "
                    "Components whose only caller is an external integration cannot "
                    "be proven unused here."
                )
        elif self.has_event_monitoring is True:
            out.append(
                "Event Monitoring EventLogFile rows are downloaded for types that "
                "map to judged components (ApexExecution/Trigger/Callout, "
                "ApexRestApi, LightningInteraction/PageView, RestApi, "
                "UniqueQuery, DatabaseSave) and used as optional Tier-B runtime "
                "evidence. Other event types (Login, URI, Flow, etc.) are skipped."
            )
        elif self.has_event_monitoring is None:
            out.append("Event Monitoring availability was not determined.")
        if self.has_dependency_api is False:
            out.append(
                "The Dependency API did not respond in this org; declarative "
                "dependency cross-checking was skipped entirely."
            )
        if self.has_field_history is False:
            out.append(
                "No field-history tracking was found, so per-field write recency "
                "is unavailable. A field populated years ago and dead since "
                "cannot be distinguished from one written yesterday."
            )
        if self.has_code_coverage is False:
            out.append("Apex code-coverage data is unavailable (no test run recorded).")
        if self.unavailable_objects:
            out.append(
                f"{len(self.unavailable_objects)} metadata object(s) could not be "
                f"queried by the configured user: "
                f"{', '.join(sorted(self.unavailable_objects)[:8])}"
                + (" …" if len(self.unavailable_objects) > 8 else "")
            )
        if self.run_as_is_admin is False:
            out.append(
                f"The API user ({self.run_as_username}) is not a System "
                "Administrator. Coverage is bounded by that user's permissions, "
                "and anything invisible to it will look unused."
            )
        return out


def _local_event_log_files() -> list[str]:
    """CSV filenames under EVENT_LOG_DIR / backend/event_data, if any."""
    try:
        from app.pipeline.event_logs import event_log_dir
        root = event_log_dir()
        if not root.is_dir():
            return []
        return sorted(p.name for p in root.glob("*.csv"))
    except Exception:
        return []


async def probe(sf: SalesforceClient, alias: str, *, deep: bool = False) -> OrgCapabilities:
    """Discover this org's capabilities.

    Costs roughly 40-60 API calls, dominated by the routing sweep. ``deep=True``
    additionally attempts to measure composite compression, which costs another
    ~90 calls and is usually not worth it - see ``_measure_composite``.
    """
    before = sf.governor.snapshot().consumed_by_run

    me = await sf.identity()
    org_id = me.get("organization_id", "unknown")
    caps = OrgCapabilities(
        org_id=org_id,
        alias=alias,
        instance_url=sf._s.sf_instance_url,
        api_version=sf._s.sf_api_version,
        run_as_username=me.get("preferred_username"),
    )

    # --- edition and budget: read, never inferred from the URL ---------------
    try:
        rows = await sf.query(
            "SELECT Id, Name, OrganizationType, IsSandbox, InstanceName "
            "FROM Organization LIMIT 1"
        )
        if rows:
            caps.edition = rows[0].get("OrganizationType")
            caps.org_name = rows[0].get("Name")
            caps.is_sandbox = rows[0].get("IsSandbox")
    except Exception as e:
        caps.probe_notes["organization"] = str(e)[:200]

    try:
        caps.api_limit_remaining, caps.api_limit_max = await sf.daily_api_remaining()
    except Exception as e:
        caps.probe_notes["limits"] = str(e)[:200]

    # --- is the API user an administrator? -----------------------------------
    # Coverage is bounded by this user. A non-admin silently narrows what the
    # analysis can see, which is indistinguishable from components being unused.
    try:
        rows = await sf.query(
            "SELECT Id, Profile.Name, Profile.PermissionsModifyAllData "
            f"FROM User WHERE Username = '{_esc(caps.run_as_username or '')}' LIMIT 1"
        )
        if rows:
            prof = rows[0].get("Profile") or {}
            caps.run_as_is_admin = bool(prof.get("PermissionsModifyAllData"))
            caps.probe_notes["run_as_profile"] = prof.get("Name")
    except Exception as e:
        caps.probe_notes["run_as_profile_error"] = str(e)[:200]

    # --- composite compression ----------------------------------------------
    caps.composite_compression, caps.composite_max_queries = await _measure_composite(
        sf, caps, deep=deep)

    # --- feature availability ------------------------------------------------
    caps.has_event_monitoring = await _probe_event_monitoring(sf, caps)
    caps.has_dependency_api, caps.dependency_api_types = await _probe_dependency_api(sf, caps)
    caps.has_code_coverage = await _probe_simple(
        sf, "SELECT Id FROM ApexCodeCoverageAggregate LIMIT 1", tooling=True)
    caps.has_field_history = await _probe_field_history(sf, caps)

    # --- routing verification -----------------------------------------------
    caps.routing_overrides, caps.unavailable_objects = await _verify_routing(sf, caps)

    caps.probe_api_cost = sf.governor.snapshot().consumed_by_run - before
    log.info("org_capabilities_probed", org_id=org_id, alias=alias,
             edition=caps.edition, compression=caps.composite_compression,
             event_monitoring=caps.has_event_monitoring,
             dependency_api=caps.has_dependency_api,
             api_cost=caps.probe_api_cost)
    return caps


async def _measure_composite(sf: SalesforceClient, caps: OrgCapabilities,
                             *, deep: bool = False
                             ) -> tuple[float | None, int | None]:
    """Measure this org's real composite/batch compression.

    Contrary to most documentation a composite call does not cost one request, so
    the ratio has to be observed. Differencing two ``/limits`` reads is useless -
    the rolling window releases calls while other clients consume them, and reads
    can move backwards - so the header on the response itself is used, comparing
    a large batch against a small one.
    """
    import re
    from urllib.parse import quote

    probe_q = quote("SELECT Id FROM Organization LIMIT 1")
    prefix = f"v{caps.api_version}/query"

    async def usage_after(n: int) -> int | None:
        subs = [{"method": "GET", "url": f"{prefix}?q={probe_q}"} for _ in range(n)]
        assert sf._http is not None
        token = await sf._tokens.token()
        body = json.dumps({"batchRequests": subs}).encode()
        async with sf.governor.lease(cost=sf.governor.composite_cost(n)) as lease:
            r = await sf._http.request(
                "POST", f"{token.instance_url}{caps_data_path(caps, 'composite/batch')}",
                headers={"Authorization": f"Bearer {token.value}",
                         "Content-Type": "application/json"},
                content=body,
            )
            hdr = r.headers.get("Sforce-Limit-Info")
            lease.observe(hdr)
        if r.status_code >= 300 or not hdr:
            return None
        m = re.search(r"api-usage=(\d+)/", hdr)
        return int(m.group(1)) if m else None

    # Deliberately NOT measured precisely, and that is the considered choice.
    #
    # Attempts to measure it here produced 9.0x on one run and 0.36x on the next
    # - the second implying composite is *worse* than individual calls, which is
    # impossible. The org's API counter moves under other clients, the rolling
    # 24h window releases calls mid-measurement, and the probe's own traffic
    # pollutes the sample. Getting a trustworthy figure took ~800 calls of
    # repeated differential sampling during development; spending that on every
    # org, for a number that only feeds a cost *estimate*, is a bad trade.
    #
    # It matters less than it appears: actual budget tracking reconciles against
    # the Sforce-Limit-Info header on every response, so a wrong ratio makes the
    # pre-flight plan pessimistic, never the live accounting wrong.
    #
    # So: assume the conservative 1:1 (each subrequest costs a full call) and
    # accept a measured value only when it is both plausible and stable. Batching
    # is still worth doing for latency regardless of the accounting.
    if not deep:
        caps.probe_notes["composite"] = (
            f"assumed {FALLBACK_COMPRESSION}x (1 call per subrequest). Not measured: "
            "the API counter is too noisy for a cheap measurement to be "
            "trustworthy, and this figure only affects the pre-flight estimate - "
            "live budget accounting reads the Sforce-Limit-Info header. "
            "Pass deep=True to measure."
        )
        return FALLBACK_COMPRESSION, 25

    ROUNDS = 5
    try:
        samples: list[float] = []
        for _ in range(ROUNDS):
            a1, a2 = await usage_after(2), await usage_after(2)
            b1, b2 = await usage_after(20), await usage_after(20)
            if None in (a1, a2, b1, b2):
                continue
            small, large = a2 - a1, b2 - b1
            if large < small:
                continue  # rolling window released calls; sample is meaningless
            samples.append((large - small) / 18.0)

        # Require agreement before believing any of it. A spread wider than 2x
        # between best and worst means we measured noise, not cost.
        plausible = [s for s in samples if 0.02 <= s <= 1.0]
        if len(plausible) < 3 or max(plausible) > 2 * min(plausible):
            caps.probe_notes["composite"] = {
                "result": "unstable; falling back to conservative 1:1",
                "samples": [round(s, 3) for s in samples],
            }
            return FALLBACK_COMPRESSION, 25

        worst = max(plausible)  # lowest compression = safest estimate
        caps.probe_notes["composite"] = {
            "samples": [round(s, 3) for s in plausible],
            "used": round(worst, 3),
            "rule": "worst plausible sample; overestimating compression would "
                    "exhaust the org's budget mid-run",
        }
        return round(min(1.0 / worst, 25.0), 2), 25
    except Exception as e:
        caps.probe_notes["composite_error"] = str(e)[:200]
        return FALLBACK_COMPRESSION, 25


def caps_data_path(caps: OrgCapabilities, path: str) -> str:
    return f"/services/data/v{caps.api_version}/{path.lstrip('/')}"


async def _probe_event_monitoring(sf: SalesforceClient, caps: OrgCapabilities) -> bool | None:
    """Available means the object is queryable AND actually returns log rows.

    A queryable-but-empty EventLogFile is the common case without a Shield or
    Event Monitoring licence, and it is important not to record that as
    "available": downstream, an available-but-empty source reads as evidence of
    non-use rather than as a blind spot.
    """
    try:
        rows = await sf.query("SELECT Id, EventType, LogDate FROM EventLogFile LIMIT 5")
        if not rows:
            caps.probe_notes["event_monitoring"] = (
                "EventLogFile is queryable but returned zero rows - Event "
                "Monitoring is not licensed, or retention has elapsed"
            )
            return False
        caps.probe_notes["event_monitoring"] = f"{len(rows)} log file(s) visible"
        return True
    except Exception as e:
        caps.probe_notes["event_monitoring"] = f"not queryable: {str(e)[:160]}"
        return False


async def refresh_event_monitoring(
    sf: SalesforceClient, caps: OrgCapabilities
) -> bool:
    """Re-probe EventLogFile and update ``caps`` in place. Returns True if now on."""
    prev = caps.has_event_monitoring
    caps.has_event_monitoring = await _probe_event_monitoring(sf, caps)
    flipped = prev is False and caps.has_event_monitoring is True
    if flipped:
        log.info("event_monitoring_became_available",
                 note=caps.probe_notes.get("event_monitoring"))
    return flipped


async def _probe_dependency_api(sf: SalesforceClient, caps: OrgCapabilities
                                ) -> tuple[bool | None, list[str]]:
    """Check the Dependency API and record which source types it actually covers.

    Coverage is partial and org-dependent. Measured in the development org: no
    rows at all for Report, Dashboard, ValidationRule, WorkflowRule,
    EmailTemplate, ApprovalProcess or CustomReportType, despite those components
    existing. Recording the observed type list keeps the report honest about
    which references this signal could never have found.
    """
    try:
        rows = await sf.query(
            "SELECT MetadataComponentType, RefMetadataComponentType "
            "FROM MetadataComponentDependency LIMIT 2000",
            tooling=True,
        )
        types = sorted({r["MetadataComponentType"] for r in rows if r.get("MetadataComponentType")})
        caps.probe_notes["dependency_api"] = {
            "rows_sampled": len(rows),
            "source_types_present": types,
            "truncation_suspected": len(rows) == 2000,
        }
        return True, types
    except Exception as e:
        caps.probe_notes["dependency_api"] = f"unavailable: {str(e)[:160]}"
        return False, []


async def _probe_field_history(sf: SalesforceClient, caps: OrgCapabilities) -> bool | None:
    """Is any *__History object queryable? Gives per-field write recency."""
    try:
        rows = await sf.query(
            "SELECT QualifiedApiName FROM EntityDefinition "
            "WHERE QualifiedApiName IN ('Account History','AccountHistory') LIMIT 5",
            tooling=True,
        )
        if rows:
            return True
    except Exception:
        pass
    try:
        await sf.query("SELECT Id, Field FROM AccountHistory LIMIT 1", check_routing=False)
        return True
    except Exception as e:
        caps.probe_notes["field_history"] = f"no history objects reachable: {str(e)[:140]}"
        return False


async def _probe_simple(sf: SalesforceClient, soql: str, *, tooling: bool = False) -> bool:
    try:
        await sf.query(soql, tooling=tooling)
        return True
    except Exception:
        return False


async def _verify_routing(sf: SalesforceClient, caps: OrgCapabilities
                          ) -> tuple[dict[str, str], list[str]]:
    """Confirm the routing registry against THIS org.

    The registry is a reasonable default, not a universal truth: three entries in
    the original hand-written version were wrong, and different orgs and editions
    expose different objects. Probing per org turns a hard failure on a
    legitimately-different org into a recorded correction.
    """
    from urllib.parse import quote

    from app.salesforce.routing import PROBE_OVERRIDES

    names = sorted(TOOLING_ONLY | DATA_ONLY)
    overrides: dict[str, str] = {}
    unavailable: list[str] = []

    async def sweep(tooling: bool) -> dict[str, bool]:
        prefix = f"v{caps.api_version}/{'tooling/' if tooling else ''}query"
        seen: dict[str, bool] = {}
        for i in range(0, len(names), 25):
            chunk = names[i : i + 25]
            subs = [
                {"method": "GET",
                 "url": f"{prefix}?q="
                        + quote(PROBE_OVERRIDES.get(n, f"SELECT Id FROM {n} LIMIT 1"))}
                for n in chunk
            ]
            try:
                results = await sf.composite_batch(subs)
            except Exception:
                for n in chunk:
                    seen[n] = False
                continue
            for n, r in zip(chunk, results, strict=False):
                seen[n] = r.get("statusCode", 500) < 300
        return seen

    try:
        on_tooling = await sweep(True)
        on_data = await sweep(False)
    except Exception as e:
        caps.probe_notes["routing_probe"] = f"failed: {str(e)[:160]}"
        return {}, []

    for n in names:
        t, d = on_tooling.get(n, False), on_data.get(n, False)
        if not t and not d:
            unavailable.append(n)
            continue
        if t and d:
            continue  # either endpoint works; no correction needed
        actual = Endpoint.TOOLING if t else Endpoint.DATA
        if actual is not endpoint_for(n):
            overrides[n] = actual.value

    if overrides:
        log.warning("routing_corrections_for_org", org_id=caps.org_id, corrections=overrides)
    return overrides, unavailable


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------

async def save(caps: OrgCapabilities) -> None:
    async with session_scope() as s:
        await s.execute(text("""
            INSERT INTO org_capabilities (
                org_id, alias, instance_url, api_version, edition, org_name,
                is_sandbox, api_limit_max, api_limit_remaining,
                composite_compression, composite_max_queries,
                has_event_monitoring, has_dependency_api, dependency_api_types,
                has_field_history, has_code_coverage,
                routing_overrides, unavailable_objects,
                run_as_username, run_as_is_admin, probed_at, probe_api_cost,
                probe_notes)
            VALUES (:org, :alias, :url, :ver, :ed, :name, :sandbox, :max, :rem,
                    :comp, :cmax, :em, :dep, :deptypes, :hist, :cov,
                    CAST(:routing AS jsonb), :unavail, :user, :admin, now(),
                    :cost, CAST(:notes AS jsonb))
            ON CONFLICT (org_id) DO UPDATE SET
                alias = EXCLUDED.alias, edition = EXCLUDED.edition,
                api_limit_max = EXCLUDED.api_limit_max,
                api_limit_remaining = EXCLUDED.api_limit_remaining,
                composite_compression = EXCLUDED.composite_compression,
                has_event_monitoring = EXCLUDED.has_event_monitoring,
                has_dependency_api = EXCLUDED.has_dependency_api,
                dependency_api_types = EXCLUDED.dependency_api_types,
                has_field_history = EXCLUDED.has_field_history,
                has_code_coverage = EXCLUDED.has_code_coverage,
                routing_overrides = EXCLUDED.routing_overrides,
                unavailable_objects = EXCLUDED.unavailable_objects,
                run_as_username = EXCLUDED.run_as_username,
                run_as_is_admin = EXCLUDED.run_as_is_admin,
                probed_at = now(), probe_api_cost = EXCLUDED.probe_api_cost,
                probe_notes = EXCLUDED.probe_notes
        """), {
            "org": caps.org_id, "alias": caps.alias, "url": caps.instance_url,
            "ver": caps.api_version, "ed": caps.edition, "name": caps.org_name,
            "sandbox": caps.is_sandbox, "max": caps.api_limit_max,
            "rem": caps.api_limit_remaining, "comp": caps.composite_compression,
            "cmax": caps.composite_max_queries, "em": caps.has_event_monitoring,
            "dep": caps.has_dependency_api, "deptypes": caps.dependency_api_types,
            "hist": caps.has_field_history, "cov": caps.has_code_coverage,
            "routing": json.dumps(caps.routing_overrides),
            "unavail": caps.unavailable_objects,
            "user": caps.run_as_username, "admin": caps.run_as_is_admin,
            "cost": caps.probe_api_cost,
            "notes": json.dumps(caps.probe_notes, default=str),
        })


async def load(org_id: str) -> OrgCapabilities | None:
    async with session_scope() as s:
        row = (await s.execute(text(
            "SELECT * FROM org_capabilities WHERE org_id = :o"), {"o": org_id})).mappings().first()
    if not row:
        return None
    d = dict(row)
    d.pop("probed_at", None)
    return OrgCapabilities(
        org_id=d["org_id"], alias=d["alias"] or "", instance_url=d["instance_url"],
        api_version=d["api_version"], edition=d["edition"], org_name=d["org_name"],
        is_sandbox=d["is_sandbox"], api_limit_max=d["api_limit_max"],
        api_limit_remaining=d["api_limit_remaining"],
        composite_compression=d["composite_compression"],
        composite_max_queries=d["composite_max_queries"],
        has_event_monitoring=d["has_event_monitoring"],
        has_dependency_api=d["has_dependency_api"],
        dependency_api_types=list(d["dependency_api_types"] or []),
        has_field_history=d["has_field_history"],
        has_code_coverage=d["has_code_coverage"],
        routing_overrides=dict(d["routing_overrides"] or {}),
        unavailable_objects=list(d["unavailable_objects"] or []),
        run_as_username=d["run_as_username"], run_as_is_admin=d["run_as_is_admin"],
        probe_api_cost=d["probe_api_cost"] or 0,
        probe_notes=dict(d["probe_notes"] or {}),
    )


def _esc(v: str) -> str:
    return v.replace("\\", "\\\\").replace("'", "\\'")


def as_dict(caps: OrgCapabilities) -> dict:
    return asdict(caps)
