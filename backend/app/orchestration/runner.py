"""Pipeline orchestrator.

Runs the analysis as an ordered set of stages, each recording its own state and
emitting events, so the pipeline is observable while it happens rather than only
afterwards.

Two properties are deliberate:

**A failing stage degrades the run; it does not abort it.** If narration cannot
reach its provider, or Event Monitoring is unlicensed, the remaining stages still
run and the missing evidence becomes a recorded gap. A gap forces NEEDS_REVIEW
downstream, so partial coverage produces caution rather than a wrong answer.

**Stage state lives in the database, not in memory.** A crashed run leaves rows
that say exactly how far it got, which is what makes the UI honest about a run
that died and what a resume would need.
"""

from __future__ import annotations

import asyncio
import json
import time
import traceback
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

import structlog
from sqlalchemy import text

from app.config import get_settings
from app.db.session import session_scope
from app.orchestration.events import EventBus

log = structlog.get_logger()


@dataclass
class StageDef:
    key: str
    title: str
    detail: str
    #: False when the stage merely enriches. A failure here degrades the run
    #: instead of failing it.
    critical: bool = False


STAGES: list[StageDef] = [
    StageDef("org.connect", "Connect & discover",
             "Authenticate, then discover this org's edition, API budget and "
             "which features are actually available", critical=True),
    StageDef("inventory", "Inventory components",
             "Find every custom field, object and Apex member, and build the "
             "alias table every later check depends on", critical=True),
    StageDef("source.retrieve", "Retrieve metadata",
             "Pull the org's metadata to disk once, so reference searching costs "
             "no API calls", critical=True),
    StageDef("index", "Index references",
             "Split code from comments, harvest string literals, and resolve "
             "every mention against the alias table", critical=True),
    StageDef("collect", "Gather evidence",
             "Eight independent collectors, each recording what it found AND "
             "what it did not", critical=True),
    StageDef("graph", "Build dependency graph",
             "Reachability from every entry point: can anything that runs or is "
             "seen actually reach this?", critical=True),
    StageDef("classify", "Classify",
             "Deterministic rules over the collected evidence. No model decides "
             "a verdict", critical=True),
    StageDef("rehearse", "Delete rehearsal",
             "Ask Salesforce whether each candidate could really be deleted. "
             "Validate-only — deletes nothing"),
    StageDef("narrate", "AI narration",
             "Plain-language summaries of verdicts already decided"),
    StageDef("report", "Build report",
             "XLSX, Markdown, JSON and a deployable delete package"),
]


class RunAborted(RuntimeError):
    pass


