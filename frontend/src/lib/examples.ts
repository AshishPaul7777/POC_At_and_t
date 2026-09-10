/**
 * Curated classification scenarios for the Examples view.
 *
 * Each scenario can bind several live components from the current run so
 * reviewers see more than one concrete instance of the same pattern.
 */
import type { ComponentRow, Verdict } from './api'
import { VERDICT_LABEL } from './verdict'

/** Cap per scenario so the page stays scannable. */
export const MAX_EXAMPLES_PER_SCENARIO = 8

export type ExampleSectionId =
  | 'ApexMethod'
  | 'ApexClass'
  | 'ApexTrigger'
  | 'CustomObject'
  | 'CustomField'
  | 'LightningComponentBundle'
  | 'AuraDefinitionBundle'

export interface ExampleScenario {
  id: string
  section: ExampleSectionId
  title: string
  about: string
  explanation: string
  verdict: Verdict
  match: (row: ComponentRow) => boolean
}

export const SECTION_META: {
  id: ExampleSectionId
  title: string
  blurb: string
}[] = [
  {
    id: 'ApexMethod',
    title: 'Apex methods',
    blurb: 'Individual methods on a class — callers, entry surfaces, and reachability.',
  },
  {
    id: 'ApexClass',
    title: 'Apex classes',
    blurb: 'Whole classes, including when a used method pulls the parent class along.',
  },
  {
    id: 'ApexTrigger',
    title: 'Apex triggers',
    blurb: 'Triggers that fire on record events — static wiring and runtime fires.',
  },
  {
    id: 'CustomObject',
    title: 'Custom objects',
    blurb: 'Objects judged by references, record data, and runtime access.',
  },
  {
    id: 'CustomField',
    title: 'Custom fields',
    blurb: 'Fields judged by references and whether records actually store values.',
  },
  {
    id: 'LightningComponentBundle',
    title: 'Lightning web components',
    blurb: 'LWC bundles — placement on pages/apps, imports, and UI activity.',
  },
  {
    id: 'AuraDefinitionBundle',
    title: 'Aura components',
    blurb: 'Aura bundles — same UI policy as LWC: placement, exposure, reachability.',
  },
]

function hasCode(row: ComponentRow, code: string): boolean {
  return (row.reason_codes ?? []).some(
    (c) => c === code || c.startsWith(`${code} `) || c.startsWith(code),
  )
}

function codesInclude(row: ComponentRow, needle: string): boolean {
  return (row.reason_codes ?? []).some((c) => c.includes(needle))
}

function isEntry(row: ComponentRow): boolean {
  return Boolean(row.attrs?.is_entry_point)
}

