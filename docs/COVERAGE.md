# What we analyse, and how — coverage and granularity

For the delivery team. Every list here is taken from the code, and the counts
are from the run of 21 Aug 2026 against the sandbox (138 components).

---

## 1. Two different lists, and the difference matters

People conflate these, and it causes most of the confusion about coverage.

**What we JUDGE** — component types that get a used / unused / review verdict
(fields, objects, Apex, LWC, Aura).

**What we SEARCH INSIDE** — ~50 metadata types are downloaded and read as
*places a reference can live*. A Flow is never told it is unused; it is read to
find out what it uses.

So "do you cover Flows?" has two answers: yes, we read every Flow to find
references — no, we do not tell you whether a Flow itself is dead.

---

## 2. What gets a verdict

| Component type | In this run | Verdict split |
|---|---|---|
| **CustomField** | 83 | 58 used · 7 unused · 16 review · 2 out of scope |
| **ApexMethod** | 28 | 16 used · 12 unused |
| **ApexClass** | 9 | 2 used · 6 unused · 1 review |
| **StandardObject** | 7 | 7 out of scope (structural only — see below) |
| **CustomObject** | 6 | 6 used |
| **ApexTrigger** | 5 | 5 used |
| **LightningComponentBundle** | (per run) | FlexiPage/tab/app/import → used; exposed + no placement → review; private + unreachable → unused |
| **AuraDefinitionBundle** | (per run) | same UI policy as LWC |

`StandardObject` is never judged. It exists in the model because standard
objects are the *parents* of custom fields — a third of this org's custom
fields live on Account, Lead and Case — and because they are reference sources
in their own right. They are always out of scope: you cannot delete Account.

**Apex is analysed at method level, not just class level.** That is the
granularity worth flagging: a class can be in use while three of its methods
are dead, and this run found exactly that — 12 unused methods against 6 unused
classes. Most tools stop at the class.

**Parent rule (asymmetric):** if any method is **USED**, its parent class is
**USED** (`HAS_USED_METHOD`). The reverse is not true — unused methods do not
force the class unused.

**API-exposed Apex** (`@RestResource`, `@HttpGet`/`Post`/…, `@AuraEnabled`,
`@InvocableMethod`, `webservice`, Schedulable/Batchable/Queueable, etc.) is
never **UNUSED** on “no internal callers” alone. Callers may live outside the
org; without Event Monitoring, REST/API traffic is unobservable. Those surfaces
get **NEEDS_REVIEW** (`UNCALLED_ENTRY_POINT`) unless there is real Tier-A/B use
evidence. Being a graph entry root is not itself proof of use.

**Bare `global` is not an entry point.** Visibility alone used to mark every
`global static` helper (and therefore its class) as an API surface. Entry is
now annotations / `webservice` / lifecycle interfaces only. Re-run analysis
after this change so inventory `is_entry_point` attrs refresh.

**LWC / Aura** are judged with a UI-specific policy: deliberate placement
(FlexiPage, CustomTab, CustomApplication, QuickAction) and cross-bundle imports
(`c/child`, `c:Child`, `aura:dependency`) are **USED** (Tier A). That differs
from CustomField-on-Layout, which stays layout-only. An `isExposed` LWC (or Aura
`access` global/public) with **no** observed placement is **NEEDS_REVIEW**, not
unused — Experience Builder can still wire it. Private bundles with no refs and
a PASS completeness gate are **UNUSED**.

---

## 3. What we read but never judge

Retrieved and searched for references, never given a verdict:

**Code and UI (pages only)** — ApexPage (Visualforce), ApexComponent,
StaticResource

**Schema** — RecordType, CompactLayout, FieldSet, GlobalValueSet,
StandardValueSet, CustomMetadata, CustomLabels

**Layout and navigation** — Layout, FlexiPage (Lightning pages), QuickAction,
WebLink, CustomTab, CustomApplication, PathAssistant, HomePageLayout
*(these are searched; FlexiPage/Tab/App placement of an LWC/Aura counts as use
of that bundle, but the page/tab/app itself is not judged)*

**Automation** — Flow, FlowDefinition, Workflow, ApprovalProcess,
AssignmentRules, EscalationRules, AutoResponseRules, SharingRules,
DuplicateRule, MatchingRule

**Reporting** — ReportType, AnalyticSnapshot, and — via a two-pass folder
enumeration — Report, Dashboard, EmailTemplate, Document

**Security and integration** — PermissionSet, PermissionSetGroup,
CustomPermission, PlatformEventChannelMember, ExternalDataSource,
NamedCredential, RemoteSiteSetting, ConnectedApp, CustomNotificationType,
LightningMessageChannel, Letterhead

> Reports, dashboards, email templates and documents live inside folders, and
> asking Salesforce for `Report` directly returns **nothing at all** — not an
> error, an empty result. The folders have to be listed first, then their
> contents. Getting this wrong silently retrieves zero reports and makes every
> field used only by a report look dead. We reconcile what we asked for against
> what arrived and raise a coverage warning if a type comes back empty.

---

## 4. Which check applies to which type

`●` runs and produces evidence · `—` does not apply · `flag` raises an
uncertainty flag rather than evidence

