"""Tool tests against the real analysis database and retrieved workspace.

These are integration tests on purpose. The tools exist to report what the
pipeline actually found, so testing them against fixtures would verify the
fixtures rather than the tools. They skip cleanly when no completed run exists.

The guardrail tests are the ones that matter most: they assert the boundaries
the agent design depends on, and none of them may be relaxed for convenience.
"""

from __future__ import annotations

import pytest

from app.agent.tools import REGISTRY, call
from app.agent.tools.analysis import _latest_run_id


@pytest.fixture
async def run_id() -> str:
    rid = await _latest_run_id()
    if not rid:
        pytest.skip("no completed run in the database")
    return rid


# -- registry -----------------------------------------------------------------

def test_every_tool_has_a_usable_schema():
    assert REGISTRY, "no tools registered"
    for t in REGISTRY.values():
        assert t.description.strip(), f"{t.name} has no description"
        assert t.schema.get("type") == "object", f"{t.name} schema is not an object"
        for name in t.schema.get("required", []):
            assert name in t.schema["properties"], \
                f"{t.name} requires {name!r} which is not in properties"


async def test_unknown_tool_reports_alternatives():
    out = await call("no_such_tool")
    assert "error" in out and out["available"]


async def test_bad_arguments_return_the_schema_not_a_crash():
    out = await call("get_component", {"wrong_kwarg": 1})
    assert "error" in out and "schema" in out


# -- analysis -----------------------------------------------------------------

async def test_run_summary_reports_collectors_and_caveats(run_id):
    out = await call("get_run_summary", {"run_id": run_id})
    assert out["ok"]
    r = out["result"]
    assert r["verdicts"], "a completed run must have verdicts"
    assert r["collectors"], "a completed run must record collector outcomes"


async def test_query_components_filters_by_verdict(run_id):
    out = await call("query_components",
                     {"run_id": run_id, "verdict": "NEEDS_REVIEW"})
    assert out["ok"]
    for c in out["result"]["components"]:
        assert c["verdict"] == "NEEDS_REVIEW"


async def test_get_component_returns_negative_evidence(run_id):
    """The load-bearing claim: collectors that searched and found nothing."""
    listing = (await call("query_components",
                          {"run_id": run_id, "limit": 200}))["result"]
    assert listing["components"], "run has no components"
    name = listing["components"][0]["api_name"]

    out = await call("get_component", {"api_name": name, "run_id": run_id})
    assert out["ok"]
    r = out["result"]
    assert r["component"]["api_name"].lower() == name.lower()
    assert "evidence" in r and "coverage_gaps" in r


async def test_unknown_component_suggests_near_matches(run_id):
    out = await call("get_component",
                     {"api_name": "Definitely_Not_A_Field__c", "run_id": run_id})
    assert out["ok"]
    assert "error" in out["result"] and "did_you_mean" in out["result"]


# -- workspace ----------------------------------------------------------------

async def test_grep_finds_a_known_token():
    out = await call("grep_workspace", {"pattern": "CustomField", "glob": "**/*.xml"})
    assert out["ok"]
    r = out["result"]
    if "error" in r:
        pytest.skip(r["error"])
    assert r["files_scanned"] > 0


async def test_grep_reports_absence_as_a_result_not_a_failure():
    out = await call("grep_workspace",
                     {"pattern": "zzz_string_that_cannot_appear_zzz"})
    assert out["ok"]
    r = out["result"]
    if "error" in r:
        pytest.skip(r["error"])
    assert r["match_count"] == 0
    assert r["note"], "a zero-match grep must say so explicitly"


async def test_bad_regex_is_reported_not_raised():
    out = await call("grep_workspace", {"pattern": "([unclosed"})
    assert out["ok"] and "error" in out["result"]


# -- guardrails ---------------------------------------------------------------

@pytest.mark.parametrize("path", [
    "../../../../etc/passwd",
    "../../.env",
    "..\\..\\.env",
])
async def test_read_file_cannot_escape_the_workspace(path):
    out = await call("read_workspace_file", {"path": path})
    assert out["ok"]
    err = out["result"].get("error", "")
    assert "escape" in err or "no such file" in err, \
        f"path traversal was not refused: {path}"


@pytest.mark.parametrize("soql", [
    "DELETE FROM Account",
    "UPDATE Account SET Name = 'x'",
    "  insert into Foo values (1)",
])
async def test_soql_refuses_writes(soql):
    out = await call("soql_query", {"soql": soql})
    assert out["ok"] and "error" in out["result"], f"write slipped through: {soql}"


async def test_select_containing_a_write_word_is_still_refused():
    """Conservative by design: a false refusal costs a retry, the reverse is fatal."""
    out = await call("soql_query",
                     {"soql": "SELECT Id FROM Case WHERE Subject = 'delete me'"})
    assert "error" in out["result"]


async def test_no_tool_can_write_outside_agent_finding():
    """Exactly one write tool may ever exist, and it is not registered yet."""
    writers = [t.name for t in REGISTRY.values() if t.write]
    assert writers in ([], ["propose_finding"]), \
        f"unexpected write tools: {writers}"
