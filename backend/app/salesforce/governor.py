"""Salesforce API rate-limit governor.

Three constraints are live simultaneously and they have different shapes, so a
plain token bucket is not enough:

  A. A rolling 24-hour request pool - NOT a daily counter that resets.
  B. A ceiling of 5 concurrent requests that run longer than 20 seconds.
  C. A per-run budget envelope, so one analysis cannot starve the org.

Two measured facts from this org shape the implementation:

  * The pool is shared with humans and other tools. ~3,250 calls were already
    consumed by unrelated activity before any run started, so a run can never
    assume it owns the budget.

  * Consecutive ``/limits`` reads moved *backwards* (-2) because the rolling
    window releases calls while other clients consume them. Per-call cost
    therefore cannot be derived by differencing two readings. We count our own
    calls for attribution and use ``Sforce-Limit-Info`` only to correct
    absolute position.
"""

from __future__ import annotations

import asyncio
import random
import re
import time
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from enum import Enum

import structlog

from app.config import Settings, get_settings

log = structlog.get_logger()

_LIMIT_RE = re.compile(r"api-usage=(\d+)/(\d+)")

#: Minimum gap between budget ticks. ~2 Hz is faster than a human reads a gauge
#: and slow enough that a long run does not flood the event log.
_TICK_INTERVAL_S = 0.5


class CallClass(str, Enum):
    """Determines which concurrency ceiling a call draws from."""

    SHORT = "SHORT"
    #: Metadata retrieve, deploy, bulk. These genuinely exceed 20s and so
    #: contend for the scarce 5-concurrent long-running slots.
    LONG = "LONG"


class BudgetExhausted(RuntimeError):
    """Raised when the org's API pool is spent. The run parks; it does not fail."""

    def __init__(self, remaining: int, floor: int) -> None:
        super().__init__(
            f"Salesforce API budget exhausted: {remaining} remaining, "
            f"safety floor is {floor}"
        )
        self.remaining = remaining
        self.floor = floor


@dataclass
class BudgetSnapshot:
    remaining: int | None
    limit: int | None
    consumed_by_run: int
    reserved: int
    long_in_flight: int
    short_in_flight: int
    breaker_open: bool
    last_reconciled_at: float | None

    def as_event(self) -> dict:
        return {
            "remaining": self.remaining,
            "limit": self.limit,
            "consumed_by_run": self.consumed_by_run,
            "reserved": self.reserved,
            "long_in_flight": self.long_in_flight,
            "short_in_flight": self.short_in_flight,
            "breaker": "open" if self.breaker_open else "closed",
        }


@dataclass
class CostPlan:
    """Pre-flight estimate, shown to the user BEFORE a run starts.

    A large org that plans out over budget should be offered a degraded plan,
    not a run that dies at 60% and reports partial results as complete.
    """

    items: dict[str, int] = field(default_factory=dict)
    remaining_at_plan: int = 0
    safety_floor: int = 0

    @property
    def total(self) -> int:
        return sum(self.items.values())

    @property
    def reservable(self) -> int:
        return max(0, self.remaining_at_plan - self.safety_floor)

    @property
    def fits(self) -> bool:
        return self.total <= self.reservable

    def as_dict(self) -> dict:
        return {
            "items": self.items,
            "total": self.total,
            "remaining_at_plan": self.remaining_at_plan,
            "safety_floor": self.safety_floor,
            "reservable": self.reservable,
            "fits": self.fits,
        }


class Lease:
    """A reservation held for the duration of one HTTP call."""

    __slots__ = ("call_class", "cost", "_gov", "observed_remaining")

    def __init__(self, gov: SalesforceGovernor, call_class: CallClass, cost: int) -> None:
        self._gov = gov
        self.call_class = call_class
        self.cost = cost
        self.observed_remaining: int | None = None

    def observe(self, limit_header: str | None) -> None:
        """Reconcile against ``Sforce-Limit-Info`` from the response.

        Free (it rides on a call we already made) and it accounts for
        consumption by humans and other tools, which our own counter cannot see.
        """
        if not limit_header:
            return
        m = _LIMIT_RE.search(limit_header)
        if not m:
            return
        used, limit = int(m.group(1)), int(m.group(2))
        self.observed_remaining = limit - used
        self._gov._reconcile(remaining=limit - used, limit=limit)


