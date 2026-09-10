"""Salesforce REST/Tooling client.

Every call goes through the governor. Nothing here shells out to the `sf` CLI:
`sf api request rest --body` mangles JSON quoting on Windows (verified), and the
CLI's own API consumption is invisible to the governor. The CLI is reserved for
`project retrieve` and `project deploy --dry-run`, and those are wrapped in an
explicit LONG lease.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any, Literal
from urllib.parse import quote

import httpx
import structlog

from app.config import Settings, get_settings
from app.salesforce.auth import TokenProvider, build_token_provider
from app.salesforce.governor import CallClass, SalesforceGovernor
from app.salesforce.routing import assert_routable, sobject_of

log = structlog.get_logger()

MAX_AGGREGATES_PER_QUERY = 40
_RETRYABLE_STATUS = {500, 502, 503, 504}


class SalesforceError(RuntimeError):
    def __init__(self, message: str, *, error_code: str = "", status: int = 0) -> None:
        super().__init__(message)
        self.error_code = error_code
        self.status = status


class SalesforceClient:
    def __init__(
        self,
        settings: Settings | None = None,
        governor: SalesforceGovernor | None = None,
        token_provider: TokenProvider | None = None,
    ) -> None:
        self._s = settings or get_settings()
        self.governor = governor or SalesforceGovernor(self._s)
        self._tokens = token_provider or build_token_provider(self._s)
        self._http: httpx.AsyncClient | None = None

    async def __aenter__(self) -> SalesforceClient:
        self._http = httpx.AsyncClient(
            http2=True,
            # The 5-concurrent ceiling applies only to requests exceeding 20s.
            # Capping ordinary calls at 15s means they can never enter that
            # class at all, which is why SHORT gets a generous semaphore.
            timeout=httpx.Timeout(self._s.sf_client_timeout_seconds, connect=10.0),
            limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
        )
        return self

    async def __aexit__(self, *exc: object) -> None:
        if self._http:
            await self._http.aclose()

    # -- core request ----------------------------------------------------------

    async def _request(
        self,
        method: str,
        path: str,
        *,
        call_class: CallClass = CallClass.SHORT,
        cost: int = 1,
        json_body: Any | None = None,
        attempt: int = 0,
    ) -> Any:
        assert self._http is not None, "use `async with SalesforceClient() as sf:`"
        token = await self._tokens.token()
        url = path if path.startswith("http") else f"{token.instance_url}{path}"

        async with self.governor.lease(call_class, cost) as lease:
            resp = await self._http.request(
                method, url,
                headers={
                    "Authorization": f"Bearer {token.value}",
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                },
                content=json.dumps(json_body).encode() if json_body is not None else None,
            )
            lease.observe(resp.headers.get("Sforce-Limit-Info"))

        if resp.status_code < 300:
            return resp.json() if resp.content else None

        code, message = _parse_error(resp)

        # Session died mid-run (org logout, token revoked). Refresh once.
        if resp.status_code == 401 and attempt == 0:
            log.info("sf_session_expired_refreshing")
            await self._tokens.token(force_refresh=True)
            return await self._request(method, path, call_class=call_class,
                                       cost=cost, json_body=json_body, attempt=1)

        # Budget gone. Do NOT retry - retrying is what turns a soft limit into a
        # hard block. Open the breaker and let the run park with checkpoints.
        if code == "REQUEST_LIMIT_EXCEEDED":
            self.governor.trip()
            raise SalesforceError(message, error_code=code, status=resp.status_code)

        if resp.status_code in _RETRYABLE_STATUS and attempt < 4:
            delay = min(30.0, 2 ** attempt) * (0.5 + 0.5 * attempt)
            log.warning("sf_retrying", status=resp.status_code, attempt=attempt, delay_s=delay)
            await asyncio.sleep(delay)
            return await self._request(method, path, call_class=call_class,
                                       cost=cost, json_body=json_body, attempt=attempt + 1)

        raise SalesforceError(message, error_code=code, status=resp.status_code)

    # -- queries ---------------------------------------------------------------

    async def query(
        self,
        soql: str,
        *,
        tooling: bool = False,
        all_rows: bool = False,
        check_routing: bool = True,
    ) -> list[dict]:
        """Run SOQL, following queryLocator pagination to completion.

        Routing is asserted before the call. Sending a query to the wrong
        endpoint returns INVALID_TYPE, which downstream is indistinguishable
        from "no records found" - the exact path by which a live component gets
        classified UNUSED.
        """
        if check_routing:
            target = sobject_of(soql)
            if target:
                assert_routable(target, tooling=tooling)
        base = f"/services/data/v{self._s.sf_api_version}/{'tooling/' if tooling else ''}query"
        path = f"{base}?q={quote(soql)}"
        records: list[dict] = []
        while True:
            page = await self._request("GET", path)
            records.extend(page.get("records", []))
            nxt = page.get("nextRecordsUrl")
            if page.get("done", True) or not nxt or not all_rows:
                if not nxt or page.get("done", True):
                    break
            path = nxt
        return records

    async def query_count(self, soql: str, *, tooling: bool = False) -> int:
        target = sobject_of(soql)
        if target:
            assert_routable(target, tooling=tooling)
        base = f"/services/data/v{self._s.sf_api_version}/{'tooling/' if tooling else ''}query"
        page = await self._request("GET", f"{base}?q={quote(soql)}")
        return int(page.get("totalSize", 0))

    async def composite_batch(
        self, subrequests: list[dict], *, halt_on_error: bool = False
    ) -> list[dict]:
        """Execute up to 25 subrequests in one HTTP round trip.

        Verified: there is NO 5-query sublimit here (unlike ``/composite``);
        batches of 7 and 20 distinct queries both returned all-200.

        Also verified, and contrary to most documentation: this does NOT cost a
        single API call. Cost scales with subrequest count, so the estimate
        comes from the governor's measured ratio rather than a flat 1.

        Governor limits (100 SOQL, 150 DML, 10s CPU) apply CUMULATIVELY across
        the whole batch and blowing one aborts everything, so callers should
        size batches by governor headroom, not just the 25 ceiling.
        """
        if not subrequests:
            return []
        if len(subrequests) > 25:
            raise ValueError(f"composite/batch accepts at most 25 subrequests, got {len(subrequests)}")

        body = {"batchRequests": subrequests, "haltOnError": halt_on_error}
        result = await self._request(
            "POST",
            f"/services/data/v{self._s.sf_api_version}/composite/batch",
            cost=self.governor.composite_cost(len(subrequests)),
            json_body=body,
        )
        return result.get("results", [])

    async def batched_queries(
        self, soqls: list[str], *, tooling: bool = False
    ) -> list[dict | None]:
        """Run many SOQL statements, packed into composite batches.

        Returns one entry per input, positionally aligned. A failed subrequest
        yields ``None`` rather than raising, so one bad query cannot discard the
        results of the other 24.
        """
        for q in soqls:
            target = sobject_of(q)
            if target:
                assert_routable(target, tooling=tooling)

        prefix = f"v{self._s.sf_api_version}/{'tooling/' if tooling else ''}query"
        out: list[dict | None] = []
        size = self._s.sf_composite_batch_size
        for i in range(0, len(soqls), size):
            chunk = soqls[i : i + size]
            subs = [{"method": "GET", "url": f"{prefix}?q={quote(q)}"} for q in chunk]
            results = await self.composite_batch(subs)
            for q, r in zip(chunk, results, strict=False):
                if r.get("statusCode", 500) < 300:
                    out.append(r.get("result"))
                else:
                    log.warning("subrequest_failed", soql=q[:120],
                                status=r.get("statusCode"), body=str(r.get("result"))[:200])
                    out.append(None)
        return out

    # -- specialised probes ----------------------------------------------------

    async def field_population(
        self, sobject: str, fields: list[str]
    ) -> tuple[dict[str, dict[str, int]], dict[str, str]]:
        """Count non-null values for many fields in as few calls as possible.

        ``COUNT(field)`` counts non-null values and many aggregates fit in one
        SELECT, which makes field population cost O(objects) instead of
        O(fields) - the difference between a feasible and an infeasible run.
        This is also precisely why Bulk API is the wrong tool: it supports
        neither COUNT() nor GROUP BY, so it would force one query per field and
        reintroduce the cost explosion.

        Not every field type accepts COUNT(), and **one unsupported field fails
        the entire query** - a Boolean field was observed doing exactly that.
        The set of rejected types is poorly documented and varies, so rather
        than trusting a hand-maintained list this parses the offending field out
        of the error and retries without it. Self-healing beats a list that
        silently rots as Salesforce adds types.

        Returns ``(populations, unsupported)`` where ``unsupported`` maps field
        name to the reason it was excluded. Those need a per-field existence
        probe, and must never be treated as "no data found".
        """
        results: dict[str, dict[str, int]] = {}
        unsupported: dict[str, str] = {}

        for i in range(0, len(fields), MAX_AGGREGATES_PER_QUERY):
            chunk = [f for f in fields[i : i + MAX_AGGREGATES_PER_QUERY]]
            # Retry-shrinking loop: each pass drops whichever field Salesforce
            # names in the error, until the remainder aggregates cleanly.
            while chunk:
                # Positional aliases: field API names are too long for SOQL alias
                # rules and would collide.
                selects = ", ".join(f"COUNT({f}) c{n}" for n, f in enumerate(chunk))
                soql = f"SELECT COUNT(Id) total, {selects} FROM {sobject}"
                try:
                    rows = await self.query(soql)
                except SalesforceError as e:
                    bad = _offending_field(str(e), chunk)
                    if not bad:
                        log.warning("aggregate_failed_unparseable",
                                    sobject=sobject, error=str(e)[:200])
                        for f in chunk:
                            unsupported[f] = "aggregate query failed"
                        break
                    unsupported[bad] = "field type does not support COUNT()"
                    chunk = [f for f in chunk if f != bad]
                    log.info("aggregate_dropped_field", sobject=sobject, field=bad,
                             remaining=len(chunk))
                    continue

                if rows:
                    row = rows[0]
                    total = int(row.get("total") or 0)
                    for n, f in enumerate(chunk):
                        results[f] = {"total": total, "populated": int(row.get(f"c{n}") or 0)}
                break

        return results, unsupported

    async def field_has_any_value(self, sobject: str, field: str) -> bool | None:
        """Existence probe for fields that cannot be aggregated.

        Returns None when even this fails, so the caller records INCONCLUSIVE
        rather than mistaking an error for an empty field.
        """
        try:
            rows = await self.query(
                f"SELECT Id FROM {sobject} WHERE {field} != null LIMIT 1"
            )
            return len(rows) > 0
        except SalesforceError as e:
            log.warning("existence_probe_failed", sobject=sobject, field=field,
                        error=str(e)[:160])
            return None

    async def get_text(
        self,
        path: str,
        *,
        call_class: CallClass = CallClass.SHORT,
        cost: int = 1,
        attempt: int = 0,
        accept: str = "text/csv, text/plain, */*",
    ) -> str:
        """GET a non-JSON body (EventLogFile CSV, raw metadata blobs, etc.)."""
        assert self._http is not None, "use `async with SalesforceClient() as sf:`"
        token = await self._tokens.token()
        url = path if path.startswith("http") else f"{token.instance_url}{path}"

        async with self.governor.lease(call_class, cost) as lease:
            resp = await self._http.request(
                "GET", url,
                headers={
                    "Authorization": f"Bearer {token.value}",
                    "Accept": accept,
                },
            )
            lease.observe(resp.headers.get("Sforce-Limit-Info"))

        if resp.status_code < 300:
            return resp.text

        code, message = _parse_error(resp)
        if resp.status_code == 401 and attempt == 0:
            log.info("sf_session_expired_refreshing")
            await self._tokens.token(force_refresh=True)
            return await self.get_text(
                path, call_class=call_class, cost=cost, attempt=1, accept=accept)

        if resp.status_code in _RETRYABLE_STATUS and attempt < 4:
            delay = min(30.0, 2 ** attempt) * (0.5 + 0.5 * attempt)
            log.warning("sf_retrying", status=resp.status_code, attempt=attempt, delay_s=delay)
            await asyncio.sleep(delay)
            return await self.get_text(
                path, call_class=call_class, cost=cost, attempt=attempt + 1, accept=accept)

        raise SalesforceError(message, error_code=code, status=resp.status_code)

    async def get_event_log_file(self, event_log_file_id: str) -> str:
        """Download the CSV body for one ``EventLogFile`` row."""
        path = (
            f"/services/data/v{self._s.sf_api_version}/sobjects/"
            f"EventLogFile/{event_log_file_id}/LogFile"
        )
        return await self.get_text(path, call_class=CallClass.LONG, cost=2)

    async def limits(self) -> dict:
        data = await self._request("GET", f"/services/data/v{self._s.sf_api_version}/limits")
        return data or {}

    async def daily_api_remaining(self) -> tuple[int, int]:
        """Returns (remaining, max) and seeds the governor's absolute position."""
        lim = await self.limits()
        d = lim.get("DailyApiRequests", {})
        remaining, maximum = int(d.get("Remaining", 0)), int(d.get("Max", 0))
        self.governor._reconcile(remaining=remaining, limit=maximum)
        return remaining, maximum

    async def identity(self) -> dict:
        """Who the analysis is running as.

        Worth recording on every run: the analysis can only see what this user
        sees, and a restricted user narrows coverage in a way that is
        indistinguishable from components genuinely being unused.
        """
        return await self._request("GET", "/services/oauth2/userinfo")


def _offending_field(error_text: str, candidates: list[str]) -> str | None:
    """Pull the rejected field name out of an aggregate error.

    Salesforce phrases it as: "field Is_Priority__c does not support aggregate
    operator COUNT". Matching against the candidate list rather than a regex on
    the message keeps this robust to wording changes across API versions.
    """
    lowered = error_text.lower()
    if "aggregate" not in lowered and "group by" not in lowered:
        return None
    # Longest first, so Order_Date__c is not matched by a shorter substring.
    for f in sorted(candidates, key=len, reverse=True):
        if f.lower() in lowered:
            return f
    return None


def _parse_error(resp: httpx.Response) -> tuple[str, str]:
    try:
        body = resp.json()
    except Exception:
        return "", f"HTTP {resp.status_code}: {resp.text[:300]}"
    if isinstance(body, list) and body:
        return body[0].get("errorCode", ""), body[0].get("message", "")
    if isinstance(body, dict):
        return (
            body.get("errorCode", body.get("error", "")),
            body.get("message", body.get("error_description", str(body)[:300])),
        )
    return "", str(body)[:300]


QueryKind = Literal["rest", "tooling"]
