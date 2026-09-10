"""Stage S40 — LLM narration of already-decided verdicts.

The boundary that matters, and the reason this is a separate stage rather than
part of classification:

    The rule engine decides first. The model explains, and may only nudge a
    verdict in carefully limited directions based on the same evidence the rules
    already saw — never invent references, never invent UNUSED.

Allowed moves (only when status=OK and JSON is parseable):

  * UNUSED → NEEDS_REVIEW via ``LLM_FLAGGED_CONCERN`` when
    ``still_might_matter`` is true (toward caution).
  * NEEDS_REVIEW → USED via Tier-A evidence from ``S40_narration`` when
    ``revise_to_used`` is true and the listed evidence already supports use
    (toward "this really is used").

It can never invent an UNUSED verdict, and it can never promote UNUSED straight
to USED.
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

A deterministic rule engine has already produced a provisional verdict. You are \
given that verdict and the evidence behind it.

Rules you MUST follow:
- Do not invent references, runtime activity, or dependencies that are not listed.
- Never recommend UNUSED or deletion. You may only move toward caution, or \
confirm clear use that the listed evidence already shows.
- If the evidence is thin, say so — do not fill gaps with plausible narrative.
- Be precise and unexcited. No praise, no hedging filler.

Reply with ONLY a JSON object, no prose around it, no code fence:

{
  "business_purpose": "<=45 words. What this component appears to do, in plain \
language, from its name, type and the listed code/refs. If you cannot tell, say so.",
  "evidence_summary": "<=90 words. Where we looked and what we found. Name the \
places that returned nothing explicitly.",
  "risk_note": "<=45 words. Strongest honest argument for KEEPING this, or \
'none identified'.",
  "still_might_matter": true|false,
  "still_might_matter_why": "<=30 words, or empty. For UNUSED only: set true \
ONLY if you see a concrete reason this could still be load-bearing that the \
listed evidence would not have caught.",
  "revise_to_used": true|false,
  "revise_to_used_why": "<=40 words, or empty. For NEEDS_REVIEW only: set true \
ONLY when the listed evidence already shows clear use (binding references, \
reachability, runtime/data, dependency API). Cite those signals. Never invent \
new ones."
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
    label = row["label"]
    if label == "UNUSED":
        verdict_line = (
            f"VERDICT (provisional): {label}. You may ONLY raise "
            "still_might_matter toward Needs review — never Used."
        )
    elif label == "NEEDS_REVIEW":
        verdict_line = (
            f"VERDICT (provisional): {label}. You may set revise_to_used=true "
            "ONLY if listed evidence already proves use. Never Unused."
        )
    else:
        verdict_line = f"VERDICT (provisional): {label}. Explain only; do not revise."

    parts = [
        f"COMPONENT: {row['api_name']}  ({row['ctype']})",
        f"OBJECT: {row.get('parent_object') or '(n/a)'}",
        f"LAST MODIFIED: {row.get('last_modified_date') or 'unknown'}",
        "",
        verdict_line,
        f"CONFIDENCE: {round(row['confidence'])}",
        f"REASONS: {', '.join(row.get('reason_codes') or [])}",
        "",
        "EVIDENCE FROM EACH COLLECTOR:",
    ]
    for e in evidence:
        payload = json.dumps(e["payload"], default=str)[:400]
        tier = e.get("tier")
        tier_bit = f" (tier {tier})" if tier else ""
        parts.append(f"  - {e['collector_id']}: {e['result']}{tier_bit}")
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
    attrs = row.get("attrs") or {}
    facts = [(k, attrs.get(k)) for k in
             ("data_type", "is_calculated", "business_status", "relationship_name",
              "modifiers", "annotations", "return_type", "on_object", "events",
              "api_version", "loc", "is_test", "is_entry_point",
              "is_exposed", "targets", "access", "interfaces")
             if attrs.get(k) not in (None, "", [], False)]
    if facts:
        parts.append("")
        parts.append("COMPONENT FACTS:")
        for k, v in facts:
            parts.append(f"  {k}: {v}")

    if samples:
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
    """A few real values for a populated field."""
    if row.get("ctype") != "CustomField" or not row.get("parent_object"):
        return None
    field = row["api_name"].split(".", 1)[-1]
    dtype = str((row.get("attrs") or {}).get("data_type") or "").lower()
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
    """Generate summaries for UNUSED and NEEDS_REVIEW; optionally revise."""
    s_cfg = get_settings()
    if not s_cfg.llm_enabled:
        log.info("narration_disabled")
        return {"status": "DISABLED", "generated": 0}

    client = LLMClient(s_cfg)
    try:
        client._build()
    except LLMUnavailable as e:
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
    generated = refused = failed = flagged = promoted = 0

    async def one(row: dict) -> None:
        nonlocal generated, refused, failed, flagged, promoted
        async with sem:
            async with session_scope() as s:
                ev = (await s.execute(text("""
                    SELECT collector_id, result::text AS result, tier::text AS tier,
                           payload
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
                        unused_rationale = EXCLUDED.unused_rationale,
                        evidence_recap = EXCLUDED.evidence_recap,
                        risk_note = EXCLUDED.risk_note,
                        response = EXCLUDED.response, status = EXCLUDED.status,
                        error_text = EXCLUDED.error_text,
                        generated_at = now()
                """), {
                    "r": run_id, "c": row["id"], "prov": res.provider,
                    "m": res.model, "hash": res.prompt_sha256,
                    "bp": p.get("business_purpose"),
                    "ur": (p.get("still_might_matter_why")
                           or p.get("revise_to_used_why") or None),
                    "er": p.get("evidence_summary"),
                    "rn": p.get("risk_note"),
                    "req": json.dumps({"system": SYSTEM, "user": prompt}),
                    "resp": json.dumps(p or {"raw": (res.text or "")[:2000]}),
                    "it": res.input_tokens, "ot": res.output_tokens,
                    "lat": res.latency_ms, "st": status,
                    "err": err_text,
                })

                if status != "OK":
                    return

                # UNUSED → NEEDS_REVIEW (caution only).
                if p.get("still_might_matter") is True and row["label"] == "UNUSED":
                    why = str(p.get("still_might_matter_why") or "")[:400]
                    await s.execute(text("""
                        INSERT INTO uncertainty_flags (run_id, component_id, code,
                                                       severity, detail, source_ref)
                        VALUES (:r, :c, 'LLM_FLAGGED_CONCERN', 'medium', :d,
                                CAST(:src AS jsonb))
                        ON CONFLICT (component_id, code) DO UPDATE SET
                            detail = EXCLUDED.detail,
                            source_ref = EXCLUDED.source_ref
                    """), {"r": run_id, "c": row["id"],
                           "d": f"AI-flagged, unverified: {why}",
                           "src": json.dumps({
                               "decision": "NEEDS_REVIEW",
                               "from": "UNUSED",
                               "why": why,
                               "model": res.model,
                           })})
                    flagged += 1

                # NEEDS_REVIEW → USED when listed evidence already supports use.
                if p.get("revise_to_used") is True and row["label"] == "NEEDS_REVIEW":
                    why = str(p.get("revise_to_used_why")
                              or p.get("evidence_summary") or "")[:400]
                    await s.execute(text("""
                        INSERT INTO evidence (run_id, component_id, collector_id,
                                              result, tier, weight, payload)
                        VALUES (:r, :c, 'S40_narration',
                                CAST('EVIDENCE_OF_USE' AS collector_result),
                                CAST('A' AS evidence_tier), 0.85,
                                CAST(:p AS jsonb))
                        ON CONFLICT (component_id, collector_id) DO UPDATE SET
                            result = EXCLUDED.result, tier = EXCLUDED.tier,
                            weight = EXCLUDED.weight, payload = EXCLUDED.payload
                    """), {"r": run_id, "c": row["id"],
                           "p": json.dumps({
                               "llm_revision": "USED",
                               "from": "NEEDS_REVIEW",
                               "why": why,
                               "model": res.model,
                               "provider": res.provider,
                               "note": "AI confirmed use from already-listed "
                                       "evidence; not a new reference invent.",
                           })})
                    await s.execute(text("""
                        INSERT INTO uncertainty_flags (run_id, component_id, code,
                                                       severity, detail, source_ref)
                        VALUES (:r, :c, 'LLM_CONFIRMS_USE', 'low', :d,
                                CAST(:src AS jsonb))
                        ON CONFLICT (component_id, code) DO UPDATE SET
                            detail = EXCLUDED.detail,
                            source_ref = EXCLUDED.source_ref
                    """), {"r": run_id, "c": row["id"],
                           "d": f"AI confirmed use from listed evidence: {why}",
                           "src": json.dumps({
                               "decision": "USED",
                               "from": "NEEDS_REVIEW",
                               "why": why,
                               "model": res.model,
                           })})
                    promoted += 1

    await asyncio.gather(*(one(dict(r)) for r in rows))
    await _record(run_id, "OK", None, generated)

    log.info("narration_complete", generated=generated, refused=refused,
             failed=failed, flagged_for_review=flagged,
             promoted_to_used=promoted)
    return {"status": "OK", "generated": generated, "refused": refused,
            "failed": failed, "flagged_for_review": flagged,
            "promoted_to_used": promoted,
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
               "m": "LLM narration of provisional verdicts. May flag UNUSED toward "
                    "NEEDS_REVIEW, or confirm NEEDS_REVIEW → USED from listed "
                    "evidence. Never invents UNUSED."})