class SalesforceGovernor:
    """Meters every Salesforce call.

    In-process rather than Redis-backed: Docker is unavailable here and the
    orchestrator runs a single worker guarded by a Postgres advisory lock, so
    process-local state is authoritative. The interface is deliberately the same
    one a Redis implementation would expose, so swapping it later is contained.
    """

    def __init__(self, settings: Settings | None = None) -> None:
        s = settings or get_settings()
        self._s = s
        self._short = asyncio.Semaphore(s.sf_max_concurrent_short)
        # One of the five slots is deliberately left free so a human using the
        # org during a run is not locked out.
        self._long = asyncio.Semaphore(s.sf_max_concurrent_long)

        self._remaining: int | None = None
        self._limit: int | None = None
        self._last_reconciled: float | None = None
        self._consumed_by_run = 0
        self._reserved = 0
        self._short_in_flight = 0
        self._long_in_flight = 0

        # Shared breaker: without it, N concurrent workers each discover
        # exhaustion independently and each burns a doomed request finding out.
        self._breaker_until: float = 0.0
        self._consecutive_trips = 0
        self._lock = asyncio.Lock()
        self._on_tick: Callable[[BudgetSnapshot], None] | None = None
        self._last_emit: float = 0.0
        self._emitted_once = False

    # -- telemetry -------------------------------------------------------------

    def on_tick(self, cb: Callable[[BudgetSnapshot], None]) -> None:
        """Register a sink for budget events so the UI meter stays live."""
        self._on_tick = cb

    def snapshot(self) -> BudgetSnapshot:
        return BudgetSnapshot(
            remaining=self._remaining,
            limit=self._limit,
            consumed_by_run=self._consumed_by_run,
            reserved=self._reserved,
            long_in_flight=self._long_in_flight,
            short_in_flight=self._short_in_flight,
            breaker_open=self.breaker_open,
            last_reconciled_at=self._last_reconciled,
        )

    @property
    def breaker_open(self) -> bool:
        return time.monotonic() < self._breaker_until

    # -- planning --------------------------------------------------------------

    def plan(self, estimates: dict[str, int], remaining: int) -> CostPlan:
        return CostPlan(
            items=dict(estimates),
            remaining_at_plan=remaining,
            safety_floor=self._s.sf_api_safety_floor,
        )

    def apply_capabilities(self, *, compression: float | None,
                           api_remaining: int | None = None,
                           api_max: int | None = None) -> None:
        """Adopt values discovered from the org this run is pointed at.

        Compression, edition and API allowance all vary per org, so anything
        measured against one org is a fallback, not a fact. Calling this replaces
        the configured default with what the target org actually does.
        """
        if compression and compression > 0:
            self._compression = float(compression)
            log.info("governor_compression_from_org", ratio=self._compression)
        if api_remaining is not None:
            self._remaining = api_remaining
        if api_max is not None:
            self._limit = api_max

    @property
    def compression(self) -> float:
        return getattr(self, "_compression", None) or self._s.sf_composite_compression_default

    def composite_cost(self, subrequests: int) -> int:
        """Estimated calls for one composite/batch of ``subrequests``.

        A composite call does NOT count as one request, contrary to most
        documentation: each subrequest carries a real cost, so a 25-subrequest
        batch compresses far less than 25x. The exact ratio is measured per org
        at connect time (see capabilities.probe); the configured value is only a
        fallback for when measurement fails.
        """
        return max(1, round(subrequests / self.compression))

    # -- leasing ---------------------------------------------------------------

    @asynccontextmanager
    async def lease(
        self,
        call_class: CallClass = CallClass.SHORT,
        cost: int = 1,
        *,
        wait_for_breaker: bool = True,
    ) -> AsyncIterator[Lease]:
        """Reserve budget and a concurrency slot for one call."""
        if wait_for_breaker:
            await self._await_breaker()

        self._guard_budget(cost)

        sem = self._long if call_class is CallClass.LONG else self._short
        await sem.acquire()
        if call_class is CallClass.LONG:
            self._long_in_flight += 1
        else:
            self._short_in_flight += 1

        self._reserved += cost
        lease = Lease(self, call_class, cost)
        try:
            yield lease
        finally:
            self._reserved -= cost
            self._consumed_by_run += cost
            # Only decrement the local estimate when the header did not already
            # give us ground truth, otherwise we would double-count.
            if lease.observed_remaining is None and self._remaining is not None:
                self._remaining -= cost
            if call_class is CallClass.LONG:
                self._long_in_flight -= 1
            else:
                self._short_in_flight -= 1
            sem.release()
            self._emit()

    def _guard_budget(self, cost: int) -> None:
        if self._remaining is None:
            return
        if self._remaining - cost < self._s.sf_api_safety_floor:
            raise BudgetExhausted(self._remaining, self._s.sf_api_safety_floor)

    async def _await_breaker(self) -> None:
        while self.breaker_open:
            await asyncio.sleep(min(1.0, max(0.05, self._breaker_until - time.monotonic())))

    # -- reconciliation and backoff --------------------------------------------

    def _reconcile(self, *, remaining: int, limit: int) -> None:
        self._remaining = remaining
        self._limit = limit
        self._last_reconciled = time.monotonic()
        self._consecutive_trips = 0

    def trip(self, *, base: float = 2.0, cap: float = 60.0) -> float:
        """Open the breaker after REQUEST_LIMIT_EXCEEDED.

        Decorrelated jitter: synchronised retries from many workers would
        otherwise arrive together and re-trip the limit immediately.
        """
        self._consecutive_trips += 1
        prev = min(cap, base * (2 ** min(self._consecutive_trips, 6)))
        delay = min(cap, random.uniform(base, prev * 3))
        self._breaker_until = time.monotonic() + delay
        log.warning("sf_budget_breaker_open", delay_s=round(delay, 1),
                    trips=self._consecutive_trips, remaining=self._remaining)
        self._emit(force=True)   # a state change must never be throttled away
        return delay

    def _emit(self, *, force: bool = False) -> None:
        """Publish a budget tick, throttled by TIME rather than call count.

        Count-based sampling (every Nth call) is wrong for a live meter: a short
        run that makes fewer than N calls emits nothing at all and the UI gauge
        appears frozen, while a burst of fast calls emits far more often than
        anyone can read. Throttling on elapsed time gives a steady refresh
        regardless of call rate.

        The first tick and every state change (breaker opening or closing) are
        always emitted, because those are exactly the moments a user is
        watching for and a throttle must never swallow them.
        """
        if not self._on_tick:
            return
        now = time.monotonic()
        if not (force or not self._emitted_once or now - self._last_emit >= _TICK_INTERVAL_S):
            return
        self._last_emit = now
        self._emitted_once = True
        try:
            self._on_tick(self.snapshot())
        except Exception:  # telemetry must never break the run
            log.exception("budget_tick_sink_failed")