class Orchestrator:
    def __init__(self, run_id: str, alias: str) -> None:
        self.run_id = run_id
        self.alias = alias
        self.bus = EventBus(run_id)
        self.ctx: dict = {}
        self._cancelled = False

    # -- stage bookkeeping ----------------------------------------------------

    async def _seed_stages(self) -> None:
        async with session_scope() as s:
            for i, sd in enumerate(STAGES):
                await s.execute(text("""
                    INSERT INTO stages (run_id, key, title, depends_on, state, ordinal)
                    VALUES (:r, :k, :t, '{}', 'PENDING', :o)
                    ON CONFLICT (run_id, key) DO UPDATE
                       SET state = 'PENDING', ordinal = EXCLUDED.ordinal
                """), {"r": self.run_id, "k": sd.key, "t": sd.title, "o": i})

    async def _set_stage(self, key: str, state: str, **fields) -> None:
        sets = ["state = CAST(:st AS stage_state)"]
        params: dict = {"r": self.run_id, "k": key, "st": state}
        for col, val in fields.items():
            sets.append(f"{col} = :{col}")
            params[col] = val
        if state == "RUNNING":
            sets.append("started_at = now()")
        if state in ("SUCCEEDED", "FAILED", "DEGRADED", "SKIPPED"):
            sets.append("finished_at = now()")
        async with session_scope() as s:
            await s.execute(text(
                f"UPDATE stages SET {', '.join(sets)} "
                "WHERE run_id = :r AND key = :k"), params)

    async def _set_run(self, state: str, error: str | None = None) -> None:
        async with session_scope() as s:
            await s.execute(text("""
                UPDATE runs SET state = CAST(:st AS run_state),
                       finished_at = CASE WHEN :st IN
                         ('SUCCEEDED','DEGRADED','FAILED','CANCELLED')
                         THEN now() ELSE finished_at END,
                       error = COALESCE(CAST(:err AS jsonb), error)
                 WHERE id = :r
            """), {"r": self.run_id, "st": state,
                   "err": json.dumps({"message": error}) if error else None})

    def cancel(self) -> None:
        self._cancelled = True

    # -- execution ------------------------------------------------------------

    async def run(self, fns: dict[str, Callable[[Orchestrator], Awaitable[dict]]]) -> str:
        await self.bus.start()
        await self._seed_stages()
        await self._set_run("RUNNING")
        self.bus.emit("run.started", None, alias=self.alias,
                      stages=[{"key": s.key, "title": s.title,
                               "detail": s.detail, "critical": s.critical}
                              for s in STAGES])
        degraded = False
        try:
            for sd in STAGES:
                if self._cancelled:
                    await self._set_stage(sd.key, "SKIPPED",
                                          skip_reason="run cancelled")
                    self.bus.emit("stage.skipped", sd.key, reason="run cancelled")
                    continue
                fn = fns.get(sd.key)
                if fn is None:
                    await self._set_stage(sd.key, "SKIPPED",
                                          skip_reason="not implemented")
                    self.bus.emit("stage.skipped", sd.key, reason="not implemented")
                    continue

                await self._set_stage(sd.key, "RUNNING")
                self.bus.emit("stage.started", sd.key, title=sd.title,
                              detail=sd.detail)
                started = time.monotonic()
                try:
                    result = await fn(self) or {}
                    ms = int((time.monotonic() - started) * 1000)
                    state = result.pop("_state", "SUCCEEDED")
                    if state == "DEGRADED":
                        degraded = True
                    await self._set_stage(
                        sd.key, state,
                        api_calls_used=int(result.get("api_calls", 0) or 0),
                        skip_reason=result.get("_reason"))
                    self.bus.emit("stage.finished", sd.key, state=state,
                                  duration_ms=ms, **_jsonable(result))
                except Exception as e:
                    ms = int((time.monotonic() - started) * 1000)
                    detail = f"{type(e).__name__}: {e}"
                    log.exception("stage_failed", stage=sd.key)
                    await self._set_stage(
                        sd.key, "FAILED",
                        error=json.dumps({"message": detail}))
                    self.bus.emit("stage.failed", sd.key, error=detail,
                                  duration_ms=ms,
                                  traceback=traceback.format_exc()[-1500:])
                    if sd.critical:
                        # Only a critical stage can end the run. Everything else
                        # leaves a gap, and a gap forces NEEDS_REVIEW rather than
                        # a confident wrong answer.
                        await self._set_run("FAILED", detail)
                        self.bus.emit("run.finished", None, state="FAILED",
                                      reason=f"critical stage {sd.key} failed")
                        return "FAILED"
                    degraded = True

            final = "CANCELLED" if self._cancelled else (
                "DEGRADED" if degraded else "SUCCEEDED")
            await self._set_run(final)
            self.bus.emit("run.finished", None, state=final)
            return final
        finally:
            # Drain before returning so a client that reconnects immediately
            # sees the terminal event rather than an apparently-hung run.
            await self.bus.stop()


def _jsonable(d: dict) -> dict:
    out = {}
    for k, v in d.items():
        if k.startswith("_"):
            continue
        out[k] = v if isinstance(v, (str, int, float, bool, type(None), list, dict)) \
            else str(v)
    return out


# ---------------------------------------------------------------------------
# Run lifecycle
# ---------------------------------------------------------------------------

_ACTIVE: dict[str, Orchestrator] = {}


def active_runs() -> dict[str, Orchestrator]:
    return _ACTIVE


async def start_run(alias: str | None = None, *, config: dict | None = None) -> str:
    """Create the run row and kick off execution in the background."""
    from app.orchestration.stages import STAGE_FNS
    from app.pipeline.inventory import abandon_stale_runs, create_run
    from app.salesforce.client import SalesforceClient
    from app.salesforce.connections import load_registry

    s_cfg = get_settings()
    conn = load_registry().get(alias)

    async with SalesforceClient() as sf:
        me = await sf.identity()
        # A crashed run leaves its row RUNNING and locks the org out. Reap first;
        # the constraint is right, it just needs a reaper.
        await abandon_stale_runs(me["organization_id"])
        run_id = await create_run(sf, alias=conn.alias, config={
            "source": "ui", "alias": conn.alias,
            "rehearsal": s_cfg.analysis_enable_delete_rehearsal,
            "llm": s_cfg.llm_enabled, **(config or {})})

    orch = Orchestrator(run_id, conn.alias)
    orch.ctx["conn"] = conn
    _ACTIVE[run_id] = orch

    async def _go() -> None:
        try:
            await orch.run(STAGE_FNS)
        finally:
            _ACTIVE.pop(run_id, None)

    asyncio.create_task(_go(), name=f"run:{run_id}")
    return run_id
