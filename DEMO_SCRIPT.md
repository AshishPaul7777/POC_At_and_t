# Demo script — Salesforce Org Cleanup Analyzer

**Runtime:** 8–9 minutes. There's a 4-minute cut marked at the end.
**Org:** `orgfarm-15850e66bf-dev-ed` · **Run:** `6007d69a` · 138 components, 636 seconds.

Everything in **bold** is a click. Everything in quotes is what you say. Every
number below is from the actual run in the database — don't round them, and if
you re-run before recording, re-check them.

> **Before you hit record**
> - Sign in already; don't film the login page.
> - Open the run `3d ago — complete, partial coverage — 25 unused, 17 to review`.
> - Close the assistant panel if it's open.
> - Browser at 100% zoom, 1440×900 or wider. The dependency graph needs the room.
> - Have `Inventory_Item__c.Aisle_Location__c` findable — you'll search for it.

---

## 0 · Cold open (20s)

*Start on the Overview page. Don't move the mouse yet.*

> "This is a Salesforce org with 138 custom components in it. Somewhere in there
> are fields nobody reads, Apex nobody calls, and automation nobody has thought
> about in three years.
>
> Every admin knows they're there. Almost nobody deletes them — because the cost
> of being wrong is a production incident, and there's no way to prove a negative.
>
> This tool doesn't ask you to trust it. It shows you its working."

---

## 1 · The problem, stated honestly (45s)

> "Here's the asymmetry that shapes everything about this product.
>
> If it wrongly tells you something is unused, you delete it, and something
> breaks in production at month-end. If it wrongly tells you something needs
> review, you spend five minutes looking and move on.
>
> Those two mistakes are not the same size. So every ambiguity in this system
> resolves toward review — and I'll show you three places where it deliberately
> refuses to give an answer."

---

## 2 · The verdict, up front (60s)

*Point at the hero number.*

> "25 components look unused, out of 129 in scope. 87 are in active use, 17 need
> a human decision, and 9 are out of scope — managed packages and standard fields
> we couldn't delete anyway."

*Point at the amber **Partial coverage** banner at the top.*

> "Now — notice what it's telling me before I've asked. This banner says one check
> could not complete. Most tools would show me a clean green number here. This one
> leads with what it couldn't do."

*Click **see what was missing**.*

*The exact caveat on screen reads:* `C40_runtime: Event Monitoring unavailable:
external API traffic, page views and report exports are unobservable in this org`

> "Event Monitoring isn't enabled on this org — it's a paid Salesforce add-on.
> Without it, three things are invisible: external API traffic, page views, and
> report exports. So if a component is only ever touched by an outside
> integration, this tool cannot see that.
>
> It doesn't quietly ignore that. It names the collector, it names what's
> unobservable, and it's the reason this run is marked partial rather than
> complete."

**The beat to land:** it volunteered its own blind spot before being asked.

---

## 3 · One field, end to end (2m 30s) — *the core of the demo*

*Go to **Components**, filter to **UNUSED**, open
`Inventory_Item__c.Aisle_Location__c`.*

> "Let's take the strongest candidate in the list. A custom field on Inventory
> Item, called Aisle Location. Confidence 100."

*Point at the evidence list.*

> "Here's what I actually care about. Six independent checks ran against this
> field, and I can see all six — including the ones that found nothing."

*Walk down them one at a time. Slowly — this is the part that sells it.*

> - "**Static references.** It read 721 retrieved metadata files — Apex, Flows,
>   layouts, LWC — and searched for every name this field could go by. Nothing.
> - "**Record data.** It asked the org whether any record actually holds a value
>   in this field. None do.
> - "**Config data.** Some orgs store field names as *data*, inside Custom
>   Metadata rows — a string in a table that Apex reads at runtime. No static
>   analyser would ever find that. This one went and read the rows. Nothing there
>   either.
> - "**Reachability.** It built a dependency graph and asked whether anything that
>   runs or that a person sees can reach this field. Nothing can.
> - "**Delete rehearsal.** This is the one I'd point a sceptic at. It asked
>   Salesforce itself whether this field could be deleted — a validate-only
>   destructive deploy. It deletes nothing, it's safe against production, and it's
>   the only signal here that's server-validated rather than inferred."

*Now point at the Dependency API row — the one that says INCONCLUSIVE.*

> "And then this one. The Dependency API is a Salesforce beta that's blind to
> reports, dashboards, validation rules and workflows. So when it comes back empty,
> that is genuinely not evidence of anything — and the tool records it as
> *inconclusive*, not as 'found nothing'.
>
> That distinction is the whole product. 'We looked properly and found nothing' is
> a completely different claim from 'we couldn't check'. Collapse those two
> together and you eventually delete something live."

*Point at the rule trace.*

> "So the verdict is rule R9 — no evidence from any collector, and coverage was
> complete for this component. No model decided that. It's a rule, it's stored,
> and anyone can re-derive it from the evidence above."

