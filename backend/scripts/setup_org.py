"""One-command onboarding for a new Salesforce org.

Validates the connection, discovers what the org can and cannot do, and reports
readiness. Idempotent - safe to re-run whenever something looks wrong or after
the org's configuration changes.

    .venv\\Scripts\\python.exe scripts\\setup_org.py                 # single org
    .venv\\Scripts\\python.exe scripts\\setup_org.py acme-prod       # a named org
    .venv\\Scripts\\python.exe scripts\\setup_org.py --all           # every org
    .venv\\Scripts\\python.exe scripts\\setup_org.py acme-prod --full # + retrieve metadata

Every check states plainly what it means, because the failure modes here are
quiet ones: a restricted API user or an unretrieved metadata type does not throw,
it just makes real components look unused.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import text                              # noqa: E402

from app.config import get_settings                      # noqa: E402
from app.db.session import engine, session_scope         # noqa: E402
from app.salesforce import capabilities as cap           # noqa: E402
from app.salesforce.auth import AuthError                # noqa: E402
from app.salesforce.client import SalesforceClient       # noqa: E402
from app.salesforce.connections import (                 # noqa: E402
    ORGS_FILE,
    OrgConnection,
    load_registry,
)

OK, WARN, BAD = "  [ok]  ", "  [warn]", "  [FAIL]"


class Report:
    def __init__(self) -> None:
        self.failures: list[str] = []
        self.warnings: list[str] = []

    def ok(self, msg: str) -> None:
        print(f"{OK} {msg}")

    def warn(self, msg: str) -> None:
        print(f"{WARN} {msg}")
        self.warnings.append(msg)

    def fail(self, msg: str, fix: str = "") -> None:
        print(f"{BAD} {msg}")
        if fix:
            print(f"         fix: {fix}")
        self.failures.append(msg)


async def step_db(r: Report) -> bool:
    print("\n1. DATABASE")
    try:
        async with session_scope() as s:
            tables = await s.scalar(text(
                "SELECT count(*) FROM information_schema.tables "
                "WHERE table_schema='public' AND table_type='BASE TABLE'"))
            has_caps = await s.scalar(text(
                "SELECT to_regclass('public.org_capabilities') IS NOT NULL"))
    except Exception as e:
        r.fail(f"cannot reach the database: {str(e)[:120]}",
               "docker compose up -d postgres")
        return False
    if not tables:
        r.fail("schema is not applied",
               "docker compose down -v && docker compose up -d postgres")
        return False
    r.ok(f"schema applied ({tables} tables)")
    if not has_caps:
        r.fail("org_capabilities table missing",
               "apply backend/db/migrations/002_org_capabilities.sql")
        return False
    r.ok("org_capabilities present")
    return True


async def step_auth(conn: OrgConnection, r: Report) -> SalesforceClient | None:
    print("\n2. CONNECTION")
    print(f"        org      : {conn.alias}")
    print(f"        instance : {conn.instance_url}")
    if "login.salesforce.com" in conn.instance_url:
        r.fail("instance_url points at login.salesforce.com",
               "use the org's My Domain host; client-credentials tokens are "
               "only issued there")
        return None
    sf = SalesforceClient()
    await sf.__aenter__()
    try:
        me = await sf.identity()
    except AuthError as e:
        r.fail(f"authentication failed: {e}",
               "check the two Connected App switches: 'Enable Client "
               "Credentials Flow', and a Run As user under Edit Policies")
        await sf.__aexit__()
        return None
    except Exception as e:
        r.fail(f"authentication failed: {str(e)[:160]}")
        await sf.__aexit__()
        return None
    r.ok(f"authenticated as {me.get('preferred_username')}")
    r.ok(f"org id {me.get('organization_id')}")
    return sf


async def step_capabilities(sf: SalesforceClient, conn: OrgConnection, r: Report):
    print("\n3. CAPABILITIES (discovered, not assumed)")
    caps = await cap.probe(sf, conn.alias)
    await cap.save(caps)

    r.ok(f"edition: {caps.edition}   org: {caps.org_name}")

    if caps.api_limit_max:
        pct = 100 * (caps.api_limit_remaining or 0) / caps.api_limit_max
        msg = (f"API budget: {caps.api_limit_remaining:,} of "
               f"{caps.api_limit_max:,} remaining ({pct:.0f}%)")
        (r.ok if pct > 25 else r.warn)(msg)
        if pct <= 25:
            print("         a full run needs a few hundred calls; consider "
                  "waiting for the rolling 24h window to free some")

    # The single most important check here. Coverage is bounded by this user,
    # and anything invisible to it is indistinguishable from something unused.
    if caps.run_as_is_admin:
        r.ok(f"API user is an administrator ({caps.run_as_username})")
    elif caps.run_as_is_admin is False:
        r.warn(f"API user {caps.run_as_username} is NOT an administrator")
        print("         anything invisible to this user will look UNUSED. "
              "Strongly consider a System Administrator as the Run As user.")
    else:
        r.warn("could not determine whether the API user is an administrator")

    print("\n        feature availability")
    for label, val, why in (
        ("Event Monitoring", caps.has_event_monitoring,
         "external API traffic is unobservable without it"),
        ("Dependency API", caps.has_dependency_api,
         "declarative dependency cross-check unavailable"),
        ("field history", caps.has_field_history,
         "per-field write recency unavailable"),
        ("code coverage", caps.has_code_coverage,
         "Apex test coverage unavailable"),
    ):
        state = {True: "yes", False: "no", None: "unknown"}[val]
        suffix = "" if val else f"   ({why})"
        print(f"          {label:18} {state}{suffix}")

    if caps.routing_overrides:
        r.warn(f"{len(caps.routing_overrides)} routing correction(s) for this org")
        for k, v in sorted(caps.routing_overrides.items()):
            print(f"          {k} -> {v}")
    else:
        r.ok("endpoint routing matches the registry")

    if caps.unavailable_objects:
        r.warn(f"{len(caps.unavailable_objects)} object(s) not queryable here")
        print(f"          {', '.join(sorted(caps.unavailable_objects)[:10])}")

    return caps


async def step_inventory(sf: SalesforceClient, conn: OrgConnection, r: Report) -> str | None:
    print("\n4. INVENTORY")
    from app.pipeline.inventory import abandon_stale_runs, create_run, inventory

    me = await sf.identity()
    if (stale := await abandon_stale_runs(me["organization_id"])):
        r.warn(f"reaped {len(stale)} abandoned run(s) that were blocking this org")
    run_id = await create_run(sf, alias=conn.alias,
                              config={"source": "setup_org", "scope": "all"})
    counts = await inventory(run_id, sf)

    in_scope = sum(v for k, v in counts.items()
                   if not any(x in k for x in ("packaged", "skipped", "out of scope")))
    r.ok(f"{in_scope} in-scope components inventoried")
    for k, v in counts.items():
        if v:
            print(f"          {k:38} {v:>5}")

    async with session_scope() as s:
        aliases = await s.scalar(
            text("SELECT count(*) FROM alias WHERE run_id = :r"), {"r": run_id})
        await s.execute(text(
            "UPDATE runs SET state='SUCCEEDED', finished_at=now() WHERE id=:r"),
            {"r": run_id})
    r.ok(f"{aliases} aliases generated (the lookup table every check depends on)")
    if in_scope and aliases / max(in_scope, 1) < 2:
        r.warn("fewer than 2 aliases per component - reference matching will be weak")
    return run_id


async def step_retrieve(conn: OrgConnection, r: Report) -> None:
    print("\n5. METADATA RETRIEVE")
    from app.pipeline.retrieve import retrieve_all
    from app.salesforce.auth import build_token_provider

    s = get_settings()
    ws = s.workspace / conn.alias
    print(f"        workspace: {ws}")
    result = await retrieve_all(conn, build_token_provider(s), ws)

    if result.files_retrieved == 0:
        r.fail("no metadata retrieved",
               "check that the Salesforce CLI is installed and SF_CLI_PATH is correct")
        return
    r.ok(f"{result.files_retrieved} files ({result.bytes_retrieved:,} bytes)")

    for label in ("Report", "Dashboard", "EmailTemplate", "Document"):
        n = result.types_requested.get(label, 0)
        if n:
            r.ok(f"{label}: {n} enumerated")
        else:
            r.warn(f"{label}: none found - if the org has any, that is a coverage gap")

    if result.failed_types:
        r.warn(f"{len(result.failed_types)} metadata type(s) failed to retrieve")
        for t, why in list(result.failed_types.items())[:6]:
            print(f"          {t}: {why[:80]}")


def summarise(r: Report, caps) -> int:
    print("\n" + "=" * 68)
    if r.failures:
        print(f"NOT READY - {len(r.failures)} blocking problem(s):")
        for f in r.failures:
            print(f"  - {f}")
        return 1

    print("READY" + (f" (with {len(r.warnings)} warning(s))" if r.warnings else ""))
    if caps:
        caveats = caps.coverage_caveats()
        if caveats:
            print("\nCoverage limitations for this org. These are not bugs - they are")
            print("the honest bounds of what can be proven here, and they appear")
            print("verbatim in the final report:")
            for c in caveats:
                print(f"  - {c}")
        else:
            print("\nNo coverage limitations detected for this org.")
    print("\nNext:  .venv\\Scripts\\python.exe scripts\\run_retrieve.py"
          if "--full" not in sys.argv else "\nThis org is set up.")
    return 0


async def setup_one(conn: OrgConnection, *, full: bool) -> int:
    print("\n" + "=" * 68)
    print(f"SETTING UP: {conn.alias}")
    print("=" * 68)
    r = Report()
    caps = None

    if not await step_db(r):
        return summarise(r, caps)

    sf = await step_auth(conn, r)
    if sf is None:
        return summarise(r, caps)

    try:
        caps = await step_capabilities(sf, conn, r)
        await step_inventory(sf, conn, r)
    finally:
        await sf.__aexit__()

    if full:
        await step_retrieve(conn, r)
    else:
        print("\n5. METADATA RETRIEVE  (skipped; pass --full to include it)")

    return summarise(r, caps)


async def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    full = "--full" in sys.argv

    try:
        registry = load_registry()
    except ValueError as e:
        print(f"{BAD} {e}")
        print(f"\n  Single org : set SF_CLIENT_ID / SF_CLIENT_SECRET / "
              f"SF_INSTANCE_URL in .env")
        print(f"  Many orgs  : copy orgs.example.json to {ORGS_FILE.name}")
        return 1

    print(f"configured orgs: {', '.join(registry.aliases)}")
    targets = registry.all() if "--all" in sys.argv else [
        registry.get(args[0] if args else None)]

    worst = 0
    for conn in targets:
        worst = max(worst, await setup_one(conn, full=full))
    await engine.dispose()
    return worst


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