export const EXAMPLE_SCENARIOS: ExampleScenario[] = [
  {
    id: 'method-used-static',
    section: 'ApexMethod',
    title: 'Called from other Apex',
    about: 'A method that other code names — classified Used because of a static reference.',
    explanation:
      'Something in the org literally calls or mentions this method. That is a '
      + 'binding reference: removing it would break a compile or a path that already '
      + 'exists. Verdict: Used.',
    verdict: 'USED',
    match: (r) => r.ctype === 'ApexMethod' && r.verdict === 'USED'
      && (r.hits ?? 0) > 0 && !hasCode(r, 'RUNTIME_OR_DATA_EVIDENCE'),
  },
  {
    id: 'method-runtime-only',
    section: 'ApexMethod',
    title: 'No static caller — but it ran',
    about: 'Nothing in metadata names this method, yet Event Monitoring (or jobs) saw it execute.',
    explanation:
      'The static scan found no caller, which often means the call is dynamic, from '
      + 'Lightning, or from outside the org. Runtime logs still prove the method ran, '
      + 'so it is marked Used on that evidence alone — not Unused.',
    verdict: 'USED',
    match: (r) => r.ctype === 'ApexMethod' && r.verdict === 'USED'
      && hasCode(r, 'RUNTIME_OR_DATA_EVIDENCE'),
  },
  {
    id: 'method-entry-uncalled',
    section: 'ApexMethod',
    title: 'Externally invokable, no caller seen',
    about: 'AuraEnabled / REST / Invocable (etc.) with no observed caller — Needs review.',
    explanation:
      'This method is an entry surface: something outside ordinary Apex can invoke it '
      + '(@AuraEnabled, HTTP, Invocable, …). We did not see a caller in metadata or '
      + 'runtime, but we also cannot prove it is dead — integrations may still hit it. '
      + 'Verdict: Needs review (uncalled entry point).',
    verdict: 'NEEDS_REVIEW',
    match: (r) => r.ctype === 'ApexMethod' && r.verdict === 'NEEDS_REVIEW'
      && (hasCode(r, 'UNCALLED_ENTRY_POINT') || isEntry(r)),
  },
  {
    id: 'method-unused-unreachable',
    section: 'ApexMethod',
    title: 'Nothing can reach it',
    about: 'No references, not an entry point, unreachable from live starters — Unused.',
    explanation:
      'No static reference, no runtime hit, and the reachability walk from triggers, '
      + 'Flows, and UI entry points never arrives here. With coverage complete, that '
      + 'is a deletion candidate: Unused.',
    verdict: 'UNUSED',
    match: (r) => r.ctype === 'ApexMethod' && r.verdict === 'UNUSED'
      && (hasCode(r, 'UNREACHABLE') || hasCode(r, 'NO_EVIDENCE')),
  },
  {
    id: 'class-via-used-method',
    section: 'ApexClass',
    title: 'Class Used because a method is Used',
    about: 'At least one method on the class is Used, so the parent class is Used too.',
    explanation:
      'You cannot delete a class while a method on it is still in use. When any method '
      + 'is classified Used, the class is promoted to Used as well — even if the class '
      + 'body itself looks quiet.',
    verdict: 'USED',
    match: (r) => r.ctype === 'ApexClass' && r.verdict === 'USED'
      && hasCode(r, 'HAS_USED_METHOD'),
  },
  {
    id: 'class-runtime-only',
    section: 'ApexClass',
    title: 'No static ref — runtime / jobs found it',
    about: 'Metadata does not name the class, but async jobs, schedules, or event logs do.',
    explanation:
      'Absence of a static reference is not enough to call a class unused. Background '
      + 'jobs, scheduled Apex, or Event Monitoring rows show it actually ran — so the '
      + 'verdict is Used from runtime evidence.',
    verdict: 'USED',
    match: (r) => r.ctype === 'ApexClass' && r.verdict === 'USED'
      && hasCode(r, 'RUNTIME_OR_DATA_EVIDENCE'),
  },
  {
    id: 'class-entry-uncalled',
    section: 'ApexClass',
    title: 'Entry-point class with no observed traffic',
    about: 'Schedulable, RestResource, Batchable, etc. — Needs review until traffic or callers appear.',
    explanation:
      'The class is built to be started from outside normal Apex (scheduler, REST, '
      + 'batch, …). With no caller and no runtime proof, we hold it at Needs review '
      + 'rather than Unused.',
    verdict: 'NEEDS_REVIEW',
    match: (r) => r.ctype === 'ApexClass' && r.verdict === 'NEEDS_REVIEW'
      && (hasCode(r, 'UNCALLED_ENTRY_POINT') || isEntry(r)),
  },
  {
    id: 'class-unused',
    section: 'ApexClass',
    title: 'Quiet class — Unused',
    about: 'No binding refs, no runtime, unreachable — a cleanup candidate.',
    explanation:
      'Required checks finished cleanly and none found use. Nothing names it, nothing '
      + 'ran it, and nothing reachable leads here. Verdict: Unused.',
    verdict: 'UNUSED',
    match: (r) => r.ctype === 'ApexClass' && r.verdict === 'UNUSED'
      && !hasCode(r, 'HAS_USED_METHOD'),
  },
  {
    id: 'trigger-used',
    section: 'ApexTrigger',
    title: 'Trigger in use',
    about: 'Wired to an object and/or seen firing in event logs — Used.',
    explanation:
      'Triggers are live automation. A static wiring to the object, reachability from '
      + 'real paths, or Event Monitoring trigger fires all support Used.',
    verdict: 'USED',
    match: (r) => r.ctype === 'ApexTrigger' && r.verdict === 'USED',
  },
  {
    id: 'trigger-runtime-only',
    section: 'ApexTrigger',
    title: 'Fired in logs without a strong static story',
    about: 'Runtime trigger activity carried the Used verdict (R4-style evidence).',
    explanation:
      'Even when the static picture is thin, ApexTrigger event-log rows mean the '
      + 'trigger actually fired in the org. That runtime evidence is enough for Used.',
    verdict: 'USED',
    match: (r) => r.ctype === 'ApexTrigger' && r.verdict === 'USED'
      && hasCode(r, 'RUNTIME_OR_DATA_EVIDENCE'),
  },
  {
    id: 'trigger-unused',
    section: 'ApexTrigger',
    title: 'Trigger looks unused',
    about: 'No evidence it fires or participates — Unused.',
    explanation:
      'If every required check completed and nothing shows this trigger in the path '
      + 'or in logs, it is a deletion candidate.',
    verdict: 'UNUSED',
    match: (r) => r.ctype === 'ApexTrigger' && r.verdict === 'UNUSED',
  },
  {
    id: 'object-used-refs',
    section: 'CustomObject',
    title: 'Object referenced in the org',
    about: 'Apex, Flows, or UI name this object — Used.',
    explanation:
      'Custom objects stay Used when automation or UI still names them. Deleting the '
      + 'object would break those bindings.',
    verdict: 'USED',
    match: (r) => r.ctype === 'CustomObject' && r.verdict === 'USED'
      && (r.hits ?? 0) > 0 && !hasCode(r, 'RUNTIME_OR_DATA_EVIDENCE'),
  },
  {
    id: 'object-data-or-runtime',
    section: 'CustomObject',
    title: 'Little metadata — but records or runtime access',
    about: 'Data population or Event Monitoring object access drove Used.',
    explanation:
      'Sometimes almost nothing in metadata points at an object, yet records exist or '
      + 'API/UI logs show access. Real data or runtime access is enough for Used.',
    verdict: 'USED',
    match: (r) => r.ctype === 'CustomObject' && r.verdict === 'USED'
      && hasCode(r, 'RUNTIME_OR_DATA_EVIDENCE'),
  },
  {
    id: 'object-unused',
    section: 'CustomObject',
    title: 'Empty and unreferenced object',
    about: 'No binding use and no meaningful data — Unused.',
    explanation:
      'With references and data checks complete and empty, the object is a cleanup '
      + 'candidate. Confirm integrations before deleting.',
    verdict: 'UNUSED',
    match: (r) => r.ctype === 'CustomObject' && r.verdict === 'UNUSED',
  },
  {
    id: 'field-used',
    section: 'CustomField',
    title: 'Field in active use',
    about: 'Named in automation/UI beyond layout, or holding real data — Used.',
    explanation:
      'A field is Used when automation or UI beyond mere layout/FLS relies on it, '
      + 'or when records store values that processes read.',
    verdict: 'USED',
    match: (r) => r.ctype === 'CustomField' && r.verdict === 'USED',
  },
  {
    id: 'field-layout-or-fls-unused',
    section: 'CustomField',
    title: 'Only on a layout or permission set — no data',
    about: 'Layout / Profile / PermissionSet presence alone, empty in records — Unused.',
    explanation:
      'Every custom field gets a layout slot and FLS entries when created. That is '
      + 'presence, not business use. With no functional reference and no populated '
      + 'data, the field is Unused (remove from layouts / hide via FLS first).',
    verdict: 'UNUSED',
    match: (r) => r.ctype === 'CustomField' && r.verdict === 'UNUSED'
      && (codesInclude(r, 'LAYOUT_ONLY') || codesInclude(r, 'PRESENCE_ONLY')),
  },
  {
    id: 'field-unused',
    section: 'CustomField',
    title: 'Field unused (nothing at all)',
    about: 'No binding use and no populated data — Unused.',
    explanation:
      'Required collectors found nothing meaningful. That is a field you can usually '
      + 'remove after clearing layouts.',
    verdict: 'UNUSED',
    match: (r) => r.ctype === 'CustomField' && r.verdict === 'UNUSED'
      && !codesInclude(r, 'LAYOUT_ONLY') && !codesInclude(r, 'PRESENCE_ONLY'),
  },
  {
    id: 'field-needs-review',
    section: 'CustomField',
    title: 'Field needs a human look',
    about: 'Checks disagreed or coverage was incomplete — Needs review.',
    explanation:
      'When a required check failed, or evidence is ambiguous beyond layout/FLS, '
      + 'we ask a person to decide rather than auto-deleting.',
    verdict: 'NEEDS_REVIEW',
    match: (r) => r.ctype === 'CustomField' && r.verdict === 'NEEDS_REVIEW',
  },
  {
    id: 'lwc-used-placement',
    section: 'LightningComponentBundle',
    title: 'LWC placed or imported',
    about: 'On a FlexiPage, tab, app, or imported by another bundle — Used.',
    explanation:
      'Deliberate UI placement or an import from another component is proof of use. '
      + 'Removing the bundle would break that page or dependency.',
    verdict: 'USED',
    match: (r) => r.ctype === 'LightningComponentBundle' && r.verdict === 'USED',
  },
  {
    id: 'lwc-exposed-no-placement',
    section: 'LightningComponentBundle',
    title: 'Exposed but not placed',
    about: 'isExposed (or similar) with no page/tab/app — Needs review.',
    explanation:
      'The bundle can be dropped onto a page by an admin, but we did not find it on '
      + 'one. It might be intentional inventory or forgotten. Verdict: Needs review.',
    verdict: 'NEEDS_REVIEW',
    match: (r) => r.ctype === 'LightningComponentBundle' && r.verdict === 'NEEDS_REVIEW',
  },
  {
    id: 'lwc-unused-private',
    section: 'LightningComponentBundle',
    title: 'Private and unreachable',
    about: 'Not exposed, not imported, unreachable — Unused.',
    explanation:
      'A private LWC that nothing imports and nothing can reach is safe cleanup '
      + 'material once you confirm no dynamic create by name.',
    verdict: 'UNUSED',
    match: (r) => r.ctype === 'LightningComponentBundle' && r.verdict === 'UNUSED',
  },
  {
    id: 'aura-used',
    section: 'AuraDefinitionBundle',
    title: 'Aura component in use',
    about: 'Referenced from pages, apps, or other Aura/LWC — Used.',
    explanation:
      'Same idea as LWC: placement or a hard dependency means Used.',
    verdict: 'USED',
    match: (r) => r.ctype === 'AuraDefinitionBundle' && r.verdict === 'USED',
  },
  {
    id: 'aura-review',
    section: 'AuraDefinitionBundle',
    title: 'Aura needs a human look',
    about: 'Exposed or uncertain with no clear placement — Needs review.',
    explanation:
      'Aura often sits behind apps and experience pages. When exposure and placement '
      + 'disagree, we ask for review rather than auto-deleting.',
    verdict: 'NEEDS_REVIEW',
    match: (r) => r.ctype === 'AuraDefinitionBundle' && r.verdict === 'NEEDS_REVIEW',
  },
  {
    id: 'aura-unused',
    section: 'AuraDefinitionBundle',
    title: 'Aura unused',
    about: 'No placement, no import, unreachable — Unused.',
    explanation:
      'With checks complete and no path to this bundle, it is a deletion candidate.',
    verdict: 'UNUSED',
    match: (r) => r.ctype === 'AuraDefinitionBundle' && r.verdict === 'UNUSED',
  },
]

export interface BoundExample {
  scenario: ExampleScenario
  components: ComponentRow[]
}

/** Bind up to N live components per scenario. */
export function bindExamples(rows: ComponentRow[]): BoundExample[] {
  return EXAMPLE_SCENARIOS.map((scenario) => {
    const components = rows
      .filter((r) => scenario.match(r))
      .sort((a, b) => (b.confidence ?? 0) - (a.confidence ?? 0)
        || a.api_name.localeCompare(b.api_name))
      .slice(0, MAX_EXAMPLES_PER_SCENARIO)
    return { scenario, components }
  })
}

export function sectionBounds(bound: BoundExample[]): {
  section: (typeof SECTION_META)[number]
  items: BoundExample[]
}[] {
  return SECTION_META.map((section) => ({
    section,
    items: bound.filter((b) => b.scenario.section === section.id),
  })).filter((g) => g.items.length > 0)
}

export function exampleVerdictLabel(v: Verdict): string {
  return VERDICT_LABEL[v]
}
