"""Stage S40 — LLM narration of already-decided verdicts.

The boundary that matters, and the reason this is a separate stage rather than
part of classification:

    The verdict is decided by deterministic rules BEFORE this runs, and arrives
    here as read-only context. The model explains; it does not judge.

A model asked "is this field used?" would answer confidently from incomplete
evidence, and its confidence would be indistinguishable from the rule engine's.
So it is never asked. It is given a decision and the evidence behind it, and
asked to write down what the component appears to do and why the evidence
supports the conclusion.

The one direction it may move a verdict is toward caution: if it spots a reason
the component might still matter, that becomes an uncertainty flag which
downgrades UNUSED to NEEDS_REVIEW. It can never promote anything to USED, and it
can never make a NEEDS_REVIEW into an UNUSED.
"""

from __future__ import annotations

import asyncio
import json
import re

import structlog
from sqlalchemy import text

from app.config import get_settings
from app.db.session import session_scope
from app.llm.client import LLMClient, LLMUnavailable

log = structlog.get_logger()

SYSTEM = """You explain evidence about Salesforce metadata to an engineer who \
must decide whether to delete something.

A deterministic rule engine has ALREADY decided the verdict. It is given to you \
as fact. Do not agree with it, disagree with it, or re-derive it. Do not \
speculate about references beyond those listed. If the evidence is thin, say the \
evidence is thin — do not fill the gap with plausible narrative.

You are reading real production metadata. Be precise and unexcited. No praise, \
no hedging filler, no restating the question.

Reply with ONLY a JSON object, no prose around it, no code fence:

{
  "business_purpose": "<=45 words. What this component appears to do, in plain \
language, inferred from its name, type and the code or definitions that touch \
it. If you genuinely cannot tell, say so.",
  "evidence_summary": "<=90 words. Where we looked and what we found. Name the \
places that returned nothing explicitly — that is the load-bearing part of the \
verdict.",
  "risk_note": "<=45 words. The strongest honest argument for KEEPING this, or \
'none identified' if there is not one.",
  "still_might_matter": true|false,
  "still_might_matter_why": "<=30 words, or empty string. Set the flag true ONLY \
if you see a concrete reason this could still be load-bearing that the listed \
evidence would not have caught."
}"""

# Credential-shaped strings are stripped before any source leaves the process.
_SECRETS = re.compile(
    r"(?i)(password|secret|token|api[_-]?key|private[_-]?key|bearer)"
    r"\s*[:=]\s*['\"]?[\w\-./+=]{8,}"
)


def _redact(s: str) -> str:
    return _SECRETS.sub(r"\1=[REDACTED]", s)


def _render_user_prompt(row: dict, evidence: list[dict], flags: list[dict],
                        refs: list[dict], source: str | None,
                        samples: list | None = None) -> str:
    parts = [
        f"COMPONENT: {row['api_name']}  ({row['ctype']})",
        f"OBJECT: {row.get('parent_object') or '(n/a)'}",
        f"LAST MODIFIED: {row.get('last_modified_date') or 'unknown'}",
        "",
        f"VERDICT (already decided — do not revisit): {row['label']}",
        f"CONFIDENCE: {round(row['confidence'])}",
        f"REASONS: {', '.join(row.get('reason_codes') or [])}",
        "",
        "EVIDENCE FROM EACH COLLECTOR:",
    ]
    for e in evidence:
        payload = json.dumps(e["payload"], default=str)[:400]
        parts.append(f"  - {e['collector_id']}: {e['result']}"
                     f"{f' (tier {e[chr(39)+chr(39)]})' if False else ''}")
        parts.append(f"      {payload}")
    if flags:
        parts.append("")
        parts.append("UNCERTAINTY FLAGS:")
        for f in flags:
            parts.append(f"  - {f['code']}: {f.get('detail') or ''}")
    if refs:
        parts.append("")
        parts.append(f"REFERENCED BY ({len(refs)} shown):")
        for r in refs[:12]:
            tag = " [test]" if r.get("is_test") else ""
            parts.append(f"  - {r['member_name']} ({r['metadata_type']}, "
                         f"tier {r['tier']}){tag}")
    # Field metadata states what a name only implies. "Number(8,2), help text
    # 'shipping weight'" turns an inference into a fact.
    attrs = row.get("attrs") or {}
    facts = [(k, attrs.get(k)) for k in
             ("data_type", "is_calculated", "business_status", "relationship_name",
              "modifiers", "annotations", "return_type", "on_object", "events",
              "api_version", "loc", "is_test", "is_entry_point")
             if attrs.get(k) not in (None, "", [], False)]
    if facts:
        parts.append("")
        parts.append("COMPONENT FACTS:")
        for k, v in facts:
            parts.append(f"  {k}: {v}")

    if samples:
        # Real values are decisive where a name is ambiguous: seeing
        # 'GOLD','SILVER' identifies a loyalty tier faster than any inference.
        parts.append("")
        parts.append(f"SAMPLE VALUES ({len(samples)} distinct, from real records):")
        for v in samples[:5]:
            parts.append(f"  {_redact(str(v))[:90]}")

    if source:
        parts.append("")
        parts.append("SOURCE CODE:")
        parts.append(_redact(source[:6000]))
    return "\n".join(parts)