| Check | Source | CustomField | CustomObject | ApexClass | ApexTrigger | ApexMethod | LWC | Aura |
|---|---|---|---|---|---|---|---|---|
| **Static references** | our own analysis of retrieved files | ● | ● | ● | ● | ● | ● | ● |
| **Record data** | Salesforce (REST, aggregate SOQL) | ● | ● | — | — | — | — | — |
| **Runtime execution** | Salesforce (REST + Tooling) | — | — | ● | ● | — | — | — |
| **Dependency API** | Salesforce (Tooling, Beta) | ● | ● | ● | ● | ● | ● | ● |
| **Config data** | Salesforce gives rows, we search them | ● | ● | ● | ● | ● | ● | ● |
| **Reachability** | entirely our own | ● | ● | ● | ● | ● | ● | ● |
| **Delete rehearsal** | Salesforce (Metadata API, validate-only) | candidates | candidates | candidates | candidates | **N/A** | candidates | candidates |
| **Recent change** | from inventory dates | flag | flag | flag | flag | flag | flag | flag |
| **Dynamic Apex** | our own code scan | flag | flag | flag | flag | flag | flag | flag |

Why the gaps are gaps:

- **Record data** asks "does any record hold a value here?" — meaningless for
  an Apex class or UI bundle, which holds no records.
- **Runtime execution** asks "has this executed?" — meaningless for a field as a
  *required* check. Salesforce records batch/scheduled job history and test
  coverage for Apex; Event Monitoring can also attach optional Tier-B hits for
  LWC/Aura (`LightningInteraction`), custom objects/fields (`RestApi` /
  `UniqueQuery` / page entity / `DatabaseSave`), and Apex REST. Those UI/schema
  EM signals are **not** required for completeness, so missing them cannot alone
  block UNUSED.
- **Delete rehearsal** runs only against components already classified unused
  (27 of 138 here), because it is the one check that costs API calls and
  touches the org. For an **ApexMethod** it is permanently not applicable: a
  method is not independently deployable — you remove one by editing its class
  — so the platform cannot validate a method-level delete. The parent class is
  rehearsed instead.
- **Recent change** and **Dynamic Apex** never produce evidence. They raise
  uncertainty flags, which *suppress* an unused verdict rather than arguing for
  or against use. This run raised 43 flags: 14 recently changed, 15 dynamic
  Apex in scope, 14 model-flagged concerns.

**So a component shows 4–6 markers, never 8.** Two checks never draw one, two
are type-specific, and the rehearsal is candidates-only. A field or class
typically shows 5, six if it was a candidate; a method or UI bundle shows 4 or 5.

---

## 5. Which checks must succeed before "unused" is allowed

Per type, these must have completed. If any is missing or degraded, the
component goes to review instead — it cannot be called unused.

| Type | Required |
|---|---|
| CustomField | static references **and** record data |
| CustomObject | static references **and** record data |
| ApexClass | static references, runtime execution, **and** reachability |
| ApexTrigger | static references, runtime execution, **and** reachability |
| ApexMethod | static references **and** reachability |
| LightningComponentBundle | static references **and** reachability |
| AuraDefinitionBundle | static references **and** reachability |

---

## 6. Granularity of a reference, not just of a component

Within static references we run three extraction layers over every file and
union the results — a parse failure in one must never delete a finding from
another:

1. **Structural** — parse the format properly (XML elements, Apex constructs).
   Precise, so the evidence can name a file and a line.
2. **String literals** — every quoted string, matched against the name table.
   This is what catches dynamic access: `obj.get('Legacy_Code__c')`,
   `Database.query('SELECT ...')`, an LWC column definition.
3. **Word tokens** — broad recall across the whole file.

Matching is **exact on the API name**. A reference to a custom field is written
`Active__c`, `Account.Active__c` or `Account$Active__c` — never `Active`, and
never the field's display label. Matching those looser forms produced pure
noise when measured against a real org: `Store_Location__c.State__c` was being
credited with the word `state` in an unrelated layout, and
`Opportunity.OrderNumber__c` with `ordernumber` in an Order layout — different
objects that happen to share a word.

We also do not count these as use, deliberately:

- a field's presence on a **page layout** — every field gets one at creation
  (R3a). **Exception:** an LWC or Aura named on a **FlexiPage, tab, app, or
  quick action** *is* use — deliberate UI placement.
- **field-level security** in a profile or permission set — means someone
  *could* see it, not that anything does
- **tab or app membership for fields** — navigation, not use
- a mention in a **comment** or a **test class** — recorded as weak, never as proof

---

## 7. Known limits, stated up front

- **Dependency API** is Beta, caps at 2,000 rows, and does not cover reports,
  dashboards, validation rules, workflows, email templates or approval
  processes. A hit is trusted; silence from it means nothing.
- **Event Monitoring** is a paid add-on. Without it we cannot observe external
  API traffic, page views or report exports — so a component used only by an
  outside integration cannot be proven used. Printed on the report.
- **Runtime-assembled Apex** cannot be resolved statically. We flag every
  object such a class touches rather than guessing.
- **Coverage is bounded by the API user.** A non-administrator sees less, and
  the run reports that bound.
- **Not judged at all:** Flows, layouts, reports, dashboards, permission sets,
  Visualforce pages, email templates and the rest of section 3. LWC and Aura
  *are* judged (section 2). Adding a further type to the judged list means
  giving it its own required-checks rule and a destructive-change mapping —
  it is a deliberate decision per type, not a switch.

---

## 8. How the UI explains a verdict

The classifier (R0–R9) is unchanged. The detail panel adds two layers on top:

1. **Decision card** — plain headline + because line (from `reason_codes` /
   `rule_trace`), plus an UNUSED flavor chip when relevant
   (`unreachable` / `no_evidence` / `layout_only`).
2. **Check-flow diagram** — collector steps use the familiar names (Static
   references, Runtime execution, Reachable from a starting point, Externally
   invocable, …). Hover/pin shows **Asks** / **We check** / **Means** /
   **Result** in positive wording. Apex classes/methods also get the external
   entry step before the verdict.

**Overview** also surfaces an **Apex classes** panel (counts + attention-
sorted rows) that deep-links into Components in Apex summary mode. The
“browse by kind” grid merges ApexClass + ApexMethod into one Apex card.
