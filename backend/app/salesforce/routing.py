"""Which API endpoint serves which object.

This exists because of a specific, dangerous failure mode. Querying ``Report``
through the Tooling API returns ``INVALID_TYPE``; querying ``ValidationRule``
through the Data API does the same. If that error is swallowed anywhere in the
collector chain it becomes "zero references found" - which is precisely how a
live component gets classified UNUSED and deleted.

So routing is not a convenience here. Sending a query to the wrong endpoint must
be a loud programming error, never a quiet empty result.

The registry below is verified empirically by ``scripts/verify_routing.py``,
which probes every entry against a real org rather than trusting documentation.
"""

from __future__ import annotations

from enum import Enum


class Endpoint(str, Enum):
    TOOLING = "tooling"
    DATA = "data"
    BOTH = "both"


#: Metadata that only the Tooling API exposes. Mostly the setup/metadata layer:
#: definitions of things, rather than records of things.
TOOLING_ONLY: frozenset[str] = frozenset({
    "ApexClass", "ApexTrigger", "ApexPage", "ApexComponent",
    "ApexCodeCoverage", "ApexCodeCoverageAggregate", "ApexTestResult",
    "ValidationRule", "Layout", "CustomField", "CustomObject", "CustomTab",
    "CustomApplication", "WebLink", "CompactLayout", "RecordType",
    "EntityDefinition", "FieldDefinition", "EntityParticle",
    "Flow", "FlowDefinition",
    "WorkflowRule", "WorkflowFieldUpdate", "WorkflowAlert",
    "MetadataComponentDependency",
    "PermissionSetTabSetting", "FlexiPage",
    "LightningComponentBundle", "AuraDefinitionBundle",
    "GlobalValueSet", "PathAssistant",
})

#: Retrieved as FILES via the Metadata API; not SOQL-queryable at all.
#:
#: A category distinct from endpoint routing, and worth naming explicitly: my
#: first registry listed these as Tooling objects, and the probe reported them
#: "not present in this org" -- which is wrong and, worse, wrong in the
#: dangerous direction. A collector that queried them would get an error, and an
#: error read as emptiness is how a live component becomes a deletion candidate.
#: These are only ever reached through the retrieved source tree.
METADATA_API_ONLY: frozenset[str] = frozenset({
    "SharingRules", "StandardValueSet", "ReportType", "FlowVersionView",
    "AssignmentRules", "EscalationRules", "AutoResponseRules",
    "ApprovalProcess", "Workflow", "Letterhead", "FieldSet",
    "ReportingSnapshot", "PlatformEventChannelMember", "Settings",
})

#: Ordinary records, plus the runtime/telemetry objects. These are Data API.
DATA_ONLY: frozenset[str] = frozenset({
    "Report", "Dashboard", "Folder", "Document",
    "EmailTemplate", "BrandTemplate",
    "User", "Profile", "PermissionSet", "PermissionSetAssignment",
    "PermissionSetGroup", "UserRole", "Group", "Organization",
    # Runtime evidence: has this actually executed / been read?
    "AsyncApexJob", "CronTrigger", "CronJobDetail",
    "EventLogFile", "SetupAuditTrail", "LoginHistory",
    "ApexEmailNotification", "AuthSession",
    "ListView", "Note", "Attachment", "ContentDocument",
    # Verified by probe, against expectation: these three read as *setup*
    # metadata but are served by the Data API, not Tooling. My initial registry
    # had all three wrong -- the reason verify_routing.py exists.
    "CustomPermission", "DuplicateRule", "MatchingRule",
})

#: Objects that reject an unfiltered ``SELECT Id ... LIMIT 1``, so the routing
#: probe needs a real query for them. Their absence from a probe result means
#: "the probe was wrong", not "the object does not exist" -- a distinction that
#: matters, since this whole module is about not confusing errors with emptiness.
PROBE_OVERRIDES: dict[str, str] = {
    # Requires a filter on the parent entity.
    "FieldDefinition": (
        "SELECT QualifiedApiName FROM FieldDefinition "
        "WHERE EntityDefinition.QualifiedApiName = 'Account' LIMIT 1"
    ),
    "EntityParticle": (
        "SELECT QualifiedApiName FROM EntityParticle "
        "WHERE EntityDefinition.QualifiedApiName = 'Account' LIMIT 1"
    ),
    # No Id field; keyed differently.
    "EntityDefinition": "SELECT QualifiedApiName FROM EntityDefinition LIMIT 1",
    "FlowVersionView": "SELECT ApiName FROM FlowVersionView LIMIT 1",
    "StandardValueSet": "SELECT MasterLabel FROM StandardValueSet LIMIT 1",
}

#: Anything not listed is assumed to be a normal sObject on the Data API, which
#: is correct for every custom object and every standard business object.
_EXPLICIT: dict[str, Endpoint] = (
    {n: Endpoint.TOOLING for n in TOOLING_ONLY}
    | {n: Endpoint.DATA for n in DATA_ONLY}
)


class RoutingError(RuntimeError):
    """Raised when a query is aimed at an endpoint that cannot serve it.

    Deliberately fatal. The alternative - letting INVALID_TYPE surface as an
    empty result set - produces false UNUSED verdicts, which is the one failure
    this system exists to prevent.
    """


def endpoint_for(sobject: str) -> Endpoint:
    """Where should a query against ``sobject`` be sent?"""
    return _EXPLICIT.get(sobject, Endpoint.DATA)


def is_tooling(sobject: str) -> bool:
    return endpoint_for(sobject) in (Endpoint.TOOLING, Endpoint.BOTH)


def assert_routable(sobject: str, *, tooling: bool) -> None:
    """Fail fast when a query is being sent to the wrong endpoint."""
    if sobject in METADATA_API_ONLY:
        raise RoutingError(
            f"{sobject} is not SOQL-queryable on any endpoint - it is retrieved "
            f"as a file via the Metadata API. Query it and you get an error that "
            f"looks like an empty result set."
        )
    want = endpoint_for(sobject)
    if want is Endpoint.BOTH:
        return
    using = Endpoint.TOOLING if tooling else Endpoint.DATA
    if want is not using:
        raise RoutingError(
            f"{sobject} must be queried via the {want.value} API, not "
            f"{using.value}. Sending it to the wrong endpoint returns "
            f"INVALID_TYPE, which is indistinguishable from 'no records found' "
            f"and would produce a false UNUSED verdict."
        )


def split_by_endpoint(sobjects: list[str]) -> tuple[list[str], list[str]]:
    """Partition into (tooling, data) so each batch goes to one endpoint."""
    tooling, data = [], []
    for s in sobjects:
        (tooling if is_tooling(s) else data).append(s)
    return tooling, data


_FROM_RE = None


def sobject_of(soql: str) -> str | None:
    """Extract the FROM target of a SOQL statement, for routing checks."""
    global _FROM_RE
    if _FROM_RE is None:
        import re
        _FROM_RE = re.compile(r"\bFROM\s+([A-Za-z0-9_]+)", re.IGNORECASE)
    m = _FROM_RE.search(soql)
    return m.group(1) if m else None