async def _sample_values(sf, row: dict) -> list | None:
    """A few real values for a populated field.

    Only for fields that actually hold data — sampling an empty field costs a
    call and returns nothing. Deliberately capped at 5 distinct values: the goal
    is to identify what the field holds, not to export its contents.
    """
    if row.get("ctype") != "CustomField" or not row.get("parent_object"):
        return None
    field = row["api_name"].split(".", 1)[-1]
    dtype = str((row.get("attrs") or {}).get("data_type") or "").lower()
    # Long text and encrypted values are unsafe or useless to sample.
    if any(x in dtype for x in ("textarea", "richtext", "encrypted", "base64")):
        return None
    try:
        rows = await sf.query(
            f"SELECT {field} FROM {row['parent_object']} "
            f"WHERE {field} != null LIMIT 5")
    except Exception:
        return None
    vals, seen = [], set()
    for r in rows:
        v = r.get(field)
        if v is not None and str(v) not in seen:
            seen.add(str(v))
            vals.append(v)
    return vals or None


async def narrate_run(run_id: str, *, only_labels: tuple[str, ...] =
                      ("UNUSED", "NEEDS_REVIEW"), limit: int = 200,
                      sf=None) -> dict:
    """Generate summaries for the components a human will actually look at.

    USED components are skipped by default: nobody needs prose explaining why a
    live field is live, and generating it would be most of the cost for none of
    the value.
    """
    s_cfg = get_settings()
    if not s_cfg.llm_enabled:
        log.info("narration_disabled")
        return {"status": "DISABLED", "generated": 0}

    client = LLMClient(s_cfg)
    try:
        client._build()
    except LLMUnavailable as e:
        # Not fatal. The analysis is complete and correct without prose.
        log.warning("narration_unavailable", detail=str(e)[:200])
        await _record(run_id, "UNAVAILABLE", str(e)[:300], 0)
        return {"status": "UNAVAILABLE", "generated": 0, "detail": str(e)[:300]}

    log.info("narration_start", **client.describe)

    async with session_scope() as s:
        rows = (await s.execute(text("""
            SELECT c.id, c.ctype::text AS ctype, c.api_name, c.parent_object,
                   c.last_modified_date, c.attrs, cl.label::text AS label,
                   cl.confidence, cl.reason_codes
              FROM classifications cl JOIN components c ON c.id = cl.component_id
             WHERE cl.run_id = :r AND cl.label = ANY(:labels) AND c.in_scope
             ORDER BY (cl.label = 'UNUSED') DESC, cl.confidence DESC
             LIMIT :lim
        """), {"r": run_id, "labels": list(only_labels), "lim": limit})).mappings().all()

    if not rows:
        return {"status": "OK", "generated": 0}

    sem = asyncio.Semaphore(s_cfg.llm_max_concurrency)
    generated = refused = failed = flagged = 0

    async def one(row: dict) -> None:
        nonlocal generated, refused, failed, flagged
        async with sem:
            async with session_scope() as s:
                ev = (await s.execute(text("""
                    SELECT collector_id, result::text AS result, payload
                      FROM evidence WHERE component_id = :c ORDER BY collector_id
                """), {"c": row["id"]})).mappings().all()
                fl = (await s.execute(text("""
                    SELECT code, detail FROM uncertainty_flags WHERE component_id = :c
                """), {"c": row["id"]})).mappings().all()
                rf = (await s.execute(text("""
                    SELECT a.metadata_type, a.member_name, a.is_test, e.tier::text AS tier
                      FROM reference_edges e JOIN artifact a ON a.id = e.from_artifact_id
                     WHERE e.to_component_id = :c ORDER BY e.tier LIMIT 20
                """), {"c": row["id"]})).mappings().all()
                src = await s.scalar(text("""
                    SELECT body FROM component_source WHERE component_id = :c
                """), {"c": row["id"]})
                # A method has no body of its own; its class does. Without this
                # every ApexMethod summary would be written from the name alone.
                if not src and row["ctype"] == "ApexMethod" and row.get("parent_object"):
                    src = await s.scalar(text("""
                        SELECT cs.body FROM component_source cs
                          JOIN components c ON c.id = cs.component_id
                         WHERE c.run_id = :r AND c.api_name = :n
                           AND c.ctype = 'ApexClass'
                    """), {"r": run_id, "n": row["parent_object"]})

            samples = await _sample_values(sf, dict(row)) if sf else None
            prompt = _render_user_prompt(dict(row), [dict(e) for e in ev],
                                         [dict(f) for f in fl],
                                         [dict(r) for r in rf], src, samples)
            res = await client.json_call(
                SYSTEM, prompt,
                expect=("business_purpose", "evidence_summary", "risk_note"))

            status = "OK"
            err_text = res.error
            if res.refused:
                status, refused = "REFUSED", refused + 1
            elif res.error or not res.parsed:
                status, failed = "ERROR", failed + 1
                # A transport error carries its own message, but an unparseable
                # reply used to be stored with a blank error_text -- an ERROR row
                # with no reason is undiagnosable. Say which of the two it was.
                if not err_text:
                    err_text = ("response did not parse as JSON "
                                f"({len(res.text or '')} chars returned)")
            else:
                generated += 1

            p = res.parsed or {}
            async with session_scope() as s:
                await s.execute(text("""
                    INSERT INTO llm_artifacts (run_id, component_id, provider,
                        model, prompt_sha256, business_purpose, unused_rationale,
                        evidence_recap, risk_note, request, response,
                        input_tokens, output_tokens, latency_ms, status, error_text)
                    VALUES (:r, :c, :prov, :m, :hash, :bp, :ur, :er, :rn,
                            CAST(:req AS jsonb), CAST(:resp AS jsonb),
                            :it, :ot, :lat, :st, :err)
                    ON CONFLICT (run_id, component_id) DO UPDATE SET
                        business_purpose = EXCLUDED.business_purpose,
                        evidence_recap = EXCLUDED.evidence_recap,
                        risk_note = EXCLUDED.risk_note,
                        response = EXCLUDED.response, status = EXCLUDED.status,
                        error_text = EXCLUDED.error_text,
                        generated_at = now()
                """), {
                    "r": run_id, "c": row["id"], "prov": res.provider,
                    "m": res.model, "hash": res.prompt_sha256,
                    "bp": p.get("business_purpose"),
                    "ur": p.get("still_might_matter_why") or None,
                    "er": p.get("evidence_summary"),
                    "rn": p.get("risk_note"),
                    # The exact prompt is stored so a reader can audit what the
                    # model was told. Absence of that reads as evasion.
                    "req": json.dumps({"system": SYSTEM, "user": prompt}),
                    "resp": json.dumps(p or {"raw": res.text[:2000]}),
                    "it": res.input_tokens, "ot": res.output_tokens,
                    "lat": res.latency_ms, "st": status,
                    "err": err_text,
                })

                # The ONLY way narration touches a verdict: toward caution.
                if status == "OK" and p.get("still_might_matter") is True \
                        and row["label"] == "UNUSED":
                    why = str(p.get("still_might_matter_why") or "")[:400]
                    await s.execute(text("""
                        INSERT INTO uncertainty_flags (run_id, component_id, code,
                                                       severity, detail)
                        VALUES (:r, :c, 'LLM_FLAGGED_CONCERN', 'medium', :d)
                        ON CONFLICT (component_id, code) DO NOTHING
                    """), {"r": run_id, "c": row["id"],
                           "d": f"AI-flagged, unverified: {why}"})
                    flagged += 1

    await asyncio.gather(*(one(dict(r)) for r in rows))
    await _record(run_id, "OK", None, generated)

    log.info("narration_complete", generated=generated, refused=refused,
             failed=failed, flagged_for_review=flagged)
    return {"status": "OK", "generated": generated, "refused": refused,
            "failed": failed, "flagged_for_review": flagged,
            "candidates": len(rows), **client.describe}


async def _record(run_id: str, status: str, reason: str | None, hits: int) -> None:
    async with session_scope() as s:
        await s.execute(text("""
            INSERT INTO collector_run (run_id, collector_id, collector_family,
                scope_key, status, method, query_text, artifacts_searched, hits,
                unavailable_reason)
            VALUES (:r, 'S40_narration', 'llm', '*', CAST(:st AS collector_status),
                    :m, :m, 0, :h, :why)
            ON CONFLICT (run_id, collector_id, scope_key) DO UPDATE SET
                status = EXCLUDED.status, hits = EXCLUDED.hits,
                unavailable_reason = EXCLUDED.unavailable_reason
        """), {"r": run_id, "st": status, "h": hits, "why": reason,
               "m": "LLM narration of already-decided verdicts. The model never "
                    "classifies; it may only raise a concern that downgrades "
                    "UNUSED to NEEDS_REVIEW."})
