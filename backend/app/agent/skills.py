"""Skills: named procedures the agent can be asked to follow.

A skill is a prompt with a method. The value is not that the model could not
work these out -- it usually can -- but that the method is then *consistent*.
Two triage sessions a week apart should investigate the same way, or comparing
their output means nothing.

They are surfaced as slash commands so the capability is discoverable. A user
who cannot see what the agent is good at will ask it for the wrong things.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Skill:
    name: str          # slash command, without the slash
    title: str
    when: str          # shown in the composer's autocomplete
    prompt: str        # expanded into the user turn


SKILLS: list[Skill] = [
    Skill(
        name="triage",
        title="Triage the next needs-review component",
        when="Investigate ONE component, then stop and wait for you",
        prompt="""Triage exactly ONE component -- the next one at NEEDS_REVIEW that has no conclusion yet in this conversation. Do not start a second one.

Steps:
  1. List the NEEDS_REVIEW components if you do not already know them.
  2. Pick the first one not yet covered above.
  3. Read its reason codes -- they say what the pipeline could not settle, and that is the question to answer.
  4. Investigate with the cheapest tool that can settle it, usually grep_workspace over the retrieved metadata.

Then report, briefly: the API name, why it was held, what you checked, and what you conclude. Say plainly when a check found nothing -- that is the evidence a deletion rests on.

Finish by naming which component is next and how many remain, then STOP and wait. Do not continue to the next one until you are asked.""",
    ),
    Skill(
        name="triage-all",
        title="Triage every needs-review component",
        when="Work the whole queue in one go -- slow and token-hungry",
        prompt="""Work through EVERY component at NEEDS_REVIEW in this run, one after another, without stopping to ask.

For each: read its reason codes, investigate with the cheapest tool that can settle it, and report the API name, why it was held, what you checked, and your conclusion. Be explicit when a check found nothing.

Be economical -- this runs against a metered gateway. Prefer one broad grep_workspace over many narrow ones, and do not re-fetch what you already have. If you approach your tool or token budget, stop and summarise which components you covered and which remain, rather than leaving the last few half-investigated.""",
    ),
    Skill(
        name="explain",
        title="Explain a verdict",
        when="Why did this component get the verdict it has?",
        prompt="""\
Explain this component's verdict. Name the rule that fired and every collector \
that contributed, including the ones that searched and found nothing. Finish \
with what would have to be true for the verdict to be different.""",
    ),
    Skill(
        name="investigate",
        title="Investigate a component",
        when="Dig into one component across every available source",
        prompt="""\
Investigate this component thoroughly. Check the stored evidence, grep the \
retrieved metadata, and read its source if it has any. Query the org only if \
the live state is genuinely the question. Report what each source said, then \
your conclusion, then your confidence and what limits it.""",
    ),
    Skill(
        name="coverage",
        title="Audit coverage gaps",
        when="What could this run not prove, and what would fix it?",
        prompt="""\
List everything this run could not establish: coverage caveats, collectors that \
did not complete, and components held back by gaps. For each, say what it means \
for the verdicts and what concrete change -- a licence, a permission, a config \
-- would remove the limitation. Distinguish gaps that are fixable from ones \
that are inherent.""",
    ),
    Skill(
        name="deletion-plan",
        title="Plan a deletion batch",
        when="What can be removed, in what order, and what must go together",
        prompt="""\
Propose a deletion plan for the UNUSED components in this run. Group anything \
that must be removed together, order the groups so no step breaks another, and \
flag any component whose removal prerequisites are unmet. Call out explicitly \
anything you would not delete yet, and why.""",
    ),
    Skill(
        name="compare",
        title="Compare two runs",
        when="What changed between runs, and did anything regress?",
        prompt="""\
Compare the two most recent runs. Lead with regressions -- anything previously \
UNUSED that is now USED, because that means an earlier deletion recommendation \
was wrong. Then summarise other verdict changes and what likely caused them.""",
    ),
]

BY_NAME = {s.name: s for s in SKILLS}


def expand(text: str) -> str:
    """Turn a leading slash command into its full instruction.

    Anything the user typed after the command is kept, so `/explain Foo__c`
    carries the target through.
    """
    if not text.startswith("/"):
        return text
    head, _, rest = text[1:].partition(" ")
    head, rest = head.strip().lower(), rest.strip()

    # "/triage all" is how a person actually types it. Without this it expands
    # the one-at-a-time prompt with a stray "all" appended, and the agent stops
    # after a single component having been asked for every one.
    if head == "triage" and rest.lower().split(" ")[0] in ("all", "everything"):
        head, rest = "triage-all", " ".join(rest.split(" ")[1:]).strip()

    skill = BY_NAME.get(head)
    if not skill:
        return text
    return f"{skill.prompt}\n\n{rest}" if rest else skill.prompt
