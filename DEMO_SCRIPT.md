# Demo script — what to say

~3 minutes. Just read it. Screen cue in brackets, spoken words underneath.

---

**[Overview]**

"This is a Salesforce org with 138 custom components. Somewhere in there are
fields nobody reads and Apex nobody calls.

Every admin knows they're there. Almost nobody deletes them, because if you're
wrong, something breaks in production.

So this tool doesn't ask you to trust it. It shows you its working."

---

**[Point at the numbers]**

"25 components look unused. 87 are in active use. 17 need a human to decide.
9 are out of scope — managed packages we couldn't delete anyway."

---

**[Point at the amber banner]**

"And before I've asked it anything, it's telling me what it couldn't check.

Event Monitoring isn't licensed on this org. That means external API traffic is
invisible. If something is only called by an outside integration, this tool
cannot see that call — so it says so, up front."

---

**[Components → UNUSED → open Aisle_Location__c]**

"Let's take the strongest candidate. A custom field called Aisle Location.

Six checks ran against it, and I can see all six — including the ones that
found nothing.

It searched 721 metadata files. Nothing. It checked whether any record holds a
value. None do. It read the Custom Metadata rows, because some orgs store field
names as data. Nothing there either.

Then it asked Salesforce itself whether this field could be deleted — a
validate-only deploy that deletes nothing. Salesforce raised no objection."

---

**[Point at the INCONCLUSIVE row]**

"And this one is the important one.

The Dependency API is a Salesforce beta that's blind to reports and validation
rules. So when it comes back empty, that proves nothing — and the tool records
it as *inconclusive*, not as 'found nothing'.

That's the whole product in one line. 'We looked and found nothing' is a
different claim from 'we couldn't check.' Mix those two up and you eventually
delete something live."

---

**[Filter to NEEDS REVIEW → open a Report_View__c field]**

"Now the interesting category. This field does have something pointing at it —
a permission set.

But a permission set grants access. It means someone *could* see the field. It
doesn't mean anything uses it.

So this isn't called unused, and it isn't called used. It goes to a human, with
the reason written down."

---

**[Dependencies]**

"Same data as a graph. Because 'five references' tells you nothing — you'd still
have to go and look at all five.

'Reached from a trigger that fires on DML' is a chain you can actually read."

---

**[How it works]**

"And if anyone asks where the data comes from, it's all documented in the app —
which API, which query, and what an empty result was allowed to mean."

---

**[Back to Overview]**

"So: 25 deletion candidates, each with everything that was searched, including
what came back empty. 17 handed to a person. And a note at the top about what
this org's licensing stopped us checking.

It generates the delete package. It never deploys it. That part's still your
call — this just gives you the evidence to make it."

---

## If you're asked

**"How accurate is it?"**
"Exactly one rule can produce 'unused'. It runs last, and only when every check
ran and every check came back empty. If anything couldn't run, the answer can't
be unused."

**"Where's the AI?"**
"It writes the plain-English summaries after the verdict is already decided. It
can flag a concern that sends something to review. It can't mark anything
unused, and it never touches the deletion path."

---

## Numbers

129 in scope of 138 · 25 unused · 87 used · 17 review · 9 out of scope
721 files searched · run took 636 seconds