---

## 4 · Where it refuses to answer (1m 15s)

*Filter to **NEEDS REVIEW**. Open any `Report_View__c` field — e.g.
`Report_View__c.Dashboard_Order__c`.*

> "Now the more interesting category. This field *does* have something pointing at
> it. Here's the reason code: weak signal only — permission set only.
>
> The only thing in the entire org that references this field is a permission set.
> And a permission set grants *access*. It means somebody could see this field. It
> does not mean anything uses it.
>
> Same for page layouts. Every custom field gets a layout entry the moment it's
> created. If you count that as usage, everything is used, you find nothing, and
> your tool looks authoritative while being useless.
>
> So this doesn't get called unused, and it doesn't get called used. It gets
> handed to a human with the reason written down. There are 17 of these, and
> honestly — that number being non-zero is the point."

---

## 5 · Why, not how many (1m)

*Go to **Dependencies**. Click a node with several edges.*

> "This is the same data as a graph. And the reason it's here is that a reference
> *count* tells a reviewer nothing.
>
> If I tell you a field has five references, you still have to go and look at all
> five. If I tell you this field is reached from a trigger that fires on DML —
> that's a chain you can read, and it tells you exactly what would have to change
> for the field to become deletable."

*Optional, if you have a clean example on screen:*

> "That's the difference between a number and an explanation."

---

## 6 · How it works, for the sceptic in the room (1m)

*Go to **How it works**. Scroll past the top into the technical reference.*

> "This page exists because somebody always asks 'yes, but where does this data
> actually come from?'"

*Scroll to **How data leaves the org**.*

> "Four API surfaces — Tooling, REST, composite batch, and the Metadata API through
> the Salesforce CLI. Four, because no single Salesforce API exposes all of it.
> Reports are on one, Apex definitions on another, and validation rules on neither —
> they only exist as files."

*Scroll to **Routing, and the failure it prevents**.*

> "And this is my favourite section, because it documents a way this tool could
> have quietly killed someone's org. Query a Report through the Tooling API and
> Salesforce returns INVALID_TYPE. If you swallow that error anywhere, it looks
> exactly like 'zero references found' — and that's how a live component gets
> deleted. So a mis-routed query is a hard failure here, never an empty result."

*Hover a heading so the **Link** button appears; click it.*

> "Every section here has a copy-link button, so when someone challenges a verdict
> you can send them the exact paragraph rather than a screenshot."

---

## 7 · Close (30s)

*Back to Overview.*

> "So: 25 deletion candidates, each with the full list of what was searched
> including what came back empty. 17 handed to a human, with the reason. And a
> banner at the top telling you what this org's licensing prevented us from
> checking at all.
>
> The tool generates a deployable delete package — and deliberately never deploys
> it. That's still your decision. What this gives you is the evidence to make it,
> and to defend it to whoever asks."

---

## The 4-minute cut

Sections **0, 2, 3, 4, 7**. Drop the graph, the how-it-works tour, and the
problem framing. Section 3 is the demo; never cut it for time.

---

## Delivery notes

- **Slow down in section 3.** The instinct is to rush the list of collectors
  because you already know them. That list *is* the product — every other cleanup
  tool shows you an answer, and this one shows you the search.
- **Say "found nothing" out loud, more than once.** It's the phrase you want
  people repeating afterwards.
- **Don't apologise for the amber banner or the 17 review items.** Present them as
  features, because they are. A tool that returned 42 unused and no caveats would
  be less trustworthy, not more.
- **If someone asks "how accurate is it?"** — the honest answer, and a better one
  than a percentage: "It's built so that the expensive error is structurally hard
  to make. Exactly one rule can produce 'unused', it's evaluated last, and it only
  fires when every check ran and every check came back empty. If any check couldn't
  run, the verdict can't be unused at all."
- **If someone asks about AI** — "It writes the plain-English summaries after the
  verdict is already decided. It can flag a concern that downgrades something to
  review. It cannot promote anything to unused, and it never touches the deletion
  path. The verdicts are rules, and they're reproducible without it."
- **If a live run is part of your recording**, know that a full run took 636
  seconds on this org. Record it separately and cut, or start one at the top and
  return to it at the end.

---

## Numbers to have on hand

| | |
|---|---|
| Components in scope | 129 of 138 |
| Unused / Used / Review / Out of scope | 25 / 87 / 17 / 9 |
| Run duration | 636 seconds |
| Metadata files searched by the static index | 721 |
| Graph artifacts examined for reachability | 859 |
| Collectors run per component | up to 8 |
| Checks that could not complete | 1 (Event Monitoring, unlicensed) |
| Org | Bounteous, Developer Edition, API v67.0 |
| Unused by type | 12 ApexMethod, 7 CustomField, 6 ApexClass |
| Review by type | 16 CustomField, 1 ApexClass |
